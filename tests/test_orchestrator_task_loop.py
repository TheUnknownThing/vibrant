from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.policy import TaskLoopStage
from vibrant.orchestrator.types import AttemptCompletion, AttemptStatus, MergeOutcome, ValidationOutcome, WorkflowStatus
from vibrant.project_init import initialize_project


def _prepare_orchestrator(tmp_path: Path):
    initialize_project(tmp_path)
    orchestrator = create_orchestrator(tmp_path)
    orchestrator.roadmap_store.add_task(TaskInfo(id="task-1", title="Implement the layered orchestrator"), index=0)
    orchestrator.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)
    return orchestrator


async def _queue_review_pending_attempt(orchestrator, monkeypatch):
    statuses: list[AttemptStatus] = []
    original_update = orchestrator.attempt_store.update

    def record_update(attempt_id: str, **kwargs):
        status = kwargs.get("status")
        if isinstance(status, AttemptStatus):
            statuses.append(status)
        return original_update(attempt_id, **kwargs)

    monkeypatch.setattr(orchestrator.attempt_store, "update", record_update)

    async def fake_start_attempt(prepared):
        lease = prepared.lease
        workspace = orchestrator.workspace_service.prepare_task_workspace(lease.task_id, branch_hint=lease.branch_hint)
        attempt = orchestrator.attempt_store.create(
            task_id=lease.task_id,
            task_definition_version=lease.task_definition_version,
            workspace_id=workspace.workspace_id,
        )
        return orchestrator.attempt_store.update(
            attempt.attempt_id,
            status=AttemptStatus.RUNNING,
            code_agent_id="agent-1",
            conversation_id="attempt-conv-1",
        )

    async def fake_await_attempt_completion(attempt_id: str):
        attempt = orchestrator.attempt_store.get(attempt_id)
        assert attempt is not None
        orchestrator.attempt_store.update(attempt_id, status=AttemptStatus.VALIDATION_PENDING)
        return AttemptCompletion(
            attempt_id=attempt.attempt_id,
            task_id=attempt.task_id,
            status="succeeded",
            code_agent_id="agent-1",
            workspace_ref=attempt.workspace_id,
            diff_ref=None,
            validation=ValidationOutcome(
                status="skipped",
                agent_ids=[],
                summary="Validation not configured yet.",
            ),
            summary="Implementation completed",
            error=None,
            conversation_ref=attempt.conversation_id,
            provider_events_ref=None,
        )

    orchestrator.task_loop.execution = SimpleNamespace(
        start_attempt=fake_start_attempt,
        await_attempt_completion=fake_await_attempt_completion,
    )
    result = await orchestrator.run_next_task()
    return result, statuses


@pytest.mark.asyncio
async def test_run_next_task_enters_validation_then_review(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)

    result, statuses = await _queue_review_pending_attempt(orchestrator, monkeypatch)

    assert result is not None
    assert result.outcome == "review_pending"
    assert AttemptStatus.VALIDATING in statuses
    assert AttemptStatus.REVIEW_PENDING in statuses
    assert orchestrator.task_loop.snapshot().stage is TaskLoopStage.REVIEW_PENDING
    assert len(orchestrator.list_pending_review_tickets()) == 1


@pytest.mark.asyncio
async def test_accept_review_ticket_enters_merge_stage_and_completes_task(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    _, statuses = await _queue_review_pending_attempt(orchestrator, monkeypatch)
    ticket = orchestrator.list_pending_review_tickets()[0]

    def fake_merge(workspace):
        return MergeOutcome(status="merged", message=f"Merged {workspace.workspace_id}", follow_up_required=False)

    monkeypatch.setattr(orchestrator.workspace_service, "merge_task_result", fake_merge)

    resolution = orchestrator.accept_review_ticket(ticket.ticket_id)
    task = orchestrator.get_task("task-1")
    attempt = orchestrator.attempt_store.get(ticket.attempt_id)

    assert resolution.decision == "accept"
    assert AttemptStatus.MERGE_PENDING in statuses
    assert AttemptStatus.ACCEPTED in statuses
    assert task is not None and task.status is TaskStatus.ACCEPTED
    assert attempt is not None and attempt.status is AttemptStatus.ACCEPTED
    assert orchestrator.task_loop.snapshot().stage is TaskLoopStage.COMPLETED


@pytest.mark.asyncio
async def test_merge_failure_snapshot_only_lists_follow_up_review_ticket(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    await _queue_review_pending_attempt(orchestrator, monkeypatch)
    ticket = orchestrator.list_pending_review_tickets()[0]

    def fake_merge(workspace):
        return MergeOutcome(status="failed", message=f"Merge failed for {workspace.workspace_id}", follow_up_required=True)

    monkeypatch.setattr(orchestrator.workspace_service, "merge_task_result", fake_merge)

    resolution = orchestrator.accept_review_ticket(ticket.ticket_id)
    pending_ids = [item.ticket_id for item in orchestrator.list_pending_review_tickets()]
    snapshot = orchestrator.task_loop.snapshot()

    assert resolution.follow_up_ticket_id is not None
    assert pending_ids == [resolution.follow_up_ticket_id]
    assert snapshot.pending_review_ticket_ids == (resolution.follow_up_ticket_id,)
    assert snapshot.stage is TaskLoopStage.REVIEW_PENDING
