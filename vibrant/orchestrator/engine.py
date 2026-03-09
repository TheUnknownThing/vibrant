"""Orchestrator workflow state machine and recovery helpers."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from vibrant.config import DEFAULT_CONFIG_DIR, find_project_root, load_config
from vibrant.consensus.parser import ConsensusParser
from vibrant.gatekeeper.gatekeeper import Gatekeeper, GatekeeperRunResult
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import GatekeeperStatus, OrchestratorState, OrchestratorStatus, ProviderRuntimeState
from vibrant.project_init import ensure_project_files


class OrchestratorEngine:
    """Manage workflow-state transitions and durable runtime state."""

    USER_INPUT_BANNER = "⚠ Gatekeeper needs your input — see Chat panel"
    ALLOWED_TRANSITIONS: dict[OrchestratorStatus, set[OrchestratorStatus]] = {
        OrchestratorStatus.INIT: {OrchestratorStatus.PLANNING},
        OrchestratorStatus.PLANNING: {OrchestratorStatus.EXECUTING, OrchestratorStatus.PAUSED},
        OrchestratorStatus.EXECUTING: {
            OrchestratorStatus.PLANNING,
            OrchestratorStatus.VALIDATING,
            OrchestratorStatus.PAUSED,
            OrchestratorStatus.COMPLETED,
        },
        OrchestratorStatus.VALIDATING: {OrchestratorStatus.COMPLETED},
        OrchestratorStatus.PAUSED: {
            OrchestratorStatus.PLANNING,
            OrchestratorStatus.EXECUTING,
            OrchestratorStatus.COMPLETED,
        },
        OrchestratorStatus.COMPLETED: set(),
    }

    def __init__(
        self,
        project_root: str | Path,
        *,
        state: OrchestratorState,
        agents: dict[str, AgentRecord] | None = None,
        consensus: ConsensusDocument | None = None,
        notification_bell_enabled: bool = True,
    ) -> None:
        self.project_root = find_project_root(project_root)
        self.vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        self.state_path = self.vibrant_dir / "state.json"
        self.agents_dir = self.vibrant_dir / "agents"
        self.consensus_path = self.vibrant_dir / "consensus.md"

        self.state = state
        self.agents = agents or {}
        self.consensus = consensus
        self.notification_bell_enabled = notification_bell_enabled
        self.emitted_events: list[dict[str, object]] = []

    @classmethod
    def load(
        cls,
        project_root: str | Path,
        *,
        notification_bell_enabled: bool = True,
    ) -> OrchestratorEngine:
        """Load durable runtime state and reconstruct derived fields from disk."""

        root = find_project_root(project_root)
        vibrant_dir = root / DEFAULT_CONFIG_DIR
        if not vibrant_dir.exists():
            raise FileNotFoundError(f"Vibrant project directory not found: {vibrant_dir}")
        ensure_project_files(root)

        state = cls._load_state(root)
        agents = cls._load_agents(vibrant_dir / "agents")
        consensus = cls._load_consensus(vibrant_dir / "consensus.md")

        engine = cls(
            root,
            state=state,
            agents=agents,
            consensus=consensus,
            notification_bell_enabled=notification_bell_enabled,
        )
        engine._reconstruct_state()
        engine.persist_state()
        return engine

    @classmethod
    def create(
        cls,
        project_root: str | Path,
        *,
        notification_bell_enabled: bool = True,
    ) -> OrchestratorEngine:
        """Create a new engine using the project configuration defaults."""

        root = find_project_root(project_root)
        config = load_config(start_path=root)
        state = OrchestratorState(
            session_id=str(uuid4()),
            status=OrchestratorStatus.INIT,
            concurrency_limit=config.concurrency_limit,
        )
        engine = cls(root, state=state, notification_bell_enabled=notification_bell_enabled)
        engine.persist_state()
        return engine

    @staticmethod
    def _load_state(project_root: Path) -> OrchestratorState:
        state_path = project_root / DEFAULT_CONFIG_DIR / "state.json"
        if state_path.exists():
            return OrchestratorState.model_validate_json(state_path.read_text(encoding="utf-8"))

        config = load_config(start_path=project_root)
        return OrchestratorState(
            session_id=str(uuid4()),
            status=OrchestratorStatus.INIT,
            concurrency_limit=config.concurrency_limit,
        )

    @staticmethod
    def _load_agents(agents_dir: Path) -> dict[str, AgentRecord]:
        if not agents_dir.exists():
            return {}

        records: dict[str, AgentRecord] = {}
        for path in sorted(agents_dir.glob("*.json")):
            record = AgentRecord.model_validate_json(path.read_text(encoding="utf-8"))
            records[record.agent_id] = record
        return records

    @staticmethod
    def _load_consensus(consensus_path: Path) -> ConsensusDocument | None:
        if not consensus_path.exists():
            return None
        return ConsensusParser().parse_file(consensus_path)

    def can_transition_to(self, next_status: OrchestratorStatus) -> bool:
        """Return whether the current workflow state may transition."""

        return next_status in self.ALLOWED_TRANSITIONS[self.state.status]

    def transition_to(self, next_status: OrchestratorStatus) -> None:
        """Transition to a new workflow state and persist immediately."""

        if not self.can_transition_to(next_status):
            raise ValueError(
                f"Invalid orchestrator state transition: {self.state.status.value} -> {next_status.value}"
            )

        self.state.status = next_status
        self.persist_state()

    def persist_state(self) -> None:
        """Write ``state.json`` atomically using a temp file and rename."""

        self.vibrant_dir.mkdir(parents=True, exist_ok=True)
        payload = self.state.model_dump_json(indent=2) + "\n"
        _atomic_write_text(self.state_path, payload)

    def upsert_agent_record(
        self,
        record: AgentRecord,
        *,
        increment_spawn: bool = False,
    ) -> Path:
        """Persist one agent record and refresh derived orchestrator state."""

        if increment_spawn and record.agent_id not in self.agents:
            self.state.total_agent_spawns += 1

        self.agents_dir.mkdir(parents=True, exist_ok=True)
        path = self.agents_dir / f"{record.agent_id}.json"
        _atomic_write_text(path, record.model_dump_json(indent=2) + "\n")

        self.agents[record.agent_id] = record
        self._reconstruct_state()
        self.persist_state()
        return path

    def register_agent(self, record: AgentRecord) -> None:
        """Track an agent record in memory and refresh derived state."""

        self.upsert_agent_record(record)

    def refresh_from_disk(self) -> None:
        """Reload agent records and consensus metadata from disk."""

        self.agents = self._load_agents(self.agents_dir)
        self.consensus = self._load_consensus(self.consensus_path)
        self._reconstruct_state()
        self.persist_state()

    def apply_gatekeeper_result(self, result: GatekeeperRunResult) -> list[dict[str, object]]:
        """Persist Gatekeeper outputs into durable orchestrator state."""

        if result.agent_record is not None:
            self.upsert_agent_record(
                result.agent_record,
                increment_spawn=result.agent_record.agent_id not in self.agents,
            )
        if result.consensus_document is not None:
            self.consensus = result.consensus_document

        self._reconstruct_state()
        self._sync_status_from_consensus()
        events: list[dict[str, object]] = []
        if result.questions:
            event = self._build_user_input_requested_event(result.questions)
            events.append(event)
            self.emitted_events.append(event)
        else:
            self.state.gatekeeper_status = GatekeeperStatus.IDLE

        self.persist_state()
        return events

    async def answer_pending_question(
        self,
        gatekeeper: Gatekeeper,
        *,
        answer: str,
        question: str | None = None,
    ) -> GatekeeperRunResult:
        """Forward a user answer back to Gatekeeper and refresh state."""

        selected_question = question or (self.state.pending_questions[0] if self.state.pending_questions else None)
        if not selected_question:
            raise ValueError("No pending Gatekeeper question to answer")

        self.state.gatekeeper_status = GatekeeperStatus.RUNNING
        self.persist_state()

        result = await gatekeeper.answer_question(selected_question, answer)
        self.apply_gatekeeper_result(result)

        resolved_event = {
            "type": "user-input.resolved",
            "timestamp": _timestamp_now(),
            "question": selected_question,
            "answer": answer,
        }
        self.emitted_events.append(resolved_event)
        self.persist_state()
        return result

    def _reconstruct_state(self) -> None:
        active_agents: list[str] = []
        completed_tasks: list[str] = []
        failed_tasks: list[str] = []
        provider_runtime: dict[str, ProviderRuntimeState] = {}
        active_gatekeeper = False

        for record in self.agents.values():
            if record.status not in AgentRecord.TERMINAL_STATUSES:
                active_agents.append(record.agent_id)
                if record.type is AgentType.GATEKEEPER:
                    active_gatekeeper = True

            if record.status is AgentStatus.COMPLETED:
                completed_tasks.append(record.task_id)
            elif record.status in {AgentStatus.FAILED, AgentStatus.KILLED}:
                failed_tasks.append(record.task_id)

            provider_thread_id = record.provider.provider_thread_id or _extract_provider_thread_id(
                record.provider.resume_cursor
            )
            if provider_thread_id or record.status not in AgentRecord.TERMINAL_STATUSES:
                provider_runtime[record.agent_id] = ProviderRuntimeState(
                    status=record.status.value,
                    provider_thread_id=provider_thread_id,
                )

        self.state.active_agents = active_agents
        self.state.completed_tasks = _dedupe_preserving_order(completed_tasks)
        self.state.failed_tasks = _dedupe_preserving_order(failed_tasks)
        self.state.provider_runtime = provider_runtime

        if self.consensus is not None:
            self.state.last_consensus_version = self.consensus.version
            self.state.pending_questions = list(self.consensus.questions)
            if self.state.status is OrchestratorStatus.INIT:
                inferred_status = _consensus_to_orchestrator_status(self.consensus.status)
                if inferred_status is not None:
                    self.state.status = inferred_status
        else:
            self.state.pending_questions = []

        if self.state.pending_questions:
            self.state.gatekeeper_status = GatekeeperStatus.AWAITING_USER
        elif active_gatekeeper:
            self.state.gatekeeper_status = GatekeeperStatus.RUNNING
        else:
            self.state.gatekeeper_status = GatekeeperStatus.IDLE

    def _sync_status_from_consensus(self) -> None:
        if self.consensus is None:
            return

        inferred_status = _consensus_to_orchestrator_status(self.consensus.status)
        if inferred_status is None or inferred_status is self.state.status:
            return
        if self.can_transition_to(inferred_status):
            self.state.status = inferred_status

    def _build_user_input_requested_event(self, questions: list[str]) -> dict[str, object]:
        return {
            "type": "user-input.requested",
            "timestamp": _timestamp_now(),
            "questions": list(questions),
            "banner_text": self.USER_INPUT_BANNER,
            "terminal_bell": self.notification_bell_enabled,
        }


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def _consensus_to_orchestrator_status(status: ConsensusStatus) -> OrchestratorStatus | None:
    mapping = {
        ConsensusStatus.INIT: OrchestratorStatus.INIT,
        ConsensusStatus.PLANNING: OrchestratorStatus.PLANNING,
        ConsensusStatus.EXECUTING: OrchestratorStatus.EXECUTING,
        ConsensusStatus.PAUSED: OrchestratorStatus.PAUSED,
        ConsensusStatus.COMPLETED: OrchestratorStatus.COMPLETED,
    }
    return mapping.get(status)


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _extract_provider_thread_id(resume_cursor: object) -> str | None:
    if not isinstance(resume_cursor, dict):
        return None
    thread_id = resume_cursor.get("threadId")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
