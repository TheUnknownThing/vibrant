"""Question persistence."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from ..repository import JsonDataclassMappingRepository
from ...types import QuestionPriority, QuestionRecord, QuestionStatus, utc_now


class QuestionStore:
    """Persist Gatekeeper user-decision requests in ``.vibrant/questions.json``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._repository = JsonDataclassMappingRepository(
            self.path,
            record_type=QuestionRecord,
            key_for=lambda record: record.question_id,
            key_field="question_id",
            normalize_payload=_normalize_question_payload,
        )

    def list(self, *, status: QuestionStatus | None = None) -> list[QuestionRecord]:
        records = list(self._load_records().values())
        if status is None:
            return records
        return [record for record in records if record.status is status]

    def list_pending(self) -> list[QuestionRecord]:
        return self.list(status=QuestionStatus.PENDING)

    def list_for_conversation(self, conversation_id: str) -> list[QuestionRecord]:
        return [
            record
            for record in self._load_records().values()
            if record.source_conversation_id == conversation_id
        ]

    def get(self, question_id: str) -> QuestionRecord | None:
        return self._repository.get(question_id)

    def create(
        self,
        *,
        text: str,
        priority: QuestionPriority,
        source_role: str,
        source_agent_id: str | None,
        source_conversation_id: str | None,
        source_turn_id: str | None,
        blocking_scope: str,
        task_id: str | None,
        question_id: str | None = None,
    ) -> QuestionRecord:
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("Question text must not be empty")

        records = self._load_records()
        record = QuestionRecord(
            question_id=question_id or f"question-{uuid4()}",
            text=normalized_text,
            priority=priority,
            source_role=source_role.strip() or "gatekeeper",
            source_agent_id=_optional_string(source_agent_id),
            source_conversation_id=_optional_string(source_conversation_id),
            source_turn_id=_optional_string(source_turn_id),
            blocking_scope=_parse_scope(blocking_scope),
            task_id=_optional_string(task_id),
        )
        records[record.question_id] = record
        self._save_records(records)
        return record

    def withdraw(self, question_id: str, *, reason: str | None = None) -> QuestionRecord:
        records = self._load_records()
        try:
            record = records[question_id]
        except KeyError as exc:
            raise KeyError(f"Unknown question: {question_id}") from exc

        record.status = QuestionStatus.WITHDRAWN
        record.withdrawn_reason = _optional_string(reason)
        record.updated_at = utc_now()
        records[question_id] = record
        self._save_records(records)
        return record

    def resolve(self, question_id: str, *, answer: str | None) -> QuestionRecord:
        records = self._load_records()
        try:
            record = records[question_id]
        except KeyError as exc:
            raise KeyError(f"Unknown question: {question_id}") from exc

        record.status = QuestionStatus.RESOLVED
        record.answer = _optional_string(answer)
        record.updated_at = utc_now()
        records[question_id] = record
        self._save_records(records)
        return record

    def _load_records(self) -> dict[str, QuestionRecord]:
        return self._repository.load_all()

    def _save_records(self, records: dict[str, QuestionRecord]) -> None:
        self._repository.save_all(records)


def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _parse_scope(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Question blocking scope must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Question blocking scope must not be empty")
    return normalized


def _normalize_question_payload(payload: dict[str, object]) -> dict[str, object] | None:
    try:
        return {
            "question_id": str(payload["question_id"]),
            "text": str(payload["text"]).strip(),
            "priority": QuestionPriority(str(payload.get("priority", QuestionPriority.BLOCKING.value))),
            "source_role": str(payload.get("source_role") or "gatekeeper"),
            "source_agent_id": _optional_string(payload.get("source_agent_id")),
            "source_conversation_id": _optional_string(payload.get("source_conversation_id")),
            "source_turn_id": _optional_string(payload.get("source_turn_id")),
            "blocking_scope": _parse_scope(payload.get("blocking_scope")),
            "task_id": _optional_string(payload.get("task_id")),
            "status": QuestionStatus(str(payload.get("status", QuestionStatus.PENDING.value))),
            "answer": _optional_string(payload.get("answer")),
            "created_at": str(payload.get("created_at") or utc_now()),
            "updated_at": str(payload.get("updated_at") or payload.get("created_at") or utc_now()),
            "withdrawn_reason": _optional_string(payload.get("withdrawn_reason")),
        }
    except (KeyError, TypeError, ValueError):
        return None
