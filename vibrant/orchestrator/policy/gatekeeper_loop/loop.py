"""Gatekeeper user loop policy."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.task import TaskInfo

from ...basic import ArtifactsCapability, ConversationCapability, AgentRuntimeCapability
from ...types import QuestionPriority, QuestionStatus, WorkflowSnapshot, WorkflowStatus
from .lifecycle import GatekeeperLifecycleService
from .models import GatekeeperLoopState, GatekeeperSubmission
from .questions import current_pending_question, normalize_question_scope, require_pending_question
from .requests import build_user_submission_request
from .transitions import end_planning as end_planning_transition
from .transitions import resume_workflow as resume_workflow_transition
from .transitions import set_workflow_status


@dataclass(slots=True)
class GatekeeperUserLoop:
    """Own the authoritative user <-> Gatekeeper submission flow."""

    project_name: str
    artifacts: ArtifactsCapability
    conversations: ConversationCapability
    runtime: AgentRuntimeCapability
    lifecycle: GatekeeperLifecycleService
    _last_submission_id: str | None = None
    _last_error: str | None = None

    async def submit_user_input(self, text: str, question_id: str | None = None) -> GatekeeperSubmission:
        session = await self.lifecycle.resume_or_start()
        conversation_id = session.conversation_id or f"gatekeeper-{uuid4().hex[:12]}"
        if question_id is None:
            pending_question = current_pending_question(self.artifacts.question_store.list_pending())
        else:
            pending_question = require_pending_question(self.artifacts.question_store.get(question_id), question_id)
        prepared = build_user_submission_request(text, pending_question)

        if session.agent_id:
            self.conversations.bind_agent(
                conversation_id=conversation_id,
                agent_id=session.agent_id,
                run_id=session.run_id,
                task_id=None,
                provider_thread_id=session.provider_thread_id,
            )

        self.conversations.record_host_message(
            conversation_id=conversation_id,
            role="user",
            text=text,
            related_question_id=prepared.related_question_id,
        )
        submission_id = f"submission-{uuid4()}"
        self._last_submission_id = submission_id

        try:
            handle = await self.lifecycle.submit(
                request=prepared.request,
                submission_id=submission_id,
                resume=True,
            )
        except Exception as exc:
            self._last_error = str(exc)
            raise

        self._last_error = None
        snapshot = self.lifecycle.snapshot()
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

    async def wait_for_submission(self, submission: GatekeeperSubmission):
        if not submission.run_id:
            raise RuntimeError("Gatekeeper submission did not produce a run id")
        result = await self.runtime.wait_for_run(submission.run_id)
        result_error = getattr(result, "error", None)
        if result_error:
            self._last_error = result_error
            return result
        if submission.question_id is not None:
            record = self.artifacts.question_store.get(submission.question_id)
            if record is not None and record.status is QuestionStatus.PENDING:
                self.artifacts.question_store.resolve(
                    submission.question_id,
                    answer=submission.answer_text,
                )
        self._last_error = None
        return result

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
    ):
        return self.artifacts.question_store.create(
            text=text,
            priority=priority,
            source_role=source_role,
            source_agent_id=source_agent_id,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
            blocking_scope=normalize_question_scope(blocking_scope),
            task_id=task_id,
        )

    def withdraw_question(self, question_id: str, *, reason: str | None = None):
        return self.artifacts.question_store.withdraw(question_id, reason=reason)

    def transition_workflow(self, status: WorkflowStatus) -> WorkflowSnapshot:
        return set_workflow_status(self.artifacts, status)

    def end_planning(self) -> WorkflowSnapshot:
        return end_planning_transition(self.artifacts)

    def resume_workflow(self) -> WorkflowSnapshot:
        return resume_workflow_transition(self.artifacts)

    def add_task(self, task: TaskInfo, *, index: int | None = None) -> TaskInfo:
        self.artifacts.roadmap_store.add_task(task, index=index)
        created = self.artifacts.roadmap_store.get_task(task.id)
        if created is None:
            raise KeyError(task.id)
        return created

    def update_task_definition(self, task_id: str, **patch: object) -> TaskInfo:
        return self.artifacts.roadmap_store.update_task_definition(task_id, patch)

    def reorder_tasks(self, ordered_task_ids: list[str]) -> RoadmapDocument:
        return self.artifacts.roadmap_store.reorder_tasks(ordered_task_ids)

    def replace_roadmap(self, *, tasks: list[TaskInfo], project: str | None = None) -> RoadmapDocument:
        return self.artifacts.roadmap_store.replace(tasks=tasks, project=project or self.project_name)

    def update_consensus(
        self,
        *,
        status: ConsensusStatus | str | None = None,
        context: str | None = None,
    ) -> ConsensusDocument:
        document = self.artifacts.consensus_store.load() or ConsensusDocument(project=self.project_name)
        if context is not None:
            document.context = context
        if status is not None:
            document.status = status if isinstance(status, ConsensusStatus) else ConsensusStatus(str(status).upper())
        return self.artifacts.consensus_store.write(document)

    def append_decision(self, **kwargs: object) -> ConsensusDocument:
        return self.artifacts.consensus_store.append_decision(**kwargs)

    def write_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        return self.artifacts.consensus_store.write(document)

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
