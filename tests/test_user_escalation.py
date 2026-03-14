"""Tests for Gatekeeper follow-up conversation handling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vibrant.agents import Gatekeeper, GatekeeperRequest, GatekeeperTrigger
from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.project_init import initialize_project
from vibrant.providers.base import RuntimeMode


class FollowUpAdapter:
    instances: list["FollowUpAdapter"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.cwd = Path(kwargs["cwd"])
        self.on_canonical_event = kwargs.get("on_canonical_event")
        self.agent_record = kwargs.get("agent_record")
        self.provider_thread_id: str | None = None
        self.start_thread_calls: list[dict[str, Any]] = []
        self.resume_thread_calls: list[dict[str, Any]] = []
        self.start_turn_calls: list[dict[str, Any]] = []
        process = type("DummyProcess", (), {"pid": 5510, "returncode": None})()
        self.client = type("DummyClient", (), {"is_running": True, "_process": process})()
        FollowUpAdapter.instances.append(self)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        return {"serverInfo": {"name": "codex"}}

    async def stop_session(self) -> None:
        if self.client._process.returncode is None:
            self.client._process.returncode = 0
        self.client.is_running = False

    async def start_thread(self, **kwargs: Any) -> Any:
        self.start_thread_calls.append(dict(kwargs))
        self.provider_thread_id = "thread-new"
        self._persist_thread_metadata(self.provider_thread_id)
        return {"thread": {"id": self.provider_thread_id}}

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        self.resume_thread_calls.append({"provider_thread_id": provider_thread_id, **kwargs})
        self.provider_thread_id = provider_thread_id
        self._persist_thread_metadata(provider_thread_id)
        return {"thread": {"id": provider_thread_id}}

    async def start_turn(self, *, input_items, runtime_mode: RuntimeMode, approval_policy: str, **kwargs: Any) -> Any:
        self.start_turn_calls.append(
            {
                "input_items": list(input_items),
                "runtime_mode": runtime_mode,
                "approval_policy": approval_policy,
                **kwargs,
            }
        )
        if self.on_canonical_event is not None:
            await self.on_canonical_event({"type": "content.delta", "delta": "Follow-up recorded."})
            await self.on_canonical_event({"type": "turn.completed", "turn": {"id": "turn-follow-up-1"}})
        self.client._process.returncode = 0
        return {"turn": {"id": "turn-follow-up-1"}}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def respond_to_request(self, request_id: int | str, *, result: Any | None = None, error=None) -> Any:
        return {"request_id": request_id, "result": result, "error": error}

    def _persist_thread_metadata(self, thread_id: str) -> None:
        if self.agent_record is None:
            return
        self.agent_record.provider.provider_thread_id = thread_id
        self.agent_record.provider.resume_cursor = {"threadId": thread_id}


def _write_gatekeeper_record(project_root: Path, *, agent_id: str, thread_id: str) -> None:
    record = AgentRecord(
        identity={
            "run_id": agent_id,
            "agent_id": agent_id,
            "role": AgentType.GATEKEEPER.value,
            "type": AgentType.GATEKEEPER,
        },
        lifecycle={"status": AgentStatus.COMPLETED},
        provider=AgentProviderMetadata(
            provider_thread_id=thread_id,
            resume_cursor={"threadId": thread_id},
        ),
    )
    agents_dir = project_root / ".vibrant" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_id}.json").write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_answer_question_resumes_latest_gatekeeper_thread(tmp_path):
    FollowUpAdapter.instances.clear()
    initialize_project(tmp_path)
    _write_gatekeeper_record(tmp_path, agent_id="gatekeeper-user_conversation-old", thread_id="thread-existing")

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FollowUpAdapter, timeout_seconds=1)
    result = await gatekeeper.answer_question(
        "Should we defer the UI?",
        "Yes, keep the first milestone backend-only.",
    )

    adapter = FollowUpAdapter.instances[0]
    assert adapter.start_thread_calls == []
    assert adapter.resume_thread_calls[0]["provider_thread_id"] == "thread-existing"
    prompt = adapter.start_turn_calls[0]["input_items"][0]["text"]
    assert "Question: Should we defer the UI?" in prompt
    assert "User Answer: Yes, keep the first milestone backend-only." in prompt
    assert result.succeeded is True
    assert result.provider_thread.thread_id == "thread-existing"


@pytest.mark.asyncio
async def test_start_answer_question_returns_gatekeeper_handle(tmp_path):
    FollowUpAdapter.instances.clear()
    initialize_project(tmp_path)
    _write_gatekeeper_record(tmp_path, agent_id="gatekeeper-user_conversation-old", thread_id="thread-existing")

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FollowUpAdapter, timeout_seconds=1)
    handle = await gatekeeper.start_answer_question(
        "Should we defer the UI?",
        "Yes, keep the first milestone backend-only.",
    )
    result = await handle.wait()

    assert handle.agent_record.identity.type is AgentType.GATEKEEPER
    assert handle.request.trigger is GatekeeperTrigger.USER_CONVERSATION
    assert result.succeeded is True
