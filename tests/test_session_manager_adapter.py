"""Tests that the current session manager path runs through the provider adapter."""

from __future__ import annotations

from typing import Any

import pytest

from vibrant.models import SessionConfig
from vibrant.providers.base import RuntimeMode
from vibrant.session_manager import SessionManager


class FakeAdapter:
    instances: list["FakeAdapter"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.start_session_calls: list[dict[str, Any]] = []
        self.start_thread_calls: list[dict[str, Any]] = []
        self.start_turn_calls: list[dict[str, Any]] = []
        self.respond_calls: list[dict[str, Any]] = []
        self.stop_calls = 0
        self.provider_thread_id = None
        self.client = type("DummyClient", (), {"is_running": True})()
        FakeAdapter.instances.append(self)

    @property
    def is_running(self) -> bool:
        return True

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        self.start_session_calls.append({"cwd": cwd, **kwargs})
        return {"serverInfo": {"name": "codex"}}

    async def stop_session(self) -> None:
        self.stop_calls += 1

    async def start_thread(self, **kwargs: Any) -> Any:
        self.start_thread_calls.append(kwargs)
        self.provider_thread_id = "thread-fake-123"
        return {"thread": {"id": self.provider_thread_id}}

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        self.provider_thread_id = provider_thread_id
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
        return {"turn": {"id": "turn-1"}}

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def respond_to_request(self, request_id: int | str, *, result: Any | None = None, error=None) -> Any:
        self.respond_calls.append({"request_id": request_id, "result": result, "error": error})


class TestSessionManagerAdapterIntegration:
    @pytest.mark.asyncio
    async def test_create_session_and_send_message_use_provider_adapter(self, tmp_path):
        FakeAdapter.instances.clear()
        manager = SessionManager(adapter_factory=FakeAdapter)
        config = SessionConfig(cwd=str(tmp_path), model="gpt-5.3-codex")

        thread = await manager.create_session(config)

        adapter = FakeAdapter.instances[0]
        assert adapter.start_session_calls[0]["cwd"] == str(tmp_path)
        assert adapter.start_thread_calls[0]["model"] == "gpt-5.3-codex"
        assert adapter.start_thread_calls[0]["runtime_mode"] is RuntimeMode.FULL_ACCESS
        assert thread.codex_thread_id == "thread-fake-123"

        native_log, canonical_log = manager.get_provider_log_paths(thread.id)
        assert native_log is not None and native_log.endswith(f"{thread.id}.ndjson")
        assert canonical_log is not None and canonical_log.endswith(f"{thread.id}.ndjson")

        await manager.send_message(thread.id, "hello adapter")

        assert adapter.start_turn_calls[0]["threadId"] == "thread-fake-123"
        assert adapter.start_turn_calls[0]["approval_policy"] == "never"
        assert adapter.start_turn_calls[0]["input_items"][0]["text"] == "hello adapter"
        assert thread.id in manager.get_active_thread_ids()

        await manager.stop_session(thread.id)
        assert adapter.stop_calls == 1
