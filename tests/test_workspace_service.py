from __future__ import annotations

import subprocess
from pathlib import Path

from vibrant.orchestrator.basic.stores import WorkspaceStore
from vibrant.orchestrator.basic.workspace import WorkspaceService


def _git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _initialize_git_repo(project_root: Path) -> None:
    _git(project_root, "init", "-b", "main")
    _git(project_root, "config", "user.name", "Vibrant Tests")
    _git(project_root, "config", "user.email", "vibrant-tests@example.com")
    (project_root / "tracked.txt").write_text("root\n", encoding="utf-8")
    _git(project_root, "add", "tracked.txt")
    _git(project_root, "commit", "-m", "Initial commit")


def test_prepare_task_workspace_ignores_stale_repo_relative_workspace_roots(tmp_path: Path) -> None:
    _initialize_git_repo(tmp_path)
    workspace_store = WorkspaceStore(tmp_path / ".vibrant" / "workspaces.json")
    workspace_service = WorkspaceService(
        project_root=tmp_path,
        worktree_root=Path("worktrees"),
        workspace_store=workspace_store,
        artifacts_root=tmp_path / ".vibrant" / "review-diffs",
    )
    first_workspace = workspace_service.prepare_task_workspace("task-1")

    assert "?? worktrees/" in _git(tmp_path, "status", "--porcelain")

    restarted_workspace_service = WorkspaceService(
        project_root=tmp_path,
        worktree_root=Path("new-worktrees"),
        workspace_store=workspace_store,
        artifacts_root=tmp_path / ".vibrant" / "review-diffs",
    )

    second_workspace = restarted_workspace_service.prepare_task_workspace("task-2")

    assert Path(first_workspace.path).exists()
    assert Path(second_workspace.path).exists()
