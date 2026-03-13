"""Configuration models and loader utilities for Vibrant."""

from __future__ import annotations

import enum
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, model_validator

from .providers.base import ProviderKind


DEFAULT_CONFIG_DIR = ".vibrant"
DEFAULT_CONFIG_FILE = "vibrant.toml"
DEFAULT_CONFIG_RELATIVE_PATH = Path(DEFAULT_CONFIG_DIR) / DEFAULT_CONFIG_FILE
DEFAULT_CONVERSATION_DIRECTORY = Path(DEFAULT_CONFIG_DIR) / "conversations"
DEFAULT_WORKTREE_DIRECTORY = "/tmp/vibrant-worktrees"


class VibrantConfigError(ValueError):
    """Raised when ``vibrant.toml`` cannot be parsed or validated."""


class RoadmapExecutionMode(str, enum.Enum):
    """Execution strategy for roadmap task dispatch."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"


class VibrantConfig(BaseModel):
    """Project-level runtime configuration loaded from ``vibrant.toml``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    provider_kind: ProviderKind = Field(
        default=ProviderKind.CODEX,
        validation_alias=AliasChoices("provider_kind", "provider-kind", "kind"),
        serialization_alias="kind",
    )
    codex_binary: str = Field(
        default="codex",
        validation_alias=AliasChoices("codex_binary", "codex-binary", "codex-binary-path"),
        serialization_alias="codex-binary",
    )
    launch_args: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("launch_args", "launch-args"),
        serialization_alias="launch-args",
    )
    codex_home: str | None = Field(
        default=None,
        validation_alias=AliasChoices("codex_home", "codex-home", "CODEX_HOME"),
        serialization_alias="codex-home",
    )
    mock_responses: bool = Field(
        default=False,
        validation_alias=AliasChoices("mock_responses", "mock-responses"),
        serialization_alias="mock-responses",
    )
    claude_cli_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("claude_cli_path", "claude-cli-path"),
        serialization_alias="claude-cli-path",
    )
    claude_settings: str | None = Field(
        default=None,
        validation_alias=AliasChoices("claude_settings", "claude-settings"),
        serialization_alias="claude-settings",
    )
    claude_add_dirs: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("claude_add_dirs", "claude-add-dirs"),
        serialization_alias="claude-add-dirs",
    )
    claude_allowed_tools: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("claude_allowed_tools", "claude-allowed-tools"),
        serialization_alias="claude-allowed-tools",
    )
    claude_disallowed_tools: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("claude_disallowed_tools", "claude-disallowed-tools"),
        serialization_alias="claude-disallowed-tools",
    )
    claude_fallback_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("claude_fallback_model", "claude-fallback-model"),
        serialization_alias="claude-fallback-model",
    )
    claude_setting_sources: list[str] = Field(
        default_factory=lambda: ["user", "project", "local"],
        validation_alias=AliasChoices("claude_setting_sources", "claude-setting-sources"),
        serialization_alias="claude-setting-sources",
    )
    model: str = "gpt-5.3-codex"
    # Leave unset to preserve Codex's default provider selection from ~/.codex/config.toml.
    model_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices("model_provider", "model-provider"),
        serialization_alias="model-provider",
    )
    approval_policy: str = Field(
        default="never",
        validation_alias=AliasChoices("approval_policy", "approval-policy"),
        serialization_alias="approval-policy",
    )
    reasoning_effort: str = Field(
        default="medium",
        validation_alias=AliasChoices("reasoning_effort", "reasoning-effort"),
        serialization_alias="reasoning-effort",
    )
    reasoning_summary: str = Field(
        default="auto",
        validation_alias=AliasChoices("reasoning_summary", "reasoning-summary"),
        serialization_alias="reasoning-summary",
    )
    sandbox_mode: str = Field(
        default="workspace-write",
        validation_alias=AliasChoices("sandbox_mode", "sandbox-mode"),
        serialization_alias="sandbox-mode",
    )
    turn_sandbox_policy: str | None = Field(
        default=None,
        validation_alias=AliasChoices("turn_sandbox_policy", "turn-sandbox-policy"),
        serialization_alias="turn-sandbox-policy",
    )
    concurrency_limit: int = Field(
        default=4,
        validation_alias=AliasChoices("concurrency_limit", "concurrency-limit"),
        serialization_alias="concurrency-limit",
    )
    agent_timeout_seconds: int = Field(
        default=25 * 60,
        validation_alias=AliasChoices(
            "agent_timeout_seconds",
            "agent-timeout-seconds",
            "agent-timeout",
        ),
        serialization_alias="agent-timeout-seconds",
    )
    worktree_directory: str = Field(
        default=DEFAULT_WORKTREE_DIRECTORY,
        validation_alias=AliasChoices("worktree_directory", "worktree-directory"),
        serialization_alias="worktree-directory",
    )
    conversation_directory: str = Field(
        default=str(DEFAULT_CONVERSATION_DIRECTORY),
        validation_alias=AliasChoices(
            "conversation_directory",
            "conversation-directory",
            "conversation_history_directory",
            "conversation-history-directory",
        ),
        serialization_alias="conversation-directory",
    )
    execution_mode: RoadmapExecutionMode = Field(
        default=RoadmapExecutionMode.AUTOMATIC,
        validation_alias=AliasChoices(
            "execution_mode",
            "execution-mode",
            "roadmap_execution_mode",
            "roadmap-execution-mode",
        ),
        serialization_alias="execution-mode",
    )
    test_commands: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("test_commands", "test-commands"),
        serialization_alias="test-commands",
    )
    extra_config: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("extra_config", "extra-config"),
        serialization_alias="extra-config",
    )

    @model_validator(mode="before")
    @classmethod
    def flatten_sections(cls, data: Any) -> Any:
        """Allow config to be grouped into simple TOML sections."""
        if not isinstance(data, Mapping):
            return data

        merged: dict[str, Any] = {}
        for key, value in data.items():
            if key in {"provider", "runtime", "orchestrator", "validation"} and isinstance(value, Mapping):
                merged.update(value)
            else:
                merged[key] = value
        return merged

    def resolve_conversation_directory(self, project_root: str | Path) -> Path:
        """Resolve the configured conversation directory against the project root."""

        return resolve_project_path(self.conversation_directory, project_root=project_root)


def find_project_root(start_path: str | Path | None = None) -> Path:
    """Best-effort project-root discovery.

    Walk upward from ``start_path`` until a directory containing either
    ``.git`` or ``.vibrant`` is found. If neither marker exists, return the
    starting directory.
    """

    candidate = Path(start_path or Path.cwd()).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent

    for directory in (candidate, *candidate.parents):
        if (directory / ".git").exists() or (directory / DEFAULT_CONFIG_DIR).exists():
            return directory
    return candidate


def resolve_config_path(path: str | Path | None = None, start_path: str | Path | None = None) -> Path:
    """Resolve the config file path.

    - ``None`` → ``<project-root>/.vibrant/vibrant.toml``
    - ``/path/to/file.toml`` → that exact file
    - ``/path/to/project`` → ``/path/to/project/.vibrant/vibrant.toml``
    - ``/path/to/project/.vibrant`` → ``/path/to/project/.vibrant/vibrant.toml``
    """

    if path is None:
        return find_project_root(start_path) / DEFAULT_CONFIG_RELATIVE_PATH

    base_dir = find_project_root(start_path)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate

    if candidate.suffix == ".toml":
        return candidate.resolve()
    if candidate.name == DEFAULT_CONFIG_DIR:
        return (candidate / DEFAULT_CONFIG_FILE).resolve()
    return (candidate / DEFAULT_CONFIG_RELATIVE_PATH).resolve()


def resolve_project_path(path: str | Path, *, project_root: str | Path) -> Path:
    """Resolve a project-relative path against ``project_root``."""

    root = Path(project_root).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def _read_toml(config_path: Path) -> dict[str, Any]:
    try:
        with config_path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise VibrantConfigError(f"Invalid TOML in {config_path}: {exc}") from exc


def load_config(
    path: str | Path | None = None,
    *,
    start_path: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> VibrantConfig:
    """Load ``vibrant.toml`` and return validated configuration.

    If the file is missing, return a config object with defaults applied.
    """

    config_path = resolve_config_path(path=path, start_path=start_path)
    raw_data: dict[str, Any] = {}

    if config_path.exists():
        raw_data = _read_toml(config_path)

    if overrides:
        raw_data = {**raw_data, **dict(overrides)}

    try:
        return VibrantConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise VibrantConfigError(f"Invalid configuration in {config_path}: {exc}") from exc


__all__ = [
    "DEFAULT_CONVERSATION_DIRECTORY",
    "DEFAULT_CONFIG_RELATIVE_PATH",
    "DEFAULT_WORKTREE_DIRECTORY",
    "RoadmapExecutionMode",
    "VibrantConfig",
    "VibrantConfigError",
    "find_project_root",
    "load_config",
    "resolve_project_path",
    "resolve_config_path",
]
