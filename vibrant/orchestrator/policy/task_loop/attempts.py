"""Attempt orchestration helpers for the task loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibrant.models.task import TaskStatus

from ...types import (
    AttemptCompletion,
    AttemptRecord,
    AttemptStatus,
    ReviewTicket,
    ReviewTicketStatus,
    TaskResult,
    ValidationOutcome,
    WorkflowStatus,
)
from . import dispatch, reviews, task_projection
from .models import DispatchLease, TaskLoopStage, TaskState, WORKER_INPUT_UNSUPPORTED_ERROR
from .prompting import prepare_task_execution

if TYPE_CHECKING:
    from .loop import TaskLoop


@dataclass(slots=True)
class AttemptRecoveryResult:
    attempt: AttemptRecord | None = None
    task_result: TaskResult | None = None


async def run_next_task(loop: TaskLoop) -> TaskResult | None:
    recovery = await recover_active_attempt(loop)
    if recovery.task_result is not None:
        return recovery.task_result
    if recovery.attempt is not None:
        lease = task_projection.build_attempt_lease(loop, recovery.attempt)
        return await await_attempt_result(loop, lease, recovery.attempt)

    leases = dispatch.select_next(loop, limit=1)
    if not leases:
        task_projection.maybe_complete_workflow(loop)
        return None

    lease = leases[0]
    loop._set_snapshot(
        stage=TaskLoopStage.CODING,
        active_lease=lease,
        active_attempt_id=None,
        blocking_reason=None,
    )

    prepared = prepare_task_execution(
        lease=lease,
        roadmap_store=loop.roadmap_store,
        consensus_store=loop.consensus_store,
        project_name=loop.consensus_store.project_name,
    )
    try:
        attempt = await loop.execution.start_attempt(prepared)
    except Exception as exc:
        loop._leased_task_ids.discard(lease.task_id)
        reason = str(exc)
        recoverable_attempt = loop.attempt_store.get_active_by_task(lease.task_id)
        if recoverable_attempt is not None:
            return _interrupt_attempt(
                loop,
                lease=lease,
                attempt_id=recoverable_attempt.attempt_id,
                task_id=lease.task_id,
                reason=reason,
            )
        task_projection.record_task_state(
            loop,
            lease.task_id,
            TaskState.BLOCKED,
            active_attempt_id=None,
            failure_reason=reason,
        )
        loop._set_snapshot(
            stage=TaskLoopStage.BLOCKED,
            active_lease=lease,
            active_attempt_id=None,
            blocking_reason=reason,
        )
        return TaskResult(task_id=lease.task_id, outcome="failed", error=reason)

    loop._leased_task_ids.discard(attempt.task_id)
    task_projection.record_task_state(loop, attempt.task_id, TaskState.ACTIVE, active_attempt_id=attempt.attempt_id)
    loop._set_snapshot(
        stage=TaskLoopStage.CODING,
        active_lease=lease,
        active_attempt_id=attempt.attempt_id,
        blocking_reason=None,
    )
    return await await_attempt_result(loop, lease, attempt)


async def await_attempt_result(
    loop: TaskLoop,
    lease: DispatchLease,
    attempt,
    *,
    auto_resume: bool = True,
) -> TaskResult:
    try:
        completion = await loop.execution.await_attempt_completion(attempt.attempt_id)
    except Exception as exc:
        reason = str(exc)
        return _interrupt_attempt(
            loop,
            lease=lease,
            attempt_id=attempt.attempt_id,
            task_id=attempt.task_id,
            reason=reason,
        )

    return await consume_attempt_completion(loop, lease, completion, auto_resume=auto_resume)


async def consume_attempt_completion(
    loop: TaskLoop,
    lease: DispatchLease,
    completion: AttemptCompletion,
    *,
    auto_resume: bool = True,
) -> TaskResult:
    if completion.status == "awaiting_input":
        reason = completion.error or WORKER_INPUT_UNSUPPORTED_ERROR
        loop.attempt_store.update(completion.attempt_id, status=AttemptStatus.FAILED)
        task_projection.record_task_state(
            loop,
            completion.task_id,
            TaskState.BLOCKED,
            active_attempt_id=completion.attempt_id,
            failure_reason=reason,
        )
        loop._set_snapshot(
            stage=TaskLoopStage.BLOCKED,
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            blocking_reason=reason,
        )
        return TaskResult(
            task_id=completion.task_id,
            outcome="failed",
            summary=completion.summary,
            error=reason,
        )

    if completion.status in {"failed", "cancelled"}:
        reason = completion.error or "Attempt cancelled"
        if _is_terminal_worker_input_failure(reason):
            terminal_status = AttemptStatus.CANCELLED if completion.status == "cancelled" else AttemptStatus.FAILED
            loop.attempt_store.update(completion.attempt_id, status=terminal_status)
            task_projection.record_task_state(
                loop,
                completion.task_id,
                TaskState.BLOCKED,
                active_attempt_id=completion.attempt_id,
                failure_reason=reason,
            )
            loop._set_snapshot(
                stage=TaskLoopStage.BLOCKED,
                active_lease=lease,
                active_attempt_id=completion.attempt_id,
                blocking_reason=reason,
            )
            return TaskResult(
                task_id=completion.task_id,
                outcome="failed",
                summary=completion.summary,
                error=completion.error,
            )
        run_record = loop.agent_run_store.get(completion.code_run_id)
        stop_reason = run_record.lifecycle.stop_reason if run_record is not None else None
        if auto_resume and stop_reason != "paused":
            recover = getattr(loop.execution, "recover_attempt", None)
            if callable(recover):
                attempt = loop.attempt_store.get(completion.attempt_id)
                if attempt is not None:
                    prepared = prepare_task_execution(
                        lease=lease,
                        roadmap_store=loop.roadmap_store,
                        consensus_store=loop.consensus_store,
                        project_name=loop.consensus_store.project_name,
                    )
                    try:
                        recovered = await recover(
                            completion.attempt_id,
                            prepared=prepared,
                        )
                    except Exception as exc:
                        reason = (
                            f"{reason}. Automatic reconnect failed: {exc}"
                            if reason
                            else f"Automatic reconnect failed: {exc}"
                        )
                    else:
                        task_projection.record_task_state(
                            loop,
                            recovered.task_id,
                            TaskState.ACTIVE,
                            active_attempt_id=recovered.attempt_id,
                        )
                        loop._set_snapshot(
                            stage=TaskLoopStage.CODING,
                            active_lease=lease,
                            active_attempt_id=recovered.attempt_id,
                            blocking_reason=None,
                        )
                        return await await_attempt_result(
                            loop,
                            lease,
                            recovered,
                            auto_resume=False,
                        )

        return _interrupt_attempt(
            loop,
            lease=lease,
            attempt_id=completion.attempt_id,
            task_id=completion.task_id,
            reason=reason,
        )

    validation = completion.validation
    if validation is None:
        try:
            validation = await loop.execution.run_validation_for_attempt(
                attempt_id=completion.attempt_id,
                code_summary=completion.summary,
            )
        except Exception as exc:
            reason = str(exc)
            loop.attempt_store.update(completion.attempt_id, status=AttemptStatus.FAILED)
            task_projection.record_task_state(
                loop,
                completion.task_id,
                TaskState.BLOCKED,
                active_attempt_id=completion.attempt_id,
                failure_reason=reason,
            )
            loop._set_snapshot(
                stage=TaskLoopStage.BLOCKED,
                active_lease=lease,
                active_attempt_id=completion.attempt_id,
                blocking_reason=reason,
            )
            return TaskResult(
                task_id=completion.task_id,
                outcome="failed",
                summary=completion.summary,
                error=reason,
            )
        completion.validation = validation

    loop._set_snapshot(
        stage=TaskLoopStage.VALIDATING,
        active_lease=lease,
        active_attempt_id=completion.attempt_id,
        blocking_reason=None,
    )
    loop.attempt_store.update(completion.attempt_id, status=AttemptStatus.VALIDATING)
    loop.attempt_store.update(
        completion.attempt_id,
        status=AttemptStatus.REVIEW_PENDING,
        validation_run_ids=list(validation.run_ids),
    )
    task_projection.record_task_state(
        loop,
        completion.task_id,
        TaskState.REVIEW_PENDING,
        active_attempt_id=completion.attempt_id,
        failure_reason=completion.error,
    )
    workspace = loop.workspace_service.get_workspace(task_id=completion.task_id, workspace_id=completion.workspace_ref)
    diff = loop.workspace_service.collect_review_diff(workspace)
    workspace = loop.workspace_service.get_workspace(task_id=completion.task_id, workspace_id=completion.workspace_ref)
    ticket = reviews.create_review_ticket(
        loop,
        completion,
        workspace=workspace,
        diff_ref=diff.path if diff is not None else completion.diff_ref,
    )
    loop._set_snapshot(
        stage=TaskLoopStage.REVIEW_PENDING,
        active_lease=lease,
        active_attempt_id=completion.attempt_id,
        blocking_reason=None,
    )
    auto_review_result = await _auto_review_ticket(
        loop,
        lease=lease,
        completion=completion,
        ticket=ticket,
        validation=validation,
        workspace_path=workspace.path,
    )
    if auto_review_result is not None:
        return auto_review_result

    return TaskResult(
        task_id=completion.task_id,
        outcome="review_pending",
        summary=validation.summary or completion.summary,
        error=completion.error,
        worktree_path=workspace.path,
    )


async def recover_active_attempt(loop: TaskLoop) -> AttemptRecoveryResult:
    background_task = loop.next_background_attempt_task()
    if background_task is not None:
        return AttemptRecoveryResult(task_result=await background_task)

    workflow = loop.workflow_snapshot()
    reason = dispatch.task_execution_block_reason(loop, workflow)
    if reason is not None:
        task_projection.set_blocked_if_needed(loop, reason)
        return AttemptRecoveryResult()
    if workflow.status is not WorkflowStatus.EXECUTING:
        task_projection.set_blocked_if_needed(loop, None)
        return AttemptRecoveryResult()

    list_selector = getattr(loop.execution, "list_active_attempt_executions", None)
    active_sessions = list_selector() if callable(list_selector) else []
    durable_completion_getter = getattr(loop.execution, "durable_attempt_completion", None)
    if callable(durable_completion_getter):
        for session in active_sessions:
            durable_completion = durable_completion_getter(session.attempt_id)
            if durable_completion is None:
                continue
            attempt = loop.attempt_store.get(session.attempt_id)
            if attempt is None:
                continue
            lease = task_projection.build_attempt_lease(loop, attempt)
            return AttemptRecoveryResult(
                task_result=await consume_attempt_completion(loop, lease, durable_completion),
            )

    recover_selector = getattr(loop.execution, "next_attempt_to_recover", None)
    session = recover_selector() if callable(recover_selector) else None
    if session is None:
        return AttemptRecoveryResult()
    attempt = loop.attempt_store.get(session.attempt_id)
    if attempt is None:
        return AttemptRecoveryResult()
    lease = task_projection.build_attempt_lease(loop, attempt)
    loop._set_snapshot(
        stage=TaskLoopStage.CODING,
        active_lease=lease,
        active_attempt_id=attempt.attempt_id,
        blocking_reason=None,
    )
    prepared = prepare_task_execution(
        lease=lease,
        roadmap_store=loop.roadmap_store,
        consensus_store=loop.consensus_store,
        project_name=loop.consensus_store.project_name,
    )
    try:
        recovered = await loop.execution.recover_attempt(
            attempt.attempt_id,
            prepared=prepared,
        )
    except Exception as exc:
        reason = str(exc)
        return AttemptRecoveryResult(
            task_result=_interrupt_attempt(
                loop,
                lease=lease,
                attempt_id=attempt.attempt_id,
                task_id=attempt.task_id,
                reason=reason,
            )
        )
    task_projection.record_task_state(
        loop,
        recovered.task_id,
        TaskState.ACTIVE,
        active_attempt_id=recovered.attempt_id,
    )
    return AttemptRecoveryResult(attempt=recovered)


async def run_until_blocked(loop: TaskLoop) -> list[TaskResult]:
    results: list[TaskResult] = []
    while True:
        result = await run_next_task(loop)
        if result is None:
            break
        results.append(result)
        if result.outcome in {"awaiting_user", "review_pending", "failed", "interrupted"}:
            break
    return results


async def resume_attempt(loop: TaskLoop, attempt_id: str) -> AttemptRecord:
    _require_attempt_resume_allowed(loop)
    attempt = loop.attempt_store.get(attempt_id)
    if attempt is None:
        raise KeyError(f"Attempt not found: {attempt_id}")
    lease = task_projection.build_attempt_lease(loop, attempt)
    prepared = prepare_task_execution(
        lease=lease,
        roadmap_store=loop.roadmap_store,
        consensus_store=loop.consensus_store,
        project_name=loop.consensus_store.project_name,
    )
    recovered = await loop.execution.resume_attempt(attempt_id, prepared=prepared)
    task_projection.record_task_state(
        loop,
        recovered.task_id,
        TaskState.ACTIVE,
        active_attempt_id=recovered.attempt_id,
    )
    loop._set_snapshot(
        stage=TaskLoopStage.CODING,
        active_lease=lease,
        active_attempt_id=recovered.attempt_id,
        blocking_reason=None,
    )
    _start_background_attempt_completion(loop, lease=lease, attempt=recovered)
    return recovered


async def resume_active_attempt(loop: TaskLoop) -> AttemptRecord | None:
    _require_attempt_resume_allowed(loop)
    session = loop.execution.next_attempt_to_recover()
    if session is None:
        return None
    return await resume_attempt(loop, session.attempt_id)


async def _auto_review_ticket(
    loop: TaskLoop,
    *,
    lease: DispatchLease,
    completion: AttemptCompletion,
    ticket: ReviewTicket,
    validation: ValidationOutcome,
    workspace_path: str,
) -> TaskResult | None:
    if loop.gatekeeper_loop is None:
        return None

    try:
        submission = await loop.gatekeeper_loop.submit_review(
            ticket,
            validation=validation,
            code_summary=completion.summary,
        )
        review_result = await loop.gatekeeper_loop.wait_for_submission(submission)
    except Exception as exc:
        reason = str(exc)
        loop._set_snapshot(
            stage=TaskLoopStage.BLOCKED,
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            blocking_reason=reason,
        )
        return TaskResult(
            task_id=completion.task_id,
            outcome="failed",
            summary=validation.summary or completion.summary,
            error=reason,
            worktree_path=workspace_path,
        )

    review_error = getattr(review_result, "error", None)
    if isinstance(review_error, str) and review_error:
        loop._set_snapshot(
            stage=TaskLoopStage.BLOCKED,
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            blocking_reason=review_error,
        )
        return TaskResult(
            task_id=completion.task_id,
            outcome="failed",
            summary=validation.summary or completion.summary,
            error=review_error,
            worktree_path=workspace_path,
        )

    resolved_ticket = loop.review_ticket_store.get(ticket.ticket_id)
    if resolved_ticket is None:
        return None

    if resolved_ticket.status is ReviewTicketStatus.ACCEPTED:
        task = loop.roadmap_store.get_task(ticket.task_id)
        if task is not None and task.status is TaskStatus.ACCEPTED:
            return TaskResult(
                task_id=ticket.task_id,
                outcome="accepted",
                summary=validation.summary or completion.summary,
                worktree_path=workspace_path,
            )
        return TaskResult(
            task_id=ticket.task_id,
            outcome="review_pending",
            summary=validation.summary or completion.summary,
            worktree_path=workspace_path,
        )

    if resolved_ticket.status is ReviewTicketStatus.RETRY:
        return TaskResult(
            task_id=ticket.task_id,
            outcome="retried",
            summary=validation.summary or completion.summary,
            error=resolved_ticket.resolution_reason,
            worktree_path=workspace_path,
        )

    if resolved_ticket.status is ReviewTicketStatus.ESCALATED:
        reason = resolved_ticket.resolution_reason or "Task escalated"
        loop._set_snapshot(
            stage=TaskLoopStage.BLOCKED,
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            blocking_reason=reason,
        )
        return TaskResult(
            task_id=ticket.task_id,
            outcome="escalated",
            summary=validation.summary or completion.summary,
            error=reason,
            worktree_path=workspace_path,
        )

    pending_review_question = _pending_review_question(loop, task_id=ticket.task_id)
    if pending_review_question is not None:
        reason = pending_review_question.text
        loop._set_snapshot(
            stage=TaskLoopStage.BLOCKED,
            active_lease=lease,
            active_attempt_id=completion.attempt_id,
            blocking_reason=reason,
        )
        return TaskResult(
            task_id=ticket.task_id,
            outcome="awaiting_user",
            summary=validation.summary or completion.summary,
            error=reason,
            worktree_path=workspace_path,
        )

    return None


def _pending_review_question(loop: TaskLoop, *, task_id: str):
    for question in loop.question_store.list_pending():
        if question.task_id == task_id:
            return question
        if question.blocking_scope == "review":
            return question
    return None


def _require_attempt_resume_allowed(loop: TaskLoop) -> None:
    workflow = loop.workflow_snapshot()
    reason = dispatch.task_execution_block_reason(loop, workflow)
    if reason is not None:
        raise RuntimeError(reason)
    if workflow.status is not WorkflowStatus.EXECUTING:
        raise RuntimeError(f"Workflow is not executing: {workflow.status.value}")


def _start_background_attempt_completion(
    loop: TaskLoop,
    *,
    lease: DispatchLease,
    attempt: AttemptRecord,
) -> None:
    if loop.background_attempt_task(attempt.attempt_id) is not None:
        return

    async def _consume() -> TaskResult:
        return await await_attempt_result(loop, lease, attempt)

    task = asyncio.create_task(
        _consume(),
        name=f"task-loop-resume-{attempt.attempt_id}",
    )
    loop.track_background_attempt_task(attempt.attempt_id, task)


def _interrupt_attempt(
    loop: TaskLoop,
    *,
    lease: DispatchLease,
    attempt_id: str,
    task_id: str,
    reason: str,
) -> TaskResult:
    loop.attempt_store.update(attempt_id, status=AttemptStatus.RECOVERY_PENDING)
    task_projection.record_task_interrupted(
        loop,
        task_id,
        active_attempt_id=attempt_id,
        failure_reason=reason,
    )
    loop._set_snapshot(
        stage=TaskLoopStage.BLOCKED,
        active_lease=lease,
        active_attempt_id=attempt_id,
        blocking_reason=reason,
    )
    return TaskResult(
        task_id=task_id,
        outcome="interrupted",
        error=reason,
    )


def _is_terminal_worker_input_failure(reason: str | None) -> bool:
    if not reason:
        return False
    normalized = reason.lower()
    return (
        "interactive provider requests are not supported during autonomous task execution" in normalized
        or "worker runs must auto-reject interactive requests" in normalized
    )
