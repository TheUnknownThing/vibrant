"""Artifact and fixture helpers for backend E2E tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibrant.project_init import initialize_project

if TYPE_CHECKING:
    from pytest import TempPathFactory

    from vibrant.orchestrator.bootstrap import Orchestrator

VIBRANT_E2E_ARTIFACT_ROOT_ENV = "VIBRANT_E2E_ARTIFACT_ROOT"


@dataclass(slots=True)
class E2EProjectContext:
    """Stable project fixture metadata for one backend E2E test."""

    test_name: str
    artifact_key: str
    artifact_root: Path
    project_root: Path
    vibrant_dir: Path
    worktree_root: Path
    manifest_path: Path
    demo_path: Path
    expected_manual_checks: list[str] = field(default_factory=list)

    def git(self, *args: str) -> str:
        """Run ``git`` in the project root and return stdout."""

        completed = subprocess.run(
            ["git", *args],
            cwd=self.project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def update_manifest(self, **fields: Any) -> dict[str, Any]:
        """Merge fields into ``artifact-manifest.json`` and persist it."""

        payload = self._base_manifest()
        if self.manifest_path.exists():
            existing = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload.update(existing)
        payload.update(_json_ready(fields))
        payload["test_name"] = self.test_name
        payload["artifact_key"] = self.artifact_key
        payload["artifact_root"] = str(self.artifact_root)
        payload["project_root"] = str(self.project_root)
        payload["expected_manual_checks"] = list(self.expected_manual_checks)
        self.manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return payload

    def snapshot_orchestrator(self, orchestrator: Orchestrator) -> dict[str, Any]:
        """Persist a manifest snapshot from durable orchestrator state."""

        return self.update_manifest(
            run_ids=[record.identity.run_id for record in orchestrator.agent_run_store.list()],
            conversation_ids=[manifest.conversation_id for manifest in orchestrator.conversation_store.list_manifests()],
            question_ids=[record.question_id for record in orchestrator.question_store.list()],
            attempt_ids=[record.attempt_id for record in orchestrator.attempt_store.list_all()],
            review_ticket_ids=_json_mapping_keys(orchestrator.review_ticket_store.path),
            workspace_ids=[record.workspace_id for record in orchestrator.workspace_store.list_all()],
            diff_paths=sorted(str(path.resolve()) for path in (orchestrator.vibrant_dir / "review-diffs").glob("*.diff")),
        )

    def _base_manifest(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "artifact_key": self.artifact_key,
            "artifact_root": str(self.artifact_root),
            "project_root": str(self.project_root),
            "run_ids": [],
            "conversation_ids": [],
            "question_ids": [],
            "attempt_ids": [],
            "review_ticket_ids": [],
            "workspace_ids": [],
            "diff_paths": [],
            "expected_manual_checks": list(self.expected_manual_checks),
        }


def create_e2e_project_context(
    test_name: str,
    *,
    tmp_path_factory: TempPathFactory,
    artifact_key: str | None = None,
) -> E2EProjectContext:
    """Create and initialize a real project fixture for one E2E test."""

    stable_artifact_key = artifact_key or test_name
    artifact_root = _allocate_artifact_root(stable_artifact_key, tmp_path_factory=tmp_path_factory)
    project_root = artifact_root / "project"
    worktree_root = artifact_root / "worktrees"
    project_root.mkdir(parents=True, exist_ok=True)
    worktree_root.mkdir(parents=True, exist_ok=True)

    vibrant_dir = initialize_project(project_root)
    demo_path = project_root / "demo.txt"
    demo_path.write_text("baseline\n", encoding="utf-8")
    _write_e2e_config(vibrant_dir / "vibrant.toml", worktree_root=worktree_root)
    _initialize_git_repo(project_root)

    context = E2EProjectContext(
        test_name=test_name,
        artifact_key=stable_artifact_key,
        artifact_root=artifact_root,
        project_root=project_root,
        vibrant_dir=vibrant_dir,
        worktree_root=worktree_root,
        manifest_path=artifact_root / "artifact-manifest.json",
        demo_path=demo_path,
        expected_manual_checks=[
            str(vibrant_dir / "attempts.json"),
            str(vibrant_dir / "questions.json"),
            str(vibrant_dir / "reviews.json"),
            str(vibrant_dir / "workspaces.json"),
            str(vibrant_dir / "agent-runs"),
            str(vibrant_dir / "logs" / "providers" / "native"),
            str(vibrant_dir / "logs" / "providers" / "canonical"),
            str(vibrant_dir / "conversations"),
            str(vibrant_dir / "review-diffs"),
            str(project_root / "demo.txt"),
            str(worktree_root),
        ],
    )
    context.update_manifest()
    return context


def _allocate_artifact_root(artifact_key: str, *, tmp_path_factory: TempPathFactory) -> Path:
    env_root = os.environ.get(VIBRANT_E2E_ARTIFACT_ROOT_ENV)
    sanitized_key = sanitize_test_name(artifact_key)
    if env_root:
        base_root = Path(env_root).expanduser().resolve()
        artifact_root = base_root / sanitized_key
        if artifact_root.exists():
            shutil.rmtree(artifact_root)
        artifact_root.mkdir(parents=True, exist_ok=True)
        return artifact_root
    return tmp_path_factory.mktemp(sanitized_key)


def sanitize_test_name(value: str) -> str:
    """Return a filesystem-friendly artifact directory name."""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return normalized or "vibrant-e2e"


def _initialize_git_repo(project_root: Path) -> None:
    _git(project_root, "init", "-b", "main")
    _git(project_root, "config", "user.name", "Vibrant E2E Tests")
    _git(project_root, "config", "user.email", "vibrant-e2e@example.com")
    _git(project_root, "add", ".")
    _git(project_root, "commit", "-m", "Initialize E2E project fixture")


def _write_e2e_config(path: Path, *, worktree_root: Path) -> None:
    content = "\n".join(
        [
            "[provider]",
            'kind = "codex"',
            'codex-binary = "codex"',
            "launch-args = []",
            'model = "gpt-5.3-codex"',
            'approval-policy = "never"',
            'reasoning-effort = "medium"',
            'reasoning-summary = "auto"',
            'sandbox-mode = "workspace-write"',
            "",
            "[orchestrator]",
            "concurrency-limit = 1",
            "agent-timeout-seconds = 1500",
            f"worktree-directory = {json.dumps(str(worktree_root))}",
            'conversation-directory = ".vibrant/conversations"',
            'execution-mode = "automatic"',
            "",
            "[validation]",
            "test-commands = []",
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")


def _git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _json_mapping_keys(path: Path) -> list[str]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    return sorted(key for key in payload if isinstance(key, str))


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    return value
