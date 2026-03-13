"""Gatekeeper-specific facade helpers for TUI widgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vibrant.models.state import OrchestratorStatus, QuestionRecord
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.orchestrator.types import AgentInstanceSnapshot, AgentOutput, AgentRunSnapshot


@dataclass(slots=True, frozen=True)
class GatekeeperSnapshot:
    """Facade-backed Gatekeeper view used by the TUI chat panel."""

    workflow_status: OrchestratorStatus | None
    questions: tuple[QuestionRecord, ...]
    pending_questions: tuple[QuestionRecord, ...]
    instance: AgentInstanceSnapshot | None
    runs: tuple[AgentRunSnapshot, ...]
    output: AgentOutput | None
    provider_thread_id: str | None


def get_gatekeeper_snapshot(facade: OrchestratorFacade | None) -> GatekeeperSnapshot:
    """Return the current Gatekeeper-facing facade snapshot for the TUI."""

    if facade is None:
        return GatekeeperSnapshot(
            workflow_status=None,
            questions=(),
            pending_questions=(),
            instance=None,
            runs=(),
            output=None,
            provider_thread_id=None,
        )

    instance = get_gatekeeper_instance(facade)
    runs = tuple(_sorted_runs(facade, instance))
    questions = tuple(record for record in facade.questions.list() if record.source_role == "gatekeeper")
    pending_questions = tuple(record for record in questions if record.is_pending())
    output = facade.instances.output(instance.agent_id) if instance is not None else None
    provider_thread_id = _provider_thread_id(instance, runs)

    return GatekeeperSnapshot(
        workflow_status=facade.workflow.status(),
        questions=questions,
        pending_questions=pending_questions,
        instance=instance,
        runs=runs,
        output=output,
        provider_thread_id=provider_thread_id,
    )


def get_gatekeeper_instance(facade: OrchestratorFacade) -> AgentInstanceSnapshot | None:
    """Return the best current Gatekeeper instance, if one exists."""

    candidates = facade.instances.list(role="gatekeeper", include_completed=True)
    if not candidates:
        return None
    return max(candidates, key=_instance_sort_key)


def _sorted_runs(
    facade: OrchestratorFacade,
    instance: AgentInstanceSnapshot | None,
) -> list[AgentRunSnapshot]:
    if instance is None:
        return []
    runs = facade.runs.for_instance(instance.agent_id)
    runs.sort(key=_run_sort_key)
    return runs


def _provider_thread_id(
    instance: AgentInstanceSnapshot | None,
    runs: tuple[AgentRunSnapshot, ...],
) -> str | None:
    if instance is not None and instance.provider.thread_id:
        return instance.provider.thread_id

    for run in reversed(runs):
        if run.provider.thread_id:
            return run.provider.thread_id
        if run.envelope.provider_thread_id:
            return run.envelope.provider_thread_id
    return None


def _instance_sort_key(instance: AgentInstanceSnapshot) -> tuple[int, int, int, float]:
    timestamp = _timestamp_value(instance.runtime.finished_at or instance.runtime.started_at)
    return (
        1 if instance.identity.scope_type == "project" else 0,
        1 if instance.runtime.active else 0,
        1 if instance.runtime.awaiting_input else 0,
        timestamp,
    )


def _run_sort_key(run: AgentRunSnapshot) -> float:
    return _timestamp_value(run.lifecycle.started_at or run.lifecycle.finished_at)


def _timestamp_value(value: datetime | None) -> float:
    if value is None:
        return 0.0
    return value.timestamp()
