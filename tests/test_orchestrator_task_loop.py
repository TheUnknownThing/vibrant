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


async def _queue_review_pending_attempt(orchestrator, monkeypatch, *, workspace_setup=None):
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
        if workspace_setup is not None:
            workspace_setup(Path(workspace.path))
        attempt = orchestrator.attempt_store.create(
            task_id=lease.task_id,
            task_definition_version=lease.task_definition_version,
            workspace_id=workspace.workspace_id,
        )
        return orchestrator.attempt_store.update(
            attempt.attempt_id,
            status=AttemptStatus.RUNNING,
            code_run_id="run-1",
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
            code_run_id="run-1",
            workspace_ref=attempt.workspace_id,
            diff_ref=None,
            validation=ValidationOutcome(
                status="skipped",
                run_ids=[],
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


@pytest.mark.asyncio
async def test_retry_review_ticket_requeues_task_for_redispatch(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    await _queue_review_pending_attempt(orchestrator, monkeypatch)
    ticket = orchestrator.list_pending_review_tickets()[0]

    resolution = orchestrator.retry_review_ticket(ticket.ticket_id, failure_reason="Address review feedback")
    task = orchestrator.get_task("task-1")
    attempt = orchestrator.attempt_store.get(ticket.attempt_id)
    leases = orchestrator.task_loop.select_next(limit=1)

    assert resolution.decision == "retry"
    assert task is not None and task.status is TaskStatus.QUEUED
    assert task.retry_count == 1
    assert attempt is not None and attempt.status is AttemptStatus.RETRY_PENDING
    assert [lease.task_id for lease in leases] == ["task-1"]


@pytest.mark.asyncio
async def test_failed_completion_marks_attempt_failed_and_inactive(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    attempt_id: str | None = None

    async def fake_start_attempt(prepared):
        nonlocal attempt_id
        lease = prepared.lease
        workspace = orchestrator.workspace_service.prepare_task_workspace(lease.task_id, branch_hint=lease.branch_hint)
        attempt = orchestrator.attempt_store.create(
            task_id=lease.task_id,
            task_definition_version=lease.task_definition_version,
            workspace_id=workspace.workspace_id,
        )
        attempt_id = attempt.attempt_id
        return orchestrator.attempt_store.update(
            attempt.attempt_id,
            status=AttemptStatus.RUNNING,
            code_run_id="run-1",
            conversation_id="attempt-conv-1",
        )

    async def fake_await_attempt_completion(attempt_id: str):
        attempt = orchestrator.attempt_store.get(attempt_id)
        assert attempt is not None
        return AttemptCompletion(
            attempt_id=attempt.attempt_id,
            task_id=attempt.task_id,
            status="failed",
            code_run_id="run-1",
            workspace_ref=attempt.workspace_id,
            diff_ref=None,
            validation=None,
            summary="Implementation failed",
            error="boom",
            conversation_ref=attempt.conversation_id,
            provider_events_ref=None,
        )

    orchestrator.task_loop.execution = SimpleNamespace(
        start_attempt=fake_start_attempt,
        await_attempt_completion=fake_await_attempt_completion,
    )

    result = await orchestrator.run_next_task()
    assert attempt_id is not None
    attempt = orchestrator.attempt_store.get(attempt_id)

    assert result is not None and result.outcome == "failed"
    assert attempt is not None and attempt.status is AttemptStatus.FAILED
    assert orchestrator.attempt_store.list_active() == []


@pytest.mark.asyncio
async def test_start_attempt_failure_releases_task_lease(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)

    async def fake_start_attempt(prepared):
        del prepared
        raise RuntimeError("workspace bootstrap failed")

    orchestrator.task_loop.execution = SimpleNamespace(start_attempt=fake_start_attempt)

    result = await orchestrator.run_next_task()

    assert result is not None and result.outcome == "failed"
    assert result.error == "workspace bootstrap failed"
    assert orchestrator.task_loop._leased_task_ids == set()


@pytest.mark.asyncio
async def test_accept_review_ticket_merges_workspace_changes_back_into_project(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    project_file = tmp_path / "demo.txt"
    project_file.write_text("root\n", encoding="utf-8")

    def workspace_setup(workspace_path: Path) -> None:
        (workspace_path / "demo.txt").write_text("workspace-change\n", encoding="utf-8")

    await _queue_review_pending_attempt(orchestrator, monkeypatch, workspace_setup=workspace_setup)
    ticket = orchestrator.list_pending_review_tickets()[0]

    resolution = orchestrator.accept_review_ticket(ticket.ticket_id)

    assert resolution.decision == "accept"
    assert project_file.read_text(encoding="utf-8") == "workspace-change\n"


@pytest.mark.asyncio
async def test_pending_review_ticket_can_be_resolved_after_restart(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    await _queue_review_pending_attempt(orchestrator, monkeypatch)
    ticket = orchestrator.list_pending_review_tickets()[0]

    restarted = create_orchestrator(tmp_path)
    resolution = restarted.accept_review_ticket(ticket.ticket_id)
    task = restarted.get_task("task-1")

    assert resolution.decision == "accept"
    assert task is not None and task.status is TaskStatus.ACCEPTED
