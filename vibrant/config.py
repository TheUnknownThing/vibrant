"""Configuration models and loader utilities for Vibrant."""

from __future__ import annotations

import enum
import json
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from .providers.base import ProviderKind
from .type_defs import JSONMapping, JSONObject, JSONValue, is_json_object

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


class GatekeeperRole(str, enum.Enum):
    """Role persona for Gatekeeper system-prompt behavior."""

    BUILDER = "builder"
    MAINTAINER = "maintainer"


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
        validation_alias=AliasChoices(
            "codex_binary", "codex-binary", "codex-binary-path"
        ),
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
        validation_alias=AliasChoices(
            "claude_disallowed_tools", "claude-disallowed-tools"
        ),
        serialization_alias="claude-disallowed-tools",
    )
    claude_fallback_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("claude_fallback_model", "claude-fallback-model"),
        serialization_alias="claude-fallback-model",
    )
    claude_setting_sources: list[str] = Field(
        default_factory=lambda: ["user", "project", "local"],
        validation_alias=AliasChoices(
            "claude_setting_sources", "claude-setting-sources"
        ),
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
    gatekeeper_role: GatekeeperRole = Field(
        default=GatekeeperRole.BUILDER,
        validation_alias=AliasChoices("gatekeeper_role", "gatekeeper-role"),
        serialization_alias="gatekeeper-role",
    )
    show_agent_logs: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("show_agent_logs", "show-agent-logs"),
        serialization_alias="show-agent-logs",
    )
    extra_config: dict[str, JsonValue] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("extra_config", "extra-config"),
        serialization_alias="extra-config",
    )

    @model_validator(mode="before")
    @classmethod
    def flatten_sections(cls, data: object) -> object:
        """Allow config to be grouped into simple TOML sections."""
        if not isinstance(data, Mapping):
            return data

        merged: JSONObject = {}
        for key, value in data.items():
            if key in {
                "provider",
                "runtime",
                "orchestrator",
                "validation",
                "ui",
            } and isinstance(value, Mapping):
                merged.update(value)
            else:
                if not isinstance(value, (str, int, float, bool, list, dict)) and value is not None:
                    raise ValueError(f"Unsupported config value type for key {key!r}: {type(value)!r}")
                merged[key] = value
        return merged

    def tui_agent_logs_visible(self, *, dev_mode: bool) -> bool:
        """Return whether the agent logs tab should be visible in the TUI."""

        if self.show_agent_logs is not None:
            return self.show_agent_logs
        return dev_mode

    def resolve_conversation_directory(self, project_root: Path) -> Path:
        """Resolve the configured conversation directory against the project root."""

        return resolve_project_path(
            self.conversation_directory, project_root=project_root
        )


class VibrantConfigPatch(BaseModel):
    """Typed patch for orchestrator-owned runtime config fields."""

    model: str | None = None
    approval_policy: str | None = None
    reasoning_effort: str | None = None

    def has_changes(self) -> bool:
        """Return whether the patch contains at least one non-empty update."""

        return any(
            value is not None
            for value in (
                self.model,
                self.approval_policy,
                self.reasoning_effort,
            )
        )


_PROVIDER_SECTION_KEYS = (
    "kind",
    "codex-binary",
    "launch-args",
    "codex-home",
    "mock-responses",
    "claude-cli-path",
    "claude-settings",
    "claude-add-dirs",
    "claude-allowed-tools",
    "claude-disallowed-tools",
    "claude-fallback-model",
    "claude-setting-sources",
    "model",
    "model-provider",
    "approval-policy",
    "reasoning-effort",
    "reasoning-summary",
    "sandbox-mode",
    "turn-sandbox-policy",
    "extra-config",
)
_ORCHESTRATOR_SECTION_KEYS = (
    "concurrency-limit",
    "agent-timeout-seconds",
    "worktree-directory",
    "conversation-directory",
    "execution-mode",
    "gatekeeper-role",
)
_BARE_TOML_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _resolve_start_directory(start_path: Path | None) -> Path:
    """Return the normalized directory used for project-root discovery."""

    start = (start_path or Path.cwd()).expanduser().resolve()
    if start.exists() and start.is_file():
        return start.parent
    return start


def find_project_root(start_path: Path | None = None) -> Path:
    """Best-effort project-root discovery.

    Walk upward from ``start_path`` until a directory containing `.vibrant` is found.
    """

    start = _resolve_start_directory(start_path)
    for parent in [start] + list(start.parents):
        if (parent / DEFAULT_CONFIG_DIR).is_dir():
            return parent
    return start


def resolve_config_path(start_path: Path | None = None) -> Path:
    """Return the canonical ``vibrant.toml`` path for the discovered project root."""

    return find_project_root(start_path) / DEFAULT_CONFIG_RELATIVE_PATH


def resolve_project_path(path: str | Path, *, project_root: Path) -> Path:
    """Resolve a project-relative path against ``project_root``."""

    root = project_root.expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def _read_toml(config_path: Path) -> JSONObject:
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
            if not is_json_object(payload):
                raise VibrantConfigError(f"Invalid TOML structure in {config_path}: expected a table at the top level")
            return payload
    except tomllib.TOMLDecodeError as exc:
        raise VibrantConfigError(f"Invalid TOML in {config_path}: {exc}") from exc


def load_config(
    start_path: Path | None = None,
    overrides: JSONMapping | None = None,
) -> VibrantConfig:
    """Load ``vibrant.toml`` and return validated configuration.

    If the file is missing, return a config object with defaults applied.
    """

    config_path = resolve_config_path(start_path=start_path)
    raw_data: JSONObject = {}

    if config_path.exists():
        raw_data = _read_toml(config_path)

    if overrides:
        raw_data = {**raw_data, **dict(overrides)}

    try:
        return VibrantConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise VibrantConfigError(
            f"Invalid configuration in {config_path}: {exc}"
        ) from exc


def save_config(
    config: VibrantConfig,
    *,
    start_path: Path | None = None,
) -> Path:
    """Persist a validated config to ``.vibrant/vibrant.toml``."""

    config_path = resolve_config_path(start_path=start_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_render_config_toml(config), encoding="utf-8")
    return config_path


def update_config(
    *,
    start_path: Path | None = None,
    patch: VibrantConfigPatch,
) -> VibrantConfig:
    """Apply a typed patch to the project config and persist the result."""

    if not patch.has_changes():
        return load_config(start_path=start_path)

    current = load_config(start_path=start_path)
    updated = current.model_copy(update=patch.model_dump(exclude_none=True))
    save_config(updated, start_path=start_path)
    return updated


def _render_config_toml(config: VibrantConfig) -> str:
    payload = config.model_dump(mode="json", by_alias=True, exclude_none=True)
    rendered_sections = [
        _render_toml_section("provider", _PROVIDER_SECTION_KEYS, payload),
        _render_toml_section("orchestrator", _ORCHESTRATOR_SECTION_KEYS, payload),
    ]
    return "\n\n".join(section for section in rendered_sections if section).rstrip() + "\n"


def _render_toml_section(
    name: str,
    ordered_keys: tuple[str, ...],
    payload: dict[str, JSONValue],
) -> str:
    lines = [f"[{name}]"]
    for key in ordered_keys:
        if key not in payload:
            continue
        lines.append(f"{_format_toml_key(key)} = {_format_toml_value(payload[key])}")
    return "\n".join(lines)


def _format_toml_key(key: str) -> str:
    if _BARE_TOML_KEY_PATTERN.match(key):
        return key
    return json.dumps(key)


def _format_toml_value(value: JsonValue) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        rendered_items = ", ".join(
            f"{_format_toml_key(str(key))} = {_format_toml_value(item)}"
            for key, item in value.items()
        )
        return "{ " + rendered_items + " }"
    raise TypeError(f"Unsupported TOML value: {value!r}")


__all__ = [
    "DEFAULT_CONVERSATION_DIRECTORY",
    "DEFAULT_CONFIG_RELATIVE_PATH",
    "DEFAULT_WORKTREE_DIRECTORY",
    "GatekeeperRole",
    "RoadmapExecutionMode",
    "VibrantConfig",
    "VibrantConfigPatch",
    "VibrantConfigError",
    "find_project_root",
    "load_config",
    "save_config",
    "resolve_project_path",
    "resolve_config_path",
    "update_config",
]
