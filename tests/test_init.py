"""Tests for the ``vibrant init`` project bootstrap command."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from vibrant.config import DEFAULT_CONVERSATION_DIRECTORY, GatekeeperRole, RoadmapExecutionMode, load_config
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.orchestrator.basic.stores import WorkflowStateStore
from vibrant.project_init import ensure_project_files
from vibrant.providers.base import ProviderKind

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DIRECTORIES = [
    ".vibrant/skills",
    ".vibrant/agent-instances",
    ".vibrant/agent-runs",
    ".vibrant/conversations",
    ".vibrant/prompts",
    ".vibrant/logs/providers/native",
    ".vibrant/logs/providers/canonical",
    ".vibrant/consensus.history",
]
EXPECTED_FILES = [
    ".vibrant/consensus.md",
    ".vibrant/roadmap.md",
    ".vibrant/vibrant.toml",
    ".vibrant/state.json",
    ".vibrant/.gitignore",
]
EXPECTED_GITIGNORE_ENTRIES = [
    "*",
]


def _git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _run_vibrant_init(cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(REPO_ROOT)
    )
    return subprocess.run(
        [sys.executable, "-m", "vibrant", "init"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _parse_consensus_meta(path: Path) -> ConsensusDocument:
    content = path.read_text(encoding="utf-8")
    match = re.search(r"<!-- META:START -->\n(?P<body>.*?)\n<!-- META:END -->", content, re.DOTALL)
    assert match is not None, "META section was not found"

    data: dict[str, str] = {}
    for line in match.group("body").splitlines():
        line = line.strip()
        bullet_match = re.match(r"- \*\*(?P<key>[^*]+)\*\*: (?P<value>.*)", line)
        assert bullet_match is not None, f"Invalid META line: {line}"
        data[bullet_match.group("key")] = bullet_match.group("value")

    return ConsensusDocument(
        project=data["Project"],
        created_at=data["Created"],
        updated_at=data["Last Updated"],
        version=int(data["Version"]),
        status=data["Status"],
    )


class TestVibrantInit:
    def test_init_creates_expected_layout(self, tmp_path):
        result = _run_vibrant_init(tmp_path)

        assert result.returncode == 0, result.stderr
        assert "Initialized Vibrant project in" in result.stdout

        for relative_dir in EXPECTED_DIRECTORIES:
            assert (tmp_path / relative_dir).is_dir(), relative_dir

        for relative_file in EXPECTED_FILES:
            assert (tmp_path / relative_file).is_file(), relative_file

        config = load_config(start_path=tmp_path)
        assert config.provider_kind is ProviderKind.CODEX
        assert config.codex_binary == "codex"
        assert config.model_provider is None
        assert config.conversation_directory == str(DEFAULT_CONVERSATION_DIRECTORY)
        assert config.execution_mode is RoadmapExecutionMode.AUTOMATIC
        assert config.gatekeeper_role is GatekeeperRole.BUILDER
        assert 'kind = "codex"' in (tmp_path / ".vibrant" / "vibrant.toml").read_text(encoding="utf-8")
        assert 'execution-mode = "automatic"' in (tmp_path / ".vibrant/vibrant.toml").read_text(encoding="utf-8")
        assert 'gatekeeper-role = "builder"' in (tmp_path / ".vibrant/vibrant.toml").read_text(encoding="utf-8")
        assert (
            'conversation-directory = ".vibrant/conversations"'
            in (tmp_path / ".vibrant/vibrant.toml").read_text(encoding="utf-8")
        )
        assert 'model-provider = "' not in (tmp_path / ".vibrant/vibrant.toml").read_text(encoding="utf-8")

        state = WorkflowStateStore(tmp_path / ".vibrant/state.json").load()
        assert state.workflow_status.value == "init"
        assert state.total_agent_spawns == 0

        consensus = _parse_consensus_meta(tmp_path / ".vibrant/consensus.md")
        assert consensus.version == 0
        assert consensus.status is ConsensusStatus.INIT

        gitignore_text = (tmp_path / ".vibrant/.gitignore").read_text(encoding="utf-8")
        for entry in EXPECTED_GITIGNORE_ENTRIES:
            assert entry in gitignore_text

    def test_init_is_idempotent(self, tmp_path):
        first = _run_vibrant_init(tmp_path)
        consensus_before = (tmp_path / ".vibrant/consensus.md").read_text(encoding="utf-8")

        second = _run_vibrant_init(tmp_path)
        consensus_after = (tmp_path / ".vibrant/consensus.md").read_text(encoding="utf-8")
        gitignore_lines = (tmp_path / ".vibrant/.gitignore").read_text(encoding="utf-8").splitlines()

        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        assert consensus_after == consensus_before
        for entry in EXPECTED_GITIGNORE_ENTRIES:
            assert gitignore_lines.count(entry) == 1

    def test_init_creates_initial_commit_for_repository_with_unborn_head(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-b", "main")
        (tmp_path / "tracked.txt").write_text("hello\n", encoding="utf-8")

        result = _run_vibrant_init(tmp_path)

        assert result.returncode == 0, result.stderr
        assert _git(tmp_path, "rev-parse", "HEAD")
        assert _git(tmp_path, "ls-files", "tracked.txt") == "tracked.txt"
        assert _git(tmp_path, "log", "-1", "--pretty=%s") == "Initialize repository for Vibrant"

    def test_init_preserves_repository_identity_for_initial_commit(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-b", "main")
        _git(tmp_path, "config", "user.name", "Configured User")
        _git(tmp_path, "config", "user.email", "configured@example.com")

        result = _run_vibrant_init(tmp_path)

        assert result.returncode == 0, result.stderr
        assert _git(tmp_path, "log", "-1", "--pretty=%an <%ae>") == "Configured User <configured@example.com>"

    def test_init_bypasses_hooks_and_commit_signing_for_initial_commit(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-b", "main")
        _git(tmp_path, "config", "user.name", "Configured User")
        _git(tmp_path, "config", "user.email", "configured@example.com")
        _git(tmp_path, "config", "commit.gpgsign", "true")

        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (hooks_dir / "pre-commit").chmod(0o755)

        result = _run_vibrant_init(tmp_path)

        assert result.returncode == 0, result.stderr
        assert _git(tmp_path, "log", "-1", "--pretty=%s") == "Initialize repository for Vibrant"

    def test_init_creates_empty_commit_for_empty_repository_with_unborn_head(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-b", "main")

        result = _run_vibrant_init(tmp_path)

        assert result.returncode == 0, result.stderr
        assert _git(tmp_path, "rev-parse", "HEAD")
        assert _git(tmp_path, "log", "-1", "--pretty=%s") == "Initialize repository for Vibrant"
        assert _git(tmp_path, "status", "--short") == ""

    def test_ensure_project_files_backfills_missing_source_of_truth_files(self, tmp_path):
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        vibrant_dir = project_root / ".vibrant"
        vibrant_dir.mkdir()
        (vibrant_dir / "agent-instances").mkdir()

        result = ensure_project_files(project_root)

        assert result == vibrant_dir
        for relative_file in EXPECTED_FILES:
            assert (project_root / relative_file).is_file(), relative_file
        for relative_dir in EXPECTED_DIRECTORIES:
            assert (project_root / relative_dir).is_dir(), relative_dir
