"""Configuration models and loader utilities for Vibrant.

Phase 0 Task 0.1 establishes the module and default schema. A fuller
``vibrant.toml`` loader is added in Task 0.2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class VibrantConfig(BaseModel):
    """Default runtime configuration for a Vibrant session."""

    codex_binary: str = "codex"
    model: str = "gpt-5.3-codex"
    model_provider: str = "openai"
    approval_policy: str = "never"
    reasoning_effort: str = "medium"
    reasoning_summary: str = "auto"
    sandbox_mode: str = "workspace-write"
    concurrency_limit: int = 4
    agent_timeout_seconds: int = 1800
    worktree_directory: str = ".vibrant/worktrees"
    test_commands: list[str] = Field(default_factory=list)


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> VibrantConfig:
    """Return a config object using defaults plus optional overrides."""

    data: dict[str, Any] = {}
    if overrides:
        data.update(overrides)
    if path:
        data.setdefault("config_path", str(Path(path)))
    data.pop("config_path", None)
    return VibrantConfig.model_validate(data)

