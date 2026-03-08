"""Tests for the Phase 4 user escalation flow."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from vibrant.consensus import ConsensusParser, ConsensusWriter
from vibrant.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperTrigger
from vibrant.models.consensus import ConsensusDecision, ConsensusStatus, DecisionAuthor
from vibrant.orchestrator.engine import OrchestratorEngine
from vibrant.project_init import initialize_project
from vibrant.providers.base import RuntimeMode


class EscalationAdapter:
    instances: list["EscalationAdapter"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.cwd = Path(kwargs["cwd"])
        self.on_canonical_event = kwargs.get("on_canonical_event")
        self.agent_record = kwargs.get("agent_record")
        self.provider_thread_id: str | None = None
        self.start_session_calls: list[dict[str, Any]] = []
        self.start_thread_calls: list[dict[str, Any]] = []
        self.resume_thread_calls: list[dict[str, Any]] = []
        self.start_turn_calls: list[dict[str, Any]] = []
        self.stop_calls = 0
        self.client = type("DummyClient", (), {"is_running": True})()
        EscalationAdapter.instances.append(self)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        self.start_session_calls.append({"cwd": cwd, **kwargs})
        return {"serverInfo": {"name": "codex"}}

    async def stop_session(self) -> None:
        self.stop_calls += 1

    async def start_thread(self, **kwargs: Any) -> Any:
        self.start_thread_calls.append(kwargs)
        self.provider_thread_id = "thread-gatekeeper-qa"
        if self.agent_record is not None:
            self.agent_record.provider.provider_thread_id = self.provider_thread_id
            self.agent_record.provider.resume_cursor = {"threadId": self.provider_thread_id}
        return {"thread": {"id": self.provider_thread_id}}

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        self.resume_thread_calls.append({"provider_thread_id": provider_thread_id, **kwargs})
        self.provider_thread_id = provider_thread_id
        if self.agent_record is not None:
            self.agent_record.provider.provider_thread_id = provider_thread_id
            self.agent_record.provider.resume_cursor = {"threadId": provider_thread_id}
        return {"thread": {"id": provider_thread_id}}

    async def start_turn(self, *, input_items, runtime_mode: RuntimeMode, approval_policy: str, **kwargs: Any) -> Any:
        payload = {
            "input_items": list(input_items),
            "runtime_mode": runtime_mode,
            "approval_policy": approval_policy,
            **kwargs,
        }
        self.start_turn_calls.append(payload)
        prompt_text = payload["input_items"][0]["text"]
        consensus_path = self.cwd / ".vibrant" / "consensus.md"
        current = ConsensusParser().parse_file(consensus_path)

        if "task_completion" in prompt_text:
            current.status = ConsensusStatus.EXECUTING
            current.questions = ["Should we ship the UI in v1?"]
            ConsensusWriter().write(consensus_path, current)
            await self._emit({"type": "content.delta", "delta": "Verdict: needs_input\n"})
        else:
            current.questions = []
            current.decisions.append(
                ConsensusDecision(
                    title="User answered UI question",
                    date=datetime(2026, 3, 8, 13, 0, tzinfo=timezone.utc),
                    made_by=DecisionAuthor.USER,
                    context="Gatekeeper requested a product decision.",
                    resolution="Defer the UI to a later milestone.",
                    impact="Execution continues without the UI scope.",
                )
            )
            ConsensusWriter().write(consensus_path, current)
            await self._emit({"type": "content.delta", "delta": "Verdict: accepted\n"})

        await self._emit({"type": "turn.completed", "turn": {"id": "turn-escalation-1"}})
        return {"turn": {"id": "turn-escalation-1"}}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def respond_to_request(self, request_id: int | str, *, result: Any | None = None, error=None) -> Any:
        return {"request_id": request_id, "result": result, "error": error}

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_canonical_event is not None:
            await self.on_canonical_event(event)


@pytest.mark.asyncio
async def test_gatekeeper_question_updates_pending_questions_and_user_answer_is_forwarded(tmp_path):
    EscalationAdapter.instances.clear()
    initialize_project(tmp_path)

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=EscalationAdapter, timeout_seconds=1)
    engine = OrchestratorEngine.load(tmp_path)

    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description="Review task-001 completion.",
            agent_summary="The implementation is ready for validation.",
        )
    )
    events = engine.apply_gatekeeper_result(result)

    assert engine.state.pending_questions == ["Should we ship the UI in v1?"]
    assert engine.state.gatekeeper_status.value == "awaiting_user"
    assert events[0]["type"] == "user-input.requested"

    persisted_state = OrchestratorEngine.load(tmp_path)
    assert persisted_state.state.pending_questions == ["Should we ship the UI in v1?"]

    follow_up = await engine.answer_pending_question(
        gatekeeper,
        answer="No, defer the UI to a later milestone.",
    )

    latest_adapter = EscalationAdapter.instances[-1]
    assert latest_adapter.resume_thread_calls[0]["provider_thread_id"] == "thread-gatekeeper-qa"
    answer_prompt = latest_adapter.start_turn_calls[0]["input_items"][0]["text"]
    assert "Question: Should we ship the UI in v1?" in answer_prompt
    assert "User Answer: No, defer the UI to a later milestone." in answer_prompt
    assert follow_up.verdict == "accepted"
    assert engine.state.pending_questions == []
    assert engine.state.gatekeeper_status.value == "idle"
    assert engine.emitted_events[-1]["type"] == "user-input.resolved"


@pytest.mark.parametrize(
    ("bell_enabled", "expected_bell"),
    [
        (True, True),
        (False, False),
    ],
)
@pytest.mark.asyncio
async def test_notification_event_contains_banner_and_terminal_bell_setting(tmp_path, bell_enabled, expected_bell):
    EscalationAdapter.instances.clear()
    initialize_project(tmp_path)

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=EscalationAdapter, timeout_seconds=1)
    engine = OrchestratorEngine.load(tmp_path, notification_bell_enabled=bell_enabled)

    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description="Review task-001 completion.",
            agent_summary="Summary",
        )
    )
    events = engine.apply_gatekeeper_result(result)

    assert events[0]["banner_text"] == "⚠ Gatekeeper needs your input — see Chat panel"
    assert events[0]["terminal_bell"] is expected_bell


@pytest.mark.asyncio
async def test_pending_question_persisted_in_state_json_across_restart(tmp_path):
    EscalationAdapter.instances.clear()
    initialize_project(tmp_path)

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=EscalationAdapter, timeout_seconds=1)
    engine = OrchestratorEngine.load(tmp_path)

    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description="Review task-001 completion.",
            agent_summary="Summary",
        )
    )
    engine.apply_gatekeeper_result(result)

    reloaded = OrchestratorEngine.load(tmp_path)
    assert reloaded.state.pending_questions == ["Should we ship the UI in v1?"]
    assert reloaded.state.gatekeeper_status.value == "awaiting_user"
