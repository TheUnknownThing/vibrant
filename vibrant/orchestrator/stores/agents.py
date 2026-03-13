"""Per-agent record persistence."""

from __future__ import annotations

from pathlib import Path

from vibrant.models.agent import AgentRecord, AgentStatus


class AgentRecordStore:
    """Persist one JSON document per agent under ``.vibrant/agents/``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def get(self, agent_id: str) -> AgentRecord | None:
        record_path = self.path / f"{agent_id}.json"
        if not record_path.exists():
            return None
        return AgentRecord.model_validate_json(record_path.read_text(encoding="utf-8"))

    def list(self) -> list[AgentRecord]:
        records: list[AgentRecord] = []
        for path in sorted(self.path.glob("*.json")):
            try:
                records.append(AgentRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return records

    def list_active(self) -> list[AgentRecord]:
        active_statuses = {
            AgentStatus.SPAWNING,
            AgentStatus.CONNECTING,
            AgentStatus.RUNNING,
            AgentStatus.AWAITING_INPUT,
        }
        return [record for record in self.list() if record.lifecycle.status in active_statuses]

    def upsert(self, record: AgentRecord) -> AgentRecord:
        path = self.path / f"{record.identity.agent_id}.json"
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record

    def delete(self, agent_id: str) -> None:
        path = self.path / f"{agent_id}.json"
        if path.exists():
            path.unlink()
