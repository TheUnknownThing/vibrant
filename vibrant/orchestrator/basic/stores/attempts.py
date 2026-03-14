"""Attempt persistence."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ..json_store import read_json, write_json
from ...types import AttemptRecord, AttemptStatus, utc_now


ACTIVE_ATTEMPT_STATUSES = {
    AttemptStatus.LEASED,
    AttemptStatus.RUNNING,
    AttemptStatus.AWAITING_INPUT,
    AttemptStatus.VALIDATION_PENDING,
    AttemptStatus.VALIDATING,
    AttemptStatus.REVIEW_PENDING,
    AttemptStatus.MERGE_PENDING,
}


class AttemptStore:
    """Persist attempt-centric execution state in ``.vibrant/attempts.json``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create(
        self,
        *,
        task_id: str,
        workspace_id: str,
        task_definition_version: int,
        status: AttemptStatus = AttemptStatus.LEASED,
        attempt_id: str | None = None,
        code_run_id: str | None = None,
        validation_run_ids: list[str] | None = None,
        merge_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> AttemptRecord:
        records = self._load_records()
        now = utc_now()
        record = AttemptRecord(
            attempt_id=attempt_id or f"attempt-{uuid4()}",
            task_id=task_id,
            status=status,
            workspace_id=workspace_id,
            code_run_id=code_run_id,
            validation_run_ids=list(validation_run_ids or []),
            merge_run_id=merge_run_id,
            task_definition_version=task_definition_version,
            conversation_id=conversation_id,
            created_at=now,
            updated_at=now,
        )
        records[record.attempt_id] = record
        self._save_records(records)
        return record

    def get(self, attempt_id: str) -> AttemptRecord | None:
        return self._load_records().get(attempt_id)

    def get_active_by_task(self, task_id: str) -> AttemptRecord | None:
        for record in self._load_records().values():
            if record.task_id == task_id and record.status in ACTIVE_ATTEMPT_STATUSES:
                return record
        return None

    def list_by_task(self, task_id: str) -> list[AttemptRecord]:
        return [record for record in self._load_records().values() if record.task_id == task_id]

    def task_id_for_run(self, run_id: str) -> str | None:
        for record in self._load_records().values():
            if run_id in _attempt_run_ids(record):
                return record.task_id
        return None

    def run_ids_for_task(self, task_id: str) -> set[str]:
        run_ids: set[str] = set()
        for record in self.list_by_task(task_id):
            run_ids.update(_attempt_run_ids(record))
        return run_ids

    def run_task_ids(self) -> dict[str, str]:
        mappings: dict[str, str] = {}
        for record in self._load_records().values():
            for run_id in _attempt_run_ids(record):
                mappings[run_id] = record.task_id
        return mappings

    def list_active(self) -> list[AttemptRecord]:
        return [record for record in self._load_records().values() if record.status in ACTIVE_ATTEMPT_STATUSES]

    def list_all(self) -> list[AttemptRecord]:
        return list(self._load_records().values())

    def update(
        self,
        attempt_id: str,
        *,
        status: AttemptStatus | None = None,
        workspace_id: str | None = None,
        code_run_id: str | None = None,
        validation_run_ids: list[str] | None = None,
        merge_run_id: str | None = None,
        task_definition_version: int | None = None,
        conversation_id: str | None = None,
    ) -> AttemptRecord:
        records = self._load_records()
        try:
            record = records[attempt_id]
        except KeyError as exc:
            raise KeyError(f"Unknown attempt: {attempt_id}") from exc

        if status is not None:
            record.status = status
        if workspace_id is not None:
            record.workspace_id = workspace_id
        if code_run_id is not None:
            record.code_run_id = code_run_id
        if validation_run_ids is not None:
            record.validation_run_ids = list(validation_run_ids)
        if merge_run_id is not None:
            record.merge_run_id = merge_run_id
        if task_definition_version is not None:
            record.task_definition_version = task_definition_version
        if conversation_id is not None:
            record.conversation_id = conversation_id
        record.updated_at = utc_now()

        records[attempt_id] = record
        self._save_records(records)
        return record

    def _load_records(self) -> dict[str, AttemptRecord]:
        raw = read_json(self.path, default={})
        if not isinstance(raw, dict):
            return {}

        records: dict[str, AttemptRecord] = {}
        for attempt_id, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            try:
                records[attempt_id] = AttemptRecord(
                    attempt_id=str(payload.get("attempt_id") or attempt_id),
                    task_id=str(payload["task_id"]),
                    status=AttemptStatus(str(payload["status"])),
                    workspace_id=str(payload["workspace_id"]),
                    code_run_id=_optional_string(payload.get("code_run_id")),
                    validation_run_ids=_string_list(payload.get("validation_run_ids")),
                    merge_run_id=_optional_string(payload.get("merge_run_id")),
                    task_definition_version=int(payload["task_definition_version"]),
                    conversation_id=_optional_string(payload.get("conversation_id")),
                    created_at=str(payload.get("created_at") or utc_now()),
                    updated_at=str(payload.get("updated_at") or payload.get("created_at") or utc_now()),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return records

    def _save_records(self, records: dict[str, AttemptRecord]) -> None:
        write_json(self.path, {attempt_id: asdict(record) | {"status": record.status.value} for attempt_id, record in records.items()})


def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _attempt_run_ids(record: AttemptRecord) -> set[str]:
    run_ids: set[str] = set()
    if record.code_run_id:
        run_ids.add(record.code_run_id)
    if record.merge_run_id:
        run_ids.add(record.merge_run_id)
    run_ids.update(run_id for run_id in record.validation_run_ids if run_id)
    return run_ids
