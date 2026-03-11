from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vibrant.models.task import TaskInfo
from vibrant.orchestrator.execution.git_manager import GitManager, GitManagerError, GitWorktreeInfo
from vibrant.orchestrator.execution.git_workspace import GitWorkspaceService


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


class _CleanupFailingManager:
    def remove_worktree(self, task_id: str) -> None:
        raise GitManagerError(f"cleanup failed for {task_id}")


class _RecreateFailingManager(_CleanupFailingManager):
    def __init__(self) -> None:
        self.calls = 0

    def create_worktree(self, task_id: str) -> GitWorktreeInfo:
        self.calls += 1
        raise GitManagerError(f"create failed for {task_id} (attempt {self.calls})")


def test_collect_diff_raises_when_git_command_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    manager = GitManager(repo_root=repo, worktree_root=tmp_path / "worktrees")
    service = GitWorkspaceService(project_root=repo, git_manager=manager)

    bogus_worktree = tmp_path / "not-a-git-repo"
    bogus_worktree.mkdir()

    with pytest.raises(GitManagerError, match=r"git status --short failed"):
        service.collect_diff(
            TaskInfo(id="task-001", title="Inspect diff", branch="vibrant/task-001"),
            GitWorktreeInfo(path=bogus_worktree, head="deadbeef", branch="vibrant/task-001"),
        )


def test_cleanup_worktree_raises_when_remove_fails(tmp_path: Path) -> None:
    service = GitWorkspaceService(project_root=tmp_path, git_manager=_CleanupFailingManager())

    with pytest.raises(GitManagerError, match="Failed to clean up worktree for task-001"):
        service.cleanup_worktree("task-001")


def test_create_fresh_worktree_surfaces_cleanup_failure(tmp_path: Path) -> None:
    service = GitWorkspaceService(project_root=tmp_path, git_manager=_RecreateFailingManager())

    with pytest.raises(GitManagerError, match="initial create failed"):
        service.create_fresh_worktree("task-001")
