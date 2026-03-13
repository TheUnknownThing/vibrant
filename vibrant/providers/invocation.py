"""Provider-neutral invocation models passed from runtime to adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .base import ProviderKind


@dataclass(frozen=True, slots=True)
class MCPAccessDescriptor:
    """Provider-neutral MCP access description produced by orchestration."""

    binding_id: str
    role: str
    session_id: str
    conversation_id: str | None = None
    visible_tools: list[str] = field(default_factory=list)
    visible_resources: list[str] = field(default_factory=list)
    endpoint_url: str | None = None
    transport_hint: Literal["http", "stdio"] | None = None
    required: bool = True
    static_headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderInvocationPlan:
    """Provider-specific launch and session plan produced by a compiler."""

    provider_kind: ProviderKind | None = None
    launch_env: dict[str, str] = field(default_factory=dict)
    launch_args: list[str] = field(default_factory=list)
    session_options: dict[str, Any] = field(default_factory=dict)
    binding_id: str | None = None
    visible_tools: list[str] = field(default_factory=list)
    visible_resources: list[str] = field(default_factory=list)
    debug_metadata: dict[str, Any] = field(default_factory=dict)
