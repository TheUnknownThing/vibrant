"""Git-backed workspace management for task attempts."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from ..stores import WorkspaceStore
from ...types import (
    DiffArtifact,
    MergeOutcome,
    WorkspaceHandle,
    WorkspaceKind,
    WorkspaceStatus,
)

_WORKSPACE_EXCLUDED_PATHS = (".vibrant", ".vibrant/**")
_BOT_NAME = "Vibrant"
_BOT_EMAIL = "vibrant@example.invalid"
_MAX_REPORTED_ORCHESTRATOR_PATHS = 5
_PROMPT_FILE_REFERENCE_PATTERN = re.compile(r"(?<!\S)@(?P<path>\S+)")


class WorkspaceService:
    """Prepare isolated git worktrees and persist their metadata."""

    def __init__(
        self,
        *,
        project_root: Path,
        worktree_root: Path,
        workspace_store: WorkspaceStore,
        artifacts_root: Path,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        candidate_worktree_root = Path(worktree_root).expanduser()
        if not candidate_worktree_root.is_absolute():
            candidate_worktree_root = self.project_root / candidate_worktree_root
        self.worktree_root = candidate_worktree_root.resolve()
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root = Path(artifacts_root).expanduser().resolve()
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.workspace_store = workspace_store
        self._workspaces: dict[str, WorkspaceHandle] = {}

    def prepare_task_workspace(
        self,
        task_id: str,
        *,
        attempt_id: str | None = None,
        branch_hint: str | None = None,
        prompt: str | None = None,
    ) -> WorkspaceHandle:
        self._ensure_git_repo()
        self._ensure_clean_target_repo(prompt=prompt)

        workspace_id = uuid4().hex[:12]
        target_ref = self._resolve_target_ref()
        base_commit = self._git_stdout(self.project_root, "rev-parse", target_ref)
        branch = self._resolve_task_branch(task_id=task_id, workspace_id=workspace_id, branch_hint=branch_hint)
        workspace_path = self.worktree_root / f"{task_id}-{workspace_id}"

        self._git(self.project_root, "worktree", "add", "-b", branch, str(workspace_path), base_commit)
        handle = WorkspaceHandle(
            workspace_id=workspace_id,
            task_id=task_id,
            attempt_id=attempt_id,
            path=str(workspace_path),
            branch=branch,
            base_branch=target_ref,
            kind=WorkspaceKind.TASK,
            target_ref=target_ref,
            base_commit=base_commit,
            status=WorkspaceStatus.ACTIVE,
        )
        persisted = self.workspace_store.create(handle)
        self._workspaces[persisted.workspace_id] = persisted
        return persisted

    def attach_attempt(self, *, workspace_id: str, attempt_id: str) -> WorkspaceHandle:
        workspace = self._require_workspace(workspace_id)
        updated = self.workspace_store.update(workspace_id, attempt_id=attempt_id)
        self._workspaces[workspace_id] = updated
        return updated

    def sync_prompt_inputs(self, workspace_root: Path, prompt: str) -> list[str]:
        copied_paths: list[str] = []
        ignore_patterns: list[str] = []
        for source_path, relative_path in self._prompt_input_paths(prompt):
            destination = workspace_root / relative_path
            if source_path.is_dir():
                shutil.copytree(source_path, destination, dirs_exist_ok=True)
                ignore_patterns.append(f"/{relative_path.as_posix()}/**")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination)
                ignore_patterns.append(f"/{relative_path.as_posix()}")
            copied_paths.append(relative_path.as_posix())
        if ignore_patterns:
            self._ensure_workspace_git_excludes(workspace_root, ignore_patterns)
        return copied_paths

    def get_workspace(self, *, task_id: str, workspace_id: str) -> WorkspaceHandle:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            workspace = self.workspace_store.get(workspace_id)
        if workspace is None or workspace.task_id != task_id:
            raise KeyError(f"Workspace not found: {workspace_id}")
        self._workspaces[workspace_id] = workspace
        return workspace

    def capture_result_commit(self, workspace: WorkspaceHandle) -> WorkspaceHandle:
        current = self._require_workspace(workspace.workspace_id)
        workspace_root = Path(current.path)
        if not workspace_root.exists():
            raise FileNotFoundError(f"Workspace path does not exist: {current.path}")
        self._fail_if_orchestrator_state_changed(workspace_root)

        head_commit = self._git_stdout(workspace_root, "rev-parse", "HEAD")
        if self._has_relevant_changes(workspace_root):
            self._git(workspace_root, "add", "-A", "--", ".", *self._workspace_excluded_pathspec())
            if not self._git_success(workspace_root, "diff", "--cached", "--quiet", "--exit-code"):
                self._git(
                    workspace_root,
                    "commit",
                    "-m",
                    f"vibrant: capture result for {current.task_id}",
                    env=self._bot_git_env(),
                )
                head_commit = self._git_stdout(workspace_root, "rev-parse", "HEAD")

        result_commit = None if head_commit == current.base_commit else head_commit
        status = WorkspaceStatus.NO_CHANGES if result_commit is None else WorkspaceStatus.RESULT_CAPTURED
        updated = self.workspace_store.update(
            current.workspace_id,
            result_commit=result_commit,
            status=status,
        )
        self._workspaces[updated.workspace_id] = updated
        return updated

    def collect_review_diff(self, workspace: WorkspaceHandle) -> DiffArtifact | None:
        current = self.capture_result_commit(workspace)
        if current.result_commit is None:
            return None

        diff_path = self.artifacts_root / f"{current.workspace_id}.diff"
        diff_text = self._git_stdout(
            self.project_root,
            "diff",
            "--binary",
            f"{current.base_commit}..{current.result_commit}",
        )
        diff_path.write_text(diff_text, encoding="utf-8")
        return DiffArtifact(
            workspace_id=current.workspace_id,
            path=str(diff_path),
            base_commit=current.base_commit,
            result_commit=current.result_commit,
            summary=f"Diff for {current.base_commit}..{current.result_commit}",
        )

    def merge_task_result(self, workspace: WorkspaceHandle) -> MergeOutcome:
        current = self.capture_result_commit(workspace)
        if current.result_commit is None:
            updated = self.workspace_store.update(current.workspace_id, status=WorkspaceStatus.MERGED)
            self._workspaces[updated.workspace_id] = updated
            return MergeOutcome(
                status="merged",
                message=f"Workspace {current.workspace_id} produced no code changes.",
                follow_up_required=False,
            )

        integration = self.create_integration_worktree(current)
        self.workspace_store.update(current.workspace_id, status=WorkspaceStatus.INTEGRATING)

        merge_process = self._git(
            Path(integration.path),
            "merge",
            "--no-ff",
            "--no-edit",
            current.result_commit,
            check=False,
            env=self._bot_git_env(),
        )
        if merge_process.returncode != 0:
            self.workspace_store.update(
                integration.workspace_id,
                status=WorkspaceStatus.CONFLICTED,
            )
            self.workspace_store.update(
                current.workspace_id,
                status=WorkspaceStatus.CONFLICTED,
            )
            self._workspaces.pop(integration.workspace_id, None)
            self._workspaces[current.workspace_id] = self._require_workspace(current.workspace_id)
            return MergeOutcome(
                status="conflicted",
                message=f"Merge conflicted for workspace {current.workspace_id}.",
                follow_up_required=True,
            )

        merge_commit = self._git_stdout(Path(integration.path), "rev-parse", "HEAD")
        self._finalize_merge(target_ref=current.target_ref, merge_commit=merge_commit)
        self.workspace_store.update(
            integration.workspace_id,
            integration_commit=merge_commit,
            status=WorkspaceStatus.MERGED,
        )
        updated = self.workspace_store.update(
            current.workspace_id,
            integration_commit=merge_commit,
            status=WorkspaceStatus.MERGED,
        )
        self._workspaces[current.workspace_id] = updated
        self.cleanup_workspace(integration.workspace_id)
        return MergeOutcome(
            status="merged",
            message=f"Merged {current.result_commit} into {current.target_ref}.",
            follow_up_required=False,
            integration_commit=merge_commit,
        )

    def create_integration_worktree(self, workspace: WorkspaceHandle) -> WorkspaceHandle:
        target_commit = self._git_stdout(self.project_root, "rev-parse", workspace.target_ref)
        workspace_id = uuid4().hex[:12]
        workspace_path = self.worktree_root / f"integration-{workspace.task_id}-{workspace_id}"
        self._git(self.project_root, "worktree", "add", "--detach", str(workspace_path), target_commit)
        handle = WorkspaceHandle(
            workspace_id=workspace_id,
            task_id=workspace.task_id,
            attempt_id=workspace.attempt_id,
            path=str(workspace_path),
            branch=f"detached/{target_commit[:12]}",
            base_branch=workspace.target_ref,
            kind=WorkspaceKind.INTEGRATION,
            target_ref=workspace.target_ref,
            base_commit=target_commit,
            status=WorkspaceStatus.ACTIVE,
        )
        persisted = self.workspace_store.create(handle)
        self._workspaces[persisted.workspace_id] = persisted
        return persisted

    def cleanup_workspace(self, workspace_id: str) -> None:
        workspace = self.workspace_store.get(workspace_id)
        if workspace is None:
            return
        path = Path(workspace.path)
        if path.exists():
            self._git(self.project_root, "worktree", "remove", "--force", str(path), check=False)
        if workspace.kind is WorkspaceKind.TASK and workspace.branch:
            self._git(self.project_root, "branch", "-D", workspace.branch, check=False)
        self._workspaces.pop(workspace_id, None)

    def _finalize_merge(self, *, target_ref: str, merge_commit: str) -> None:
        ref_name = target_ref if target_ref.startswith("refs/") else f"refs/heads/{target_ref}"
        self._git(self.project_root, "update-ref", ref_name, merge_commit)
        self._git(
            self.project_root,
            "restore",
            "--source",
            target_ref,
            "--staged",
            "--worktree",
            "--",
            ".",
            *self._target_excluded_pathspec(),
        )

    def _require_workspace(self, workspace_id: str) -> WorkspaceHandle:
        workspace = self._workspaces.get(workspace_id) or self.workspace_store.get(workspace_id)
        if workspace is None:
            raise KeyError(f"Workspace not found: {workspace_id}")
        self._workspaces[workspace_id] = workspace
        return workspace

    def _ensure_git_repo(self) -> None:
        if not self._git_success(self.project_root, "rev-parse", "--is-inside-work-tree"):
            raise RuntimeError(f"Project root is not a git repository: {self.project_root}")

    def _ensure_clean_target_repo(self, *, prompt: str | None = None) -> None:
        status = self._git_stdout(
            self.project_root,
            "status",
            "--porcelain",
            "--",
            ".",
            *self._target_excluded_pathspec(prompt=prompt),
        )
        if status.strip():
            raise RuntimeError("Project repository has uncommitted changes outside orchestrator-owned paths.")

    def _resolve_target_ref(self) -> str:
        return self._git_stdout(self.project_root, "symbolic-ref", "--quiet", "--short", "HEAD")

    @staticmethod
    def _resolve_task_branch(*, task_id: str, workspace_id: str, branch_hint: str | None) -> str:
        if branch_hint:
            normalized = branch_hint.strip().strip("/")
            if normalized:
                return f"{normalized}/{workspace_id}"
        return f"vibrant/task/{task_id}/{workspace_id}"

    def _has_relevant_changes(self, workspace_root: Path) -> bool:
        status = self._git_stdout(
            workspace_root,
            "status",
            "--porcelain",
            "--",
            ".",
            *self._workspace_excluded_pathspec(),
        )
        return bool(status.strip())

    def _fail_if_orchestrator_state_changed(self, workspace_root: Path) -> None:
        changed_paths = self._orchestrator_state_changes(workspace_root)
        if not changed_paths:
            return
        rendered_paths = ", ".join(changed_paths[:_MAX_REPORTED_ORCHESTRATOR_PATHS])
        if len(changed_paths) > _MAX_REPORTED_ORCHESTRATOR_PATHS:
            rendered_paths = f"{rendered_paths}, ..."
        raise RuntimeError(
            "Task workspace modified orchestrator-owned `.vibrant` state; these changes are not durable. "
            f"Changed paths: {rendered_paths}"
        )

    def _orchestrator_state_changes(self, workspace_root: Path) -> list[str]:
        status = self._git_stdout(workspace_root, "status", "--porcelain", "--", ".vibrant")
        if not status:
            return []
        changed_paths: list[str] = []
        for line in status.splitlines():
            if not line:
                continue
            path_field = line[3:] if len(line) > 3 else ""
            normalized = path_field.split(" -> ", 1)[-1].strip()
            if normalized:
                changed_paths.append(normalized)
        return changed_paths

    def _target_excluded_pathspec(self, *, prompt: str | None = None) -> tuple[str, ...]:
        excluded_paths = list(_WORKSPACE_EXCLUDED_PATHS)
        excluded_paths.extend(self._project_relative_excluded_paths(self.worktree_root))
        excluded_paths.extend(self._project_relative_excluded_paths(self.artifacts_root))
        excluded_paths.extend(self._prompt_input_untracked_excluded_paths(prompt or ""))
        return tuple(f":(exclude){path}" for path in excluded_paths)

    @staticmethod
    def _workspace_excluded_pathspec() -> tuple[str, ...]:
        return tuple(f":(exclude){path}" for path in _WORKSPACE_EXCLUDED_PATHS)

    def _project_relative_excluded_paths(self, path: Path) -> tuple[str, ...]:
        resolved_path = path.expanduser().resolve()
        try:
            relative_path = resolved_path.relative_to(self.project_root)
        except ValueError:
            return ()
        if not relative_path.parts:
            return ()
        normalized = relative_path.as_posix().strip("/")
        if not normalized:
            return ()
        return (normalized, f"{normalized}/**")

    def _prompt_input_paths(self, prompt: str) -> list[tuple[Path, Path]]:
        resolved_paths: list[tuple[Path, Path]] = []
        seen: set[Path] = set()
        for match in _PROMPT_FILE_REFERENCE_PATTERN.finditer(prompt):
            token = match.group("path").strip()
            if not token:
                continue
            candidate = Path(token).expanduser()
            if not candidate.is_absolute():
                candidate = self.project_root / candidate
            resolved_candidate = candidate.resolve(strict=False)
            if not resolved_candidate.exists():
                continue
            try:
                relative_path = resolved_candidate.relative_to(self.project_root)
            except ValueError:
                continue
            if resolved_candidate in seen:
                continue
            seen.add(resolved_candidate)
            resolved_paths.append((resolved_candidate, relative_path))
        return resolved_paths

    def _prompt_input_untracked_excluded_paths(self, prompt: str) -> tuple[str, ...]:
        return self._pathspecs_for_relative_paths(self._prompt_input_untracked_paths(prompt))

    def _prompt_input_untracked_paths(self, prompt: str) -> tuple[Path, ...]:
        untracked_paths: list[Path] = []
        seen: set[Path] = set()
        for source_path, relative_path in self._prompt_input_paths(prompt):
            if source_path.is_dir():
                candidates = self._git_stdout(
                    self.project_root,
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "--",
                    relative_path.as_posix(),
                ).splitlines()
                nested_paths = [
                    Path(candidate.strip())
                    for candidate in candidates
                    if candidate.strip()
                ]
            elif self._is_tracked_path(relative_path):
                nested_paths = []
            else:
                nested_paths = [relative_path]
            for nested_path in nested_paths:
                if nested_path in seen:
                    continue
                seen.add(nested_path)
                untracked_paths.append(nested_path)
        return tuple(untracked_paths)

    def _is_tracked_path(self, relative_path: Path) -> bool:
        tracked_paths = self._git_stdout(
            self.project_root,
            "ls-files",
            "--cached",
            "--",
            relative_path.as_posix(),
        )
        return bool(tracked_paths.strip())

    @staticmethod
    def _pathspecs_for_relative_paths(paths: Iterable[Path]) -> tuple[str, ...]:
        rendered: list[str] = []
        seen: set[str] = set()
        for relative_path in paths:
            normalized = relative_path.as_posix().strip("/")
            if not normalized:
                continue
            candidates = (
                normalized,
                f"{normalized}/**",
            )
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                rendered.append(candidate)
        return tuple(rendered)

    def _ensure_workspace_git_excludes(self, workspace_root: Path, patterns: list[str]) -> None:
        exclude_path_value = self._git_stdout(workspace_root, "rev-parse", "--git-path", "info/exclude")
        exclude_path = Path(exclude_path_value)
        if not exclude_path.is_absolute():
            exclude_path = (workspace_root / exclude_path).resolve()
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing_patterns: set[str] = set()
        if exclude_path.exists():
            existing_patterns = {
                line.strip()
                for line in exclude_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        additions = [pattern for pattern in patterns if pattern not in existing_patterns]
        if not additions:
            return
        with exclude_path.open("a", encoding="utf-8") as handle:
            for pattern in additions:
                handle.write(f"{pattern}\n")

    @staticmethod
    def _bot_git_env() -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": _BOT_NAME,
                "GIT_AUTHOR_EMAIL": _BOT_EMAIL,
                "GIT_COMMITTER_NAME": _BOT_NAME,
                "GIT_COMMITTER_EMAIL": _BOT_EMAIL,
            }
        )
        return env

    def _git_stdout(self, cwd: Path, *args: str) -> str:
        process = self._git(cwd, *args)
        return process.stdout.strip()

    def _git_success(self, cwd: Path, *args: str) -> bool:
        process = self._git(cwd, *args, check=False)
        return process.returncode == 0

    def _git(
        self,
        cwd: Path,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if check and process.returncode != 0:
            stderr = process.stderr.strip() or process.stdout.strip()
            raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {stderr}")
        return process
