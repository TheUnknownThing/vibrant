"""Provider-neutral adapter interfaces used by Vibrant.

Task 2.1 defines the abstract session/thread/turn control surface that any
provider integration must implement. Vibrant's internal runtime modes map to
Codex sandbox controls as follows:

- ``read_only`` -> thread sandbox ``read-only`` and turn policy ``readOnly``
- ``workspace_write`` -> thread sandbox ``workspace-write`` and turn policy ``workspaceWrite``
- ``full_access`` -> thread sandbox ``danger-full-access`` and turn policy ``dangerFullAccess``
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, NotRequired, Required, TypeAlias, TypedDict

CanonicalEventOrigin: TypeAlias = Literal["provider", "orchestrator"]
CanonicalRequestKind: TypeAlias = Literal["request", "approval", "user-input"]
CanonicalKnownEventType: TypeAlias = Literal[
    "session.started",
    "session.state.changed",
    "thread.started",
    "turn.started",
    "content.delta",
    "reasoning.summary.delta",
    "request.opened",
    "request.resolved",
    "user-input.requested",
    "user-input.resolved",
    "task.progress",
    "task.completed",
    "turn.completed",
    "runtime.error",
]
CanonicalPayload: TypeAlias = Mapping[str, Any]


class CanonicalEventEnvelope(TypedDict, total=False):
    """Stable envelope shared by all canonical events.

    Canonical events remain ordinary ``dict`` objects at runtime, but this
    envelope defines the reserved keys that every backend must target.
    Unknown event-specific fields are allowed via the generic fallback event
    below so new providers can evolve without breaking older consumers.
    """

    type: Required[str]
    timestamp: Required[str]
    origin: NotRequired[CanonicalEventOrigin]
    provider: NotRequired[str]
    agent_id: NotRequired[str]
    task_id: NotRequired[str]
    provider_thread_id: NotRequired[str]
    provider_payload: NotRequired[CanonicalPayload]


class SessionStartedEvent(CanonicalEventEnvelope):
    type: Required[Literal["session.started"]]
    cwd: NotRequired[str]


class SessionStateChangedEvent(CanonicalEventEnvelope):
    type: Required[Literal["session.state.changed"]]
    state: NotRequired[str]


class ThreadStartedEvent(CanonicalEventEnvelope):
    type: Required[Literal["thread.started"]]
    resumed: NotRequired[bool]
    thread: NotRequired[CanonicalPayload]
    thread_path: NotRequired[str]


class TurnStartedEvent(CanonicalEventEnvelope):
    type: Required[Literal["turn.started"]]
    turn_id: NotRequired[str]
    turn_status: NotRequired[str]
    turn: NotRequired[CanonicalPayload]


class ContentDeltaEvent(CanonicalEventEnvelope):
    type: Required[Literal["content.delta"]]
    item_id: Required[str]
    delta: Required[str]
    turn_id: NotRequired[str]


class ReasoningSummaryDeltaEvent(CanonicalEventEnvelope):
    type: Required[Literal["reasoning.summary.delta"]]
    item_id: Required[str]
    delta: Required[str]
    turn_id: NotRequired[str]
    summary_index: NotRequired[int]


class RequestOpenedEvent(CanonicalEventEnvelope):
    type: Required[Literal["request.opened"]]
    request_id: Required[int | str]
    request_kind: Required[CanonicalRequestKind]
    method: Required[str]
    params: NotRequired[CanonicalPayload]


class RequestResolvedEvent(CanonicalEventEnvelope):
    type: Required[Literal["request.resolved"]]
    request_id: Required[int | str]
    request_kind: Required[CanonicalRequestKind]
    method: Required[str]
    result: NotRequired[Any]
    error: NotRequired[Any]
    error_message: NotRequired[str]


class UserInputRequestedEvent(CanonicalEventEnvelope):
    type: Required[Literal["user-input.requested"]]
    request_id: NotRequired[int | str]
    method: NotRequired[str]
    params: NotRequired[CanonicalPayload]
    questions: NotRequired[list[str]]
    banner_text: NotRequired[str]
    terminal_bell: NotRequired[bool]


class UserInputResolvedEvent(CanonicalEventEnvelope):
    type: Required[Literal["user-input.resolved"]]
    request_id: NotRequired[int | str]
    method: NotRequired[str]
    result: NotRequired[Any]
    error: NotRequired[Any]
    error_message: NotRequired[str]
    question: NotRequired[str]
    answer: NotRequired[str]


class TaskProgressEvent(CanonicalEventEnvelope):
    type: Required[Literal["task.progress"]]
    item: Required[Any]
    turn_id: NotRequired[str]
    item_type: NotRequired[str]
    text: NotRequired[str]


class TaskCompletedEvent(CanonicalEventEnvelope):
    type: Required[Literal["task.completed"]]
    turn_id: NotRequired[str]
    turn_status: NotRequired[str]
    turn: NotRequired[CanonicalPayload]


class TurnCompletedEvent(CanonicalEventEnvelope):
    type: Required[Literal["turn.completed"]]
    turn_id: NotRequired[str]
    turn_status: NotRequired[str]
    turn: NotRequired[CanonicalPayload]


class RuntimeErrorEvent(CanonicalEventEnvelope):
    type: Required[Literal["runtime.error"]]
    error: NotRequired[Any]
    error_message: NotRequired[str]
    error_code: NotRequired[int | str]


class GenericCanonicalEvent(CanonicalEventEnvelope):
    """Forward-compatible fallback for new canonical event types."""

    type: Required[str]


CanonicalEvent: TypeAlias = (
    SessionStartedEvent
    | SessionStateChangedEvent
    | ThreadStartedEvent
    | TurnStartedEvent
    | ContentDeltaEvent
    | ReasoningSummaryDeltaEvent
    | RequestOpenedEvent
    | RequestResolvedEvent
    | UserInputRequestedEvent
    | UserInputResolvedEvent
    | TaskProgressEvent
    | TaskCompletedEvent
    | TurnCompletedEvent
    | RuntimeErrorEvent
    | GenericCanonicalEvent
)
CanonicalEventHandler: TypeAlias = Callable[[CanonicalEvent], Any]


class CodexAuthMode(str, enum.Enum):
    """Authentication strategies for Codex app-server sessions.

    - ``SYSTEM``: rely on the user's existing Codex auth state on disk.
    - ``API_KEY``: login with an OpenAI API key via ``account/login/start``.
    - ``CHATGPT``: managed ChatGPT browser login via ``account/login/start``.
    - ``CHATGPT_AUTH_TOKENS``: host-provided ChatGPT tokens via ``account/login/start``.
    """

    SYSTEM = "system"
    API_KEY = "apiKey"
    CHATGPT = "chatgpt"
    CHATGPT_AUTH_TOKENS = "chatgptAuthTokens"


class ProviderKind(str, enum.Enum):
    """Supported provider backends."""

    CODEX = "codex"
    CLAUDE = "claude"


@dataclass(slots=True)
class CodexAuthConfig:
    """Optional authentication configuration for a Codex session.

    When ``mode`` is ``SYSTEM``, Vibrant does not call login RPCs and Codex
    uses its default persisted configuration (typically under ``CODEX_HOME``).
    """

    mode: CodexAuthMode = CodexAuthMode.SYSTEM
    api_key: str | None = None
    id_token: str | None = None
    access_token: str | None = None

    def to_login_params(self) -> dict[str, Any] | None:
        """Return ``account/login/start`` params for this configuration."""

        if self.mode is CodexAuthMode.SYSTEM:
            return None

        params: dict[str, Any] = {"type": self.mode.value}
        if self.mode is CodexAuthMode.API_KEY:
            if not self.api_key:
                raise ValueError("CodexAuthConfig.api_key is required for API_KEY auth mode")
            params["apiKey"] = self.api_key
        elif self.mode is CodexAuthMode.CHATGPT_AUTH_TOKENS:
            if not self.id_token or not self.access_token:
                raise ValueError("CodexAuthConfig.id_token and access_token are required for CHATGPT_AUTH_TOKENS")
            params["idToken"] = self.id_token
            params["accessToken"] = self.access_token
        return params


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
    async def send_request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Send a provider-native control-plane request.

        This surface is used for Codex management endpoints such as
        ``skills/list``, ``config/mcpServer/reload``, and ``account/read``.
        """

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
