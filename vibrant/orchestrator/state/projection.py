"""State projection helpers for orchestrator-owned derived fields."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from vibrant.models.agent import AgentRecord, AgentStatus
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import (
    GatekeeperStatus,
    OrchestratorState,
    OrchestratorStatus,
    ProviderRuntimeState,
)
from vibrant.providers.base import CanonicalEvent
from vibrant.orchestrator.agents.catalog import build_builtin_role_catalog


_ROLE_CATALOG = build_builtin_role_catalog()


def rebuild_derived_state(
    state: OrchestratorState,
    *,
    agent_records: Iterable[AgentRecord],
    consensus: ConsensusDocument | None,
) -> None:
    active_agents: list[str] = []
    completed_tasks: list[str] = []
    failed_tasks: list[str] = []
    provider_runtime: dict[str, ProviderRuntimeState] = {}
    active_gatekeeper = False

    for record in agent_records:
        if record.lifecycle.status not in AgentRecord.TERMINAL_STATUSES:
            active_agents.append(record.identity.agent_id)
            role_spec = _ROLE_CATALOG.try_get(record.identity.role)
            if role_spec is not None and role_spec.contributes_control_plane_status:
                active_gatekeeper = True

        if record.lifecycle.status is AgentStatus.COMPLETED:
            completed_tasks.append(record.identity.task_id)
        elif record.lifecycle.status in {AgentStatus.FAILED, AgentStatus.KILLED}:
            failed_tasks.append(record.identity.task_id)

        provider_thread_id = record.provider.provider_thread_id or _extract_provider_thread_id(record.provider.resume_cursor)
        if provider_thread_id or record.lifecycle.status not in AgentRecord.TERMINAL_STATUSES:
            provider_runtime[record.identity.agent_id] = ProviderRuntimeState(
                status=record.lifecycle.status.value,
                provider_thread_id=provider_thread_id,
            )

    state.active_agents = active_agents
    state.completed_tasks = _dedupe_preserving_order(completed_tasks)
    state.failed_tasks = _dedupe_preserving_order(failed_tasks)
    state.provider_runtime = provider_runtime

    if consensus is not None:
        state.last_consensus_version = consensus.version
        state.sync_pending_question_projection()
        if state.status is OrchestratorStatus.INIT:
            inferred_status = _consensus_to_orchestrator_status(consensus.status)
            state.status = inferred_status
    else:
        state.sync_pending_question_projection()

    if state.pending_questions:
        state.gatekeeper_status = GatekeeperStatus.AWAITING_USER
    elif active_gatekeeper:
        state.gatekeeper_status = GatekeeperStatus.RUNNING
    else:
        state.gatekeeper_status = GatekeeperStatus.IDLE


def sync_status_from_consensus(
    state: OrchestratorState,
    *,
    consensus: ConsensusDocument | None,
    can_transition_to: Callable[[OrchestratorStatus], bool],
) -> None:
    if consensus is None:
        return

    inferred_status = _consensus_to_orchestrator_status(consensus.status)
    if inferred_status is state.status:
        return
    if can_transition_to(inferred_status):
        state.status = inferred_status


def build_user_input_requested_event(
    questions: list[str],
    *,
    banner_text: str,
    terminal_bell: bool,
) -> CanonicalEvent:
    return {
        "type": "user-input.requested",
        "timestamp": _timestamp_now(),
        "origin": "orchestrator",
        "questions": list(questions),
        "banner_text": banner_text,
        "terminal_bell": terminal_bell,
    }



def _consensus_to_orchestrator_status(status: ConsensusStatus) -> OrchestratorStatus:
    mapping = {
        ConsensusStatus.INIT: OrchestratorStatus.INIT,
        ConsensusStatus.PLANNING: OrchestratorStatus.PLANNING,
        ConsensusStatus.EXECUTING: OrchestratorStatus.EXECUTING,
        ConsensusStatus.PAUSED: OrchestratorStatus.PAUSED,
        ConsensusStatus.COMPLETED: OrchestratorStatus.COMPLETED,
    }
    try:
        return mapping[status]
    except KeyError as exc:
        raise ValueError(f"Unsupported consensus status: {status!r}") from exc



def _dedupe_preserving_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))



def _extract_provider_thread_id(resume_cursor: object) -> str | None:
    if not isinstance(resume_cursor, dict):
        return None
    thread_id = resume_cursor.get("threadId")
    return thread_id if isinstance(thread_id, str) and thread_id else None



def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
