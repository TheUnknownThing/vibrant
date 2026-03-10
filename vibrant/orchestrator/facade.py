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
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.consensus import ConsensusStatus
from vibrant.models.state import OrchestratorStatus, QuestionPriority, QuestionRecord
from vibrant.models.task import TaskInfo

from .lifecycle import CodeAgentLifecycle
from .types import CodeAgentLifecycleResult, OrchestratorAgentSnapshot

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
    question_records: tuple[QuestionRecord, ...]
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

    @property
    def questions(self) -> list[QuestionRecord]:
        return list(self._snapshot.question_records)

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
        if not self.can_transition_to(next_status):
            current_value = getattr(current, "value", str(current))
            raise ValueError(f"Invalid orchestrator state transition: {current_value} -> {next_status.value}")

        self._sync_consensus_status(next_status)

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

    def _state_store(self) -> Any | None:
        return getattr(self.lifecycle, "state_store", None)

    def _agent_manager(self) -> Any | None:
        return getattr(self.lifecycle, "agent_manager", None)

    def _pending_questions_from_engine(self) -> list[str]:
        state_store = self._state_store()
        pending_questions = getattr(state_store, "pending_questions", None)
        if callable(pending_questions):
            return list(pending_questions())

        engine = self._engine()
        state = getattr(engine, "state", None)
        questions = getattr(state, "pending_questions", None)
        if not questions:
            return []
        return [question for question in questions if isinstance(question, str) and question]

    def _question_records_from_engine(self) -> tuple[QuestionRecord, ...]:
        state_store = self._state_store()
        state = getattr(state_store, "state", None)
        records = getattr(state, "questions", None)
        if isinstance(records, list):
            return tuple(record for record in records if isinstance(record, QuestionRecord))

        engine = self._engine()
        state = getattr(engine, "state", None)
        records = getattr(state, "questions", None)
        if not isinstance(records, list):
            return ()
        return tuple(record for record in records if isinstance(record, QuestionRecord))

    def _agent_records_from_engine(self) -> tuple[AgentRecord, ...]:
        agent_manager = self._agent_manager()
        list_records = getattr(agent_manager, "list_records", None)
        if callable(list_records):
            return tuple(list_records())
        engine = self._engine()
        agents = getattr(engine, "agents", None)
        if not isinstance(agents, dict):
            return ()
        return tuple(record for record in agents.values() if isinstance(record, AgentRecord))

    @staticmethod
    def _normalize_agent_type(value: object) -> AgentType | None:
        if isinstance(value, AgentType):
            return value
        if isinstance(value, str):
            try:
                return AgentType(value.strip().lower())
            except ValueError:
                return None
        return None

    @staticmethod
    def _normalize_agent_status(value: object) -> AgentStatus | None:
        if isinstance(value, AgentStatus):
            return value
        if isinstance(value, str):
            try:
                return AgentStatus(value.strip().lower())
            except ValueError:
                return None
        return None

    def _snapshot_from_record(self, record: AgentRecord) -> OrchestratorAgentSnapshot:
        status = record.status.value
        done = record.status in AgentRecord.TERMINAL_STATUSES
        return OrchestratorAgentSnapshot(
            agent_id=record.agent_id,
            task_id=record.task_id,
            agent_type=record.type.value,
            status=status,
            state=status,
            has_handle=False,
            active=not done,
            done=done,
            awaiting_input=record.status is AgentStatus.AWAITING_INPUT,
            pid=record.pid,
            branch=record.branch,
            worktree_path=record.worktree_path,
            started_at=record.started_at,
            finished_at=record.finished_at,
            summary=record.summary,
            error=record.error,
            provider_thread_id=record.provider.provider_thread_id,
            provider_thread_path=record.provider.thread_path,
            provider_resume_cursor=record.provider.resume_cursor,
            input_requests=[],
            native_event_log=record.provider.native_event_log,
            canonical_event_log=record.provider.canonical_event_log,
        )

    def _coerce_agent_snapshot(self, value: object) -> OrchestratorAgentSnapshot | None:
        if isinstance(value, OrchestratorAgentSnapshot):
            return value
        if isinstance(value, AgentRecord):
            return self._snapshot_from_record(value)

        agent_id = getattr(value, "agent_id", None)
        task_id = getattr(value, "task_id", None)
        if not isinstance(agent_id, str) or not agent_id:
            return None
        if not isinstance(task_id, str) or not task_id:
            return None

        status_value = getattr(value, "status", None)
        state_value = getattr(value, "state", status_value)
        agent_type_value = getattr(value, "agent_type", getattr(value, "type", None))
        status = self._normalize_agent_status(status_value)
        state = self._normalize_agent_status(state_value)
        agent_type = self._normalize_agent_type(agent_type_value)
        done = bool(getattr(value, "done", status in AgentRecord.TERMINAL_STATUSES if status is not None else False))
        awaiting_input = bool(
            getattr(value, "awaiting_input", status is AgentStatus.AWAITING_INPUT or state is AgentStatus.AWAITING_INPUT)
        )
        active = bool(getattr(value, "active", not done))

        return OrchestratorAgentSnapshot(
            agent_id=agent_id,
            task_id=task_id,
            agent_type=agent_type.value if agent_type is not None else str(agent_type_value or "unknown"),
            status=status.value if status is not None else str(status_value or "unknown"),
            state=state.value if state is not None else str(state_value or status_value or "unknown"),
            has_handle=bool(getattr(value, "has_handle", False)),
            active=active,
            done=done,
            awaiting_input=awaiting_input,
            pid=getattr(value, "pid", None),
            branch=getattr(value, "branch", None),
            worktree_path=getattr(value, "worktree_path", None),
            started_at=getattr(value, "started_at", None),
            finished_at=getattr(value, "finished_at", None),
            summary=getattr(value, "summary", None),
            error=getattr(value, "error", None),
            provider_thread_id=getattr(value, "provider_thread_id", None),
            provider_thread_path=getattr(value, "provider_thread_path", None),
            provider_resume_cursor=getattr(value, "provider_resume_cursor", None),
            input_requests=list(getattr(value, "input_requests", []) or []),
            native_event_log=getattr(value, "native_event_log", None),
            canonical_event_log=getattr(value, "canonical_event_log", None),
        )

    def _fallback_agent_snapshots(self) -> list[OrchestratorAgentSnapshot]:
        return [self._snapshot_from_record(record) for record in self._agent_records_from_engine()]

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
        snapshot = self.snapshot()
        return LegacyOrchestratorEngineView(self, snapshot, fallback_engine=engine)

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
        state_store = self._state_store()
        state = getattr(state_store, "state", None) or getattr(engine, "state", None)
        status = getattr(state, "status", OrchestratorStatus.INIT)
        roadmap_document = getattr(self.lifecycle, "roadmap_document", None)
        consensus_document = getattr(state_store, "consensus", None)
        if consensus_document is None:
            consensus_document = getattr(engine, "consensus", None)
        consensus_path = getattr(engine, "consensus_path", None)
        if consensus_path is not None:
            consensus_path = Path(consensus_path)

        user_input_banner = getattr(state_store, "user_input_banner", None)
        notification_bell_enabled = getattr(state_store, "notification_bell_enabled", None)

        return OrchestratorSnapshot(
            status=self._normalize_status(status),
            pending_questions=tuple(self.pending_questions()),
            question_records=tuple(self.question_records()),
            roadmap=roadmap_document,
            consensus=consensus_document if isinstance(consensus_document, ConsensusDocument) else None,
            consensus_path=consensus_path if isinstance(consensus_path, Path) else None,
            agent_records=self._agent_records_from_engine(),
            execution_mode=self.execution_mode,
            user_input_banner=str(
                user_input_banner() if callable(user_input_banner) else getattr(engine, "USER_INPUT_BANNER", "⚠ Gatekeeper needs your input — see Chat panel")
            ),
            notification_bell_enabled=bool(
                notification_bell_enabled() if callable(notification_bell_enabled) else getattr(engine, "notification_bell_enabled", False)
            ),
        )

    def workflow_status(self) -> OrchestratorStatus:
        return self.snapshot().status

    def consensus_document(self) -> ConsensusDocument | None:
        return self.snapshot().consensus

    def roadmap(self) -> RoadmapDocument | None:
        return self.snapshot().roadmap

    def consensus_source_path(self) -> Path | None:
        return self.snapshot().consensus_path

    def agent_records(self) -> list[AgentRecord]:
        return list(self.snapshot().agent_records)

    def get_agent(self, agent_id: str) -> OrchestratorAgentSnapshot | None:
        agent_manager = self._agent_manager()
        get_agent = getattr(agent_manager, "get_agent", None)
        if callable(get_agent):
            return self._coerce_agent_snapshot(get_agent(agent_id))

        for snapshot in self._fallback_agent_snapshots():
            if snapshot.agent_id == agent_id:
                return snapshot
        return None

    def list_agents(
        self,
        *,
        task_id: str | None = None,
        agent_type: AgentType | str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[OrchestratorAgentSnapshot]:
        agent_manager = self._agent_manager()
        list_agents = getattr(agent_manager, "list_agents", None)
        if callable(list_agents):
            snapshots = [
                snapshot
                for item in list_agents(
                    task_id=task_id,
                    agent_type=agent_type,
                    include_completed=include_completed,
                    active_only=active_only,
                )
                if (snapshot := self._coerce_agent_snapshot(item)) is not None
            ]
            return snapshots

        resolved_type = self._normalize_agent_type(agent_type)
        snapshots = self._fallback_agent_snapshots()
        if task_id is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.task_id == task_id]
        if resolved_type is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.agent_type == resolved_type.value]
        if active_only:
            return [snapshot for snapshot in snapshots if snapshot.active]
        if not include_completed:
            return [snapshot for snapshot in snapshots if not snapshot.done or snapshot.awaiting_input]
        return snapshots

    def list_active_agents(self) -> list[OrchestratorAgentSnapshot]:
        return self.list_agents(active_only=True)

    def question_records(self) -> list[QuestionRecord]:
        if self.questions is not None:
            records = getattr(self.questions, "records", None)
            if callable(records):
                return list(records())
        return list(self._question_records_from_engine())

    def pending_question_records(self) -> list[QuestionRecord]:
        if self.questions is not None:
            pending_records = getattr(self.questions, "pending_records", None)
            if callable(pending_records):
                return list(pending_records())
        records = self._question_records_from_engine()
        if records:
            return [record for record in records if record.is_pending()]
        return []

    def task(self, task_id: str) -> TaskInfo | None:
        roadmap_service = getattr(self.lifecycle, "roadmap_service", None)
        get_task = getattr(roadmap_service, "get_task", None)
        if callable(get_task):
            return get_task(task_id)
        roadmap = self.roadmap()
        if roadmap is None:
            return None
        for task in roadmap.tasks:
            if task.id == task_id:
                return task
        return None

    def add_task(self, task: TaskInfo | dict[str, Any], *, index: int | None = None) -> TaskInfo:
        roadmap_service = getattr(self.lifecycle, "roadmap_service", None)
        add_task = getattr(roadmap_service, "add_task", None)
        if not callable(add_task):
            raise AttributeError("Lifecycle does not support roadmap task creation")
        task_info = task if isinstance(task, TaskInfo) else TaskInfo.model_validate(task)
        return add_task(task_info, index=index)

    def update_task(self, task_id: str, **updates: Any) -> TaskInfo:
        roadmap_service = getattr(self.lifecycle, "roadmap_service", None)
        update_task = getattr(roadmap_service, "update_task", None)
        if not callable(update_task):
            raise AttributeError("Lifecycle does not support roadmap task updates")
        return update_task(task_id, **updates)

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        roadmap_service = getattr(self.lifecycle, "roadmap_service", None)
        reorder_tasks = getattr(roadmap_service, "reorder_tasks", None)
        if not callable(reorder_tasks):
            raise AttributeError("Lifecycle does not support roadmap reordering")
        return reorder_tasks(ordered_task_ids)

    def update_consensus(self, **updates: Any) -> ConsensusDocument:
        consensus_service = getattr(self.lifecycle, "consensus_service", None)
        update = getattr(consensus_service, "update", None)
        if not callable(update):
            raise AttributeError("Lifecycle does not support consensus updates")
        return update(**updates)

    def ask_question(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        priority: QuestionPriority = QuestionPriority.BLOCKING,
    ) -> QuestionRecord:
        if self.questions is None:
            raise AttributeError("Lifecycle does not support question creation")
        ask = getattr(self.questions, "ask", None)
        if not callable(ask):
            raise AttributeError("Lifecycle does not support question creation")
        return ask(text, source_agent_id=source_agent_id, source_role=source_role, priority=priority)

    def resolve_question(self, question_id: str, *, answer: str | None = None) -> QuestionRecord:
        if self.questions is None:
            raise AttributeError("Lifecycle does not support question resolution")
        resolve = getattr(self.questions, "resolve", None)
        if not callable(resolve):
            raise AttributeError("Lifecycle does not support question resolution")
        return resolve(question_id, answer=answer)

    def task_summaries(self) -> dict[str, str]:
        by_task: dict[str, tuple[float, str]] = {}
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
            pending_questions = getattr(self.questions, "pending_questions", None)
            if callable(pending_questions):
                return pending_questions()
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
