"""Unit tests for the provider adapter abstraction layer."""

from __future__ import annotations

from typing import Any

import pytest

from vibrant.providers.base import ProviderAdapter, RuntimeMode


class DummyProviderAdapter(ProviderAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        return {"cwd": cwd, **kwargs}

    async def stop_session(self) -> Any:
        return None

    async def start_thread(self, **kwargs: Any) -> Any:
        return kwargs

    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        return {"threadId": provider_thread_id, **kwargs}

    async def start_turn(
        self,
        *,
        input_items,
        runtime_mode: RuntimeMode,
        approval_policy: str,
        **kwargs: Any,
    ) -> Any:
        return {
            "input": list(input_items),
            "runtime_mode": runtime_mode,
            "approval_policy": approval_policy,
            **kwargs,
        }

    async def interrupt_turn(self, **kwargs: Any) -> Any:
        return kwargs

    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: Any | None = None,
        error=None,
    ) -> Any:
        return {"id": request_id, "result": result, "error": error}

    async def on_canonical_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class TestProviderAdapterInterface:
    def test_provider_adapter_is_abstract(self):
        assert {
            "start_session",
            "stop_session",
            "start_thread",
            "resume_thread",
            "start_turn",
            "interrupt_turn",
            "respond_to_request",
            "on_canonical_event",
        }.issubset(ProviderAdapter.__abstractmethods__)

        with pytest.raises(TypeError, match="abstract class ProviderAdapter"):
            ProviderAdapter()

    def test_concrete_subclass_can_instantiate(self):
        adapter = DummyProviderAdapter()
        assert isinstance(adapter, ProviderAdapter)


class TestRuntimeMode:
    @pytest.mark.parametrize(
        ("mode", "thread_sandbox", "turn_policy"),
        [
            (RuntimeMode.READ_ONLY, "read-only", {"type": "readOnly"}),
            (RuntimeMode.WORKSPACE_WRITE, "workspace-write", {"type": "workspaceWrite"}),
            (RuntimeMode.FULL_ACCESS, "danger-full-access", {"type": "dangerFullAccess"}),
        ],
    )
    def test_codex_mapping(self, mode: RuntimeMode, thread_sandbox: str, turn_policy: dict[str, str]):
        assert mode.codex_thread_sandbox == thread_sandbox
        assert mode.codex_turn_sandbox_policy == turn_policy
