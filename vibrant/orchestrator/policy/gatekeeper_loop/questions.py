"""Question policy helpers for the Gatekeeper loop."""

from __future__ import annotations

from ...basic.stores import QuestionStore
from ...types import QuestionRecord, QuestionStatus


DEFAULT_BLOCKING_SCOPE = "planning"
VALID_BLOCKING_SCOPES = frozenset({"planning", "workflow", "task", "review"})


def normalize_question_scope(value: object, *, default: str = DEFAULT_BLOCKING_SCOPE) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    if normalized not in VALID_BLOCKING_SCOPES:
        return default
    return normalized


def normalize_blocking_scope(value: object, *, default: str = DEFAULT_BLOCKING_SCOPE) -> str:
    return normalize_question_scope(value, default=default)


def list_pending_questions(question_store: QuestionStore) -> tuple[QuestionRecord, ...]:
    return tuple(question_store.list_pending())


def current_pending_question(pending_questions: list[QuestionRecord] | tuple[QuestionRecord, ...]) -> QuestionRecord | None:
    return pending_questions[0] if pending_questions else None


def require_pending_question(record: QuestionRecord | None, question_id: str) -> QuestionRecord:
    if record is None:
        raise KeyError(f"Unknown question: {question_id}")
    if record.status is not QuestionStatus.PENDING:
        raise ValueError(f"Question is not pending: {question_id}")
    return record


def select_pending_question(question_store: QuestionStore, question_id: str | None) -> QuestionRecord | None:
    if question_id is not None:
        return require_pending_question(question_store.get(question_id), question_id)
    return current_pending_question(list_pending_questions(question_store))


def select_pending_question_by_text(
    pending_questions: list[QuestionRecord] | tuple[QuestionRecord, ...],
    text: str | None,
) -> QuestionRecord | None:
    if not text:
        return current_pending_question(pending_questions)
    return next((record for record in pending_questions if record.text == text), current_pending_question(pending_questions))
