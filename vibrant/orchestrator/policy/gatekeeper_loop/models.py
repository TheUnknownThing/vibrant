"""Gatekeeper-loop policy models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...types import GatekeeperSessionSnapshot, QuestionView


class GatekeeperMessageKind(str, Enum):
    USER_MESSAGE = "user_message"
    USER_ANSWER = "user_answer"
    REVIEW = "review"
    SYSTEM = "system"


@dataclass(slots=True)
class GatekeeperSubmission:
    submission_id: str
    session: GatekeeperSessionSnapshot
    conversation_id: str
    agent_id: str | None
    run_id: str | None
    incarnation_id: str | None
    accepted: bool
    active_turn_id: str | None
    question_id: str | None = None
    answer_text: str | None = None
    error: str | None = None


@dataclass(slots=True)
class GatekeeperLoopState:
    session: GatekeeperSessionSnapshot
    conversation_id: str | None
    pending_question: QuestionView | None
    pending_questions: tuple[QuestionView, ...] = ()
    last_submission_id: str | None = None
    last_error: str | None = None
    busy: bool = False
