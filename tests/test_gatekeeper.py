"""Tests for the runtime-based Gatekeeper implementation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from vibrant.agents import (
    Gatekeeper,
    GatekeeperRequest,
    GatekeeperTrigger,
    GATEKEEPER_SYSTEM_PROMPT_CURSOR_KEY,
    GATEKEEPER_SYSTEM_PROMPT_VERSION,
    MCP_TOOL_NAMES,
    PLANNING_COMPLETE_MCP_TOOL,
)
from vibrant.agents.runtime import RunState
from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.project_init import initialize_project
from vibrant.providers.base import RuntimeMode


class FakeGatekeeperAdapter:
    instances: list["FakeGatekeeperAdapter"] = []
    scenario: str = "complete"

    def __init__(self, **kwargs: Any) -> None:
        self.cwd = Path(kwargs["cwd"])
        self.on_canonical_event = kwargs.get("on_canonical_event")
        self.agent_record = kwargs.get("agent_record")
        self.provider_thread_id: str | None = None
        self.start_session_calls: list[dict[str, Any]] = []
        self.start_thread_calls: list[dict[str, Any]] = []
        self.resume_thread_calls: list[dict[str, Any]] = []
        self.start_turn_calls: list[dict[str, Any]] = []
        self.respond_calls: list[dict[str, Any]] = []
        self.stop_calls = 0
        self._request_resolved = asyncio.Event()
        process = type("DummyProcess", (), {"pid": 4512, "returncode": None})()
        self.client = type("DummyClient", (), {"is_running": True, "_process": process})()
        FakeGatekeeperAdapter.instances.append(self)

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        self.start_session_calls.append({"cwd": cwd, **kwargs})
        return {"serverInfo": {"name": "codex"}}

    async def stop_session(self) -> None:
        self.stop_calls += 1
        if self.client._process.returncode is None:
            self.client._process.returncode = 0
        self.client.is_running = False

    async def start_thread(self, **kwargs: Any) -> Any:
        self.start_thread_calls.append(dict(kwargs))
        self.provider_thread_id = "thread-gatekeeper-123"
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
        if self.scenario == "complete":
            await self._emit({"type": "content.delta", "delta": "Planning review complete."})
            await self._emit({"type": "turn.completed", "turn": {"id": "turn-gatekeeper-1"}})
            self.client._process.returncode = 0
        elif self.scenario == "request":
            asyncio.create_task(self._simulate_request_flow(), name="fake-gatekeeper-request")
        elif self.scenario == "start_turn_error":
            self.client._process.returncode = 1
            raise RuntimeError("provider start_turn failed")
        else:
            raise AssertionError(f"Unknown fake Gatekeeper scenario: {self.scenario}")
        return {"turn": {"id": "turn-gatekeeper-1"}}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> Any:
        self.respond_calls.append({"request_id": request_id, "result": result, "error": error})
        self._request_resolved.set()
        return {"request_id": request_id, "result": result, "error": error}

    async def _simulate_request_flow(self) -> None:
        await self._emit(
            {
                "type": "request.opened",
                "request_id": "req-1",
                "request_kind": "user-input",
                "message": "Choose the API strategy.",
            }
        )
        await self._request_resolved.wait()
        await self._emit(
            {
                "type": "request.resolved",
                "request_id": "req-1",
                "request_kind": "user-input",
            }
        )
        await self._emit({"type": "content.delta", "delta": "Recorded the user decision."})
        await self._emit({"type": "turn.completed", "turn": {"id": "turn-gatekeeper-1"}})
        self.client._process.returncode = 0

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.on_canonical_event is not None:
            await self.on_canonical_event(event)

    def _persist_thread_metadata(self, thread_id: str) -> None:
        if self.agent_record is None:
            return
        self.agent_record.provider.provider_thread_id = thread_id
        self.agent_record.provider.resume_cursor = {"threadId": thread_id}


@pytest.mark.parametrize(
    ("trigger", "description", "summary"),
    [
        (GatekeeperTrigger.PROJECT_START, "Create the initial plan.", None),
        (GatekeeperTrigger.TASK_COMPLETION, "Evaluate task-001 completion.", "Agent summary text."),
        (GatekeeperTrigger.TASK_FAILURE, "Task-002 failed with timeout.", "Failure summary."),
        (GatekeeperTrigger.MAX_RETRIES_EXCEEDED, "Task-003 exhausted retries.", "Retry history."),
        (GatekeeperTrigger.USER_CONVERSATION, "User wants to pivot scope.", "Conversation context."),
    ],
)
def test_prompt_template_renders_for_each_trigger(tmp_path, trigger, description, summary):
    initialize_project(tmp_path)
    skills_dir = tmp_path / ".vibrant" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "testing-strategy.md").write_text(
        "# testing-strategy\nWrite focused tests before broader validation.\n",
        encoding="utf-8",
    )

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter)
    system_prompt = gatekeeper.render_system_prompt()
    prompt = gatekeeper.render_prompt(
        GatekeeperRequest(trigger=trigger, trigger_description=description, agent_summary=summary)
    )

    assert f"{trigger.value}: {description}" in prompt
    assert "You are read-only. Do not edit repository files or .vibrant state directly." in system_prompt
    assert "Read `.vibrant/consensus.md` directly" in system_prompt
    assert "testing-strategy: Write focused tests before broader validation." in system_prompt
    assert "## Current Roadmap" in prompt
    assert "## MCP Tools" in system_prompt
    assert PLANNING_COMPLETE_MCP_TOOL in system_prompt
    assert all(tool_name in system_prompt for tool_name in MCP_TOOL_NAMES)
    assert "## Current Consensus" not in prompt
    if trigger is GatekeeperTrigger.USER_CONVERSATION:
        assert "## Agent Summary (if applicable)" not in prompt
    elif summary:
        assert summary in prompt
    else:
        assert "N/A" in prompt


@pytest.mark.asyncio
async def test_gatekeeper_runs_read_only_and_resumes_latest_thread(tmp_path):
    FakeGatekeeperAdapter.instances.clear()
    FakeGatekeeperAdapter.scenario = "complete"
    initialize_project(tmp_path)
    seeded_marker = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter).agent._system_prompt_marker()

    prior_record = AgentRecord(
        identity={
            "run_id": "gatekeeper-project_start-old",
            "agent_id": "gatekeeper-project_start-old",
            "role": AgentType.GATEKEEPER.value,
            "type": AgentType.GATEKEEPER,
        },
        lifecycle={"status": AgentStatus.COMPLETED},
        provider=AgentProviderMetadata(
            provider_thread_id="thread-existing",
            resume_cursor={
                "threadId": "thread-existing",
                GATEKEEPER_SYSTEM_PROMPT_CURSOR_KEY: seeded_marker,
            },
        ),
    )
    runs_dir = tmp_path / ".vibrant" / "agent-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{prior_record.identity.run_id}.json").write_text(
        prior_record.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter, timeout_seconds=1)
    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description="Review the new project direction.",
            agent_summary="Conversation context.",
        ),
        resume_latest_thread=True,
    )

    adapter = FakeGatekeeperAdapter.instances[0]
    assert adapter.start_session_calls[0]["cwd"] == str(tmp_path)
    assert adapter.start_thread_calls == []
    assert adapter.resume_thread_calls[0]["provider_thread_id"] == "thread-existing"
    assert adapter.resume_thread_calls[0]["runtime_mode"] is RuntimeMode.READ_ONLY
    assert "instructions" not in adapter.resume_thread_calls[0]
    assert adapter.start_turn_calls[0]["runtime_mode"] is RuntimeMode.READ_ONLY
    prompt = adapter.start_turn_calls[0]["input_items"][0]["text"]
    assert "## Current Roadmap" in prompt
    assert "## Current Consensus" not in prompt
    assert "You are read-only. Do not edit repository files or .vibrant state directly." not in prompt
    assert result.succeeded is True
    assert result.state is RunState.COMPLETED
    assert result.provider_thread.thread_id == "thread-existing"
    assert result.role == AgentType.GATEKEEPER.value
    assert result.status is AgentStatus.COMPLETED
    assert result.run_id.startswith("gatekeeper-user_conversation-")


@pytest.mark.asyncio
async def test_gatekeeper_seeds_system_prompt_only_when_starting_new_thread(tmp_path):
    FakeGatekeeperAdapter.instances.clear()
    FakeGatekeeperAdapter.scenario = "complete"
    initialize_project(tmp_path)

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter, timeout_seconds=1)
    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.PROJECT_START,
            trigger_description="Create the first plan.",
        ),
        resume_latest_thread=False,
    )

    adapter = FakeGatekeeperAdapter.instances[0]
    assert "instructions" in adapter.start_thread_calls[0]
    assert "You are a long-lived, project-scoped planning and review agent." in adapter.start_thread_calls[0]["instructions"]
    assert "Read `.vibrant/consensus.md` directly" in adapter.start_thread_calls[0]["instructions"]
    prompt = adapter.start_turn_calls[0]["input_items"][0]["text"]
    assert "## Current Roadmap" in prompt
    assert "## Current Consensus" not in prompt
    assert "## MCP Tools" not in prompt
    assert result.succeeded is True
    assert result.provider_thread.resume_cursor is not None
    assert result.provider_thread.resume_cursor[GATEKEEPER_SYSTEM_PROMPT_CURSOR_KEY].startswith(
        f"v{GATEKEEPER_SYSTEM_PROMPT_VERSION}:"
    )


@pytest.mark.asyncio
async def test_gatekeeper_reseeds_resumed_thread_when_system_prompt_marker_is_stale(tmp_path):
    FakeGatekeeperAdapter.instances.clear()
    FakeGatekeeperAdapter.scenario = "complete"
    initialize_project(tmp_path)

    prior_record = AgentRecord(
        identity={
            "run_id": "gatekeeper-project_start-old",
            "agent_id": "gatekeeper-project_start-old",
            "role": AgentType.GATEKEEPER.value,
            "type": AgentType.GATEKEEPER,
        },
        lifecycle={"status": AgentStatus.COMPLETED},
        provider=AgentProviderMetadata(
            provider_thread_id="thread-existing",
            resume_cursor={
                "threadId": "thread-existing",
                GATEKEEPER_SYSTEM_PROMPT_CURSOR_KEY: "v1:stale-marker",
            },
        ),
    )
    runs_dir = tmp_path / ".vibrant" / "agent-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{prior_record.identity.run_id}.json").write_text(
        prior_record.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter, timeout_seconds=1)
    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description="Review the refreshed project instructions.",
        ),
        resume_latest_thread=True,
    )

    adapter = FakeGatekeeperAdapter.instances[0]
    assert adapter.resume_thread_calls[0]["provider_thread_id"] == "thread-existing"
    assert "instructions" in adapter.resume_thread_calls[0]
    assert "You are a long-lived, project-scoped planning and review agent." in adapter.resume_thread_calls[0]["instructions"]
    assert result.provider_thread.resume_cursor is not None
    assert result.provider_thread.resume_cursor[GATEKEEPER_SYSTEM_PROMPT_CURSOR_KEY].startswith(
        f"v{GATEKEEPER_SYSTEM_PROMPT_VERSION}:"
    )


@pytest.mark.asyncio
async def test_gatekeeper_does_not_mark_system_prompt_seeded_before_turn_starts(tmp_path):
    FakeGatekeeperAdapter.instances.clear()
    FakeGatekeeperAdapter.scenario = "start_turn_error"
    initialize_project(tmp_path)

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter, timeout_seconds=1)
    first_result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.PROJECT_START,
            trigger_description="Create the first plan.",
        ),
        resume_latest_thread=False,
    )

    first_adapter = FakeGatekeeperAdapter.instances[0]
    assert first_result.succeeded is False
    assert first_adapter.start_thread_calls[0]["instructions"]
    assert first_result.provider_thread.resume_cursor == {"threadId": "thread-gatekeeper-123"}

    FakeGatekeeperAdapter.scenario = "complete"
    second_result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description="Retry the planning turn.",
        ),
        resume_latest_thread=True,
    )

    second_adapter = FakeGatekeeperAdapter.instances[1]
    assert second_adapter.resume_thread_calls[0]["provider_thread_id"] == "thread-gatekeeper-123"
    assert "instructions" in second_adapter.resume_thread_calls[0]
    assert second_result.succeeded is True


@pytest.mark.asyncio
async def test_gatekeeper_start_run_surfaces_provider_requests_through_agent_handle(tmp_path):
    FakeGatekeeperAdapter.instances.clear()
    FakeGatekeeperAdapter.scenario = "request"
    initialize_project(tmp_path)

    gatekeeper = Gatekeeper(tmp_path, adapter_factory=FakeGatekeeperAdapter, timeout_seconds=1)
    handle = await gatekeeper.start_run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description="Review task-001 output.",
            agent_summary="The task implementation is ready for review.",
        )
    )

    for _ in range(100):
        if handle.awaiting_input:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("Gatekeeper handle never entered awaiting_input state")

    assert handle.awaiting_input is True
    assert handle.agent_record.lifecycle.status is AgentStatus.AWAITING_INPUT
    assert handle.input_requests[0].request_id == "req-1"
    assert handle.input_requests[0].message == "Choose the API strategy."

    await handle.respond_to_request("req-1", result={"answer": "Use OAuth first."})
    result = await handle.wait()

    adapter = FakeGatekeeperAdapter.instances[0]
    assert adapter.respond_calls[0]["request_id"] == "req-1"
    assert adapter.respond_calls[0]["result"] == {"answer": "Use OAuth first."}
    assert result.succeeded is True
    assert result.status is AgentStatus.COMPLETED
    assert "Recorded the user decision." in result.transcript
    assert any(event["type"] == "request.opened" for event in result.events)
    assert any(event["type"] == "request.resolved" for event in result.events)


@pytest.mark.asyncio
async def test_gatekeeper_forwards_canonical_events_to_external_callback(tmp_path):
    FakeGatekeeperAdapter.instances.clear()
    FakeGatekeeperAdapter.scenario = "complete"
    initialize_project(tmp_path)

    forwarded: list[dict[str, Any]] = []
    gatekeeper = Gatekeeper(
        tmp_path,
        adapter_factory=FakeGatekeeperAdapter,
        on_canonical_event=forwarded.append,
        timeout_seconds=1,
    )

    result = await gatekeeper.run(
        GatekeeperRequest(
            trigger=GatekeeperTrigger.PROJECT_START,
            trigger_description="Build a resilient multi-agent orchestrator.",
        )
    )

    assert forwarded
    assert forwarded[0]["agent_id"] == "gatekeeper"
    assert forwarded[0]["role"] == "gatekeeper"
    assert forwarded[0].get("task_id") is None
    assert any(event["type"] == "content.delta" for event in forwarded)
    assert result.status is AgentStatus.COMPLETED
