"""Stable agent-instance persistence."""

from __future__ import annotations

from pathlib import Path

from vibrant.models.agent import AgentInstanceRecord


class AgentInstanceStore:
    """Persist one JSON document per stable instance under ``.vibrant/agent-instances/``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def get(self, agent_id: str) -> AgentInstanceRecord | None:
        record_path = self.path / f"{agent_id}.json"
        if not record_path.exists():
            return None
        return AgentInstanceRecord.model_validate_json(record_path.read_text(encoding="utf-8"))

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
        records: list[AgentInstanceRecord] = []
        for path in sorted(self.path.glob("*.json")):
            try:
                records.append(AgentInstanceRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return records

    def upsert(self, record: AgentInstanceRecord) -> AgentInstanceRecord:
        path = self.path / f"{record.identity.agent_id}.json"
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record

    def delete(self, agent_id: str) -> None:
        path = self.path / f"{agent_id}.json"
        if path.exists():
            path.unlink()
