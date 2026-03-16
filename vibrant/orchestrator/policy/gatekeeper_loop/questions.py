"""Question policy helpers for the Gatekeeper loop."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

from ...basic.stores import QuestionStore
from ...types import QuestionRecord, QuestionStatus


DEFAULT_BLOCKING_SCOPE = "planning"
VALID_BLOCKING_SCOPES = frozenset({"planning", "workflow", "task", "review"})


class _QuestionLike(Protocol):
    question_id: str
    text: str
    status: QuestionStatus


TQuestion = TypeVar("TQuestion", bound=_QuestionLike)


def normalize_question_scope(value: object, *, default: str = DEFAULT_BLOCKING_SCOPE) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    if normalized not in VALID_BLOCKING_SCOPES:
        return default
    return normalized


def list_pending_questions(question_store: QuestionStore) -> tuple[QuestionRecord, ...]:
    return tuple(question_store.list_pending())


def current_pending_question(pending_questions: Sequence[TQuestion]) -> TQuestion | None:
    return pending_questions[0] if pending_questions else None


def require_pending_question(record: TQuestion | None, question_id: str) -> TQuestion:
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
    pending_questions: Sequence[TQuestion],
    text: str | None,
) -> TQuestion | None:
    if not text:
        return current_pending_question(pending_questions)
    return next((record for record in pending_questions if record.text == text), current_pending_question(pending_questions))
