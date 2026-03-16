"""End-to-end backend policy-loop coverage using the fixture provider.

Run the focused suite with:

    VIBRANT_E2E_ARTIFACT_ROOT=/tmp/vibrant-e2e uv run pytest tests/e2e/test_policy_loops_e2e.py -q -s

Artifacts are preserved under the configured artifact root for manual
inspection of `.vibrant/` state, provider logs, conversations, worktrees, and
review diffs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.artifacts import E2EProjectContext
from tests.e2e.fixture_provider import FixtureProviderAdapter
from vibrant.agents.gatekeeper import Gatekeeper
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.types import (
    AttemptStatus,
    GatekeeperLifecycleStatus,
    QuestionStatus,
    ReviewTicketStatus,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_ndjson(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _build_task(*, task_id: str, title: str, prompt: str, acceptance: list[str]) -> TaskInfo:
    return TaskInfo(
        id=task_id,
        title=title,
        prompt=prompt,
        acceptance_criteria=acceptance,
    )


def _create_restarted_orchestrator(project: E2EProjectContext):
    gatekeeper = Gatekeeper(
        project.project_root,
        adapter_factory=FixtureProviderAdapter,
    )
    return create_orchestrator(
        project.project_root,
        gatekeeper=gatekeeper,
        adapter_factory=FixtureProviderAdapter,
    )


async def _wait_for_gatekeeper_input_request(orchestrator, run_id: str, *, timeout: float = 3.0) -> Any:
    async with asyncio.timeout(timeout):
        while True:
            run = orchestrator.control_plane.get_run(run_id)
            if run is not None and run.runtime.awaiting_input and run.runtime.input_requests:
                return run
            await asyncio.sleep(0.01)


async def _respond_to_gatekeeper_request(
    orchestrator,
    *,
    run_id: str,
    request_id: int | str,
    result: dict[str, Any],
) -> None:
    await orchestrator.control_plane.respond_to_gatekeeper_request(
        run_id,
        request_id,
        result=result,
    )


async def _run_single_task_to_review_pending(
    orchestrator,
    *,
    task_id: str,
    prompt: str,
) -> tuple[Any, Any, Any]:
    orchestrator.control_plane.add_task(
        _build_task(
            task_id=task_id,
            title="Deterministic worker change",
            prompt=prompt,
            acceptance=["demo.txt contains the requested content"],
        )
    )
    orchestrator.control_plane.end_planning_phase()
    results = await orchestrator.control_plane.run_until_blocked()
    assert len(results) == 1
    result = results[0]
    ticket = orchestrator.control_plane.list_pending_review_tickets()[0]
    attempt = orchestrator.control_plane.get_attempt_execution(ticket.attempt_id)
    return result, ticket, attempt


def _assert_log_contains(path: str | Path, *, event: str) -> None:
    lines = _read_ndjson(path)
    assert any(line["event"] == event for line in lines), f"{event!r} not found in {path}"


@pytest.mark.asyncio
async def test_gatekeeper_question_resolution_e2e(e2e_project: E2EProjectContext, e2e_orchestrator) -> None:
    question = e2e_orchestrator.control_plane.request_user_decision(
        "Should we start with OAuth or email auth first?\n[mock:question]",
        blocking_scope="planning",
    )

    submission = await e2e_orchestrator.control_plane.submit_user_input("Start with OAuth first.")
    pending_before = e2e_orchestrator.control_plane.get_question(question.question_id)
    state_after_submit = e2e_orchestrator.control_plane.gatekeeper_state()

    assert pending_before is not None
    assert pending_before.status is QuestionStatus.PENDING
    assert state_after_submit.session.lifecycle_state in {
        GatekeeperLifecycleStatus.STARTING,
        GatekeeperLifecycleStatus.RUNNING,
        GatekeeperLifecycleStatus.AWAITING_USER,
    }

    handle_snapshot = await _wait_for_gatekeeper_input_request(e2e_orchestrator, submission.run_id)
    awaiting_state = e2e_orchestrator.control_plane.gatekeeper_state()

    assert awaiting_state.session.lifecycle_state is GatekeeperLifecycleStatus.AWAITING_USER
    assert len(handle_snapshot.runtime.input_requests) == 1

    await _respond_to_gatekeeper_request(
        e2e_orchestrator,
        run_id=submission.run_id,
        request_id=handle_snapshot.runtime.input_requests[0].request_id,
        result={"answer": "Use OAuth first."},
    )

    result = await e2e_orchestrator.control_plane.wait_for_gatekeeper_submission(submission)
    resolved = e2e_orchestrator.control_plane.get_question(question.question_id)
    gatekeeper_run = e2e_orchestrator.control_plane.get_run(submission.run_id)
    conversation = e2e_orchestrator.control_plane.conversation(submission.conversation_id)
    snapshot = e2e_project.snapshot_orchestrator(e2e_orchestrator)

    assert result.error is None
    assert resolved is not None
    assert resolved.status is QuestionStatus.RESOLVED
    assert resolved.answer == "Start with OAuth first."
    assert e2e_orchestrator.control_plane.gatekeeper_state().session.lifecycle_state is GatekeeperLifecycleStatus.IDLE
    assert gatekeeper_run is not None
    assert gatekeeper_run.provider.native_event_log is not None
    assert gatekeeper_run.provider.canonical_event_log is not None
    assert Path(gatekeeper_run.provider.native_event_log).exists()
    assert Path(gatekeeper_run.provider.canonical_event_log).exists()
    assert conversation is not None
    assert any(entry.role == "user" and entry.kind == "message" for entry in conversation.entries)
    assert any(
        entry.kind == "status"
        and entry.text == "Fixture provider needs one follow-up decision before continuing."
        for entry in conversation.entries
    )
    assert snapshot["question_ids"] == [question.question_id]
    _assert_log_contains(gatekeeper_run.provider.canonical_event_log, event="request.opened")
    _assert_log_contains(gatekeeper_run.provider.canonical_event_log, event="request.resolved")


@pytest.mark.asyncio
async def test_task_loop_happy_path_review_restart_accept_e2e(
    e2e_project: E2EProjectContext,
    e2e_orchestrator,
) -> None:
    result, ticket, attempt = await _run_single_task_to_review_pending(
        e2e_orchestrator,
        task_id="task-happy-path",
        prompt=(
            "Update demo.txt so it contains workspace-change.\n"
            "Leave enough evidence in logs for review.\n"
            "[mock:write demo.txt]\n"
            "[mock:content workspace-change]\n"
            "[mock:tool]"
        ),
    )
    task = e2e_orchestrator.control_plane.get_task("task-happy-path")
    review_diff = Path(ticket.diff_ref or "")
    worker_run = e2e_orchestrator.control_plane.get_run(ticket.run_id)

    assert result.outcome == "review_pending"
    assert task is not None and task.status is TaskStatus.COMPLETED
    assert attempt is not None and attempt.status is AttemptStatus.REVIEW_PENDING
    assert e2e_project.demo_path.read_text(encoding="utf-8") == "baseline\n"
    assert ticket.base_commit is not None
    assert ticket.result_commit is not None
    assert ticket.diff_ref is not None
    assert review_diff.exists()
    assert "diff --git a/demo.txt b/demo.txt" in review_diff.read_text(encoding="utf-8")
    assert worker_run is not None
    assert worker_run.provider.canonical_event_log is not None
    _assert_log_contains(worker_run.provider.canonical_event_log, event="tool.call.started")

    e2e_project.snapshot_orchestrator(e2e_orchestrator)
    await e2e_orchestrator.shutdown()

    restarted = _create_restarted_orchestrator(e2e_project)
    try:
        resolution = restarted.control_plane.accept_review_ticket(ticket.ticket_id)
        accepted_task = restarted.control_plane.get_task("task-happy-path")
        accepted_attempt = restarted.control_plane.get_attempt_execution(ticket.attempt_id)
        accepted_ticket = restarted.control_plane.get_review_ticket(ticket.ticket_id)
        snapshot = e2e_project.snapshot_orchestrator(restarted)

        assert resolution.decision == "accept"
        assert accepted_task is not None and accepted_task.status is TaskStatus.ACCEPTED
        assert accepted_attempt is not None and accepted_attempt.status is AttemptStatus.ACCEPTED
        assert accepted_ticket is not None
        assert accepted_ticket.status is ReviewTicketStatus.ACCEPTED
        assert e2e_project.demo_path.read_text(encoding="utf-8") == "workspace-change\n"
        assert ticket.ticket_id in snapshot["review_ticket_ids"]
        assert ticket.attempt_id in snapshot["attempt_ids"]
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_task_loop_retry_cycle_e2e(e2e_project: E2EProjectContext, e2e_orchestrator) -> None:
    first_result, first_ticket, first_attempt = await _run_single_task_to_review_pending(
        e2e_orchestrator,
        task_id="task-retry-cycle",
        prompt=(
            "Update demo.txt so it contains first-change.\n"
            "[mock:write demo.txt]\n"
            "[mock:content first-change]"
        ),
    )

    retry_resolution = e2e_orchestrator.control_plane.retry_review_ticket(
        first_ticket.ticket_id,
        failure_reason="Need a different deterministic result.",
        prompt_patch=(
            "Update demo.txt so it contains second-change.\n"
            "[mock:write demo.txt]\n"
            "[mock:content second-change]\n"
            "[mock:tool]"
        ),
    )
    second_results = await e2e_orchestrator.control_plane.run_until_blocked()
    second_ticket = e2e_orchestrator.control_plane.list_pending_review_tickets()[0]
    second_attempt = e2e_orchestrator.control_plane.get_attempt_execution(second_ticket.attempt_id)
    task = e2e_orchestrator.control_plane.get_task("task-retry-cycle")
    review_history = e2e_orchestrator.control_plane.list_review_tickets(task_id="task-retry-cycle")
    first_ticket_state = e2e_orchestrator.control_plane.get_review_ticket(first_ticket.ticket_id)

    assert first_result.outcome == "review_pending"
    assert retry_resolution.decision == "retry"
    assert len(second_results) == 1
    assert second_results[0].outcome == "review_pending"
    assert first_attempt is not None
    assert second_attempt is not None
    assert first_ticket.status is ReviewTicketStatus.PENDING
    assert first_ticket_state is not None and first_ticket_state.status is ReviewTicketStatus.RETRY
    assert task is not None and task.retry_count == 1
    assert first_attempt.attempt_id != second_attempt.attempt_id
    assert first_ticket.ticket_id != second_ticket.ticket_id
    assert first_ticket.run_id != second_ticket.run_id
    assert len(review_history) == 2

    resolution = e2e_orchestrator.control_plane.accept_review_ticket(second_ticket.ticket_id)
    accepted_task = e2e_orchestrator.control_plane.get_task("task-retry-cycle")
    accepted_attempt = e2e_orchestrator.control_plane.get_attempt_execution(second_ticket.attempt_id)
    snapshot = e2e_project.snapshot_orchestrator(e2e_orchestrator)

    assert resolution.decision == "accept"
    assert accepted_task is not None and accepted_task.status is TaskStatus.ACCEPTED
    assert accepted_attempt is not None and accepted_attempt.status is AttemptStatus.ACCEPTED
    assert e2e_project.demo_path.read_text(encoding="utf-8") == "second-change\n"
    assert len(snapshot["attempt_ids"]) == 2
    assert len(snapshot["review_ticket_ids"]) == 2


@pytest.mark.asyncio
async def test_task_loop_worker_request_is_rejected_e2e(e2e_project: E2EProjectContext, e2e_orchestrator) -> None:
    e2e_orchestrator.control_plane.add_task(
        _build_task(
            task_id="task-worker-request-rejected",
            title="Worker request rejection",
            prompt="Ask for interactive input.\n[mock:question]",
            acceptance=["worker run fails instead of awaiting user input"],
        )
    )
    e2e_orchestrator.control_plane.end_planning_phase()

    results = await e2e_orchestrator.control_plane.run_until_blocked()
    task = e2e_orchestrator.control_plane.get_task("task-worker-request-rejected")
    attempts = e2e_orchestrator.control_plane.list_attempt_executions(task_id="task-worker-request-rejected")
    code_attempt = attempts[0] if attempts else None
    worker_run = (
        e2e_orchestrator.control_plane.get_run(code_attempt.run_id)
        if code_attempt is not None and code_attempt.run_id is not None
        else None
    )
    pending_tickets = e2e_orchestrator.control_plane.list_pending_review_tickets()
    snapshot = e2e_project.snapshot_orchestrator(e2e_orchestrator)

    assert len(results) == 1
    assert results[0].outcome == "failed"
    assert code_attempt is not None and code_attempt.status is AttemptStatus.FAILED
    assert task is not None and task.status is TaskStatus.FAILED
    assert pending_tickets == []
    assert snapshot["diff_paths"] == []
    assert worker_run is not None
    assert worker_run.provider.canonical_event_log is not None
    canonical_lines = _read_ndjson(worker_run.provider.canonical_event_log)
    assert any(line["event"] == "request.opened" for line in canonical_lines)
    assert any(line["event"] == "runtime.error" for line in canonical_lines)
    assert not any((e2e_project.vibrant_dir / "review-diffs").glob("*.diff"))
    reviews_path = e2e_project.vibrant_dir / "reviews.json"
    if reviews_path.exists():
        assert _read_json(reviews_path) == {}
