"""Git worktree management helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from vibrant.config import DEFAULT_WORKTREE_DIRECTORY, find_project_root, load_config


class GitManagerError(RuntimeError):
    """Raised when a Git worktree operation fails."""


@dataclass(frozen=True)
class GitWorktreeInfo:
    """Summary of one active Git worktree."""

    path: Path
    head: str
    branch: str | None = None


@dataclass(frozen=True)
class GitMergeResult:
    """Result of merging a task branch into the main branch."""

    branch: str
    merged: bool
    conflicted_files: list[str]
    stdout: str = ""
    stderr: str = ""

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicted_files)


class GitManager:
    """Create, reset, merge, inspect, and remove task worktrees."""

    def __init__(
        self,
        repo_root: str | Path = ".",
        *,
        worktree_root: str | Path | None = None,
        main_branch: str = "main",
        branch_prefix: str = "vibrant",
    ) -> None:
        self.repo_root = find_project_root(repo_root)
        config = load_config(start_path=self.repo_root)

        configured_root = worktree_root or config.worktree_directory or DEFAULT_WORKTREE_DIRECTORY
        configured_path = Path(configured_root).expanduser()
        if not configured_path.is_absolute():
            configured_path = self.repo_root / configured_path

        self.worktree_root = configured_path.resolve()
        self.main_branch = main_branch
        self.branch_prefix = branch_prefix

    def create_worktree(self, task_id: str) -> GitWorktreeInfo:
        """Create a task branch and detached worktree from the main branch."""

        branch = self.branch_name(task_id)
        worktree_path = self.worktree_path(task_id)
        starting_commit = self.rev_parse(self.main_branch)

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        if worktree_path.exists() and any(worktree_path.iterdir()):
            raise GitManagerError(f"Worktree path already exists and is not empty: {worktree_path}")

        completed = self._run_git(
            ["worktree", "add", str(worktree_path), "-b", branch, self.main_branch],
            cwd=self.repo_root,
        )
        try:
            self._run_git(["update-ref", self.base_ref_name(task_id), starting_commit], cwd=self.repo_root)
        except Exception:
            self._run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=self.repo_root, check=False)
            self._delete_branch(branch)
            raise

        return self._parse_worktree_from_output(completed.stdout, fallback_path=worktree_path, branch=branch)

    def remove_worktree(self, task_id: str) -> None:
        """Remove a task worktree and delete its branch metadata."""

        branch = self.branch_name(task_id)
        worktree_path = self.worktree_path(task_id)

        if worktree_path.exists():
            self._run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=self.repo_root)

        self._delete_branch(branch)
        self._run_git(["update-ref", "-d", self.base_ref_name(task_id)], cwd=self.repo_root, check=False)

    def merge_task(self, task_id: str) -> GitMergeResult:
        """Merge a task branch into the configured main branch and detect conflicts."""

        branch = self.branch_name(task_id)
        self._checkout_main_branch()

        completed = self._run_git(
            ["merge", "--no-ff", "--no-edit", branch],
            cwd=self.repo_root,
            check=False,
        )
        conflicted_files = self._list_conflicted_files()

        if completed.returncode == 0:
            return GitMergeResult(
                branch=branch,
                merged=True,
                conflicted_files=[],
                stdout=completed.stdout,
                stderr=completed.stderr,
            )

        if conflicted_files:
            return GitMergeResult(
                branch=branch,
                merged=False,
                conflicted_files=conflicted_files,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )

        raise GitManagerError(
            f"git merge failed for {branch}: {(completed.stderr or completed.stdout).strip()}"
        )

    def reset_worktree(self, task_id: str) -> str:
        """Reset a task worktree back to its starting commit and clean untracked files."""

        worktree_path = self.worktree_path(task_id)
        starting_commit = self._resolve_starting_commit(task_id)
        self._run_git(["reset", "--hard", starting_commit], cwd=worktree_path)
        self._run_git(["clean", "-fd"], cwd=worktree_path)
        return starting_commit

    def list_worktrees(self) -> list[GitWorktreeInfo]:
        """Return active worktrees known to Git."""

        completed = self._run_git(["worktree", "list", "--porcelain"], cwd=self.repo_root)
        return self._parse_worktree_list(completed.stdout)

    def branch_name(self, task_id: str) -> str:
        """Return the branch name used for a task."""

        return f"{self.branch_prefix}/{task_id}"

    def worktree_path(self, task_id: str) -> Path:
        """Return the filesystem path used for a task worktree."""

        return self.worktree_root / task_id

    def base_ref_name(self, task_id: str) -> str:
        """Return the internal ref storing the starting commit for a task."""

        return f"refs/vibrant/base/{task_id}"

    def rev_parse(self, revision: str, *, cwd: str | Path | None = None) -> str:
        """Resolve a Git revision to a commit hash."""

        completed = self._run_git(["rev-parse", revision], cwd=cwd or self.repo_root)
        return completed.stdout.strip()

    def branch_exists(self, branch: str) -> bool:
        """Return whether a local branch exists."""

        completed = self._run_git(["show-ref", "--verify", f"refs/heads/{branch}"], cwd=self.repo_root, check=False)
        return completed.returncode == 0

    def _checkout_main_branch(self) -> None:
        current_branch = self._run_git(["branch", "--show-current"], cwd=self.repo_root).stdout.strip()
        if current_branch != self.main_branch:
            self._run_git(["switch", self.main_branch], cwd=self.repo_root)

    def _resolve_starting_commit(self, task_id: str) -> str:
        base_ref = self.base_ref_name(task_id)
        completed = self._run_git(["rev-parse", "--verify", base_ref], cwd=self.repo_root, check=False)
        if completed.returncode == 0:
            return completed.stdout.strip()
        return self._run_git(
            ["merge-base", self.main_branch, self.branch_name(task_id)],
            cwd=self.repo_root,
        ).stdout.strip()

    def _list_conflicted_files(self) -> list[str]:
        completed = self._run_git(
            ["diff", "--name-only", "--diff-filter=U"],
            cwd=self.repo_root,
            check=False,
        )
        return [line for line in completed.stdout.splitlines() if line.strip()]

    def _delete_branch(self, branch: str) -> None:
        if not self.branch_exists(branch):
            return

        completed = self._run_git(["branch", "-d", branch], cwd=self.repo_root, check=False)
        if completed.returncode != 0:
            self._run_git(["branch", "-D", branch], cwd=self.repo_root)

    def _run_git(
        self,
        args: list[str],
        *,
        cwd: str | Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
        if check and completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            raise GitManagerError(f"git {' '.join(args)} failed: {message}")
        return completed

    def _parse_worktree_from_output(
        self,
        output: str,
        *,
        fallback_path: Path,
        branch: str,
    ) -> GitWorktreeInfo:
        path = fallback_path
        if output:
            first_line = output.splitlines()[0].strip()
            if first_line.startswith("Preparing worktree"):
                quoted = first_line.split("'", 2)
                if len(quoted) >= 2:
                    path = Path(quoted[1])
        head = self.rev_parse("HEAD", cwd=path)
        return GitWorktreeInfo(path=path, head=head, branch=branch)

    def _parse_worktree_list(self, output: str) -> list[GitWorktreeInfo]:
        entries: list[GitWorktreeInfo] = []
        current: dict[str, str] = {}

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                if current:
                    entries.append(self._build_worktree_entry(current))
                    current = {}
                continue

            key, _, value = line.partition(" ")
            current[key] = value

        if current:
            entries.append(self._build_worktree_entry(current))

        return entries

    def _build_worktree_entry(self, entry: dict[str, str]) -> GitWorktreeInfo:
        branch = entry.get("branch")
        if branch and branch.startswith("refs/heads/"):
            branch = branch.removeprefix("refs/heads/")
        return GitWorktreeInfo(
            path=Path(entry["worktree"]),
            head=entry.get("HEAD", ""),
            branch=branch,
        )
