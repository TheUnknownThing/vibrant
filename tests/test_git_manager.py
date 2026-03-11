"""Integration tests for the Phase 1 Git worktree manager."""

from __future__ import annotations

import subprocess
from pathlib import Path

from vibrant.orchestrator.execution.git_manager import GitManager


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {(completed.stderr or completed.stdout).strip()}")
    return completed


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Vibrant Tests")
    _git(repo, "config", "user.email", "vibrant@example.com")
    _write(repo / "app.txt", "base\n")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


class TestGitManager:
    def test_create_worktree_creates_branch_and_accessible_files(self, tmp_path):
        repo = _init_repo(tmp_path)
        manager = GitManager(repo_root=repo, worktree_root=tmp_path / "worktrees")

        worktree = manager.create_worktree("task-001")

        assert worktree.path == tmp_path / "worktrees" / "task-001"
        assert worktree.branch == "vibrant/task-001"
        assert (worktree.path / "app.txt").read_text(encoding="utf-8") == "base\n"
        assert manager.branch_exists("vibrant/task-001")
        assert any(item.path == worktree.path for item in manager.list_worktrees())

    def test_merge_task_updates_main_on_clean_merge(self, tmp_path):
        repo = _init_repo(tmp_path)
        manager = GitManager(repo_root=repo, worktree_root=tmp_path / "worktrees")
        worktree = manager.create_worktree("task-002")

        _write(worktree.path / "app.txt", "feature\n")
        _git(worktree.path, "add", "app.txt")
        _git(worktree.path, "commit", "-m", "feature change")

        result = manager.merge_task("task-002")

        assert result.merged is True
        assert result.has_conflicts is False
        assert (repo / "app.txt").read_text(encoding="utf-8") == "feature\n"
        assert _git(repo, "branch", "--show-current").stdout.strip() == "main"

    def test_merge_task_detects_conflicts_and_returns_info(self, tmp_path):
        repo = _init_repo(tmp_path)
        manager = GitManager(repo_root=repo, worktree_root=tmp_path / "worktrees")
        worktree = manager.create_worktree("task-003")

        _write(worktree.path / "app.txt", "branch change\n")
        _git(worktree.path, "add", "app.txt")
        _git(worktree.path, "commit", "-m", "branch change")

        _write(repo / "app.txt", "main change\n")
        _git(repo, "add", "app.txt")
        _git(repo, "commit", "-m", "main change")

        result = manager.merge_task("task-003")

        assert result.merged is False
        assert result.has_conflicts is True
        assert result.conflicted_files == ["app.txt"]
        assert "CONFLICT" in (result.stdout + result.stderr)

        _git(repo, "merge", "--abort")

    def test_reset_worktree_restores_clean_state(self, tmp_path):
        repo = _init_repo(tmp_path)
        manager = GitManager(repo_root=repo, worktree_root=tmp_path / "worktrees")
        worktree = manager.create_worktree("task-004")

        _write(worktree.path / "app.txt", "dirty\n")
        _write(worktree.path / "scratch.txt", "temporary\n")

        starting_commit = manager.reset_worktree("task-004")

        assert starting_commit == manager.rev_parse("main")
        assert (worktree.path / "app.txt").read_text(encoding="utf-8") == "base\n"
        assert not (worktree.path / "scratch.txt").exists()
        assert _git(worktree.path, "status", "--porcelain").stdout.strip() == ""

    def test_remove_worktree_removes_directory_and_branch(self, tmp_path):
        repo = _init_repo(tmp_path)
        manager = GitManager(repo_root=repo, worktree_root=tmp_path / "worktrees")
        worktree = manager.create_worktree("task-005")

        manager.remove_worktree("task-005")

        assert not worktree.path.exists()
        assert not manager.branch_exists("vibrant/task-005")
        assert all(item.path != worktree.path for item in manager.list_worktrees())
