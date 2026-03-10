"""Application and session settings models."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel

from ..config import DEFAULT_CONVERSATION_DIRECTORY


class ApprovalMode(str, enum.Enum):
    SUGGEST = "suggest"
    AUTO_EDIT = "auto-edit"
    FULL_AUTO = "full-auto"


class SessionConfig(BaseModel):
    """Configuration for a single Codex session."""

    model: str = "gpt-5.3-codex"
    approval_mode: ApprovalMode = ApprovalMode.FULL_AUTO
    cwd: str | None = None
    effort: str = "medium"
    codex_binary: str = "codex"


class AppSettings(BaseModel):
    """Global application settings."""

    default_model: str = "gpt-5.3-codex"
    default_approval_mode: ApprovalMode = ApprovalMode.FULL_AUTO
    default_cwd: str | None = None
    default_effort: str = "medium"
    codex_binary: str = "codex"
    history_dir: str = str(DEFAULT_CONVERSATION_DIRECTORY)

    def to_session_config(self, **overrides: Any) -> SessionConfig:
        base = {
            "model": self.default_model,
            "approval_mode": self.default_approval_mode,
            "cwd": self.default_cwd,
            "effort": self.default_effort,
            "codex_binary": self.codex_binary,
        }
        base.update(overrides)
        return SessionConfig(**base)
