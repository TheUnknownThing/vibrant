"""Binding registry for the loopback FastMCP transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .common import MCPPrincipal

if TYPE_CHECKING:
    from vibrant.providers.invocation import MCPAccessDescriptor


BINDING_HEADER_NAME = "X-Vibrant-Binding"


@dataclass(frozen=True, slots=True)
class RegisteredMCPBinding:
    """Resolved effective binding for one MCP client session."""

    binding_id: str
    principal: MCPPrincipal
    access: MCPAccessDescriptor
    visible_tools: frozenset[str]
    visible_resources: frozenset[str]


class MCPBindingRegistry:
    """Store active binding definitions keyed by binding id."""

    def __init__(self) -> None:
        self._bindings: dict[str, RegisteredMCPBinding] = {}

    def register(
        self,
        *,
        principal: MCPPrincipal,
        access: MCPAccessDescriptor,
    ) -> RegisteredMCPBinding:
        binding = RegisteredMCPBinding(
            binding_id=access.binding_id,
            principal=principal,
            access=access,
            visible_tools=frozenset(access.visible_tools),
            visible_resources=frozenset(access.visible_resources),
        )
        self._bindings[binding.binding_id] = binding
        return binding

    def resolve(self, binding_id: str | None) -> RegisteredMCPBinding | None:
        if not binding_id:
            return None
        return self._bindings.get(binding_id)

    def discard(self, binding_id: str | None) -> None:
        if binding_id:
            self._bindings.pop(binding_id, None)

    def clear(self) -> None:
        self._bindings.clear()
