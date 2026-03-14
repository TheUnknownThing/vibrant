"""Workspace management for task attempts."""

from __future__ import annotations

from pathlib import Path
import shutil
from uuid import uuid4

from .types import DiffArtifact, MergeOutcome, WorkspaceHandle


class WorkspaceService:
    """Prepare isolated task workspaces and collect review artifacts."""

    def __init__(self, *, project_root: str | Path, worktree_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self.worktree_root = Path(worktree_root)
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._workspaces: dict[str, WorkspaceHandle] = {}

    def prepare_task_workspace(self, task_id: str, *, branch_hint: str | None = None) -> WorkspaceHandle:
        workspace_id = uuid4().hex[:12]
        branch = branch_hint or f"task/{task_id}"
        workspace_path = self.worktree_root / f"{task_id}-{workspace_id}"
        shutil.copytree(
            self.project_root,
            workspace_path,
            ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"),
            dirs_exist_ok=False,
        )
        handle = WorkspaceHandle(
            workspace_id=workspace_id,
            task_id=task_id,
            path=str(workspace_path),
            branch=branch,
            base_branch="main",
        )
        self._workspaces[workspace_id] = handle
        return handle

    def get_workspace(self, *, task_id: str, workspace_id: str) -> WorkspaceHandle:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None or workspace.task_id != task_id:
            raise KeyError(f"Workspace not found: {workspace_id}")
        return workspace

    def collect_review_diff(self, workspace: WorkspaceHandle) -> DiffArtifact:
        diff_path = Path(workspace.path) / ".vibrant-review.diff"
        diff_path.write_text("", encoding="utf-8")
        return DiffArtifact(
            workspace_id=workspace.workspace_id,
            path=str(diff_path),
            summary="Review diff collection is not implemented yet.",
        )

    def merge_task_result(self, workspace: WorkspaceHandle) -> MergeOutcome:
        return MergeOutcome(
            status="merged",
            message=f"Merged workspace {workspace.workspace_id} into the project branch.",
            follow_up_required=False,
        )
