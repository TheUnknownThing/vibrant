"""Unit tests for the Vibrant configuration loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from vibrant.config import (
    DEFAULT_CONVERSATION_DIRECTORY,
    DEFAULT_WORKTREE_DIRECTORY,
    RoadmapExecutionMode,
    VibrantConfigError,
    find_project_root,
    load_config,
    resolve_config_path,
)
from vibrant.providers.base import ProviderKind


class TestLoadConfig:
    def test_parse_sample_vibrant_toml(self, tmp_path):
        project_root = tmp_path / "project"
        config_dir = project_root / ".vibrant"
        config_dir.mkdir(parents=True)
        (project_root / ".git").mkdir()
        (config_dir / "vibrant.toml").write_text(
            textwrap.dedent(
                """
                [provider]
                kind = "codex"
                codex-binary = "/opt/codex/bin/codex"
                launch-args = ["app-server", "--verbose"]
                codex-home = "/tmp/codex-home"
                model = "gpt-5.3-codex-spark"
                model-provider = "openai"
                approval-policy = "on-request"
                reasoning-effort = "high"
                reasoning-summary = "detailed"
                sandbox-mode = "danger-full-access"
                turn-sandbox-policy = "dangerFullAccess"
                extra-config = { persistExtendedHistory = true }

                [orchestrator]
                concurrency-limit = 8
                agent-timeout-seconds = 2700
                worktree-directory = "/var/tmp/vibrant-worktrees"
                conversation-directory = ".vibrant/session-history"
                execution-mode = "manual"

                [validation]
                test-commands = ["pytest -q", "ruff check ."]
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        config = load_config(start_path=project_root / "src")

        assert config.provider_kind is ProviderKind.CODEX
        assert config.codex_binary == "/opt/codex/bin/codex"
        assert config.launch_args == ["app-server", "--verbose"]
        assert config.codex_home == "/tmp/codex-home"
        assert config.model == "gpt-5.3-codex-spark"
        assert config.model_provider == "openai"
        assert config.approval_policy == "on-request"
        assert config.reasoning_effort == "high"
        assert config.reasoning_summary == "detailed"
        assert config.sandbox_mode == "danger-full-access"
        assert config.turn_sandbox_policy == "dangerFullAccess"
        assert config.concurrency_limit == 8
        assert config.agent_timeout_seconds == 2700
        assert config.worktree_directory == "/var/tmp/vibrant-worktrees"
        assert config.conversation_directory == ".vibrant/session-history"
        assert config.resolve_conversation_directory(project_root) == project_root / ".vibrant" / "session-history"
        assert config.execution_mode is RoadmapExecutionMode.MANUAL
        assert config.test_commands == ["pytest -q", "ruff check ."]
        assert config.extra_config == {"persistExtendedHistory": True}

    def test_missing_file_uses_defaults(self, tmp_path):
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()

        config = load_config(start_path=project_root)

        assert config.provider_kind is ProviderKind.CODEX
        assert config.codex_binary == "codex"
        assert config.model == "gpt-5.3-codex"
        assert config.model_provider is None
        assert config.approval_policy == "never"
        assert config.reasoning_effort == "medium"
        assert config.reasoning_summary == "auto"
        assert config.sandbox_mode == "workspace-write"
        assert config.concurrency_limit == 4
        assert config.agent_timeout_seconds == 1500
        assert config.worktree_directory == DEFAULT_WORKTREE_DIRECTORY
        assert config.conversation_directory == str(DEFAULT_CONVERSATION_DIRECTORY)
        assert config.resolve_conversation_directory(project_root) == project_root / ".vibrant" / "conversations"
        assert config.execution_mode is RoadmapExecutionMode.AUTOMATIC
        assert config.test_commands == []

    def test_parse_claude_provider_configuration(self, tmp_path):
        project_root = tmp_path / "project"
        config_dir = project_root / ".vibrant"
        config_dir.mkdir(parents=True)
        (project_root / ".git").mkdir()
        (config_dir / "vibrant.toml").write_text(
            textwrap.dedent(
                """
                [provider]
                kind = "claude"
                claude-cli-path = "/opt/claude/bin/claude"
                claude-settings = "/tmp/claude-settings.json"
                claude-add-dirs = ["../shared", "./fixtures"]
                claude-allowed-tools = ["Read", "WebFetch"]
                claude-disallowed-tools = ["WebSearch"]
                claude-fallback-model = "claude-haiku-4-5"
                claude-setting-sources = ["project", "local"]
                model = "claude-sonnet-4-5"
                approval-policy = "never"
                sandbox-mode = "workspace-write"
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        config = load_config(start_path=project_root)

        assert config.provider_kind is ProviderKind.CLAUDE
        assert config.model == "claude-sonnet-4-5"
        assert config.claude_cli_path == "/opt/claude/bin/claude"
        assert config.claude_settings == "/tmp/claude-settings.json"
        assert config.claude_add_dirs == ["../shared", "./fixtures"]
        assert config.claude_allowed_tools == ["Read", "WebFetch"]
        assert config.claude_disallowed_tools == ["WebSearch"]
        assert config.claude_fallback_model == "claude-haiku-4-5"
        assert config.claude_setting_sources == ["project", "local"]

    def test_invalid_toml_raises_clear_error(self, tmp_path):
        project_root = tmp_path / "project"
        config_dir = project_root / ".vibrant"
        config_dir.mkdir(parents=True)
        (project_root / ".git").mkdir()
        config_path = config_dir / "vibrant.toml"
        config_path.write_text('model = "gpt-5.3-codex"\nnot valid toml\n', encoding="utf-8")

        with pytest.raises(VibrantConfigError, match=r"Invalid TOML in .*vibrant\.toml"):
            load_config(start_path=project_root)


class TestConfigPathResolution:
    def test_find_project_root_walks_up_from_nested_directory(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        nested_dir = project_root / "src" / "package"
        (project_root / ".vibrant").mkdir(parents=True)
        nested_dir.mkdir(parents=True)

        assert find_project_root(nested_dir) == project_root

    def test_resolve_config_path_uses_discovered_project_root(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        nested_dir = project_root / "src"
        (project_root / ".vibrant").mkdir(parents=True)
        nested_dir.mkdir(parents=True)

        assert resolve_config_path(nested_dir) == project_root / ".vibrant" / "vibrant.toml"
