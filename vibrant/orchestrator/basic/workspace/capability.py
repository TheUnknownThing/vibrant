"""Workspace capability wrapper."""

from __future__ import annotations

from dataclasses import dataclass

from ...types import DiffArtifact, MergeOutcome, WorkspaceHandle
from .service import WorkspaceService


@dataclass(slots=True)
class WorkspaceCapability:
    service: WorkspaceService

    def prepare_task_workspace(self, task_id: str, *, branch_hint: str | None = None) -> WorkspaceHandle:
        return self.service.prepare_task_workspace(task_id, branch_hint=branch_hint)

    def get_workspace(self, *, task_id: str, workspace_id: str) -> WorkspaceHandle:
        return self.service.get_workspace(task_id=task_id, workspace_id=workspace_id)

    def collect_review_diff(self, workspace: WorkspaceHandle) -> DiffArtifact:
        return self.service.collect_review_diff(workspace)

    def merge_task_result(self, workspace: WorkspaceHandle) -> MergeOutcome:
        return self.service.merge_task_result(workspace)
