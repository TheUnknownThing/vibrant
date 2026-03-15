"""Provider-specific invocation-plan compilers for orchestration bindings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from .base import ProviderKind
from .invocation import MCPAccessDescriptor, ProviderInvocationPlan
from .registry import normalize_provider_kind


def compile_provider_invocation(
    provider_kind: ProviderKind | str | None,
    access: MCPAccessDescriptor | Mapping[str, Any] | None = None,
) -> ProviderInvocationPlan:
    """Compile provider-neutral MCP access into provider-native invocation data."""

    normalized_kind = normalize_provider_kind(provider_kind) if provider_kind is not None else None
    descriptor = _coerce_descriptor(access)
    if descriptor is None:
        return ProviderInvocationPlan(provider_kind=normalized_kind)

    if normalized_kind is ProviderKind.CODEX:
        return _compile_codex_invocation(descriptor)

    return ProviderInvocationPlan(
        provider_kind=normalized_kind,
        binding_id=descriptor.binding_id,
        visible_tools=list(descriptor.visible_tools),
        visible_resources=list(descriptor.visible_resources),
        debug_metadata={
            "mcp_access": descriptor.to_mapping(),
            "mcp_runtime_supported": False,
        },
    )


class InvocationPlanRuntime:
    """Runtime wrapper that injects a fixed invocation plan into start/resume calls."""

    def __init__(self, runtime: Any, invocation_plan: ProviderInvocationPlan | None) -> None:
        self._runtime = runtime
        self.invocation_plan = invocation_plan

    async def start(
        self,
        *,
        agent_record: Any,
        prompt: str,
        cwd: str | None = None,
        resume_thread_id: str | None = None,
        on_record_updated: Any = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> Any:
        return await self._runtime.start(
            agent_record=agent_record,
            prompt=prompt,
            cwd=cwd,
            resume_thread_id=resume_thread_id,
            on_record_updated=on_record_updated,
            invocation_plan=invocation_plan or self.invocation_plan,
        )

    async def resume_run(
        self,
        *,
        agent_record: Any,
        prompt: str,
        provider_thread: Any,
        cwd: str | None = None,
        on_record_updated: Any = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> Any:
        return await self._runtime.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            provider_thread=provider_thread,
            cwd=cwd,
            on_record_updated=on_record_updated,
            invocation_plan=invocation_plan or self.invocation_plan,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)


def _compile_codex_invocation(access: MCPAccessDescriptor) -> ProviderInvocationPlan:
    debug_metadata = {"mcp_access": access.to_mapping()}
    if not access.endpoint_url or not access.server_id:
        debug_metadata["mcp_transport_ready"] = False
        return ProviderInvocationPlan(
            provider_kind=ProviderKind.CODEX,
            binding_id=access.binding_id,
            visible_tools=list(access.visible_tools),
            visible_resources=list(access.visible_resources),
            debug_metadata=debug_metadata,
        )

    overrides = [
        f"mcp_servers.{access.server_id}.enabled={_toml_literal(True)}",
        f"mcp_servers.{access.server_id}.url={_toml_literal(access.endpoint_url)}",
        f"mcp_servers.{access.server_id}.required={_toml_literal(access.required)}",
    ]
    if access.visible_tools:
        overrides.append(
            f"mcp_servers.{access.server_id}.enabled_tools={_toml_literal(access.visible_tools)}"
        )
    if access.static_headers:
        overrides.append(
            f"mcp_servers.{access.server_id}.http_headers={_toml_literal(access.static_headers)}"
        )

    launch_args: list[str] = []
    for override in overrides:
        launch_args.extend(["--config", override])

    debug_metadata.update(
        {
            "mcp_transport_ready": True,
            "codex_config_overrides": list(overrides),
        }
    )
    return ProviderInvocationPlan(
        provider_kind=ProviderKind.CODEX,
        launch_args=launch_args,
        binding_id=access.binding_id,
        visible_tools=list(access.visible_tools),
        visible_resources=list(access.visible_resources),
        debug_metadata=debug_metadata,
    )


def _coerce_descriptor(access: MCPAccessDescriptor | Mapping[str, Any] | None) -> MCPAccessDescriptor | None:
    if access is None:
        return None
    if isinstance(access, MCPAccessDescriptor):
        return access
    payload = dict(access)
    if "run_id" not in payload and "session_id" in payload:
        payload["run_id"] = payload.pop("session_id")
    else:
        payload.pop("session_id", None)
    return MCPAccessDescriptor(**payload)


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, Mapping):
        items = ", ".join(
            f"{_toml_key(str(key))} = {_toml_literal(item)}"
            for key, item in value.items()
        )
        return "{ " + items + " }"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML literal type: {type(value)!r}")


def _toml_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return _toml_literal(value)
