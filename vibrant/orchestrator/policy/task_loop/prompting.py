"""Prompt construction policy for task execution."""

from __future__ import annotations

from vibrant.consensus.roadmap import RoadmapParser
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.task import TaskInfo

from ...basic.stores import ConsensusStore, RoadmapStore
from .models import DispatchLease, PreparedTaskExecution


def build_task_prompt(
    *,
    task: TaskInfo,
    consensus: ConsensusDocument | None,
    project_name: str,
) -> str:
    consensus_document = consensus or ConsensusDocument(project=project_name)
    return RoadmapParser().build_task_prompt(task, consensus_document)


def prepare_task_execution(
    *,
    lease: DispatchLease,
    roadmap_store: RoadmapStore,
    consensus_store: ConsensusStore,
    project_name: str,
) -> PreparedTaskExecution:
    task = roadmap_store.get_task(lease.task_id)
    if task is None:
        raise KeyError(f"Task not found: {lease.task_id}")
    prompt = build_task_prompt(
        task=task,
        consensus=consensus_store.load(),
        project_name=project_name,
    )
    return PreparedTaskExecution(
        lease=lease,
        task=task,
        prompt=prompt,
    )


def retry_definition_patch(
    *,
    prompt_patch: str | None,
    acceptance_patch: list[str] | None,
) -> dict[str, object]:
    patch: dict[str, object] = {}
    if prompt_patch is not None:
        patch["prompt"] = prompt_patch
    if acceptance_patch is not None:
        patch["acceptance_criteria"] = acceptance_patch
    return patch
