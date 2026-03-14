"""Top-level control plane for the redesigned orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .types import GatekeeperMessageKind, GatekeeperSubmission, WorkflowSnapshot, WorkflowStatus


@dataclass(slots=True)
class OrchestratorControlPlane:
    """Route host actions across orchestrator subsystems."""

    workflow_state_store: Any
    question_store: Any
    attempt_store: Any
    agent_store: Any
    gatekeeper_lifecycle: Any
    conversation_stream: Any
    workflow_policy: Any
    roadmap_store: Any
    review_control: Any

    async def submit_user_message(self, text: str) -> GatekeeperSubmission:
        session = await self.gatekeeper_lifecycle.resume_or_start()
        conversation_id = session.conversation_id or f"gatekeeper-{uuid4()}"
        if session.agent_id:
            self.conversation_stream.bind_agent(
                conversation_id=conversation_id,
                agent_id=session.agent_id,
                task_id=None,
                provider_thread_id=session.provider_thread_id,
            )
        self.conversation_stream.record_host_message(
            conversation_id=conversation_id,
            role="user",
            text=text,
        )
        submission_id = f"submission-{uuid4()}"
        handle = await self.gatekeeper_lifecycle.submit(
            message_kind=GatekeeperMessageKind.USER_MESSAGE,
            text=text,
            submission_id=submission_id,
            resume=True,
        )
        snapshot = self.gatekeeper_lifecycle.snapshot()
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

    async def answer_user_decision(self, question_id: str, answer: str) -> GatekeeperSubmission:
        question = self.question_store.resolve(question_id, answer=answer)
        session = await self.gatekeeper_lifecycle.resume_or_start()
        conversation_id = session.conversation_id or f"gatekeeper-{uuid4()}"
        self.conversation_stream.record_host_message(
            conversation_id=conversation_id,
            role="user",
            text=answer,
            related_question_id=question_id,
        )
        submission_id = f"submission-{uuid4()}"
        prompt = f"Question: {question.text}\nUser answer: {answer}"
        handle = await self.gatekeeper_lifecycle.submit(
            message_kind=GatekeeperMessageKind.USER_ANSWER,
            text=prompt,
            submission_id=submission_id,
            resume=True,
        )
        snapshot = self.gatekeeper_lifecycle.snapshot()
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

    async def start_execution(self) -> WorkflowSnapshot:
        self.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)
        return self.snapshot()

    async def pause_workflow(self) -> WorkflowSnapshot:
        self.workflow_state_store.update_workflow_status(WorkflowStatus.PAUSED)
        return self.snapshot()

    async def resume_workflow(self) -> WorkflowSnapshot:
        self.workflow_state_store.update_workflow_status(WorkflowStatus.EXECUTING)
        return self.snapshot()

    async def restart_gatekeeper(self, reason: str | None = None):
        return await self.gatekeeper_lifecycle.restart_session(reason=reason)

    async def stop_gatekeeper(self):
        return await self.gatekeeper_lifecycle.stop_session()

    def conversation(self, conversation_id: str):
        return self.conversation_stream.rebuild(conversation_id)

    def subscribe_conversation(
        self,
        conversation_id: str,
        callback,
        *,
        replay: bool = False,
    ):
        return self.conversation_stream.subscribe(conversation_id, callback, replay=replay)

    def snapshot(self) -> WorkflowSnapshot:
        store_state = self.workflow_state_store.load()
        active_attempts = tuple(attempt.attempt_id for attempt in self.attempt_store.list_active())
        active_agents = tuple(
            record.identity.agent_id
            for record in self.agent_store.list()
            if record.lifecycle.status.value in {"spawning", "connecting", "running", "awaiting_input"}
        )
        pending_questions = tuple(question.question_id for question in self.question_store.list_pending())
        return WorkflowSnapshot(
            status=store_state.workflow_status,
            concurrency_limit=store_state.concurrency_limit,
            gatekeeper=store_state.gatekeeper_session,
            pending_question_ids=pending_questions,
            active_attempt_ids=active_attempts,
            active_agent_ids=active_agents,
        )
