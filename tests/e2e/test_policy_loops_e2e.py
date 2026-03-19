"""End-to-end backend policy-loop coverage using the fixture provider.

Run the focused suite with:

    VIBRANT_E2E_ARTIFACT_ROOT=/tmp/vibrant-e2e uv run pytest tests/e2e/test_policy_loops_e2e.py -q -s

Artifacts are preserved under the configured artifact root for manual
inspection of `.vibrant/` state, provider logs, conversations, worktrees, and
review diffs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.artifacts import E2EProjectContext
from tests.e2e.fixture_provider import FixtureProviderAdapter
from vibrant.agents.gatekeeper import Gatekeeper
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator import OrchestratorFacade, create_orchestrator
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


def _facade(orchestrator) -> OrchestratorFacade:
    return OrchestratorFacade(orchestrator)


async def _wait_for_gatekeeper_state(orchestrator, *states: GatekeeperLifecycleStatus, timeout: float = 8.0) -> Any:
    facade = _facade(orchestrator)
    expected = set(states)
    async with asyncio.timeout(timeout):
        while True:
            state = facade.gatekeeper_state()
            if state.session.lifecycle_state in expected:
                return state
            await asyncio.sleep(0.01)


async def _wait_for_gatekeeper_input_request(orchestrator, run_id: str, *, timeout: float = 8.0) -> Any:
    facade = _facade(orchestrator)
    async with asyncio.timeout(timeout):
        while True:
            run = facade.get_run(run_id)
            if run is not None and run.runtime.awaiting_input and run.runtime.input_requests:
                return run
            await asyncio.sleep(0.01)


async def _wait_for_attempt_execution(
    orchestrator,
    task_id: str,
    *,
    status: AttemptStatus | None = None,
    live: bool | None = None,
    run_stop_reason: str | None = None,
    timeout: float = 8.0,
) -> Any:
    facade = _facade(orchestrator)
    async with asyncio.timeout(timeout):
        while True:
            attempts = facade.list_attempt_executions(task_id=task_id)
            for attempt in attempts:
                if status is not None and attempt.status is not status:
                    continue
                if live is not None and attempt.live is not live:
                    continue
                if run_stop_reason is not None and attempt.run_stop_reason != run_stop_reason:
                    continue
                return attempt
            await asyncio.sleep(0.01)


async def _wait_for_pending_review_ticket(orchestrator, task_id: str, *, timeout: float = 8.0) -> Any:
    facade = _facade(orchestrator)
    async with asyncio.timeout(timeout):
        while True:
            for ticket in facade.list_pending_review_tickets():
                if ticket.task_id == task_id:
                    return ticket
            await asyncio.sleep(0.01)


async def _respond_to_gatekeeper_request(
    orchestrator,
    *,
    run_id: str,
    request_id: int | str,
    result: dict[str, Any],
) -> None:
    await _facade(orchestrator).respond_to_gatekeeper_request(
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
    facade = _facade(orchestrator)
    facade.add_task(
        _build_task(
            task_id=task_id,
            title="Deterministic worker change",
            prompt=prompt,
            acceptance=["demo.txt contains the requested content"],
        )
    )
    facade.end_planning_phase()
    results = await facade.run_until_blocked()
    assert len(results) == 1
    result = results[0]
    ticket = facade.list_pending_review_tickets()[0]
    attempt = facade.get_attempt_execution(ticket.attempt_id)
    return result, ticket, attempt


def _assert_log_contains(path: str | Path, *, event: str) -> None:
    lines = _read_ndjson(path)
    assert any(line["event"] == event for line in lines), f"{event!r} not found in {path}"


@pytest.mark.asyncio
async def test_gatekeeper_question_resolution_e2e(e2e_project: E2EProjectContext, e2e_orchestrator) -> None:
    facade = _facade(e2e_orchestrator)
    question = facade.request_user_decision(
        "Should we start with OAuth or email auth first?\n[mock:question]",
        blocking_scope="planning",
    )

    submission = await facade.submit_user_message("Start with OAuth first.")
    pending_before = facade.get_question(question.question_id)
    state_after_submit = facade.gatekeeper_state()

    assert pending_before is not None
    assert pending_before.status is QuestionStatus.PENDING
    assert state_after_submit.session.lifecycle_state in {
        GatekeeperLifecycleStatus.STARTING,
        GatekeeperLifecycleStatus.RUNNING,
        GatekeeperLifecycleStatus.AWAITING_USER,
    }

    handle_snapshot = await _wait_for_gatekeeper_input_request(e2e_orchestrator, submission.run_id)
    awaiting_state = facade.gatekeeper_state()

    assert awaiting_state.session.lifecycle_state is GatekeeperLifecycleStatus.AWAITING_USER
    assert len(handle_snapshot.runtime.input_requests) == 1

    await _respond_to_gatekeeper_request(
        e2e_orchestrator,
        run_id=submission.run_id,
        request_id=handle_snapshot.runtime.input_requests[0].request_id,
        result={"answer": "Use OAuth first."},
    )

    result = await facade.wait_for_gatekeeper_submission(submission)
    resolved = facade.get_question(question.question_id)
    gatekeeper_run = facade.get_run(submission.run_id)
    conversation = facade.conversation(submission.conversation_id)
    snapshot = e2e_project.snapshot_orchestrator(e2e_orchestrator)

    assert result.error is None
    assert resolved is not None
    assert resolved.status is QuestionStatus.RESOLVED
    assert resolved.answer == "Start with OAuth first."
    assert facade.gatekeeper_state().session.lifecycle_state is GatekeeperLifecycleStatus.IDLE
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
    facade = _facade(e2e_orchestrator)
    result, ticket, attempt = await _run_single_task_to_review_pending(
        e2e_orchestrator,
        task_id="task-happy-path",
        prompt=(
            "Update demo.txt so it contains workspace-change.\n"
            "Leave enough evidence in logs for review.\n"
            "[mock:write demo.txt]\n"
            "[mock:content workspace-change]"
        ),
    )
    task = facade.get_task("task-happy-path")
    review_diff = Path(ticket.diff_ref or "")
    worker_run = facade.get_run(ticket.run_id)

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
    assert worker_run.provider.native_event_log is not None
    _assert_log_contains(worker_run.provider.canonical_event_log, event="tool.call.started")
    _assert_log_contains(worker_run.provider.native_event_log, event="fixture.mcp.resource.read.completed")

    e2e_project.snapshot_orchestrator(e2e_orchestrator)
    await e2e_orchestrator.shutdown()

    restarted = _create_restarted_orchestrator(e2e_project)
    try:
        restarted_facade = _facade(restarted)
        resolution = restarted_facade.accept_review_ticket(ticket.ticket_id)
        accepted_task = restarted_facade.get_task("task-happy-path")
        accepted_attempt = restarted_facade.get_attempt_execution(ticket.attempt_id)
        accepted_ticket = restarted_facade.get_review_ticket(ticket.ticket_id)
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
async def test_task_loop_pause_resume_workflow_e2e(
    e2e_project: E2EProjectContext,
    e2e_orchestrator,
) -> None:
    facade = _facade(e2e_orchestrator)
    facade.add_task(
        _build_task(
            task_id="task-paused-workflow",
            title="Workflow pause and resume",
            prompt=(
                "Update demo.txt so it contains paused-change.\n"
                "[mock:write demo.txt]\n"
                "[mock:content paused-change]"
            ),
            acceptance=["demo.txt contains the requested content after resume"],
        )
    )
    facade.end_planning_phase()

    paused = facade.pause_workflow()
    results_while_paused = await facade.run_until_blocked()
    task_while_paused = facade.get_task("task-paused-workflow")
    attempts_while_paused = facade.list_attempt_executions(task_id="task-paused-workflow")

    assert paused.value == "paused"
    assert results_while_paused == []
    assert task_while_paused is not None and task_while_paused.status is TaskStatus.PENDING
    assert attempts_while_paused == []
    assert e2e_project.demo_path.read_text(encoding="utf-8") == "baseline\n"

    resumed = facade.resume_workflow()
    results_after_resume = await facade.run_until_blocked()
    ticket = facade.list_pending_review_tickets()[0]
    attempt = facade.get_attempt_execution(ticket.attempt_id)
    task_after_resume = facade.get_task("task-paused-workflow")

    assert resumed.value == "executing"
    assert len(results_after_resume) == 1
    assert results_after_resume[0].outcome == "review_pending"
    assert task_after_resume is not None and task_after_resume.status is TaskStatus.COMPLETED
    assert attempt is not None and attempt.status is AttemptStatus.REVIEW_PENDING


@pytest.mark.asyncio
async def test_gatekeeper_interrupt_e2e(e2e_project: E2EProjectContext, e2e_orchestrator) -> None:
    facade = _facade(e2e_orchestrator)
    submission = await facade.submit_user_message("Produce a longer planning update.\n[mock:long]")

    running = await _wait_for_gatekeeper_state(e2e_orchestrator, GatekeeperLifecycleStatus.RUNNING)
    interrupted = await facade.interrupt_gatekeeper()
    result = await facade.wait_for_gatekeeper_submission(submission)
    idle = await _wait_for_gatekeeper_state(e2e_orchestrator, GatekeeperLifecycleStatus.IDLE)
    gatekeeper_run = facade.get_run(submission.run_id)
    snapshot = e2e_project.snapshot_orchestrator(e2e_orchestrator)

    assert running.session.run_id == submission.run_id
    assert interrupted is True
    assert result.error is None
    assert idle.session.lifecycle_state is GatekeeperLifecycleStatus.IDLE
    assert gatekeeper_run is not None
    assert gatekeeper_run.provider.canonical_event_log is not None
    canonical_lines = _read_ndjson(gatekeeper_run.provider.canonical_event_log)
    assert any(
        line["event"] == "turn.completed" and line["data"].get("turn_status") == "interrupted"
        for line in canonical_lines
    )
    assert not any(line["event"] == "runtime.error" for line in canonical_lines)
    assert submission.run_id in snapshot["run_ids"]


@pytest.mark.asyncio
async def test_gatekeeper_pause_resume_e2e(e2e_project: E2EProjectContext, e2e_orchestrator) -> None:
    facade = _facade(e2e_orchestrator)
    submission = await facade.submit_user_message("Need one follow-up answer before continuing.\n[mock:question]")

    awaiting = await _wait_for_gatekeeper_state(e2e_orchestrator, GatekeeperLifecycleStatus.AWAITING_USER)
    paused = await facade.pause_gatekeeper("manual pause for e2e")
    resumed = await facade.resume_gatekeeper()
    run_after_resume = facade.get_run(submission.run_id)
    snapshot = e2e_project.snapshot_orchestrator(e2e_orchestrator)

    assert awaiting.session.run_id == submission.run_id
    assert paused.session.lifecycle_state is GatekeeperLifecycleStatus.STOPPED
    assert paused.session.run_id == submission.run_id
    assert paused.session.last_error == "manual pause for e2e"
    assert resumed.session.lifecycle_state is GatekeeperLifecycleStatus.IDLE
    assert resumed.session.run_id == submission.run_id
    assert resumed.session.last_error is None
    assert run_after_resume is not None
    assert run_after_resume.runtime.stop_reason == "paused"
    assert submission.run_id in snapshot["run_ids"]


@pytest.mark.asyncio
async def test_gatekeeper_persists_stopped_session_and_resumes_agent_after_restart_e2e(
    e2e_project: E2EProjectContext,
    e2e_orchestrator,
) -> None:
    facade = _facade(e2e_orchestrator)
    submission = await facade.submit_user_message("Need one follow-up answer before continuing.\n[mock:question]")

    awaiting = await _wait_for_gatekeeper_state(e2e_orchestrator, GatekeeperLifecycleStatus.AWAITING_USER)
    paused = await facade.pause_gatekeeper("persist across restart")
    e2e_project.snapshot_orchestrator(e2e_orchestrator)
    await e2e_orchestrator.shutdown()

    restarted = _create_restarted_orchestrator(e2e_project)
    try:
        restarted_facade = _facade(restarted)
        restored = restarted_facade.gatekeeper_state()
        resumed_submission = await restarted_facade.submit_user_message(
            "Continue after restart with a longer planning update.\n[mock:long]"
        )
        result = await restarted_facade.wait_for_gatekeeper_submission(resumed_submission)
        gatekeeper_run = restarted_facade.get_run(resumed_submission.run_id)
        conversation = restarted_facade.conversation(submission.conversation_id)
        snapshot = e2e_project.snapshot_orchestrator(restarted)

        assert awaiting.session.run_id == submission.run_id
        assert paused.session.provider_thread_id is not None
        assert restored.session.lifecycle_state is GatekeeperLifecycleStatus.STOPPED
        assert restored.session.run_id == submission.run_id
        assert restored.session.provider_thread_id == paused.session.provider_thread_id
        assert restored.session.resumable is True
        assert resumed_submission.run_id == submission.run_id
        assert resumed_submission.conversation_id == submission.conversation_id
        assert result.error is None
        assert gatekeeper_run is not None
        assert gatekeeper_run.provider.thread_id == paused.session.provider_thread_id
        assert gatekeeper_run.provider.canonical_event_log is not None
        assert gatekeeper_run.provider.native_event_log is not None
        assert conversation is not None
        assert sum(
            1
            for entry in conversation.entries
            if entry.role == "user" and entry.kind == "message"
        ) >= 2
        canonical_lines = _read_ndjson(gatekeeper_run.provider.canonical_event_log)
        native_lines = _read_ndjson(gatekeeper_run.provider.native_event_log)
        assert any(
            line["event"] == "thread.started" and line["data"].get("resumed") is True
            for line in canonical_lines
        )
        assert any(line["event"] == "fixture.thread.resumed" for line in native_lines)
        assert submission.run_id in snapshot["run_ids"]
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_task_loop_persists_paused_attempt_and_recovers_after_restart_e2e(
    e2e_project: E2EProjectContext,
    e2e_orchestrator,
) -> None:
    facade = _facade(e2e_orchestrator)
    facade.add_task(
        _build_task(
            task_id="task-recover-paused-attempt",
            title="Persist and recover paused attempt",
            prompt=(
                "Inspect workflow status before finishing and update demo.txt.\n"
                "[mock:write demo.txt]\n"
                "[mock:content resumed-change]\n"
                "[mock:long]"
            ),
            acceptance=["demo.txt contains the resumed change after review acceptance"],
        )
    )
    facade.end_planning_phase()

    task_runner = asyncio.create_task(facade.run_until_blocked())
    active_attempt = await _wait_for_attempt_execution(
        e2e_orchestrator,
        "task-recover-paused-attempt",
        status=AttemptStatus.RUNNING,
        live=True,
    )
    paused_attempts = await facade.pause_task_execution()
    task_runner.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task_runner

    assert len(paused_attempts) == 1
    assert paused_attempts[0].attempt_id == active_attempt.attempt_id
    assert paused_attempts[0].provider_resume_handle is not None
    paused_thread_id = paused_attempts[0].provider_resume_handle.thread_id
    assert paused_thread_id is not None

    e2e_project.snapshot_orchestrator(e2e_orchestrator)
    await e2e_orchestrator.shutdown()

    restarted = _create_restarted_orchestrator(e2e_project)
    try:
        restarted_facade = _facade(restarted)
        recovered_attempt = await _wait_for_attempt_execution(
            restarted,
            "task-recover-paused-attempt",
            status=AttemptStatus.RUNNING,
            live=False,
            run_stop_reason="paused",
        )
        results = await restarted_facade.run_until_blocked()
        ticket = await _wait_for_pending_review_ticket(restarted, "task-recover-paused-attempt")
        recovered_ticket_attempt = restarted_facade.get_attempt_execution(ticket.attempt_id)
        worker_run = restarted_facade.get_run(ticket.run_id)
        snapshot = e2e_project.snapshot_orchestrator(restarted)

        assert recovered_attempt.attempt_id == active_attempt.attempt_id
        assert recovered_attempt.run_id == active_attempt.run_id
        assert recovered_attempt.provider_thread_id == paused_thread_id
        assert recovered_attempt.resumable is True
        assert len(results) == 1
        assert results[0].outcome == "review_pending"
        assert ticket.attempt_id == active_attempt.attempt_id
        assert recovered_ticket_attempt is not None
        assert recovered_ticket_attempt.status is AttemptStatus.REVIEW_PENDING
        assert worker_run is not None
        assert worker_run.provider.thread_id == paused_thread_id
        assert worker_run.provider.canonical_event_log is not None
        assert worker_run.provider.native_event_log is not None
        canonical_lines = _read_ndjson(worker_run.provider.canonical_event_log)
        native_lines = _read_ndjson(worker_run.provider.native_event_log)
        assert any(
            line["event"] == "thread.started" and line["data"].get("resumed") is True
            for line in canonical_lines
        )
        assert any(line["event"] == "fixture.thread.resumed" for line in native_lines)
        assert active_attempt.attempt_id in snapshot["attempt_ids"]
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_task_loop_retry_cycle_e2e(e2e_project: E2EProjectContext, e2e_orchestrator) -> None:
    facade = _facade(e2e_orchestrator)
    first_result, first_ticket, first_attempt = await _run_single_task_to_review_pending(
        e2e_orchestrator,
        task_id="task-retry-cycle",
        prompt=(
            "Update demo.txt so it contains first-change.\n"
            "[mock:write demo.txt]\n"
            "[mock:content first-change]"
        ),
    )

    retry_resolution = facade.retry_review_ticket(
        first_ticket.ticket_id,
        failure_reason="Need a different deterministic result.",
        prompt_patch=(
            "Update demo.txt so it contains second-change.\n"
            "[mock:write demo.txt]\n"
            "[mock:content second-change]"
        ),
    )
    second_results = await facade.run_until_blocked()
    second_ticket = facade.list_pending_review_tickets()[0]
    second_attempt = facade.get_attempt_execution(second_ticket.attempt_id)
    task = facade.get_task("task-retry-cycle")
    review_history = facade.list_review_tickets(task_id="task-retry-cycle")
    first_ticket_state = facade.get_review_ticket(first_ticket.ticket_id)

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

    resolution = facade.accept_review_ticket(second_ticket.ticket_id)
    accepted_task = facade.get_task("task-retry-cycle")
    accepted_attempt = facade.get_attempt_execution(second_ticket.attempt_id)
    snapshot = e2e_project.snapshot_orchestrator(e2e_orchestrator)

    assert resolution.decision == "accept"
    assert accepted_task is not None and accepted_task.status is TaskStatus.ACCEPTED
    assert accepted_attempt is not None and accepted_attempt.status is AttemptStatus.ACCEPTED
    assert e2e_project.demo_path.read_text(encoding="utf-8") == "second-change\n"
    assert len(snapshot["attempt_ids"]) == 2
    assert len(snapshot["review_ticket_ids"]) == 2


@pytest.mark.asyncio
async def test_task_loop_worker_request_is_rejected_e2e(e2e_project: E2EProjectContext, e2e_orchestrator) -> None:
    facade = _facade(e2e_orchestrator)
    facade.add_task(
        _build_task(
            task_id="task-worker-request-rejected",
            title="Worker request rejection",
            prompt="Ask for interactive input.\n[mock:question]",
            acceptance=["worker run fails instead of awaiting user input"],
        )
    )
    facade.end_planning_phase()

    results = await facade.run_until_blocked()
    task = facade.get_task("task-worker-request-rejected")
    attempts = facade.list_attempt_executions(task_id="task-worker-request-rejected")
    code_attempt = attempts[0] if attempts else None
    worker_run = (
        facade.get_run(code_attempt.run_id)
        if code_attempt is not None and code_attempt.run_id is not None
        else None
    )
    pending_tickets = facade.list_pending_review_tickets()
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
