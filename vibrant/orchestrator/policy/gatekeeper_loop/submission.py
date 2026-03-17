"""Gatekeeper user-submission flow helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from ...basic.artifacts import build_workflow_snapshot
from ...types import QuestionStatus, QuestionView, ReviewTicket, ValidationOutcome, WorkflowSnapshot
from .models import GatekeeperLoopState, GatekeeperSubmission
from .questions import current_pending_question, require_pending_question
from .requests import build_review_submission_request, build_user_submission_request

if TYPE_CHECKING:
    from .loop import GatekeeperUserLoop


async def submit_user_input(
    loop: GatekeeperUserLoop,
    text: str,
    question_id: str | None = None,
) -> GatekeeperSubmission:
    session = await loop.lifecycle.resume_or_start()
    conversation_id = session.conversation_id or f"gatekeeper-{uuid4().hex[:12]}"
    if question_id is None:
        pending_question = current_pending_question(loop.question_store.list_pending())
    else:
        pending_question = require_pending_question(loop.question_store.get(question_id), question_id)
    prepared = build_user_submission_request(text, pending_question)

    loop.conversation_service.record_host_message(
        conversation_id=conversation_id,
        role="user",
        text=text,
        related_question_id=prepared.related_question_id,
    )
    submission_id = f"submission-{uuid4()}"
    loop._last_submission_id = submission_id

    try:
        handle = await loop.lifecycle.submit(
            request=prepared.request,
            submission_id=submission_id,
            resume=True,
        )
    except Exception as exc:
        loop._last_error = str(exc)
        raise

    loop._last_error = None
    snapshot = loop.lifecycle.snapshot()
    identity = getattr(getattr(handle, "agent_record", None), "identity", None)
    return GatekeeperSubmission(
        submission_id=submission_id,
        session=snapshot,
        conversation_id=conversation_id,
        agent_id=getattr(identity, "agent_id", snapshot.agent_id),
        run_id=getattr(identity, "run_id", snapshot.run_id),
        accepted=True,
        active_turn_id=snapshot.active_turn_id,
        question_id=pending_question.question_id if pending_question is not None else None,
        answer_text=text if pending_question is not None else None,
    )


async def submit_review(
    loop: GatekeeperUserLoop,
    ticket: ReviewTicket,
    *,
    validation: ValidationOutcome | None = None,
    code_summary: str | None = None,
) -> GatekeeperSubmission:
    session = await loop.lifecycle.resume_or_start()
    conversation_id = session.conversation_id or f"gatekeeper-{uuid4().hex[:12]}"
    prepared = build_review_submission_request(
        ticket,
        validation=validation,
        code_summary=code_summary,
    )

    loop.conversation_service.record_host_message(
        conversation_id=conversation_id,
        role="system",
        text=f"Review requested for ticket {ticket.ticket_id} on task {ticket.task_id}.",
    )
    submission_id = f"submission-{uuid4()}"
    loop._last_submission_id = submission_id

    try:
        handle = await loop.lifecycle.submit(
            request=prepared.request,
            submission_id=submission_id,
            resume=True,
        )
    except Exception as exc:
        loop._last_error = str(exc)
        raise

    loop._last_error = None
    snapshot = loop.lifecycle.snapshot()
    identity = getattr(getattr(handle, "agent_record", None), "identity", None)
    return GatekeeperSubmission(
        submission_id=submission_id,
        session=snapshot,
        conversation_id=conversation_id,
        agent_id=getattr(identity, "agent_id", snapshot.agent_id),
        run_id=getattr(identity, "run_id", snapshot.run_id),
        accepted=True,
        active_turn_id=snapshot.active_turn_id,
    )


async def wait_for_submission(loop: GatekeeperUserLoop, submission: GatekeeperSubmission):
    if not submission.run_id:
        raise RuntimeError("Gatekeeper submission did not produce a run id")
    wait_for_run = loop.runtime_service.wait_for_run
    result = await wait_for_run(submission.run_id)
    result_error = getattr(result, "error", None)
    if result_error:
        loop._last_error = result_error
        return result
    if submission.question_id is not None:
        record = loop.question_store.get(submission.question_id)
        if record is not None and record.status is QuestionStatus.PENDING:
            loop.question_store.resolve(
                submission.question_id,
                answer=submission.answer_text,
            )
    loop._last_error = None
    return result


def snapshot(loop: GatekeeperUserLoop) -> GatekeeperLoopState:
    session = loop.lifecycle.snapshot()
    pending_questions = tuple(QuestionView.from_record(record) for record in loop.question_store.list_pending())
    return GatekeeperLoopState(
        session=session,
        conversation_id=session.conversation_id,
        pending_question=pending_questions[0] if pending_questions else None,
        pending_questions=pending_questions,
        last_submission_id=loop._last_submission_id,
        last_error=loop._last_error or session.last_error,
        busy=loop.lifecycle.busy,
    )


def workflow_snapshot(loop: GatekeeperUserLoop) -> WorkflowSnapshot:
    return build_workflow_snapshot(
        workflow_state_store=loop.workflow_state_store,
        agent_run_store=loop.agent_run_store,
        question_store=loop.question_store,
        attempt_store=loop.attempt_store,
    )


def conversation(loop: GatekeeperUserLoop, conversation_id: str):
    return loop.conversation_service.rebuild(conversation_id)


def subscribe_conversation(loop: GatekeeperUserLoop, conversation_id: str, callback, *, replay: bool = False):
    return loop.conversation_service.subscribe(conversation_id, callback, replay=replay)
