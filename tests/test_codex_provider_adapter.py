"""Unit and optional integration tests for the Codex provider adapter."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from vibrant.models.agent import AgentRecord, AgentType
from vibrant.models.wire import JsonRpcNotification
from vibrant.providers.base import CodexAuthConfig, CodexAuthMode, RuntimeMode
from vibrant.providers.codex.adapter import CodexProviderAdapter
from vibrant.providers.codex.client import CodexClientError


class FakeCodexClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.responses: dict[str, Any] = {}
        self.server_responses: list[tuple[int | str, Any, Any]] = []
        self.started = False
        self.stopped = False
        self._on_notification = None
        self._on_stderr = None

    async def start(self) -> None:
        self.started = True
        self.calls.append(("start", None))

    async def stop(self) -> None:
        self.stopped = True
        self.calls.append(("stop", None))

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params))
        return self.responses.get(method, {})

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.calls.append((f"notify:{method}", params))

    def respond_to_server_request(self, jsonrpc_id: int | str, result: Any = None, error: dict[str, Any] | None = None) -> None:
        self.server_responses.append((jsonrpc_id, result, error))


class TestCodexProviderAdapter:
    @pytest.mark.asyncio
    async def test_start_session_handshake_and_start_thread(self):
        client = FakeCodexClient()
        client.responses["initialize"] = {"serverInfo": {"name": "codex"}}
        client.responses["thread/start"] = {
            "thread": {
                "id": "thread_abc123",
                "path": ".codex/threads/thread_abc123",
                "rolloutPath": ".codex/threads/thread_abc123/rollout.jsonl",
            }
        }
        agent = AgentRecord(agent_id="agent-task-001", task_id="task-001", type=AgentType.CODE)
        events: list[dict[str, Any]] = []
        adapter = CodexProviderAdapter(client=client, agent_record=agent, on_canonical_event=events.append)

        await adapter.start_session(cwd="/tmp/project")
        await adapter.start_thread(
            model="gpt-5.3-codex",
            cwd="/tmp/project",
            runtime_mode=RuntimeMode.WORKSPACE_WRITE,
            approval_policy="never",
            model_provider="openai",
            reasoning_effort="medium",
            reasoning_summary="auto",
        )

        assert client.calls[0] == ("start", None)
        assert client.calls[1][0] == "initialize"
        assert client.calls[1][1]["capabilities"]["experimentalApi"] is True
        assert client.calls[2] == ("notify:initialized", None)
        assert client.calls[3][0] == "thread/start"
        assert client.calls[3][1]["sandbox"] == "workspace-write"
        assert client.calls[3][1]["approvalPolicy"] == "never"
        assert client.calls[3][1]["persistExtendedHistory"] is True

        assert agent.provider.provider_thread_id == "thread_abc123"
        assert agent.provider.resume_cursor == {"threadId": "thread_abc123", "approvalPolicy": "never"}
        assert agent.provider.thread_path == ".codex/threads/thread_abc123"
        assert agent.provider.rollout_path == ".codex/threads/thread_abc123/rollout.jsonl"
        assert [event["type"] for event in events[:2]] == ["session.started", "thread.started"]

    @pytest.mark.asyncio
    async def test_notification_delta_maps_to_canonical_content_delta(self):
        events: list[dict[str, Any]] = []
        adapter = CodexProviderAdapter(client=FakeCodexClient(), on_canonical_event=events.append)

        await adapter._handle_notification(
            JsonRpcNotification(
                method="item/agentMessage/delta",
                params={"itemId": "item-1", "turnId": "turn-1", "delta": "hello"},
            )
        )

        assert len(events) == 1
        assert events[0]["type"] == "content.delta"
        assert events[0]["item_id"] == "item-1"
        assert events[0]["turn_id"] == "turn-1"
        assert events[0]["delta"] == "hello"

    @pytest.mark.asyncio
    async def test_reasoning_summary_delta_maps_to_canonical_event(self):
        events: list[dict[str, Any]] = []
        adapter = CodexProviderAdapter(client=FakeCodexClient(), on_canonical_event=events.append)

        await adapter._handle_notification(
            JsonRpcNotification(
                method="item/reasoning/summaryTextDelta",
                params={"itemId": "item-r1", "turnId": "turn-1", "delta": "summary", "summaryIndex": 0},
            )
        )

        assert len(events) == 1
        assert events[0]["type"] == "reasoning.summary.delta"
        assert events[0]["item_id"] == "item-r1"
        assert events[0]["turn_id"] == "turn-1"
        assert events[0]["delta"] == "summary"
        assert events[0]["summary_index"] == 0

    @pytest.mark.asyncio
    async def test_turn_completed_maps_to_canonical_turn_completed(self):
        events: list[dict[str, Any]] = []
        adapter = CodexProviderAdapter(client=FakeCodexClient(), on_canonical_event=events.append)

        await adapter._handle_notification(
            JsonRpcNotification(
                method="turn/completed",
                params={"turn": {"id": "turn-123", "status": "completed"}},
            )
        )

        assert [event["type"] for event in events] == ["turn.completed", "task.completed"]
        assert events[0]["turn"]["id"] == "turn-123"

    @pytest.mark.asyncio
    async def test_server_request_maps_to_request_opened_and_responds(self):
        client = FakeCodexClient()
        events: list[dict[str, Any]] = []
        adapter = CodexProviderAdapter(client=client, on_canonical_event=events.append)

        await adapter._handle_notification(
            JsonRpcNotification(
                method="item/commandExecution/requestApproval",
                params={"command": "rm -rf /", "_jsonrpc_id": 10},
            )
        )
        await adapter.respond_to_request(10, result={"approved": True})

        assert events[0]["type"] == "request.opened"
        assert events[0]["request_id"] == 10
        assert events[0]["request_kind"] == "approval"
        assert client.server_responses == [(10, {"approved": True}, None)]
        assert events[1]["type"] == "request.resolved"

    @pytest.mark.asyncio
    async def test_send_request_delegates_to_client(self):
        client = FakeCodexClient()
        client.responses["skills/list"] = {"data": []}
        adapter = CodexProviderAdapter(client=client)

        result = await adapter.send_request("skills/list", {"cwds": ["/tmp"]})

        assert result == {"data": []}
        assert client.calls[0] == ("skills/list", {"cwds": ["/tmp"]})

    @pytest.mark.asyncio
    async def test_start_session_with_custom_auth_calls_account_login_start(self):
        client = FakeCodexClient()
        client.responses["initialize"] = {"serverInfo": {"name": "codex"}}
        client.responses["account/login/start"] = {"type": "apiKey"}
        adapter = CodexProviderAdapter(client=client)

        await adapter.start_session(
            cwd="/tmp/project",
            auth_config=CodexAuthConfig(mode=CodexAuthMode.API_KEY, api_key="sk-test"),
        )

        methods = [call[0] for call in client.calls]
        assert methods == ["start", "initialize", "notify:initialized", "account/login/start"]
        assert client.calls[3][1]["type"] == "apiKey"
        assert client.calls[3][1]["apiKey"] == "sk-test"

    @pytest.mark.asyncio
    async def test_reasoning_item_completed_sanitizes_raw_content(self):
        events: list[dict[str, Any]] = []
        adapter = CodexProviderAdapter(client=FakeCodexClient(), on_canonical_event=events.append)

        await adapter._handle_notification(
            JsonRpcNotification(
                method="item/completed",
                params={
                    "item": {
                        "type": "reasoning",
                        "id": "r1",
                        "summary": ["line 1", "line 2"],
                        "content": [{"type": "text", "text": "raw reasoning"}],
                    }
                },
            )
        )

        assert len(events) == 1
        assert events[0]["type"] == "task.progress"
        item = events[0]["item"]
        assert item.get("text") == "line 1\nline 2"
        assert "content" not in item

    @pytest.mark.asyncio
    async def test_resume_thread_uses_thread_resume(self):
        client = FakeCodexClient()
        client.responses["thread/resume"] = {"thread": {"id": "thread_abc123"}}
        adapter = CodexProviderAdapter(client=client)

        await adapter.resume_thread(
            "thread_abc123",
            runtime_mode=RuntimeMode.READ_ONLY,
            approval_policy="on-request",
        )

        assert client.calls[0][0] == "thread/resume"
        assert client.calls[0][1]["threadId"] == "thread_abc123"
        assert client.calls[0][1]["sandbox"] == "read-only"
        assert client.calls[0][1]["approvalPolicy"] == "on-request"


@pytest.mark.asyncio
async def test_codex_app_server_handshake_integration(tmp_path: Path):
    if os.environ.get("VIBRANT_RUN_CODEX_INTEGRATION") != "1":
        pytest.skip("Set VIBRANT_RUN_CODEX_INTEGRATION=1 to run the real codex integration test")

    codex_binary = shutil.which("codex")
    if not codex_binary:
        pytest.skip("codex CLI is not available")

    agent = AgentRecord(agent_id="agent-task-real", task_id="task-real", type=AgentType.CODE)
    events: list[dict[str, Any]] = []
    adapter = CodexProviderAdapter(
        cwd=str(tmp_path),
        codex_binary=codex_binary,
        agent_record=agent,
        on_canonical_event=events.append,
    )

    try:
        await adapter.start_session(cwd=str(tmp_path))
        result = await adapter.start_thread(
            model="gpt-5.3-codex",
            cwd=str(tmp_path),
            runtime_mode=RuntimeMode.WORKSPACE_WRITE,
            approval_policy="never",
        )
    except CodexClientError:
        raise
    finally:
        try:
            await adapter.stop_session()
        except Exception:
            pass

    assert isinstance(result, dict)
    assert agent.provider.provider_thread_id is not None
    assert any(event["type"] == "session.started" for event in events)
