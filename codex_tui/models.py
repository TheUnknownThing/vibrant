"""Data models for the Codex TUI.

Defines Pydantic models for the JSON-RPC wire protocol and
higher-level domain objects (threads, turns, items, settings).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# JSON-RPC wire protocol
# ---------------------------------------------------------------------------

class JsonRpcRequest(BaseModel):
    """Outgoing JSON-RPC request (client → codex app-server)."""
    id: int | str
    method: str
    params: dict[str, Any] | None = None

    def to_line(self) -> str:
        """Serialize to a single JSONL line (no trailing newline)."""
        # Codex uses "JSON-RPC lite" — omits the jsonrpc field
        return self.model_dump_json(exclude_none=True)


class JsonRpcResponse(BaseModel):
    """Incoming JSON-RPC response (codex app-server → client)."""
    id: int | str
    result: Any | None = None
    error: dict[str, Any] | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @property
    def error_message(self) -> str:
        if self.error:
            return self.error.get("message", "Unknown error")
        return ""


class JsonRpcNotification(BaseModel):
    """Server-initiated notification (no id → no response expected)."""
    method: str
    params: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Item types (content within a turn)
# ---------------------------------------------------------------------------

class ItemType(str, enum.Enum):
    TEXT = "text"
    CODE = "code"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FILE_CHANGE = "file_change"
    FILE_READ = "file_read"
    COMMAND = "command"
    APPROVAL_REQUEST = "approval_request"
    USER_INPUT_REQUEST = "user_input_request"
    UNKNOWN = "unknown"


class ItemInfo(BaseModel):
    """A single content item within a turn."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: ItemType = ItemType.UNKNOWN
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Turn (a unit of work: user prompt → agent response)
# ---------------------------------------------------------------------------

class TurnStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class TurnRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class TurnInfo(BaseModel):
    """A turn within a thread (one user prompt + agent response cycle)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: TurnRole = TurnRole.USER
    items: list[ItemInfo] = Field(default_factory=list)
    status: TurnStatus = TurnStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Thread (a conversation container)
# ---------------------------------------------------------------------------

class ThreadStatus(str, enum.Enum):
    ACTIVE = "active"       # codex app-server running, ready
    RUNNING = "running"     # turn in progress
    IDLE = "idle"           # session connected but no work happening
    ERROR = "error"
    STOPPED = "stopped"     # session closed


class ThreadInfo(BaseModel):
    """Top-level thread metadata."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codex_thread_id: str | None = None  # ID returned by codex app-server
    title: str = "New Thread"
    status: ThreadStatus = ThreadStatus.STOPPED
    model: str | None = None
    cwd: str | None = None
    turns: list[TurnInfo] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: str | None = None

    @property
    def message_count(self) -> int:
        return len(self.turns)

    @property
    def display_title(self) -> str:
        """Derive a title from the first user message if title is default."""
        if self.title != "New Thread":
            return self.title
        for turn in self.turns:
            if turn.role == TurnRole.USER:
                for item in turn.items:
                    if item.type == ItemType.TEXT and item.content:
                        text = item.content.strip()
                        return text[:60] + "…" if len(text) > 60 else text
        return self.title


# ---------------------------------------------------------------------------
# Session configuration
# ---------------------------------------------------------------------------

class ApprovalMode(str, enum.Enum):
    SUGGEST = "suggest"          # ask for every action
    AUTO_EDIT = "auto-edit"      # auto-approve file edits
    FULL_AUTO = "full-auto"      # approve everything


class SessionConfig(BaseModel):
    """Configuration for a single Codex session."""
    model: str = "gpt-5.3-codex"
    approval_mode: ApprovalMode = ApprovalMode.FULL_AUTO
    cwd: str | None = None
    effort: str = "medium"
    codex_binary: str = "codex"


# ---------------------------------------------------------------------------
# App-level settings
# ---------------------------------------------------------------------------

class AppSettings(BaseModel):
    """Global application settings (persisted to disk)."""
    default_model: str = "gpt-5.3-codex"
    default_approval_mode: ApprovalMode = ApprovalMode.FULL_AUTO
    default_cwd: str | None = None
    default_effort: str = "medium"
    codex_binary: str = "codex"
    history_dir: str = "~/.codex-tui/history"

    def to_session_config(self, **overrides: Any) -> SessionConfig:
        """Create a SessionConfig from the current app settings."""
        base = {
            "model": self.default_model,
            "approval_mode": self.default_approval_mode,
            "cwd": self.default_cwd,
            "effort": self.default_effort,
            "codex_binary": self.codex_binary,
        }
        base.update(overrides)
        return SessionConfig(**base)
