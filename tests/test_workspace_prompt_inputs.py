from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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


def test_sync_prompt_inputs_copies_untracked_referenced_file_into_workspace(tmp_path: Path) -> None:
    _initialize_git_repo(tmp_path)
    image_path = tmp_path / "2026-03-18.png"
    image_path.write_bytes(b"png-bytes")
    workspace_service = WorkspaceService(
        project_root=tmp_path,
        worktree_root=tmp_path / ".vibrant" / "worktrees",
        workspace_store=WorkspaceStore(tmp_path / ".vibrant" / "workspaces.json"),
        artifacts_root=tmp_path / ".vibrant" / "review-diffs",
    )

    prompt = "Use the screenshot at @2026-03-18.png to implement the task."
    workspace = workspace_service.prepare_task_workspace("task-1", prompt=prompt)
    copied_paths = workspace_service.sync_prompt_inputs(
        Path(workspace.path),
        prompt,
    )

    mirrored_path = Path(workspace.path) / "2026-03-18.png"
    assert copied_paths == ["2026-03-18.png"]
    assert mirrored_path.read_bytes() == b"png-bytes"
    assert _git(Path(workspace.path), "status", "--porcelain") == ""


def test_prepare_task_workspace_rejects_dirty_tracked_prompt_reference(tmp_path: Path) -> None:
    _initialize_git_repo(tmp_path)
    tracked_path = tmp_path / "tracked.txt"
    tracked_path.write_text("locally edited\n", encoding="utf-8")
    workspace_service = WorkspaceService(
        project_root=tmp_path,
        worktree_root=tmp_path / ".vibrant" / "worktrees",
        workspace_store=WorkspaceStore(tmp_path / ".vibrant" / "workspaces.json"),
        artifacts_root=tmp_path / ".vibrant" / "review-diffs",
    )

    prompt = "Use the current contents of @tracked.txt to implement the task."

    with pytest.raises(
        RuntimeError,
        match="Project repository has uncommitted changes outside orchestrator-owned paths.",
    ):
        workspace_service.prepare_task_workspace("task-1", prompt=prompt)
