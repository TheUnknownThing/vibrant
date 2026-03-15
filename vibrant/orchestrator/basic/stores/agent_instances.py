"""Stable agent-instance persistence."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from vibrant.models.agent import AgentInstanceRecord, AgentRunRecord

from ..repository import JsonDirectoryRepository


class AgentInstanceStore:
    """Persist one JSON document per stable instance under ``.vibrant/agent-instances/``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._repository = JsonDirectoryRepository(
            self.path,
            parse_text=AgentInstanceRecord.model_validate_json,
            serialize_record=lambda record: record.model_dump_json(indent=2),
            key_for=lambda record: record.identity.agent_id,
        )

    def get(self, agent_id: str) -> AgentInstanceRecord | None:
        return self._repository.get(agent_id)

    def find(
        self,
        *,
        role: str,
        scope_type: str,
        scope_id: str | None,
    ) -> AgentInstanceRecord | None:
        normalized_role = role.strip().lower()
        normalized_scope_type = scope_type.strip().lower()
        for record in self.list():
            if record.identity.role != normalized_role:
                continue
            if record.scope.scope_type != normalized_scope_type:
                continue
            if record.scope.scope_id != scope_id:
                continue
            return record
        return None

    def list(self) -> list[AgentInstanceRecord]:
        return self._repository.list()

    def upsert(self, record: AgentInstanceRecord) -> AgentInstanceRecord:
        return self._repository.upsert(record)

    def delete(self, agent_id: str) -> None:
        self._repository.delete(agent_id)

    def reconcile_active_runs(
        self,
        *,
        live_run_ids: Collection[str],
        run_by_id: dict[str, AgentRunRecord],
    ) -> list[str]:
        rewritten: list[str] = []
        for record in self.list():
            active_run_id = record.active_run_id
            if active_run_id is None:
                continue
            run_record = run_by_id.get(active_run_id)
            run_is_live = active_run_id in live_run_ids
            run_is_terminal = (
                run_record is None or run_record.lifecycle.status in AgentRunRecord.TERMINAL_STATUSES
            )
            if run_is_live and not run_is_terminal:
                continue
            record.active_run_id = None
            self.upsert(record)
            rewritten.append(record.identity.agent_id)
        return rewritten
