"""Tests for adapter-backed native and canonical provider logs."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from vibrant.models.agent import AgentRecord, AgentType
from vibrant.models.wire import JsonRpcNotification
from vibrant.providers.base import CodexAuthConfig, CodexAuthMode, RuntimeMode
from vibrant.providers.codex.adapter import CodexProviderAdapter


class LoggingFakeCodexClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.responses: dict[str, Any] = {}
        self.server_responses: list[tuple[int | str, Any, Any]] = []
        self._on_notification = None
        self._on_stderr = None
        self._on_raw_event = None
        self.is_running = True

    async def start(self) -> None:
        self.calls.append(("start", None))

    async def stop(self) -> None:
        self.calls.append(("stop", None))
        self.is_running = False

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params))
        if self._on_raw_event is not None:
            self._on_raw_event({"event": "jsonrpc.request.sent", "data": {"method": method, "params": params or {}}})
        return self.responses.get(method, {})

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.calls.append((f"notify:{method}", params))
        if self._on_raw_event is not None:
            self._on_raw_event({"event": "jsonrpc.notification.sent", "data": {"method": method, "params": params or {}}})

    def respond_to_server_request(self, jsonrpc_id: int | str, result: Any = None, error: dict[str, Any] | None = None) -> None:
        self.server_responses.append((jsonrpc_id, result, error))
        if self._on_raw_event is not None:
            self._on_raw_event({"event": "jsonrpc.response.sent", "data": {"id": jsonrpc_id, "result": result, "error": error}})


def _read_lines(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


class TestCodexProviderLogging:
    @pytest.mark.asyncio
    async def test_adapter_writes_native_and_canonical_logs(self, tmp_path):
        client = LoggingFakeCodexClient()
        client.responses["initialize"] = {"serverInfo": {"name": "codex"}}
        client.responses["thread/start"] = {"thread": {"id": "thread_abc123", "path": ".codex/thread_abc123"}}

        agent = AgentRecord(
            agent_id="agent-task-001",
            task_id="task-001",
            type=AgentType.CODE,
            provider={
                "native_event_log": str(tmp_path / "native.ndjson"),
                "canonical_event_log": str(tmp_path / "canonical.ndjson"),
            },
        )
        adapter = CodexProviderAdapter(client=client, cwd=str(tmp_path), agent_record=agent)

        await adapter.start_session(cwd=str(tmp_path))
        await adapter.start_thread(
            model="gpt-5.3-codex",
            cwd=str(tmp_path),
            runtime_mode=RuntimeMode.WORKSPACE_WRITE,
            approval_policy="never",
        )
        await adapter._handle_notification(
            JsonRpcNotification(
                method="item/agentMessage/delta",
                params={"itemId": "item-1", "turnId": "turn-1", "delta": "hello"},
            )
        )
        client._on_raw_event({"event": "stderr.line", "data": {"line": "boom"}})

        native_lines = _read_lines(agent.provider.native_event_log)
        canonical_lines = _read_lines(agent.provider.canonical_event_log)

        assert any(line["event"] == "jsonrpc.request.sent" for line in native_lines)
        assert any(line["event"] == "stderr.line" for line in native_lines)
        assert [line["event"] for line in canonical_lines[:3]] == ["session.started", "thread.started", "content.delta"]

    @pytest.mark.asyncio
    async def test_adapter_redacts_auth_secrets_in_native_log(self, tmp_path: Path):
        client = LoggingFakeCodexClient()
        client.responses["initialize"] = {"serverInfo": {"name": "codex"}}
        client.responses["account/login/start"] = {"type": "apiKey"}

        agent = AgentRecord(
            agent_id="agent-auth-redact",
            task_id="task-auth-redact",
            type=AgentType.CODE,
            provider={
                "native_event_log": str(tmp_path / "native.ndjson"),
                "canonical_event_log": str(tmp_path / "canonical.ndjson"),
            },
        )
        adapter = CodexProviderAdapter(client=client, cwd=str(tmp_path), agent_record=agent)

        await adapter.start_session(
            cwd=str(tmp_path),
            auth_config=CodexAuthConfig(mode=CodexAuthMode.API_KEY, api_key="sk-secret"),
        )
        await adapter.stop_session()

        native_text = Path(agent.provider.native_event_log).read_text(encoding="utf-8")
        assert "sk-secret" not in native_text
        assert "***REDACTED***" in native_text


@pytest.mark.asyncio
async def test_real_agent_run_populates_both_logs(tmp_path: Path):
    if os.environ.get("VIBRANT_RUN_CODEX_INTEGRATION") != "1":
        pytest.skip("Set VIBRANT_RUN_CODEX_INTEGRATION=1 to run the real codex integration test")

    codex_binary = shutil.which("codex")
    if not codex_binary:
        pytest.skip("codex CLI is not available")

    agent = AgentRecord(agent_id="agent-task-real-log", task_id="task-real-log", type=AgentType.CODE)
    adapter = CodexProviderAdapter(cwd=str(tmp_path), codex_binary=codex_binary, agent_record=agent)

    await adapter.start_session(cwd=str(tmp_path))
    try:
        await adapter.start_thread(
            model="gpt-5.3-codex",
            cwd=str(tmp_path),
            runtime_mode=RuntimeMode.WORKSPACE_WRITE,
            approval_policy="never",
        )
    finally:
        await adapter.stop_session()

    native_lines = _read_lines(agent.provider.native_event_log)
    canonical_lines = _read_lines(agent.provider.canonical_event_log)

    assert native_lines
    assert canonical_lines
