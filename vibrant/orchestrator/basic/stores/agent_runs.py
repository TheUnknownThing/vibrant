"""Per-run record persistence."""

from __future__ import annotations

import json
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

    def resume_handle_for_run(self, run_id: str) -> ProviderResumeHandle | None:
        record = self.get(run_id)
        if record is None:
            return None
        handle = ProviderResumeHandle.from_provider_metadata(record.provider)
        if handle is None or handle.empty:
            return None
        return handle

    def upsert(self, record: AgentRunRecord) -> AgentRunRecord:
        path = self.path / f"{record.identity.run_id}.json"
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return record

    def delete(self, run_id: str) -> None:
        path = self.path / f"{run_id}.json"
        if path.exists():
            path.unlink()

    def normalize_files(self) -> list[str]:
        rewritten: list[str] = []
        for path in sorted(self.path.glob("*.json")):
            try:
                raw_payload = json.loads(path.read_text(encoding="utf-8"))
                record = AgentRunRecord.model_validate(raw_payload)
            except Exception:
                continue
            normalized_payload = record.model_dump(mode="json")
            if raw_payload == normalized_payload:
                continue
            path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
            rewritten.append(path.name)
        return rewritten
