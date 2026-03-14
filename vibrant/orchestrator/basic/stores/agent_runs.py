"""Per-run record persistence."""

from __future__ import annotations

from pathlib import Path

from vibrant.models.agent import AgentRunRecord, AgentStatus, ProviderResumeHandle


class AgentRunStore:
    """Persist one JSON document per run under ``.vibrant/agent-runs/``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def get(self, run_id: str) -> AgentRunRecord | None:
        record_path = self.path / f"{run_id}.json"
        if not record_path.exists():
            return None
        return AgentRunRecord.model_validate_json(record_path.read_text(encoding="utf-8"))

    def list(self) -> list[AgentRunRecord]:
        records: list[AgentRunRecord] = []
        for path in sorted(self.path.glob("*.json")):
            try:
                records.append(AgentRunRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        records.sort(
            key=lambda record: (
                (record.lifecycle.started_at or record.lifecycle.finished_at) is None,
                record.lifecycle.started_at or record.lifecycle.finished_at,
                record.identity.run_id,
            )
        )
        return records

    def list_active(self) -> list[AgentRunRecord]:
        active_statuses = {
            AgentStatus.SPAWNING,
            AgentStatus.CONNECTING,
            AgentStatus.RUNNING,
            AgentStatus.AWAITING_INPUT,
        }
        return [record for record in self.list() if record.lifecycle.status in active_statuses]

    def list_for_agent(self, agent_id: str) -> list[AgentRunRecord]:
        return [record for record in self.list() if record.identity.agent_id == agent_id]

    def latest_for_agent(self, agent_id: str) -> AgentRunRecord | None:
        records = self.list_for_agent(agent_id)
        return records[-1] if records else None

    def provider_thread_handle(self, agent_id: str) -> ProviderResumeHandle | None:
        for record in reversed(self.list_for_agent(agent_id)):
            handle = ProviderResumeHandle.from_provider_metadata(record.provider)
            if handle is not None and not handle.empty:
                return handle
        return None

    def upsert(self, record: AgentRunRecord) -> AgentRunRecord:
        path = self.path / f"{record.identity.run_id}.json"
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record

    def delete(self, run_id: str) -> None:
        path = self.path / f"{run_id}.json"
        if path.exists():
            path.unlink()
