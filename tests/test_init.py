"""Tests for the ``vibrant init`` project bootstrap command."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from vibrant.config import load_config
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorState, OrchestratorStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DIRECTORIES = [
    ".vibrant/skills",
    ".vibrant/agents",
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
EXPECTED_GITIGNORE_ENTRIES = ["logs/", "conversations/", "agents/*.json"]


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
        assert config.codex_binary == "codex"

        state = OrchestratorState.model_validate_json((tmp_path / ".vibrant/state.json").read_text(encoding="utf-8"))
        assert state.status is OrchestratorStatus.PAUSED
        assert state.last_consensus_version == 0

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
