"""Unit tests for the Vibrant configuration loader."""

from __future__ import annotations

import textwrap

import pytest

from vibrant.config import (
    DEFAULT_WORKTREE_DIRECTORY,
    VibrantConfigError,
    load_config,
    resolve_config_path,
)


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

                [validation]
                test-commands = ["pytest -q", "ruff check ."]
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        config = load_config(start_path=project_root / "src")

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
        assert config.test_commands == ["pytest -q", "ruff check ."]
        assert config.extra_config == {"persistExtendedHistory": True}

    def test_missing_file_uses_defaults(self, tmp_path):
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()

        config = load_config(start_path=project_root)

        assert config.codex_binary == "codex"
        assert config.model == "gpt-5.3-codex"
        assert config.model_provider == "openai"
        assert config.approval_policy == "never"
        assert config.reasoning_effort == "medium"
        assert config.reasoning_summary == "auto"
        assert config.sandbox_mode == "workspace-write"
        assert config.concurrency_limit == 4
        assert config.agent_timeout_seconds == 1500
        assert config.worktree_directory == DEFAULT_WORKTREE_DIRECTORY
        assert config.test_commands == []

    def test_invalid_toml_raises_clear_error(self, tmp_path):
        project_root = tmp_path / "project"
        config_dir = project_root / ".vibrant"
        config_dir.mkdir(parents=True)
        (project_root / ".git").mkdir()
        config_path = config_dir / "vibrant.toml"
        config_path.write_text('model = "gpt-5.3-codex"\nnot valid toml\n', encoding="utf-8")

        with pytest.raises(VibrantConfigError, match=r"Invalid TOML in .*vibrant\.toml"):
            load_config(start_path=project_root)

    def test_resolve_config_path_accepts_directory_inputs(self, tmp_path):
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()

        assert resolve_config_path(start_path=project_root) == project_root / ".vibrant" / "vibrant.toml"
        assert resolve_config_path(project_root, start_path=tmp_path) == project_root / ".vibrant" / "vibrant.toml"
        assert resolve_config_path(project_root / ".vibrant", start_path=tmp_path) == project_root / ".vibrant" / "vibrant.toml"
