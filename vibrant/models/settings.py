"""Application and session settings models."""

from __future__ import annotations

import enum

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

    def to_session_config(
        self,
        *,
        model: str | None = None,
        approval_mode: ApprovalMode | None = None,
        cwd: str | None = None,
        effort: str | None = None,
        codex_binary: str | None = None,
    ) -> SessionConfig:
        return SessionConfig(
            model=model or self.default_model,
            approval_mode=approval_mode or self.default_approval_mode,
            cwd=cwd if cwd is not None else self.default_cwd,
            effort=effort or self.default_effort,
            codex_binary=codex_binary or self.codex_binary,
        )
