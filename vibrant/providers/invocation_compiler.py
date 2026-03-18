"""Provider-specific invocation-plan compilers for orchestration bindings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import re
from vibrant.agents.runtime import AgentHandle, AgentRecordCallback, AgentRuntime, ProviderThreadHandle
from vibrant.models.agent import AgentRecord
from typing import Any, TypeAlias
from vibrant.type_defs import is_json_mapping

from .base import ProviderKind
from .invocation import MCPAccessDescriptor, ProviderInvocationPlan
from .registry import normalize_provider_kind

MCPAccessInput: TypeAlias = MCPAccessDescriptor | Mapping[str, Any]


def compile_provider_invocation(
    provider_kind: ProviderKind | str | None,
    access: MCPAccessInput | Sequence[MCPAccessInput] | None = None,
) -> ProviderInvocationPlan:
    """Compile provider-neutral MCP access into provider-native invocation data."""

    normalized_kind = normalize_provider_kind(provider_kind) if provider_kind is not None else None
    descriptors = _coerce_descriptors(access)
    if not descriptors:
        return ProviderInvocationPlan(provider_kind=normalized_kind)

    if normalized_kind is ProviderKind.CODEX:
        return _compile_codex_invocation(descriptors)

    return ProviderInvocationPlan(
        provider_kind=normalized_kind,
        binding_id=_resolve_binding_id(descriptors),
        visible_tools=_merge_visible_values(descriptors, "visible_tools"),
        visible_resources=_merge_visible_values(descriptors, "visible_resources"),
        debug_metadata={
            "mcp_access": _serialize_descriptors(descriptors),
            "mcp_runtime_supported": False,
        },
    )


class InvocationPlanRuntime:
    """Runtime wrapper that injects a fixed invocation plan into start/resume calls."""

    def __init__(self, runtime: AgentRuntime, invocation_plan: ProviderInvocationPlan | None) -> None:
        self._runtime = runtime
        self.invocation_plan = invocation_plan

    async def start(
        self,
        *,
        agent_record: AgentRecord,
        prompt: str,
        cwd: str | None = None,
        resume_thread_id: str | None = None,
        on_record_updated: AgentRecordCallback | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> AgentHandle:
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
        agent_record: AgentRecord,
        prompt: str,
        provider_thread: ProviderThreadHandle,
        cwd: str | None = None,
        on_record_updated: AgentRecordCallback | None = None,
        invocation_plan: ProviderInvocationPlan | None = None,
    ) -> AgentHandle:
        return await self._runtime.resume_run(
            agent_record=agent_record,
            prompt=prompt,
            provider_thread=provider_thread,
            cwd=cwd,
            on_record_updated=on_record_updated,
            invocation_plan=invocation_plan or self.invocation_plan,
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._runtime, name)


def _compile_codex_invocation(accesses: Sequence[MCPAccessDescriptor]) -> ProviderInvocationPlan:
    _validate_codex_server_ids(accesses)
    overrides: list[str] = []
    ready_bindings: list[str] = []
    pending_bindings: list[str] = []
    for access in accesses:
        if not access.server_id:
            pending_bindings.append(access.binding_id)
            continue

        if access.endpoint_url:
            ready_bindings.append(access.binding_id)
            overrides.extend(
                [
                    f"mcp_servers.{access.server_id}.enabled={_toml_literal(True)}",
                    f"mcp_servers.{access.server_id}.url={_toml_literal(access.endpoint_url)}",
                    f"mcp_servers.{access.server_id}.required={_toml_literal(access.required)}",
                ]
            )
            if access.visible_tools:
                overrides.append(
                    f"mcp_servers.{access.server_id}.enabled_tools={_toml_literal(access.visible_tools)}"
                )
            if access.static_headers:
                overrides.append(
                    f"mcp_servers.{access.server_id}.http_headers={_toml_literal(access.static_headers)}"
                )
            continue

        if access.stdio_command:
            ready_bindings.append(access.binding_id)
            overrides.extend(
                [
                    f"mcp_servers.{access.server_id}.enabled={_toml_literal(True)}",
                    f"mcp_servers.{access.server_id}.command={_toml_literal(access.stdio_command)}",
                    f"mcp_servers.{access.server_id}.required={_toml_literal(access.required)}",
                ]
            )
            if access.stdio_args:
                overrides.append(
                    f"mcp_servers.{access.server_id}.args={_toml_literal(access.stdio_args)}"
                )
            if access.stdio_env:
                overrides.append(
                    f"mcp_servers.{access.server_id}.env={_toml_literal(access.stdio_env)}"
                )
            if access.visible_tools:
                overrides.append(
                    f"mcp_servers.{access.server_id}.enabled_tools={_toml_literal(access.visible_tools)}"
                )
            continue

        pending_bindings.append(access.binding_id)

    debug_metadata = {
        "mcp_access": _serialize_descriptors(accesses),
        "mcp_transport_ready": bool(ready_bindings),
        "mcp_ready_bindings": ready_bindings,
        "mcp_pending_bindings": pending_bindings,
    }
    launch_args: list[str] = []
    for override in overrides:
        launch_args.extend(["--config", override])

    debug_metadata["codex_config_overrides"] = list(overrides)
    return ProviderInvocationPlan(
        provider_kind=ProviderKind.CODEX,
        launch_args=launch_args,
        binding_id=_resolve_binding_id(accesses),
        visible_tools=_merge_visible_values(accesses, "visible_tools"),
        visible_resources=_merge_visible_values(accesses, "visible_resources"),
        debug_metadata=debug_metadata,
    )


def _coerce_descriptors(access: MCPAccessInput | Sequence[MCPAccessInput] | None) -> list[MCPAccessDescriptor]:
    if access is None:
        return []
    if isinstance(access, Sequence) and not isinstance(access, (str, bytes, bytearray, Mapping, MCPAccessDescriptor)):
        return [_coerce_descriptor(item) for item in access]
    return [_coerce_descriptor(access)]


def _coerce_descriptor(access: MCPAccessInput) -> MCPAccessDescriptor:
    if isinstance(access, MCPAccessDescriptor):
        return access
    payload = dict(access)
    if "run_id" not in payload and "session_id" in payload:
        payload["run_id"] = payload.pop("session_id")
    else:
        payload.pop("session_id", None)
    assert is_json_mapping(payload), "MCP access payload must be JSON-compatible"
    return MCPAccessDescriptor(**payload)


def _merge_visible_values(
    descriptors: Iterable[MCPAccessDescriptor],
    field_name: str,
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for descriptor in descriptors:
        for value in getattr(descriptor, field_name):
            if value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged


def _resolve_binding_id(descriptors: Sequence[MCPAccessDescriptor]) -> str | None:
    if len(descriptors) == 1:
        return descriptors[0].binding_id
    return None


def _serialize_descriptors(
    descriptors: Sequence[MCPAccessDescriptor],
) -> dict[str, Any] | list[dict[str, Any]]:
    serialized = [descriptor.to_mapping() for descriptor in descriptors]
    if len(serialized) == 1:
        return serialized[0]
    return serialized


def _validate_codex_server_ids(accesses: Sequence[MCPAccessDescriptor]) -> None:
    seen_server_ids: set[str] = set()
    duplicate_server_ids: set[str] = set()
    for access in accesses:
        if not access.server_id:
            continue
        if access.server_id in seen_server_ids:
            duplicate_server_ids.add(access.server_id)
            continue
        seen_server_ids.add(access.server_id)
    if duplicate_server_ids:
        joined = ", ".join(sorted(duplicate_server_ids))
        raise ValueError(f"Duplicate MCP server_id values are not allowed: {joined}")


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
