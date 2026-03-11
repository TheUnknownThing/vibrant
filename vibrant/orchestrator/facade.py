"""Public orchestrator facade used by the UI and future MCP surfaces.

The preferred surface is intentionally small:

- stable reads via ``snapshot()`` and related helpers
- stable user/operator intents such as Gatekeeper messaging and pause/resume

Legacy runtime-driving methods remain available for compatibility during the
orchestrator migration, but they are intentionally routed through internal
bridges instead of being treated as the long-term public contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from vibrant.config import RoadmapExecutionMode
from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapDocument
from vibrant.models.agent import AgentRecord
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.consensus import ConsensusStatus
from vibrant.models.state import OrchestratorStatus

from .lifecycle import CodeAgentLifecycle
from .types import CodeAgentLifecycleResult

_WORKFLOW_TO_CONSENSUS = {
    OrchestratorStatus.INIT: ConsensusStatus.INIT,
    OrchestratorStatus.PLANNING: ConsensusStatus.PLANNING,
    OrchestratorStatus.EXECUTING: ConsensusStatus.EXECUTING,
    OrchestratorStatus.PAUSED: ConsensusStatus.PAUSED,
    OrchestratorStatus.COMPLETED: ConsensusStatus.COMPLETED,
}


@dataclass(frozen=True)
class OrchestratorSnapshot:
    """Stable read model for orchestrator-backed consumers."""

    status: OrchestratorStatus
    pending_questions: tuple[str, ...]
    roadmap: RoadmapDocument | None
    consensus: ConsensusDocument | None
    consensus_path: Path | None
    agent_records: tuple[AgentRecord, ...]
    execution_mode: RoadmapExecutionMode | None
    user_input_banner: str
    notification_bell_enabled: bool


class LegacyOrchestratorStateView:
    """Compatibility view that exposes the legacy state shape."""

    def __init__(self, snapshot: OrchestratorSnapshot, fallback_state: object | None = None) -> None:
        self._snapshot = snapshot
        self._fallback_state = fallback_state

    @property
    def status(self) -> OrchestratorStatus:
        return self._snapshot.status

    @property
    def pending_questions(self) -> list[str]:
        return list(self._snapshot.pending_questions)

    def __getattr__(self, name: str) -> Any:
        if self._fallback_state is None:
            raise AttributeError(name)
        return getattr(self._fallback_state, name)


class LegacyOrchestratorEngineView:
    """Compatibility view that preserves the old ``facade.engine`` access pattern."""

    def __init__(
        self,
        facade: OrchestratorFacade,
        snapshot: OrchestratorSnapshot,
        fallback_engine: object | None = None,
    ) -> None:
        self._facade = facade
        self._fallback_engine = fallback_engine
        fallback_state = getattr(fallback_engine, "state", None)
        self.state = LegacyOrchestratorStateView(snapshot, fallback_state=fallback_state)
        self.agents = self._build_agents(snapshot, fallback_engine)
        self.consensus = snapshot.consensus
        self.consensus_path = snapshot.consensus_path
        self.notification_bell_enabled = snapshot.notification_bell_enabled
        self.USER_INPUT_BANNER = snapshot.user_input_banner

    @staticmethod
    def _build_agents(
        snapshot: OrchestratorSnapshot,
        fallback_engine: object | None,
    ) -> dict[str, AgentRecord]:
        agents = getattr(fallback_engine, "agents", None)
        if isinstance(agents, dict):
            return agents
        return {record.agent_id: record for record in snapshot.agent_records}

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        method = getattr(self._fallback_engine, "can_transition_to", None)
        if callable(method):
            return bool(method(next_status))
        return self._facade.can_transition_to(next_status)

    def refresh_from_disk(self) -> None:
        self._facade.reload_from_disk()
        refreshed = self._facade.snapshot()
        fallback_state = getattr(self._fallback_engine, "state", None)
        self.state = LegacyOrchestratorStateView(refreshed, fallback_state=fallback_state)
        self.agents = self._build_agents(refreshed, self._fallback_engine)
        self.consensus = refreshed.consensus
        self.consensus_path = refreshed.consensus_path
        self.notification_bell_enabled = refreshed.notification_bell_enabled
        self.USER_INPUT_BANNER = refreshed.user_input_banner

    def __getattr__(self, name: str) -> Any:
        if self._fallback_engine is None:
            raise AttributeError(name)
        return getattr(self._fallback_engine, name)


class _LifecycleExecutionCompat:
    """Internal compatibility adapter for runtime-driving lifecycle calls."""

    def __init__(self, lifecycle: CodeAgentLifecycle | Any) -> None:
        self.lifecycle = lifecycle

    def reload_from_disk(self) -> RoadmapDocument:
        reload_from_disk = getattr(self.lifecycle, "reload_from_disk", None)
        if not callable(reload_from_disk):
            raise AttributeError("Lifecycle does not support reload_from_disk")
        return reload_from_disk()

    async def execute_until_blocked(self) -> list[CodeAgentLifecycleResult]:
        execute = getattr(self.lifecycle, "execute_until_blocked", None)
        if not callable(execute):
            raise AttributeError("Lifecycle does not support execute_until_blocked")
        return await execute()

    async def execute_next_task(self) -> CodeAgentLifecycleResult | None:
        execute = getattr(self.lifecycle, "execute_next_task", None)
        if not callable(execute):
            raise AttributeError("Lifecycle does not support execute_next_task")
        return await execute()


class _WorkflowTransitionCompat:
    """Internal transition bridge that prefers services and falls back to the raw engine."""

    def __init__(self, facade: OrchestratorFacade) -> None:
        self.facade = facade
        self.lifecycle = facade.lifecycle

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        state_store = getattr(self.lifecycle, "state_store", None)
        method = getattr(state_store, "can_transition_to", None)
        if callable(method):
            return bool(method(next_status))

        engine = self.facade._engine()
        method = getattr(engine, "can_transition_to", None)
        if not callable(method):
            return False
        return bool(method(next_status))

    def transition_to(self, next_status: OrchestratorStatus) -> None:
        engine = self.facade._engine()
        if engine is None:
            raise RuntimeError("Project lifecycle is not initialized")

        current = getattr(getattr(engine, "state", None), "status", None)
        if current is next_status:
            return
        if not self.can_transition_to(next_status):
            current_value = getattr(current, "value", str(current))
            raise ValueError(f"Invalid orchestrator state transition: {current_value} -> {next_status.value}")

        self._sync_consensus_status(next_status)

        current = getattr(getattr(engine, "state", None), "status", None)
        if current is next_status:
            return
        if not self.can_transition_to(next_status):
            current_value = getattr(current, "value", str(current))
            raise ValueError(f"Invalid orchestrator state transition: {current_value} -> {next_status.value}")

        state_store = getattr(self.lifecycle, "state_store", None)
        transition = getattr(state_store, "transition_to", None)
        if callable(transition):
            transition(next_status)
        else:
            engine.transition_to(next_status)

        refresh = getattr(state_store, "refresh", None)
        if callable(refresh):
            refresh()
        else:
            engine.refresh_from_disk()

    def pause(self) -> None:
        if self.facade.workflow_status() is OrchestratorStatus.PAUSED:
            return
        self.transition_to(OrchestratorStatus.PAUSED)

    def resume(self) -> None:
        current = self.facade.workflow_status()
        if current is OrchestratorStatus.EXECUTING:
            return
        if current is not OrchestratorStatus.PAUSED:
            raise ValueError(f"Cannot resume workflow from {current.value}")
        self.transition_to(OrchestratorStatus.EXECUTING)

    def _sync_consensus_status(self, next_status: OrchestratorStatus) -> None:
        target_consensus_status = _WORKFLOW_TO_CONSENSUS.get(next_status)
        if target_consensus_status is None:
            return

        consensus_service = getattr(self.lifecycle, "consensus_service", None)
        set_status = getattr(consensus_service, "set_status", None)
        if callable(set_status):
            set_status(target_consensus_status)
            return

        engine = self.facade._engine()
        consensus_document = getattr(engine, "consensus", None)
        consensus_path = self.facade._consensus_path(engine)
        if consensus_path is None or not consensus_path.exists():
            return

        document = consensus_document
        if document is None:
            document = ConsensusParser().parse_file(consensus_path)
        updated_document = document.model_copy(deep=True)
        updated_document.status = target_consensus_status
        engine.consensus = ConsensusWriter().write(consensus_path, updated_document)


class OrchestratorFacade:
    """Single entry point for orchestrator-backed app operations.

    Preferred stable surface:

    - read snapshots and small projection helpers
    - Gatekeeper/user intent entrypoints
    - semantic workflow actions such as pause/resume

    Compatibility surface retained during migration:

    - raw ``engine`` passthrough
    - ``reload_from_disk()``
    - ``execute_*`` runtime-driving helpers
    - generic state-transition helpers
    """

    def __init__(self, lifecycle: CodeAgentLifecycle | Any) -> None:
        self.lifecycle = lifecycle
        self.questions = getattr(lifecycle, "question_service", None)
        self._execution_compat = _LifecycleExecutionCompat(lifecycle)
        self._workflow_compat = _WorkflowTransitionCompat(self)

    def _engine(self) -> Any | None:
        return getattr(self.lifecycle, "engine", None)

    def _pending_questions_from_engine(self) -> list[str]:
        engine = self._engine()
        state = getattr(engine, "state", None)
        questions = getattr(state, "pending_questions", None)
        if not questions:
            return []
        return [question for question in questions if isinstance(question, str) and question]

    def _agent_records_from_engine(self) -> tuple[AgentRecord, ...]:
        engine = self._engine()
        agents = getattr(engine, "agents", None)
        if not isinstance(agents, dict):
            return ()
        return tuple(record for record in agents.values() if isinstance(record, AgentRecord))

    @staticmethod
    def _task_summary_timestamp(record: object) -> float:
        started_at = getattr(record, "started_at", None)
        if started_at is not None:
            timestamp = getattr(started_at, "timestamp", None)
            if callable(timestamp):
                return float(timestamp())

        finished_at = getattr(record, "finished_at", None)
        if finished_at is not None:
            timestamp = getattr(finished_at, "timestamp", None)
            if callable(timestamp):
                return float(timestamp())

        return 0.0

    @property
    def engine(self):
        engine = self._engine()
        if engine is not None:
            return engine
        snapshot = self.snapshot()
        return LegacyOrchestratorEngineView(self, snapshot, fallback_engine=None)

    @property
    def roadmap_document(self) -> RoadmapDocument | None:
        return getattr(self.lifecycle, "roadmap_document", None)

    @property
    def execution_mode(self) -> RoadmapExecutionMode | None:
        return self._normalize_execution_mode(getattr(self.lifecycle, "execution_mode", None))

    @staticmethod
    def _normalize_status(value: object) -> OrchestratorStatus:
        if isinstance(value, OrchestratorStatus):
            return value
        if isinstance(value, str):
            try:
                return OrchestratorStatus(value.strip().lower())
            except ValueError:
                pass
        return OrchestratorStatus.INIT

    @staticmethod
    def _normalize_execution_mode(value: object) -> RoadmapExecutionMode | None:
        if isinstance(value, RoadmapExecutionMode):
            return value
        if isinstance(value, str):
            try:
                return RoadmapExecutionMode(value.strip().lower())
            except ValueError:
                return None
        return None

    def snapshot(self) -> OrchestratorSnapshot:
        engine = self._engine()
        state = getattr(engine, "state", None)
        status = getattr(state, "status", OrchestratorStatus.INIT)
        roadmap_document = getattr(self.lifecycle, "roadmap_document", None)
        consensus_document = getattr(engine, "consensus", None)
        consensus_path = getattr(engine, "consensus_path", None)
        if consensus_path is not None:
            consensus_path = Path(consensus_path)

        return OrchestratorSnapshot(
            status=self._normalize_status(status),
            pending_questions=tuple(self.pending_questions()),
            roadmap=roadmap_document,
            consensus=consensus_document if isinstance(consensus_document, ConsensusDocument) else None,
            consensus_path=consensus_path if isinstance(consensus_path, Path) else None,
            agent_records=self._agent_records_from_engine(),
            execution_mode=self.execution_mode,
            user_input_banner=str(
                getattr(engine, "USER_INPUT_BANNER", "⚠ Gatekeeper needs your input — see Chat panel")
            ),
            notification_bell_enabled=bool(getattr(engine, "notification_bell_enabled", False)),
        )

    def workflow_status(self) -> OrchestratorStatus:
        return self.snapshot().status

    def consensus_document(self) -> ConsensusDocument | None:
        return self.snapshot().consensus

    def consensus_source_path(self) -> Path | None:
        return self.snapshot().consensus_path

    def agent_records(self) -> list[AgentRecord]:
        return list(self.snapshot().agent_records)

    def task_summaries(self) -> dict[str, str]:
        by_task: dict[str, tuple[float, str]] = {}
        engine = self._engine()
        if engine is not None and isinstance(getattr(engine, "agents", None), dict):
            records = tuple(engine.agents.values())
        else:
            records = self.snapshot().agent_records

        for record in records:
            summary = getattr(record, "summary", None)
            task_id = getattr(record, "task_id", None)
            if not summary or not isinstance(task_id, str) or not task_id:
                continue
            sort_key = self._task_summary_timestamp(record)
            previous = by_task.get(task_id)
            if previous is None or sort_key >= previous[0]:
                by_task[task_id] = (sort_key, str(summary))
        return {task_id: summary for task_id, (_, summary) in by_task.items()}

    def _consensus_path(self, engine: Any | None) -> Path | None:
        consensus_path = getattr(engine, "consensus_path", None)
        if consensus_path:
            return Path(consensus_path)

        project_root = getattr(self.lifecycle, "project_root", None)
        if project_root:
            return Path(project_root) / ".vibrant" / "consensus.md"

        default_cwd = getattr(getattr(self.lifecycle, "settings", None), "default_cwd", None)
        if default_cwd:
            return Path(default_cwd) / ".vibrant" / "consensus.md"

        return Path(os.getcwd()) / ".vibrant" / "consensus.md"

    def user_input_banner(self) -> str:
        return self.snapshot().user_input_banner

    def notification_bell_enabled(self) -> bool:
        return self.snapshot().notification_bell_enabled

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        consensus_service = getattr(self.lifecycle, "consensus_service", None)
        write = getattr(consensus_service, "write", None)
        if callable(write):
            written = write(document)
            refresh = getattr(getattr(consensus_service, "state_store", None), "refresh", None)
            if callable(refresh):
                refresh()
            return written

        engine = self._engine()
        consensus_path = self._consensus_path(engine)
        if consensus_path is None:
            raise RuntimeError("Consensus path is unavailable")

        written = ConsensusWriter().write(consensus_path, document)
        if engine is not None:
            engine.consensus = written
            refresh = getattr(engine, "refresh_from_disk", None)
            if callable(refresh):
                refresh()
        return written

    async def submit_gatekeeper_message(self, text: str) -> Any:
        submit = getattr(self.lifecycle, "submit_gatekeeper_message", None)
        if callable(submit):
            return await submit(text)
        raise AttributeError("Lifecycle does not support Gatekeeper planning messages")

    async def answer_pending_question(self, answer: str, *, question: str | None = None) -> Any:
        if self.questions is not None:
            return await self.questions.answer(answer, question=question)

        answer_pending = getattr(self.lifecycle, "answer_pending_question", None)
        if callable(answer_pending):
            return await answer_pending(answer, question=question)

        engine = self._engine()
        gatekeeper = getattr(self.lifecycle, "gatekeeper", None)
        if engine is not None and gatekeeper is not None:
            return await engine.answer_pending_question(gatekeeper, answer=answer, question=question)

        raise AttributeError("Lifecycle does not support answering pending Gatekeeper questions")

    def pause_workflow(self) -> None:
        self._workflow_compat.pause()

    def resume_workflow(self) -> None:
        self._workflow_compat.resume()

    def pending_questions(self) -> list[str]:
        if self.questions is not None:
            return self.questions.pending_questions()
        return self._pending_questions_from_engine()

    def current_pending_question(self) -> str | None:
        if self.questions is not None:
            return self.questions.current_question()
        questions = self.pending_questions()
        return questions[0] if questions else None

    def reload_from_disk(self) -> RoadmapDocument:
        return self._execution_compat.reload_from_disk()

    async def execute_until_blocked(self) -> list[CodeAgentLifecycleResult]:
        return await self._execution_compat.execute_until_blocked()

    async def execute_next_task(self) -> CodeAgentLifecycleResult | None:
        return await self._execution_compat.execute_next_task()

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        return self._workflow_compat.can_transition_to(next_status)

    def transition_workflow_state(self, next_status: OrchestratorStatus) -> None:
        self._workflow_compat.transition_to(next_status)


__all__ = [
    "LegacyOrchestratorEngineView",
    "LegacyOrchestratorStateView",
    "OrchestratorFacade",
    "OrchestratorSnapshot",
]
