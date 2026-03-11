"""Git worktree and merge service."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from vibrant.models.task import TaskInfo

from .git_manager import GitManager, GitManagerError, GitMergeResult, GitWorktreeInfo


class GitWorkspaceService:
    """Own git worktree, diff, and merge operations."""

    def __init__(self, *, project_root: str | Path, git_manager: GitManager) -> None:
        self.project_root = Path(project_root)
        self.git_manager = git_manager

    def create_fresh_worktree(self, task_id: str) -> GitWorktreeInfo:
        try:
            return self.git_manager.create_worktree(task_id)
        except GitManagerError as exc:
            try:
                self.cleanup_worktree(task_id)
            except Exception as cleanup_exc:
                raise GitManagerError(
                    f"Failed to recreate worktree for {task_id}: "
                    f"initial create failed ({exc}) and cleanup also failed ({cleanup_exc})"
                ) from cleanup_exc
            return self.git_manager.create_worktree(task_id)

    def cleanup_worktree(self, task_id: str) -> None:
        try:
            self.git_manager.remove_worktree(task_id)
        except Exception as exc:
            raise GitManagerError(f"Failed to clean up worktree for {task_id}: {exc}") from exc

    def branch_name(self, task_id: str) -> str:
        return self.git_manager.branch_name(task_id)

    def collect_diff(self, task: TaskInfo, worktree: GitWorktreeInfo) -> str:
        branch = task.branch or self.git_manager.branch_name(task.id)
        sections: list[str] = []

        status = _run_git_capture(worktree.path, "status", "--short")
        if status:
            sections.extend(["Git Status:", status])

        worktree_diff = _run_git_capture(worktree.path, "diff", "--find-renames")
        if worktree_diff:
            sections.extend(["Working Tree Diff:", worktree_diff])

        staged_diff = _run_git_capture(worktree.path, "diff", "--cached", "--find-renames")
        if staged_diff:
            sections.extend(["Staged Diff:", staged_diff])

        branch_diff = _run_git_capture(self.project_root, "diff", "--find-renames", f"{self.git_manager.main_branch}...{branch}")
        if branch_diff:
            sections.extend(["Branch Diff:", branch_diff])

        if not sections:
            return "No diff available."
        return "\n".join(sections)

    def merge_task(self, task_id: str) -> GitMergeResult:
        return self.git_manager.merge_task(task_id)

    def abort_merge_if_needed(self) -> None:
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=str(self.project_root),
            text=True,
            capture_output=True,
            check=False,
        )


def scoped_worktree_root(project_root: Path, configured_root: str) -> Path:
    root = Path(configured_root).expanduser()
    if not root.is_absolute():
        root = project_root / root
    return root / _project_worktree_scope(project_root)


def format_merge_error(result: GitMergeResult) -> str:
    details = (result.stderr or result.stdout).strip()
    if result.conflicted_files:
        suffix = f" Conflicted files: {', '.join(result.conflicted_files)}"
    else:
        suffix = ""
    return f"Merge failed for {result.branch}.{suffix} {details}".strip()


def _project_worktree_scope(project_root: Path) -> str:
    digest = hashlib.sha1(str(project_root).encode("utf-8")).hexdigest()[:12]
    return f"{project_root.name}-{digest}"


def _run_git_capture(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip() or f"exit code {completed.returncode}"
        raise GitManagerError(f"git {' '.join(args)} failed in {cwd}: {details}")
    return completed.stdout.strip()
