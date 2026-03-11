"""Direct file-backed storage for durable agent records."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from vibrant.agents.runtime import ProviderThreadHandle
from vibrant.models.agent import AgentRecord, AgentType
from ..state.store import StateStore


class AgentRecordStore:
    """Persist and query agent records directly from ``.vibrant/agents``.

    Agent records now have a single durable source of truth on disk. The
    orchestrator state backend rebuilds projections from persisted records
    instead of relying on an in-memory compatibility mirror.
    """

    def __init__(
        self,
        *,
        vibrant_dir: str | Path,
        state_store: StateStore,
    ) -> None:
        self.vibrant_dir = Path(vibrant_dir)
        self.agents_dir = self.vibrant_dir / "agents"
        self.state_store = state_store
        self._records: dict[str, AgentRecord] = {}
        self.refresh()

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._records

    def get(self, agent_id: str) -> AgentRecord | None:
        return self._records.get(agent_id)

    def list_records(self) -> list[AgentRecord]:
        return [self._records[agent_id] for agent_id in sorted(self._records)]

    def records_for_task(self, task_id: str) -> list[AgentRecord]:
        records = [record for record in self._records.values() if record.task_id == task_id]
        return sorted(
            records,
            key=lambda record: (
                (record.started_at or record.finished_at) is None,
                record.started_at or record.finished_at,
                record.agent_id,
            ),
        )

    def latest_for_task(
        self,
        task_id: str,
        *,
        agent_type: AgentType | None = None,
    ) -> AgentRecord | None:
        matches = self.records_for_task(task_id)
        if agent_type is not None:
            matches = [record for record in matches if record.type is agent_type]
        return matches[-1] if matches else None

    def provider_thread_handle(self, agent_id: str) -> ProviderThreadHandle | None:
        record = self.get(agent_id)
        if record is None:
            return None
        provider = record.provider
        if (
            provider.provider_thread_id is None
            and provider.thread_path is None
            and provider.resume_cursor is None
        ):
            return None
        return ProviderThreadHandle(
            thread_id=provider.provider_thread_id,
            thread_path=provider.thread_path,
            resume_cursor=provider.resume_cursor,
        )

    def refresh(self) -> dict[str, AgentRecord]:
        records: dict[str, AgentRecord] = {}
        if self.agents_dir.exists():
            for path in sorted(self.agents_dir.glob("*.json")):
                record = AgentRecord.model_validate_json(path.read_text(encoding="utf-8"))
                records[record.agent_id] = record
        self._records = records
        return dict(self._records)

    def upsert(
        self,
        record: AgentRecord,
        *,
        increment_spawn: bool = False,
        rebuild_state: bool = True,
    ) -> Path:
        if increment_spawn and record.agent_id not in self._records:
            self.state_store.increment_total_agent_spawns()

        path = self.agents_dir / f"{record.agent_id}.json"
        _atomic_write_text(path, record.model_dump_json(indent=2) + "\n")

        self._records[record.agent_id] = record
        if rebuild_state:
            self.state_store.rebuild_derived_state()
        return path




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
