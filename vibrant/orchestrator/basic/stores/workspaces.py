"""Workspace metadata persistence."""

from __future__ import annotations

from pathlib import Path

from ..repository import JsonDataclassMappingRepository
from ...types import WorkspaceHandle, WorkspaceKind, WorkspaceStatus, utc_now


class _Unset:
    pass


_UNSET = _Unset()


class WorkspaceStore:
    """Persist workspace metadata in ``.vibrant/workspaces.json``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._repository = JsonDataclassMappingRepository(
            self.path,
            record_type=WorkspaceHandle,
            key_for=lambda record: record.workspace_id,
            key_field="workspace_id",
            normalize_payload=_normalize_workspace_payload,
        )

    def create(self, record: WorkspaceHandle) -> WorkspaceHandle:
        return self._repository.upsert(record)

    def get(self, workspace_id: str) -> WorkspaceHandle | None:
        return self._repository.get(workspace_id)

    def list_all(self) -> list[WorkspaceHandle]:
        return self._repository.list()

    def update(
        self,
        workspace_id: str,
        *,
        attempt_id: str | None | _Unset = _UNSET,
        path: str | None = None,
        branch: str | None = None,
        base_branch: str | None = None,
        kind: WorkspaceKind | None = None,
        target_ref: str | None = None,
        base_commit: str | None | _Unset = _UNSET,
        result_commit: str | None | _Unset = _UNSET,
        integration_commit: str | None | _Unset = _UNSET,
        status: WorkspaceStatus | None = None,
    ) -> WorkspaceHandle:
        record = self.get(workspace_id)
        if record is None:
            raise KeyError(f"Unknown workspace: {workspace_id}")

        if attempt_id is not _UNSET:
            record.attempt_id = _optional_string(attempt_id)
        if path is not None:
            record.path = path
        if branch is not None:
            record.branch = branch
        if base_branch is not None:
            record.base_branch = base_branch
        if kind is not None:
            record.kind = kind
        if target_ref is not None:
            record.target_ref = target_ref
        if base_commit is not _UNSET:
            record.base_commit = _optional_string(base_commit)
        if result_commit is not _UNSET:
            record.result_commit = _optional_string(result_commit)
        if integration_commit is not _UNSET:
            record.integration_commit = _optional_string(integration_commit)
        if status is not None:
            record.status = status
        record.updated_at = utc_now()
        return self._repository.upsert(record)


def _normalize_workspace_payload(payload: dict[str, object]) -> dict[str, object] | None:
    try:
        target_ref = _optional_string(payload.get("target_ref")) or _optional_string(payload.get("base_branch")) or ""
        return {
            "workspace_id": str(payload["workspace_id"]),
            "task_id": str(payload["task_id"]),
            "path": str(payload["path"]),
            "branch": str(payload["branch"]),
            "base_branch": _optional_string(payload.get("base_branch")) or target_ref,
            "attempt_id": _optional_string(payload.get("attempt_id")),
            "kind": WorkspaceKind(str(payload.get("kind", WorkspaceKind.TASK.value))),
            "target_ref": target_ref,
            "base_commit": _optional_string(payload.get("base_commit")),
            "result_commit": _optional_string(payload.get("result_commit")),
            "integration_commit": _optional_string(payload.get("integration_commit")),
            "status": WorkspaceStatus(str(payload.get("status", WorkspaceStatus.ACTIVE.value))),
            "created_at": str(payload.get("created_at") or utc_now()),
            "updated_at": str(payload.get("updated_at") or payload.get("created_at") or utc_now()),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None
