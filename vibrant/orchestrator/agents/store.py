"""Direct file-backed storage for stable agent instances and run records."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from vibrant.agents.runtime import ProviderThreadHandle
from vibrant.models.agent import AgentInstanceRecord, AgentRecord

from .catalog import normalize_role_name
from ..state.store import StateStore


class AgentInstanceStore:
    """Persist and query stable agent instances from ``.vibrant/agent-instances``."""

    def __init__(self, *, vibrant_dir: str | Path) -> None:
        self.vibrant_dir = Path(vibrant_dir)
        self.instances_dir = self.vibrant_dir / "agent-instances"
        self._records: dict[str, AgentInstanceRecord] = {}
        self.refresh()

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._records

    def get(self, agent_id: str) -> AgentInstanceRecord | None:
        return self._records.get(agent_id)

    def list_records(self) -> list[AgentInstanceRecord]:
        return [self._records[agent_id] for agent_id in sorted(self._records)]

    def find(self, *, role: str, scope_type: str, scope_id: str | None) -> AgentInstanceRecord | None:
        normalized_role = normalize_role_name(role)
        normalized_scope_type = scope_type.strip().lower()
        for record in self._records.values():
            if record.identity.role != normalized_role:
                continue
            if record.scope.scope_type != normalized_scope_type:
                continue
            if record.scope.scope_id != scope_id:
                continue
            return record
        return None

    def refresh(self) -> dict[str, AgentInstanceRecord]:
        records: dict[str, AgentInstanceRecord] = {}
        if self.instances_dir.exists():
            for path in sorted(self.instances_dir.glob("*.json")):
                record = AgentInstanceRecord.model_validate_json(path.read_text(encoding="utf-8"))
                records[record.identity.agent_id] = record
        self._records = records
        return dict(self._records)

    def upsert(self, record: AgentInstanceRecord) -> Path:
        path = self.instances_dir / f"{record.identity.agent_id}.json"
        _atomic_write_text(path, record.model_dump_json(indent=2) + "\n")
        self._records[record.identity.agent_id] = record
        return path


class AgentRecordStore:
    """Persist and query agent run records from ``.vibrant/agent-runs``.

    The legacy ``.vibrant/agents`` directory is still read as a migration input,
    but new writes go to ``.vibrant/agent-runs``.
    """

    def __init__(
        self,
        *,
        vibrant_dir: str | Path,
        state_store: StateStore,
    ) -> None:
        self.vibrant_dir = Path(vibrant_dir)
        self.runs_dir = self.vibrant_dir / "agent-runs"
        self.legacy_runs_dir = self.vibrant_dir / "agents"
        self.state_store = state_store
        self._records: dict[str, AgentRecord] = {}
        self.refresh()

    def __contains__(self, run_id: str) -> bool:
        return run_id in self._records

    def get(self, run_id: str) -> AgentRecord | None:
        return self._records.get(run_id)

    def list_records(self) -> list[AgentRecord]:
        return sorted(self._records.values(), key=_run_sort_key)

    def records_for_agent(self, agent_id: str) -> list[AgentRecord]:
        return sorted(
            [record for record in self._records.values() if record.identity.agent_id == agent_id],
            key=_run_sort_key,
        )

    def records_for_task(self, task_id: str) -> list[AgentRecord]:
        return sorted(
            [record for record in self._records.values() if record.identity.task_id == task_id],
            key=_run_sort_key,
        )

    def latest_for_agent(self, agent_id: str) -> AgentRecord | None:
        matches = self.records_for_agent(agent_id)
        return matches[-1] if matches else None

    def latest_for_task(
        self,
        task_id: str,
        *,
        role: str | None = None,
    ) -> AgentRecord | None:
        matches = self.records_for_task(task_id)
        if role is not None:
            resolved_role = normalize_role_name(role)
            matches = [record for record in matches if record.identity.role == resolved_role]
        return matches[-1] if matches else None

    def provider_thread_handle(self, agent_id: str) -> ProviderThreadHandle | None:
        for record in reversed(self.records_for_agent(agent_id)):
            handle = ProviderThreadHandle.from_provider_metadata(record.provider)
            if handle is not None and not handle.empty:
                return handle
        return None

    def refresh(self) -> dict[str, AgentRecord]:
        records: dict[str, AgentRecord] = {}
        for directory in (self.legacy_runs_dir, self.runs_dir):
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.json")):
                record = AgentRecord.model_validate_json(path.read_text(encoding="utf-8"))
                records[record.identity.run_id] = record
        self._records = records
        return dict(self._records)

    def upsert(
        self,
        record: AgentRecord,
        *,
        increment_spawn: bool = False,
        rebuild_state: bool = True,
    ) -> Path:
        if increment_spawn and record.identity.run_id not in self._records:
            self.state_store.increment_total_agent_spawns()

        path = self.runs_dir / f"{record.identity.run_id}.json"
        _atomic_write_text(path, record.model_dump_json(indent=2) + "\n")

        self._records[record.identity.run_id] = record
        if rebuild_state:
            self.state_store.rebuild_derived_state()
        return path



def _run_sort_key(record: AgentRecord) -> tuple[bool, object, str]:
    return (
        (record.lifecycle.started_at or record.lifecycle.finished_at) is None,
        record.lifecycle.started_at or record.lifecycle.finished_at,
        record.identity.run_id,
    )



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
