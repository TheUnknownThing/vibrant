"""Task-loop role and instance policy."""

from __future__ import annotations

from vibrant.models.agent import AgentInstanceProviderConfig, AgentInstanceRecord
from vibrant.models.task import TaskInfo

from ...basic.stores import AgentInstanceStore

DEFAULT_TASK_AGENT_ROLE = "code"


def resolve_task_agent_role(task: TaskInfo) -> str:
    return task.agent_role or DEFAULT_TASK_AGENT_ROLE


def ensure_task_agent_instance(
    store: AgentInstanceStore,
    *,
    task: TaskInfo,
    provider: AgentInstanceProviderConfig,
) -> AgentInstanceRecord:
    role = resolve_task_agent_role(task)
    existing = store.find(role=role, scope_type="task", scope_id=task.id)
    if existing is not None:
        return existing

    record = AgentInstanceRecord(
        identity={
            "agent_id": f"task-{task.id}-{role}",
            "role": role,
        },
        scope={
            "scope_type": "task",
            "scope_id": task.id,
        },
        provider=provider,
    )
    store.upsert(record)
    return record
