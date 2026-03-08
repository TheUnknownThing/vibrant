"""Provider-neutral adapter interfaces used by Vibrant.

Task 2.1 defines the abstract session/thread/turn control surface that any
provider integration must implement. Vibrant's internal runtime modes map to
Codex sandbox controls as follows:

- ``read_only`` -> thread sandbox ``read-only`` and turn policy ``readOnly``
- ``workspace_write`` -> thread sandbox ``workspace-write`` and turn policy ``workspaceWrite``
- ``full_access`` -> thread sandbox ``danger-full-access`` and turn policy ``dangerFullAccess``
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeAlias

CanonicalEvent: TypeAlias = dict[str, Any]
CanonicalEventHandler: TypeAlias = Callable[[CanonicalEvent], Any]


class RuntimeMode(str, enum.Enum):
    """Provider-neutral runtime modes used by Vibrant orchestration logic."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL_ACCESS = "full_access"

    @property
    def codex_thread_sandbox(self) -> str:
        """Return the Codex thread sandbox string for this runtime mode."""

        return {
            RuntimeMode.READ_ONLY: "read-only",
            RuntimeMode.WORKSPACE_WRITE: "workspace-write",
            RuntimeMode.FULL_ACCESS: "danger-full-access",
        }[self]

    @property
    def codex_turn_sandbox_policy(self) -> dict[str, str]:
        """Return the Codex ``turn/start`` sandbox policy object."""

        return {
            RuntimeMode.READ_ONLY: {"type": "readOnly"},
            RuntimeMode.WORKSPACE_WRITE: {"type": "workspaceWrite"},
            RuntimeMode.FULL_ACCESS: {"type": "dangerFullAccess"},
        }[self]


class ProviderAdapter(ABC):
    """Abstract provider adapter for session lifecycle and event delivery."""

    def __init__(self, on_canonical_event: CanonicalEventHandler | None = None) -> None:
        self._canonical_event_handler = on_canonical_event

    @property
    def canonical_event_handler(self) -> CanonicalEventHandler | None:
        """Optional callback invoked with normalized provider events."""

        return self._canonical_event_handler

    @canonical_event_handler.setter
    def canonical_event_handler(self, handler: CanonicalEventHandler | None) -> None:
        self._canonical_event_handler = handler

    @abstractmethod
    async def start_session(self, *, cwd: str | None = None, **kwargs: Any) -> Any:
        """Start the underlying provider session/process."""

    @abstractmethod
    async def stop_session(self) -> Any:
        """Stop the underlying provider session/process."""

    @abstractmethod
    async def start_thread(self, **kwargs: Any) -> Any:
        """Open a fresh provider thread/conversation handle."""

    @abstractmethod
    async def resume_thread(self, provider_thread_id: str, **kwargs: Any) -> Any:
        """Resume an existing provider thread using durable provider metadata."""

    @abstractmethod
    async def start_turn(
        self,
        *,
        input_items: Sequence[Mapping[str, Any]],
        runtime_mode: RuntimeMode,
        approval_policy: str,
        **kwargs: Any,
    ) -> Any:
        """Start a turn with structured input items and sandbox controls."""

    @abstractmethod
    async def interrupt_turn(self, **kwargs: Any) -> Any:
        """Interrupt the currently running turn, if any."""

    @abstractmethod
    async def respond_to_request(
        self,
        request_id: int | str,
        *,
        result: Any | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> Any:
        """Respond to a server-initiated JSON-RPC request."""

    @abstractmethod
    async def on_canonical_event(self, event: CanonicalEvent) -> None:
        """Handle a normalized canonical event emitted by the provider."""
