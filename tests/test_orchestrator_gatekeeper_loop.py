from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from vibrant.agents.gatekeeper import GatekeeperTrigger

from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.types import QuestionPriority, QuestionStatus
from vibrant.project_init import initialize_project


def _prepare_orchestrator(tmp_path: Path):
    initialize_project(tmp_path)
    return create_orchestrator(tmp_path)


@pytest.mark.asyncio
async def test_control_plane_routes_pending_question_through_single_submit_command(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    question = orchestrator.question_store.create(
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

    monkeypatch.setattr(orchestrator.gatekeeper_lifecycle, "submit", fake_submit)
    monkeypatch.setattr(orchestrator.runtime_service, "wait_for_run", fake_wait_for_run)

    submission = await orchestrator.control_plane.submit_user_input("Start with OAuth.")
    pending = orchestrator.question_store.get(question.question_id)

    assert submission.agent_id == "gatekeeper-agent"
    assert captured["request"].trigger is GatekeeperTrigger.USER_CONVERSATION
    assert "Should we use OAuth or email auth first?" in str(captured["request"].trigger_description)
    assert pending is not None and pending.status is QuestionStatus.PENDING

    await orchestrator.control_plane.wait_for_gatekeeper_submission(submission)

    resolved = orchestrator.question_store.get(question.question_id)
    assert resolved is not None and resolved.status is QuestionStatus.RESOLVED


@pytest.mark.asyncio
async def test_failed_answer_submission_leaves_question_pending(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    question = orchestrator.question_store.create(
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

    monkeypatch.setattr(orchestrator.gatekeeper_lifecycle, "submit", fake_submit)

    with pytest.raises(RuntimeError, match="submit failed"):
        await orchestrator.control_plane.submit_user_input("Not for v1.")

    persisted = orchestrator.question_store.get(question.question_id)
    assert persisted is not None
    assert persisted.status is QuestionStatus.PENDING
    assert persisted.answer is None
    assert orchestrator.control_plane.gatekeeper_state().pending_question.question_id == question.question_id
    assert "submit failed" in (orchestrator.control_plane.gatekeeper_state().last_error or "")


@pytest.mark.asyncio
async def test_failed_submission_result_leaves_question_pending(tmp_path: Path, monkeypatch) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)
    question = orchestrator.question_store.create(
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

    monkeypatch.setattr(orchestrator.gatekeeper_lifecycle, "submit", fake_submit)
    monkeypatch.setattr(orchestrator.runtime_service, "wait_for_run", fake_wait_for_run)

    submission = await orchestrator.control_plane.submit_user_input("Yes, keep it.")
    result = await orchestrator.control_plane.wait_for_gatekeeper_submission(submission)
    persisted = orchestrator.question_store.get(question.question_id)

    assert result.error == "provider failure"
    assert persisted is not None
    assert persisted.status is QuestionStatus.PENDING
    assert persisted.answer is None


def test_question_store_preserves_non_policy_scopes(tmp_path: Path) -> None:
    orchestrator = _prepare_orchestrator(tmp_path)

    question = orchestrator.question_store.create(
        text="Keep scope as provided",
        priority=QuestionPriority.NORMAL,
        source_role="gatekeeper",
        source_agent_id=None,
        source_conversation_id=None,
        source_turn_id=None,
        blocking_scope="custom-scope",
        task_id=None,
    )

    persisted = orchestrator.question_store.get(question.question_id)

    assert persisted is not None
    assert persisted.blocking_scope == "custom-scope"
