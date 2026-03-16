"""Task-loop role and instance policy."""

from __future__ import annotations

from vibrant.models.agent import AgentInstanceProviderConfig, AgentInstanceRecord
from vibrant.models.task import TaskInfo

from ...basic.stores import AgentInstanceStore

DEFAULT_TASK_AGENT_ROLE = "code"
DEFAULT_VALIDATION_AGENT_ROLE = "test"


def resolve_task_agent_role(task: TaskInfo) -> str:
    return task.agent_role or DEFAULT_TASK_AGENT_ROLE


def ensure_task_agent_instance(
    store: AgentInstanceStore,
    *,
    task: TaskInfo,
    provider: AgentInstanceProviderConfig,
) -> AgentInstanceRecord:
    role = resolve_task_agent_role(task)
    return _ensure_scoped_agent_instance(
        store,
        task_id=task.id,
        role=role,
        provider=provider,
    )


def ensure_validation_agent_instance(
    store: AgentInstanceStore,
    *,
    task_id: str,
    provider: AgentInstanceProviderConfig,
    role: str = DEFAULT_VALIDATION_AGENT_ROLE,
) -> AgentInstanceRecord:
    return _ensure_scoped_agent_instance(
        store,
        task_id=task_id,
        role=role,
        provider=provider,
    )


def _ensure_scoped_agent_instance(
    store: AgentInstanceStore,
    *,
    task_id: str,
    role: str,
    provider: AgentInstanceProviderConfig,
) -> AgentInstanceRecord:
    existing = store.find(role=role, scope_type="task", scope_id=task_id)
    if existing is not None:
        return existing

    record = AgentInstanceRecord(
        identity={
            "agent_id": f"task-{task_id}-{role}",
            "role": role,
        },
        scope={
            "scope_type": "task",
            "scope_id": task_id,
        },
        provider=provider,
    )
    store.upsert(record)
    return record
