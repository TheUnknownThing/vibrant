"""Gatekeeper request shaping policy."""

from __future__ import annotations

from dataclasses import dataclass

from vibrant.agents.gatekeeper import GatekeeperRequest, GatekeeperTrigger
from vibrant.prompts import build_user_answer_trigger_description

from ...types import QuestionRecord, ReviewTicket, ValidationOutcome
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
            agent_summary=agent_summary,
        )
    return GatekeeperRequest(
        trigger=GatekeeperTrigger.USER_CONVERSATION,
        trigger_description=trigger_description or text,
        agent_summary=agent_summary,
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


def build_review_submission_request(
    ticket: ReviewTicket,
    *,
    validation: ValidationOutcome | None,
    code_summary: str | None,
) -> GatekeeperSubmissionRequest:
    validation_status = validation.status if validation is not None else "skipped"
    if validation is None:
        validation_summary = "Test stage not configured."
    else:
        validation_summary = validation.summary or "Test stage summary unavailable."
    trigger_description = "\n".join(
        [
            f"Review ticket: {ticket.ticket_id}",
            f"Task ID: {ticket.task_id}",
            f"Attempt ID: {ticket.attempt_id}",
            f"Review kind: {ticket.review_kind}",
            f"Validation status: {validation_status}",
            f"Validation summary: {validation_summary}",
            f"Code summary: {code_summary or ticket.summary or 'No implementation summary was captured.'}",
            "Inspect the review ticket via MCP resources, then explicitly accept, retry, escalate, or request user input.",
        ]
    )
    return GatekeeperSubmissionRequest(
        message_kind=GatekeeperMessageKind.REVIEW,
        request=build_request(
            message_kind=GatekeeperMessageKind.REVIEW,
            text=trigger_description,
            trigger_description=trigger_description,
            agent_summary=code_summary or ticket.summary or validation_summary,
        ),
    )
