"""Gatekeeper user loop policy facade."""

from __future__ import annotations

from dataclasses import dataclass
from ...basic.conversation import ConversationStreamService
from ...basic.runtime import AgentRuntimeService
from ...basic.stores import AgentRunStore, AttemptStore, ConsensusStore, QuestionStore, RoadmapStore, WorkflowStateStore
from ...types import QuestionPriority, WorkflowSnapshot, WorkflowStatus
from . import commands, submission as submission_flow
from .lifecycle import GatekeeperLifecycleService
from .models import GatekeeperLoopState, GatekeeperSubmission


@dataclass(slots=True)
class GatekeeperUserLoop:
    """Own the authoritative user <-> Gatekeeper submission flow."""

    project_name: str
    workflow_state_store: WorkflowStateStore
    agent_run_store: AgentRunStore
    attempt_store: AttemptStore
    question_store: QuestionStore
    consensus_store: ConsensusStore
    roadmap_store: RoadmapStore
    conversation_service: ConversationStreamService
    runtime_service: AgentRuntimeService
    lifecycle: GatekeeperLifecycleService
    _last_submission_id: str | None = None
    _last_error: str | None = None

    async def submit_user_input(self, text: str, question_id: str | None = None) -> GatekeeperSubmission:
        return await submission_flow.submit_user_input(self, text, question_id=question_id)

    async def wait_for_submission(self, submission: GatekeeperSubmission) -> object:
        return await submission_flow.wait_for_submission(self, submission)

    async def restart(self, reason: str | None = None) -> GatekeeperLoopState:
        await self.lifecycle.restart_session(reason=reason)
        self._last_error = self.lifecycle.snapshot().last_error
        return self.snapshot()

    async def stop(self) -> GatekeeperLoopState:
        await self.lifecycle.stop_session()
        self._last_error = self.lifecycle.snapshot().last_error
        return self.snapshot()

    def request_user_decision(
        self,
        text: str,
        *,
        priority: QuestionPriority = QuestionPriority.BLOCKING,
        blocking_scope: str = "planning",
        task_id: str | None = None,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        source_conversation_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> object:
        return commands.request_user_decision(
            self,
            text,
            priority=priority,
            blocking_scope=blocking_scope,
            task_id=task_id,
            source_agent_id=source_agent_id,
            source_role=source_role,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
        )

    def withdraw_question(self, question_id: str, *, reason: str | None = None) -> object:
        return commands.withdraw_question(self, question_id, reason=reason)

    def transition_workflow(self, status: WorkflowStatus) -> WorkflowSnapshot:
        return commands.transition_workflow(self, status)

    def end_planning(self) -> WorkflowSnapshot:
        return commands.end_planning(self)

    def resume_workflow(self) -> WorkflowSnapshot:
        return commands.resume_workflow(self)

    def add_task(self, task, *, index: int | None = None) -> object:
        return commands.add_task(self, task, index=index)

    def update_task_definition(self, task_id: str, **patch: object) -> object:
        return commands.update_task_definition(self, task_id, **patch)

    def reorder_tasks(self, ordered_task_ids: list[str]) -> object:
        return commands.reorder_tasks(self, ordered_task_ids)

    def replace_roadmap(self, *, tasks, project: str | None = None) -> object:
        return commands.replace_roadmap(self, tasks=tasks, project=project)

    def update_consensus(self, *, status=None, context: str | None = None) -> object:
        return commands.update_consensus(self, status=status, context=context)

    def write_consensus_document(self, document) -> object:
        return commands.write_consensus_document(self, document)

    def snapshot(self) -> GatekeeperLoopState:
        return submission_flow.snapshot(self)

    def workflow_snapshot(self) -> WorkflowSnapshot:
        return submission_flow.workflow_snapshot(self)

    def conversation(self, conversation_id: str) -> object:
        return submission_flow.conversation(self, conversation_id)

    def subscribe_conversation(self, conversation_id: str, callback, *, replay: bool = False) -> object:
        return submission_flow.subscribe_conversation(self, conversation_id, callback, replay=replay)
