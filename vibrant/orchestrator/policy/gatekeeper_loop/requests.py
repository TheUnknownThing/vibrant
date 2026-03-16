"""Gatekeeper request shaping policy."""

from __future__ import annotations

from dataclasses import dataclass

from vibrant.agents.gatekeeper import GatekeeperRequest, GatekeeperTrigger
from vibrant.prompts import build_user_answer_trigger_description

from ...types import QuestionRecord
from .models import GatekeeperMessageKind


@dataclass(frozen=True, slots=True)
class GatekeeperSubmissionRequest:
    message_kind: GatekeeperMessageKind
    request: GatekeeperRequest
    related_question_id: str | None = None


def build_request(
    *,
    message_kind: GatekeeperMessageKind,
    text: str,
    trigger_description: str | None = None,
    agent_summary: str | None = None,
) -> GatekeeperRequest:
    if message_kind is GatekeeperMessageKind.REVIEW:
        return GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description=trigger_description or text,
            agent_summary=agent_summary or text,
        )
    return GatekeeperRequest(
        trigger=GatekeeperTrigger.USER_CONVERSATION,
        trigger_description=trigger_description or text,
        agent_summary=agent_summary or text,
    )


def build_user_submission_request(text: str, pending_question: QuestionRecord | None) -> GatekeeperSubmissionRequest:
    if pending_question is None:
        return GatekeeperSubmissionRequest(
            message_kind=GatekeeperMessageKind.USER_MESSAGE,
            request=build_request(
                message_kind=GatekeeperMessageKind.USER_MESSAGE,
                text=text,
            ),
        )
    return GatekeeperSubmissionRequest(
        message_kind=GatekeeperMessageKind.USER_ANSWER,
        request=build_request(
            message_kind=GatekeeperMessageKind.USER_ANSWER,
            text=text,
            trigger_description=build_user_answer_trigger_description(
                question=pending_question.text,
                answer=text,
            ),
        ),
        related_question_id=pending_question.question_id,
    )
