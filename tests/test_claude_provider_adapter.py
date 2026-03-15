"""Unit tests for the Claude provider adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from claude_agent_sdk.types import AssistantMessage, ResultMessage, TaskProgressMessage, TaskStartedMessage, TextBlock

from vibrant.models.agent import AgentRecord, AgentType
from vibrant.providers.base import RuntimeMode
from vibrant.providers.claude.adapter import ClaudeProviderAdapter


def _make_agent_record(
    *,
    agent_id: str,
    agent_type: AgentType,
    run_id: str | None = None,
) -> AgentRecord:
    return AgentRecord(
        identity={
            "agent_id": agent_id,
            "run_id": run_id or agent_id,
            "role": agent_type.value,
            "type": agent_type,
        }
    )


class FakeClaudeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.messages: list[Any] = []
        self.server_info = {"protocol": "sdk", "output_style": "stream-json"}
        self.model: str | None = None
        self.permission_mode: str | None = None

    async def connect(self) -> None:
        self.calls.append(("connect", None))

    async def disconnect(self) -> None:
        self.calls.append(("disconnect", None))

    async def get_server_info(self) -> dict[str, Any]:
        self.calls.append(("get_server_info", None))
        return dict(self.server_info)

    async def set_model(self, model: str | None = None) -> None:
        self.model = model
        self.calls.append(("set_model", model))

    async def set_permission_mode(self, mode: str) -> None:
        self.permission_mode = mode
        self.calls.append(("set_permission_mode", mode))

    async def query(self, prompt: str) -> None:
        self.calls.append(("query", prompt))

    async def receive_response(self):
        for message in list(self.messages):
            yield message

    async def interrupt(self) -> None:
        self.calls.append(("interrupt", None))

    async def get_mcp_status(self) -> dict[str, Any]:
        self.calls.append(("get_mcp_status", None))
        return {"mcpServers": []}


class TestClaudeProviderAdapter:
    def test_build_client_options_preserves_resume_and_tool_rules(self):
        adapter = ClaudeProviderAdapter(
            cwd="/tmp/project",
            resume_thread_id="session-existing",
            claude_allowed_tools=["Read", "Bash", "Read"],
            claude_disallowed_tools=["Write", "Edit", "Write"],
            claude_fallback_model="claude-haiku-4-5",
            claude_setting_sources=["project", "local"],
            claude_model="claude-sonnet-4-5",
            claude_effort="high",
        )

        options = adapter._build_client_options()

        assert options.resume == "session-existing"
        assert options.allowed_tools == ["Read", "Bash"]
        assert options.disallowed_tools == ["Write", "Edit"]
        assert options.fallback_model == "claude-haiku-4-5"
        assert options.setting_sources == ["project", "local"]
        assert options.model == "claude-sonnet-4-5"
        assert options.effort == "high"

    @pytest.mark.asyncio
    async def test_start_turn_maps_claude_messages_to_canonical_events(self, tmp_path):
        client = FakeClaudeClient()
        client.messages = [
            TaskStartedMessage(
                subtype="task_started",
                data={"type": "system", "subtype": "task_started"},
                task_id="task-1",
                description="Inspecting the workspace",
                uuid="msg-1",
                session_id="session-123",
            ),
            TaskProgressMessage(
                subtype="task_progress",
                data={"type": "system", "subtype": "task_progress"},
                task_id="task-1",
                description="Reading files",
                usage={"total_tokens": 12, "tool_uses": 1, "duration_ms": 45},
                uuid="msg-2",
                session_id="session-123",
                last_tool_name="Read",
            ),
            AssistantMessage(
                content=[TextBlock(text="Implemented the requested change.")],
                model="claude-sonnet-4-5",
            ),
            ResultMessage(
                subtype="success",
                duration_ms=250,
                duration_api_ms=200,
                is_error=False,
                num_turns=3,
                session_id="session-123",
                result="Implemented the requested change.",
                usage={"input_tokens": 10, "output_tokens": 20},
            ),
        ]

        agent = _make_agent_record(
            agent_id="agent-claude-1",
            run_id="run-claude-1",
            agent_type=AgentType.CODE,
        )
        events: list[dict[str, Any]] = []
        adapter = ClaudeProviderAdapter(client=client, cwd=str(tmp_path), agent_record=agent, on_canonical_event=events.append)

        await adapter.start_session(cwd=str(tmp_path))
        await adapter.start_thread(
            model="claude-sonnet-4-5",
            runtime_mode=RuntimeMode.WORKSPACE_WRITE,
            approval_policy="never",
        )
        result = await adapter.start_turn(
            input_items=[{"type": "text", "text": "Implement the feature"}],
            runtime_mode=RuntimeMode.WORKSPACE_WRITE,
            approval_policy="never",
        )

        assert client.calls[:5] == [
            ("connect", None),
            ("get_server_info", None),
            ("set_model", "claude-sonnet-4-5"),
            ("set_permission_mode", "default"),
            ("query", "Implement the feature"),
        ]
        assert result["session_id"] == "session-123"
        assert agent.provider.kind == "claude"
        assert agent.provider.provider_thread_id == "session-123"
        assert agent.provider.resume_cursor == {"sessionId": "session-123"}
        assert agent.provider.native_event_log is not None and agent.provider.native_event_log.endswith("run-claude-1.ndjson")
        assert (
            agent.provider.canonical_event_log is not None
            and agent.provider.canonical_event_log.endswith("run-claude-1.ndjson")
        )
        assert [event["type"] for event in events] == [
            "session.started",
            "turn.started",
            "thread.started",
            "task.progress",
            "task.progress",
            "content.delta",
            "turn.completed",
            "task.completed",
        ]
        assert {event["run_id"] for event in events} == {"run-claude-1"}
        assert events[2]["thread"]["id"] == "session-123"
        assert events[5]["delta"] == "Implemented the requested change."
        assert events[6]["turn_status"] == "completed"

    @pytest.mark.asyncio
    async def test_resume_thread_reuses_existing_session_id(self, tmp_path):
        client = FakeClaudeClient()
        client.messages = [
            ResultMessage(
                subtype="success",
                duration_ms=120,
                duration_api_ms=100,
                is_error=False,
                num_turns=1,
                session_id="session-existing",
                result="Follow-up complete.",
            )
        ]

        agent = _make_agent_record(
            agent_id="agent-claude-2",
            run_id="run-claude-2",
            agent_type=AgentType.GATEKEEPER,
        )
        events: list[dict[str, Any]] = []
        adapter = ClaudeProviderAdapter(
            client=client,
            cwd=str(tmp_path),
            resume_thread_id="session-existing",
            agent_record=agent,
            on_canonical_event=events.append,
        )

        await adapter.start_session(cwd=str(tmp_path))
        await adapter.resume_thread(
            "session-existing",
            model="claude-sonnet-4-5",
            runtime_mode=RuntimeMode.READ_ONLY,
            approval_policy="never",
        )
        await adapter.start_turn(
            input_items=[{"type": "text", "text": "Continue the conversation"}],
            runtime_mode=RuntimeMode.READ_ONLY,
            approval_policy="never",
        )

        assert agent.provider.provider_thread_id == "session-existing"
        assert events[1]["type"] == "thread.started"
        assert events[1]["resumed"] is True
        assert events[1]["thread"]["id"] == "session-existing"

    @pytest.mark.asyncio
    async def test_workspace_write_permission_callback_blocks_outside_workspace_paths(self, tmp_path):
        adapter = ClaudeProviderAdapter(
            client=FakeClaudeClient(),
            cwd=str(tmp_path),
            claude_add_dirs=[str(tmp_path / "shared")],
        )

        await adapter.start_session(cwd=str(tmp_path))
        await adapter.start_thread(
            model="claude-sonnet-4-5",
            runtime_mode=RuntimeMode.WORKSPACE_WRITE,
            approval_policy="never",
        )

        denied = await adapter._can_use_tool(
            "Write",
            {"file_path": "/etc/passwd"},
            SimpleNamespace(suggestions=[]),
        )
        allowed = await adapter._can_use_tool(
            "Write",
            {"file_path": str(tmp_path / "app.py")},
            SimpleNamespace(suggestions=[]),
        )

        assert getattr(denied, "behavior", None) == "deny"
        assert "workspace roots" in getattr(denied, "message", "")
        assert getattr(allowed, "behavior", None) == "allow"
