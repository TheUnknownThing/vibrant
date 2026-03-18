"""Shared data models for Vibrant."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, Field, JsonValue

from .agent import (
    AgentInstanceProviderConfig,
    AgentInstanceRecord,
    AgentInstanceScope,
    AgentProviderMetadata,
    AgentRecord,
    AgentRunRecord,
    AgentStatus,
    AgentType,
    ProviderResumeHandle,
)
from .consensus import (
    ConsensusDocument,
    ConsensusPool,
    ConsensusStatus,
)
from .settings import AppSettings, ApprovalMode, SessionConfig
from .task import TaskInfo, TaskLifecycle, TaskStatus
from .wire import JsonRpcNotification, JsonRpcRequest, JsonRpcResponse


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
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
    """A turn within a thread."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: TurnRole = TurnRole.USER
    items: list[ItemInfo] = Field(default_factory=list)
    status: TurnStatus = TurnStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ThreadStatus(str, enum.Enum):
    ACTIVE = "active"
    RUNNING = "running"
    IDLE = "idle"
    ERROR = "error"
    STOPPED = "stopped"


class ThreadInfo(BaseModel):
    """Top-level thread metadata."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codex_thread_id: str | None = None
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
        if self.title != "New Thread":
            return self.title
        for turn in self.turns:
            if turn.role == TurnRole.USER:
                for item in turn.items:
                    if item.type == ItemType.TEXT and item.content:
                        text = item.content.strip()
                        return text[:60] + "…" if len(text) > 60 else text
        return self.title


__all__ = [
    "AgentInstanceProviderConfig",
    "AgentInstanceRecord",
    "AgentInstanceScope",
    "AgentProviderMetadata",
    "AgentRecord",
    "AgentRunRecord",
    "AgentStatus",
    "AgentType",
    "AppSettings",
    "ApprovalMode",
    "ConsensusDocument",
    "ConsensusPool",
    "ConsensusStatus",
    "ItemInfo",
    "ItemType",
    "JsonRpcNotification",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "ProviderResumeHandle",
    "SessionConfig",
    "TaskInfo",
    "TaskLifecycle",
    "TaskStatus",
    "ThreadInfo",
    "ThreadStatus",
    "TurnInfo",
    "TurnRole",
    "TurnStatus",
]
