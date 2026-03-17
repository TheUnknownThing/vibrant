from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from vibrant.agents.gatekeeper import GatekeeperRequest, GatekeeperTrigger
from vibrant.agents.runtime import NormalizedRunResult, RunState
from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, ProviderResumeHandle, AgentType

from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.orchestrator.types import GatekeeperLifecycleStatus, QuestionPriority, QuestionStatus
from vibrant.project_init import initialize_project


def _prepare_orchestrator(tmp_path: Path):
    initialize_project(tmp_path)
    return create_orchestrator(tmp_path)


@pytest.mark.asyncio
async def test_control_plane_routes_pending_question_through_single_submit_command(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    question = orchestrator._question_store.create(
        text="Should we use OAuth or email auth first?",
        priority=QuestionPriority.BLOCKING,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="planning",
        task_id=None,
    )
    captured: dict[str, object] = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            agent_record=SimpleNamespace(identity=SimpleNamespace(agent_id="gatekeeper-agent", run_id="run-1"))
        )

    async def fake_wait_for_run(run_id: str):
        assert run_id == "run-1"
        return SimpleNamespace(error=None, summary="done")

    monkeypatch.setattr(orchestrator._gatekeeper_lifecycle, "submit", fake_submit)
    monkeypatch.setattr(orchestrator._runtime_service, "wait_for_run", fake_wait_for_run)

    submission = await facade.submit_user_message("Start with OAuth.")
    pending = orchestrator._question_store.get(question.question_id)

    assert submission.agent_id == "gatekeeper-agent"
    assert captured["request"].trigger is GatekeeperTrigger.USER_CONVERSATION
    assert "Should we use OAuth or email auth first?" in str(captured["request"].trigger_description)
    assert pending is not None and pending.status is QuestionStatus.PENDING

    await facade.wait_for_gatekeeper_submission(submission)

    resolved = orchestrator._question_store.get(question.question_id)
    assert resolved is not None and resolved.status is QuestionStatus.RESOLVED


@pytest.mark.asyncio
async def test_failed_answer_submission_leaves_question_pending(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    question = orchestrator._question_store.create(
        text="Do we need mobile support in v1?",
        priority=QuestionPriority.BLOCKING,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="planning",
        task_id=None,
    )

    async def fake_submit(**kwargs):
        raise RuntimeError("submit failed")

    monkeypatch.setattr(orchestrator._gatekeeper_lifecycle, "submit", fake_submit)

    with pytest.raises(RuntimeError, match="submit failed"):
        await facade.submit_user_message("Not for v1.")

    persisted = orchestrator._question_store.get(question.question_id)
    assert persisted is not None
    assert persisted.status is QuestionStatus.PENDING
    assert persisted.answer is None
    assert facade.gatekeeper_state().pending_question.question_id == question.question_id
    assert "submit failed" in (facade.gatekeeper_state().last_error or "")


@pytest.mark.asyncio
async def test_failed_submission_result_leaves_question_pending(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    question = orchestrator._question_store.create(
        text="Should we keep SQLite for local mode?",
        priority=QuestionPriority.BLOCKING,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="planning",
        task_id=None,
    )

    async def fake_submit(**kwargs):
        return SimpleNamespace(
            agent_record=SimpleNamespace(identity=SimpleNamespace(agent_id="gatekeeper-agent", run_id="run-2"))
        )

    async def fake_wait_for_run(run_id: str):
        assert run_id == "run-2"
        return SimpleNamespace(error="provider failure", summary=None)

    monkeypatch.setattr(orchestrator._gatekeeper_lifecycle, "submit", fake_submit)
    monkeypatch.setattr(orchestrator._runtime_service, "wait_for_run", fake_wait_for_run)

    submission = await facade.submit_user_message("Yes, keep it.")
    result = await facade.wait_for_gatekeeper_submission(submission)
    persisted = orchestrator._question_store.get(question.question_id)

    assert result.error == "provider failure"
    assert persisted is not None
    assert persisted.status is QuestionStatus.PENDING
    assert persisted.answer is None


def test_question_store_preserves_non_policy_scopes(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)

    question = orchestrator._question_store.create(
        text="Keep scope as provided",
        priority=QuestionPriority.NORMAL,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="custom-scope",
        task_id=None,
    )

    persisted = orchestrator._question_store.get(question.question_id)

    assert persisted is not None
    assert persisted.blocking_scope == "custom-scope"


@pytest.mark.asyncio
async def test_gatekeeper_runtime_error_is_not_cleared_by_late_turn_completed(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    lifecycle = orchestrator._gatekeeper_lifecycle
    lifecycle._session.run_id = "run-1"
    lifecycle._session.lifecycle_state = GatekeeperLifecycleStatus.RUNNING
    lifecycle._session.active_turn_id = "turn-1"

    await orchestrator._runtime_service.ingest_event(
        {
            "type": "runtime.error",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "error_message": "provider failed",
        }
    )
    await orchestrator._runtime_service.ingest_event(
        {
            "type": "turn.completed",
            "run_id": "run-1",
            "turn_id": "turn-1",
        }
    )

    state = facade.gatekeeper_state()

    assert state.session.lifecycle_state is GatekeeperLifecycleStatus.FAILED
    assert state.session.last_error == "provider failed"
    assert state.session.active_turn_id is None


@pytest.mark.asyncio
async def test_gatekeeper_pause_and_resume_reuses_logical_run_id(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    lifecycle = orchestrator._gatekeeper_lifecycle
    prior_record = AgentRecord(
        identity={
            "run_id": "gatekeeper-session-1",
            "agent_id": "gatekeeper",
            "role": AgentType.GATEKEEPER.value,
            "type": AgentType.GATEKEEPER,
        },
        lifecycle={"status": AgentStatus.RUNNING},
        provider=AgentProviderMetadata(
            provider_thread_id="thread-existing",
            resume_cursor={"threadId": "thread-existing"},
        ),
    )
    orchestrator._agent_run_store.upsert(prior_record)
    lifecycle._session.run_id = "gatekeeper-session-1"
    lifecycle._session.agent_id = "gatekeeper"
    lifecycle._session.conversation_id = "gatekeeper-conversation"
    lifecycle._session.lifecycle_state = GatekeeperLifecycleStatus.RUNNING
    lifecycle._active_handle_run_id = "gatekeeper-session-1"
    lifecycle._active_handle = object()

    def fake_annotate(run_id: str, *, stop_reason: str | None = None) -> None:
        record = orchestrator._agent_run_store.get(run_id)
        assert record is not None
        record.lifecycle.stop_reason = stop_reason
        orchestrator._agent_run_store.upsert(record)

    async def fake_kill_run(run_id: str):
        assert run_id == "gatekeeper-session-1"
        return SimpleNamespace()

    captured: dict[str, object] = {}

    class _FakeHandle:
        def __init__(self, agent_record) -> None:
            self.agent_record = agent_record
            self.state = SimpleNamespace(value=RunState.RUNNING.value)
            self.provider_thread = ProviderResumeHandle(thread_id="thread-existing")
            self.awaiting_input = False
            self.input_requests = []

        async def wait(self):
            return NormalizedRunResult(
                run_id=self.agent_record.identity.run_id,
                agent_id=self.agent_record.identity.agent_id,
                role=self.agent_record.identity.role,
                status=AgentStatus.COMPLETED,
                state=RunState.COMPLETED,
                provider_thread=ProviderResumeHandle(thread_id="thread-existing"),
            )

    async def fake_resume_run(**kwargs):
        captured.update(kwargs)
        return _FakeHandle(kwargs["agent_record"])

    async def fake_start_run(**kwargs):
        raise AssertionError("gatekeeper resume should reuse the existing logical run")

    monkeypatch.setattr(orchestrator._runtime_service, "annotate_run", fake_annotate)
    monkeypatch.setattr(orchestrator._runtime_service, "kill_run", fake_kill_run)
    monkeypatch.setattr(orchestrator._runtime_service, "resume_run", fake_resume_run)
    monkeypatch.setattr(orchestrator._runtime_service, "start_run", fake_start_run)

    paused = await facade.pause_gatekeeper("user_paused")
    await lifecycle.submit(
        request=SimpleNamespace(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description="Resume after pause.",
            agent_summary=None,
        ),
        submission_id="submission-1",
        resume=True,
    )

    agent_record = captured["agent_record"]
    assert paused.session.lifecycle_state is GatekeeperLifecycleStatus.STOPPED
    assert paused.session.run_id == "gatekeeper-session-1"
    assert lifecycle.snapshot().run_id == "gatekeeper-session-1"
    assert agent_record.identity.run_id == "gatekeeper-session-1"


@pytest.mark.asyncio
async def test_gatekeeper_failed_resume_preserves_previous_provider_handle(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    lifecycle = orchestrator._gatekeeper_lifecycle
    orchestrator._agent_run_store.upsert(
        AgentRecord(
            identity={
                "run_id": "gatekeeper-session-1",
                "agent_id": "gatekeeper",
                "role": AgentType.GATEKEEPER.value,
                "type": AgentType.GATEKEEPER,
            },
            lifecycle={"status": AgentStatus.KILLED, "stop_reason": "paused"},
            provider=AgentProviderMetadata(
                provider_thread_id="thread-existing",
                resume_cursor={"threadId": "thread-existing"},
            ),
        )
    )
    lifecycle._session.run_id = "gatekeeper-session-1"
    lifecycle._session.agent_id = "gatekeeper"
    lifecycle._session.conversation_id = "gatekeeper-conversation"
    lifecycle._session.lifecycle_state = GatekeeperLifecycleStatus.STOPPED

    async def fake_resume_run(**kwargs):
        raise RuntimeError("resume failed")

    monkeypatch.setattr(orchestrator._runtime_service, "resume_run", fake_resume_run)

    with pytest.raises(RuntimeError, match="resume failed"):
        await lifecycle.submit(
            request=GatekeeperRequest(
                trigger=GatekeeperTrigger.USER_CONVERSATION,
                trigger_description="Resume after failure.",
            ),
            submission_id="submission-1",
            resume=True,
        )

    persisted = orchestrator._agent_run_store.get("gatekeeper-session-1")

    assert persisted is not None
    assert persisted.provider.provider_thread_id == "thread-existing"
    assert persisted.provider.resume_cursor == {"threadId": "thread-existing"}


@pytest.mark.asyncio
async def test_resume_gatekeeper_clears_stopped_state_without_new_submission(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    facade = OrchestratorFacade(orchestrator)
    lifecycle = orchestrator._gatekeeper_lifecycle
    lifecycle._session.run_id = "gatekeeper-session-1"
    lifecycle._session.agent_id = "gatekeeper"
    lifecycle._session.conversation_id = "gatekeeper-conversation"
    lifecycle._session.lifecycle_state = GatekeeperLifecycleStatus.STOPPED
    lifecycle._session.last_error = "paused"

    resumed = await facade.resume_gatekeeper()

    assert resumed.session.lifecycle_state is GatekeeperLifecycleStatus.IDLE
    assert resumed.session.run_id == "gatekeeper-session-1"
    assert resumed.session.last_error is None
