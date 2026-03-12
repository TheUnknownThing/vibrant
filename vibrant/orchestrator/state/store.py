"""State-store service backed by the orchestrator state backend."""

from __future__ import annotations

from vibrant.agents.gatekeeper import GatekeeperRunResult
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.state import GatekeeperStatus, OrchestratorState, OrchestratorStatus, reconcile_question_records
from vibrant.orchestrator.agents.catalog import build_builtin_role_catalog
from vibrant.providers.base import CanonicalEvent
from .backend import OrchestratorStateBackend

from .projection import build_user_input_requested_event, rebuild_derived_state, sync_status_from_consensus


if False:  # pragma: no cover
    from ..agents.store import AgentRecordStore


_ROLE_CATALOG = build_builtin_role_catalog()
_DEFAULT_QUESTION_SOURCE_ROLE = _ROLE_CATALOG.default_question_source_role()


class StateStore:
    """Thin service boundary around durable backend-backed orchestrator state."""

    def __init__(self, engine: OrchestratorStateBackend) -> None:
        self.engine = engine
        self._agent_store: AgentRecordStore | None = None

    def bind_agent_store(self, agent_store: AgentRecordStore) -> None:
        self._agent_store = agent_store

    @property
    def state(self) -> OrchestratorState:
        return self.engine.state

    @property
    def status(self) -> OrchestratorStatus:
        return self.engine.state.status

    @property
    def consensus(self) -> ConsensusDocument | None:
        return self.engine.consensus

    def refresh(self) -> None:
        self.engine.refresh_from_disk()
        if self._agent_store is not None:
            self._agent_store.refresh()

    def persist(self) -> None:
        self.engine.persist_state()

    def pending_questions(self) -> list[str]:
        return [record.text for record in self.state.pending_question_records()]

    def has_pending_questions(self) -> bool:
        return bool(self.pending_questions())

    def active_agent_ids(self) -> list[str]:
        return list(self.state.active_agents)

    def has_active_agents(self) -> bool:
        return bool(self.state.active_agents)

    def user_input_banner(self) -> str:
        return getattr(self.engine, "USER_INPUT_BANNER", "⚠ Gatekeeper needs your input — see Chat panel")

    def notification_bell_enabled(self) -> bool:
        return bool(getattr(self.engine, "notification_bell_enabled", False))

    def agent_records(self) -> list:
        return list(self._agent_records())

    def increment_total_agent_spawns(self) -> None:
        self.engine.state.total_agent_spawns += 1

    def set_consensus(self, document: ConsensusDocument | None) -> None:
        self.engine.consensus = document

    def set_gatekeeper_status(self, status: GatekeeperStatus) -> None:
        self.engine.state.gatekeeper_status = status
        self.persist()

    def set_status(self, status: OrchestratorStatus) -> None:
        self.engine.state.status = status
        self.persist()

    def append_event(self, event: CanonicalEvent) -> None:
        self.engine.emitted_events.append(event)
        self.persist()

    def rebuild_derived_state(self) -> None:
        rebuild_derived_state(
            self.state,
            agent_records=self._agent_records(),
            consensus=self.consensus,
        )
        self.persist()

    def sync_status_from_consensus(self) -> None:
        sync_status_from_consensus(
            self.state,
            consensus=self.consensus,
            can_transition_to=self.can_transition_to,
        )

    def apply_gatekeeper_result(self, result: GatekeeperRunResult) -> list[CanonicalEvent]:
        if result.agent_record is not None:
            increment_spawn = self._agent_store is None or result.agent_record.identity.run_id not in self._agent_store
            if self._agent_store is not None:
                self._agent_store.upsert(
                    result.agent_record,
                    increment_spawn=increment_spawn,
                    rebuild_state=False,
                )
            else:
                self.engine.upsert_agent_record(result.agent_record, increment_spawn=increment_spawn)

        self.engine.reload_consensus()

        self.rebuild_derived_state()
        self.sync_status_from_consensus()

        events: list[CanonicalEvent] = []
        pending_messages = [
            request.message or f"Gatekeeper request: {request.request_kind}"
            for request in result.input_requests
        ]
        if pending_messages:
            source_role = _DEFAULT_QUESTION_SOURCE_ROLE
            if result.agent_record is not None:
                role_spec = _ROLE_CATALOG.try_get(result.agent_record.identity.role)
                if role_spec is not None and role_spec.question_source_role is not None:
                    source_role = role_spec.question_source_role
            self.engine.state.replace_questions(
                reconcile_question_records(
                    self.engine.state.questions,
                    pending_messages,
                    source_agent_id=result.agent_record.identity.agent_id if result.agent_record is not None else None,
                    source_role=source_role,
                )
            )
            self.engine.state.gatekeeper_status = GatekeeperStatus.AWAITING_USER
            event = build_user_input_requested_event(
                pending_messages,
                banner_text=self.user_input_banner(),
                terminal_bell=self.notification_bell_enabled(),
            )
            events.append(event)
            self.engine.emitted_events.append(event)
        elif not self.engine.state.pending_questions and self.engine.state.gatekeeper_status is not GatekeeperStatus.RUNNING:
            self.engine.state.gatekeeper_status = GatekeeperStatus.IDLE

        self.persist()
        return events

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        return self.engine.can_transition_to(next_status)

    def transition_to(self, next_status: OrchestratorStatus) -> None:
        self.engine.transition_to(next_status)

    def _agent_records(self):
        if self._agent_store is not None:
            return self._agent_store.list_records()
        list_agent_records = getattr(self.engine, "list_agent_records", None)
        if callable(list_agent_records):
            return list(list_agent_records())
        return []
