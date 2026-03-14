"""Workspace management for task attempts."""

from __future__ import annotations

from pathlib import Path
import shutil
from uuid import uuid4

from ...types import DiffArtifact, MergeOutcome, WorkspaceHandle

_COPY_IGNORE_PATTERNS = (".git", ".venv", "__pycache__", ".pytest_cache")
_MERGE_IGNORED_NAMES = frozenset({
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    ".vibrant",
    ".vibrant-review.diff",
})


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
            ignore=shutil.ignore_patterns(*_COPY_IGNORE_PATTERNS),
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
        if workspace is None:
            workspace = self._restore_workspace(task_id=task_id, workspace_id=workspace_id)
        if workspace.task_id != task_id:
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
        workspace_root = Path(workspace.path)
        if not workspace_root.exists():
            return MergeOutcome(
                status="failed",
                message=f"Workspace path does not exist: {workspace.path}",
                follow_up_required=True,
            )

        self._sync_workspace_tree(workspace_root)
        return MergeOutcome(
            status="merged",
            message=f"Merged workspace {workspace.workspace_id} into the project branch.",
            follow_up_required=False,
        )

    def _restore_workspace(self, *, task_id: str, workspace_id: str) -> WorkspaceHandle:
        workspace_path = self.worktree_root / f"{task_id}-{workspace_id}"
        if not workspace_path.exists():
            raise KeyError(f"Workspace not found: {workspace_id}")
        handle = WorkspaceHandle(
            workspace_id=workspace_id,
            task_id=task_id,
            path=str(workspace_path),
            branch=f"task/{task_id}",
            base_branch="main",
        )
        self._workspaces[workspace_id] = handle
        return handle

    def _sync_workspace_tree(self, workspace_root: Path) -> None:
        project_entries = self._scan_entries(self.project_root)
        workspace_entries = self._scan_entries(workspace_root)

        stale_paths = sorted(
            set(project_entries) - set(workspace_entries),
            key=lambda relative_path: (len(relative_path.parts), relative_path.as_posix()),
            reverse=True,
        )
        for relative_path in stale_paths:
            target = self.project_root / relative_path
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

        for relative_path in sorted(workspace_entries, key=lambda item: (len(item.parts), item.as_posix())):
            source = workspace_root / relative_path
            target = self.project_root / relative_path
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _scan_entries(self, root: Path) -> dict[Path, str]:
        entries: dict[Path, str] = {}
        for path in root.rglob("*"):
            relative_path = path.relative_to(root)
            if self._is_ignored(relative_path):
                continue
            entries[relative_path] = "dir" if path.is_dir() else "file"
        return entries

    def _is_ignored(self, relative_path: Path) -> bool:
        return any(part in _MERGE_IGNORED_NAMES for part in relative_path.parts)
