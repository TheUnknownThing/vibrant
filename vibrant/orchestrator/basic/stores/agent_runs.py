"""Per-run record persistence."""

from __future__ import annotations

from pathlib import Path

from vibrant.models.agent import AgentRunRecord, AgentStatus, ProviderResumeHandle

from ..repository import JsonDirectoryRepository
from ..session import authoritative_resume_handle


class AgentRunStore:
    """Persist one JSON document per run under ``.vibrant/agent-runs/``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._repository = JsonDirectoryRepository(
            self.path,
            parse_text=AgentRunRecord.model_validate_json,
            serialize_record=lambda record: record.model_dump_json(indent=2),
            key_for=lambda record: record.identity.run_id,
        )

    def get(self, run_id: str) -> AgentRunRecord | None:
        return self._repository.get(run_id)

    def list(self) -> list[AgentRunRecord]:
        records = self._repository.list()
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

    def resume_handle_for_run(self, run_id: str) -> ProviderResumeHandle | None:
        record = self.get(run_id)
        if record is None:
            return None
        return authoritative_resume_handle(
            ProviderResumeHandle.from_provider_metadata(record.provider)
        )

    def upsert(self, record: AgentRunRecord) -> AgentRunRecord:
        return self._repository.upsert(record)

    def delete(self, run_id: str) -> None:
        self._repository.delete(run_id)
