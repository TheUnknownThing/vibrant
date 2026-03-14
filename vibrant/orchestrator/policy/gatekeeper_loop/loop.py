"""Gatekeeper user loop policy."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from vibrant.prompts import build_user_answer_trigger_description

from ...basic import ArtifactsCapability, ConversationCapability, AgentRuntimeCapability
from ...gatekeeper import GatekeeperLifecycleService
from ...types import GatekeeperMessageKind, GatekeeperSubmission, QuestionStatus
from .state import GatekeeperLoopState


@dataclass(slots=True)
class GatekeeperUserLoop:
    """Own the authoritative user <-> Gatekeeper submission flow."""

    artifacts: ArtifactsCapability
    conversations: ConversationCapability
    runtime: AgentRuntimeCapability
    lifecycle: GatekeeperLifecycleService
    _last_submission_id: str | None = None
    _last_error: str | None = None

    async def submit_user_input(self, text: str, question_id: str | None = None) -> GatekeeperSubmission:
        session = await self.lifecycle.resume_or_start()
        conversation_id = session.conversation_id or f"gatekeeper-{uuid4().hex[:12]}"
        pending_question = self._select_pending_question(question_id)

        if session.agent_id:
            self.conversations.bind_agent(
                conversation_id=conversation_id,
                agent_id=session.agent_id,
                task_id=None,
                provider_thread_id=session.provider_thread_id,
            )

        self.conversations.record_host_message(
            conversation_id=conversation_id,
            role="user",
            text=text,
            related_question_id=pending_question.question_id if pending_question is not None else None,
        )
        submission_id = f"submission-{uuid4()}"
        self._last_submission_id = submission_id

        message_kind = GatekeeperMessageKind.USER_MESSAGE
        trigger_description = text
        if pending_question is not None:
            message_kind = GatekeeperMessageKind.USER_ANSWER
            trigger_description = build_user_answer_trigger_description(
                question=pending_question.text,
                answer=text,
            )

        try:
            handle = await self.lifecycle.submit(
                message_kind=message_kind,
                text=text,
                submission_id=submission_id,
                resume=True,
                trigger_description=trigger_description,
                agent_summary=text,
            )
        except Exception as exc:
            self._last_error = str(exc)
            raise

        if pending_question is not None:
            self.artifacts.question_store.resolve(pending_question.question_id, answer=text)

        self._last_error = None
        snapshot = self.lifecycle.snapshot()
        return GatekeeperSubmission(
            submission_id=submission_id,
            session=snapshot,
            conversation_id=conversation_id,
            agent_id=getattr(getattr(handle, "agent_record", None), "identity", None).agent_id
            if getattr(handle, "agent_record", None) is not None
            else snapshot.agent_id,
            accepted=True,
            active_turn_id=snapshot.active_turn_id,
        )

    async def wait_for_submission(self, submission: GatekeeperSubmission):
        if not submission.agent_id:
            raise RuntimeError("Gatekeeper submission did not produce an agent id")
        return await self.runtime.wait_for_run(submission.agent_id)

    async def restart(self, reason: str | None = None) -> GatekeeperLoopState:
        await self.lifecycle.restart_session(reason=reason)
        self._last_error = self.lifecycle.snapshot().last_error
        return self.snapshot()

    async def stop(self) -> GatekeeperLoopState:
        await self.lifecycle.stop_session()
        self._last_error = self.lifecycle.snapshot().last_error
        return self.snapshot()

    def snapshot(self) -> GatekeeperLoopState:
        session = self.lifecycle.snapshot()
        pending_questions = tuple(self.artifacts.question_store.list_pending())
        return GatekeeperLoopState(
            session=session,
            conversation_id=session.conversation_id,
            pending_question=pending_questions[0] if pending_questions else None,
            pending_questions=pending_questions,
            last_submission_id=self._last_submission_id,
            last_error=self._last_error or session.last_error,
            busy=self.lifecycle.busy,
        )

    def conversation(self, conversation_id: str):
        return self.conversations.rebuild(conversation_id)

    def subscribe_conversation(self, conversation_id: str, callback, *, replay: bool = False):
        return self.conversations.subscribe(conversation_id, callback, replay=replay)

    def _select_pending_question(self, question_id: str | None):
        if question_id is not None:
            record = self.artifacts.question_store.get(question_id)
            if record is None:
                raise KeyError(f"Unknown question: {question_id}")
            if record.status is not QuestionStatus.PENDING:
                raise ValueError(f"Question is not pending: {question_id}")
            return record

        pending = self.artifacts.question_store.list_pending()
        return pending[0] if pending else None
