"""Authoritative workflow lifecycle policy."""

from __future__ import annotations

from dataclasses import dataclass, replace

from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus

from ..basic.artifacts import build_workflow_snapshot
from ..basic.stores import AgentRunStore, AttemptStore, ConsensusStore, QuestionStore, RoadmapStore, WorkflowStateStore
from ..basic.stores.gatekeeper_session import project_gatekeeper_session
from ..types import WorkflowSessionSnapshot, WorkflowSnapshot, WorkflowState, WorkflowStatus


def workflow_to_consensus_status(status: WorkflowStatus) -> ConsensusStatus:
    return {
        WorkflowStatus.INIT: ConsensusStatus.INIT,
        WorkflowStatus.PLANNING: ConsensusStatus.PLANNING,
        WorkflowStatus.EXECUTING: ConsensusStatus.EXECUTING,
        WorkflowStatus.PAUSED: ConsensusStatus.PAUSED,
        WorkflowStatus.COMPLETED: ConsensusStatus.COMPLETED,
        WorkflowStatus.FAILED: ConsensusStatus.FAILED,
    }[status]
def infer_resume_workflow_status(
    *,
    roadmap: RoadmapDocument | None,
) -> WorkflowStatus:
    if roadmap is not None and roadmap.tasks:
        return WorkflowStatus.EXECUTING
    return WorkflowStatus.PLANNING


@dataclass(slots=True)
class WorkflowSessionResource:
    workflow_state_store: WorkflowStateStore
    agent_run_store: AgentRunStore
    consensus_store: ConsensusStore
    roadmap_store: RoadmapStore | None
    question_store: QuestionStore
    attempt_store: AttemptStore

    def get(self, session_id: str | None = None) -> WorkflowSessionSnapshot:
        state = self.workflow_state_store.load()
        resolved_session_id = session_id or state.session_id
        if resolved_session_id != state.session_id:
            raise KeyError(f"Unknown workflow session: {resolved_session_id}")
        return self._project_state(state)

    def set_status(
        self,
        status: WorkflowStatus,
        *,
        resume_status: WorkflowStatus | None = None,
        preserve_resume_status: bool = False,
        session_id: str | None = None,
    ) -> WorkflowSessionSnapshot:
        snapshot = self.get(session_id)
        next_resume_status = snapshot.resume_status if preserve_resume_status else resume_status
        return self._freeze(replace(snapshot, status=status, resume_status=next_resume_status))

    def pause(self, *, session_id: str | None = None) -> WorkflowSessionSnapshot:
        snapshot = self.get(session_id)
        resume_status = snapshot.status if snapshot.status is not WorkflowStatus.PAUSED else snapshot.resume_status
        return self._freeze(
            replace(
                snapshot,
                status=WorkflowStatus.PAUSED,
                resume_status=resume_status,
            )
        )

    def resume(self, *, session_id: str | None = None) -> WorkflowSessionSnapshot:
        snapshot = self.get(session_id)
        next_status = snapshot.resume_status or infer_resume_workflow_status(
            roadmap=self.roadmap_store.load() if self.roadmap_store is not None else None,
        )
        return self._freeze(
            replace(
                snapshot,
                status=next_status,
                resume_status=None,
            )
        )

    def set_concurrency_limit(
        self,
        concurrency_limit: int,
        *,
        session_id: str | None = None,
    ) -> WorkflowSessionSnapshot:
        if concurrency_limit < 1:
            raise ValueError("concurrency_limit must be >= 1")
        return self._freeze(
            replace(
                self.get(session_id),
                concurrency_limit=concurrency_limit,
            )
        )

    def _freeze(self, snapshot: WorkflowSessionSnapshot) -> WorkflowSessionSnapshot:
        state = self.workflow_state_store.load()
        if snapshot.session_id != state.session_id:
            raise KeyError(f"Unknown workflow session: {snapshot.session_id}")
        state.workflow_status = snapshot.status
        state.resume_status = snapshot.resume_status
        state.concurrency_limit = snapshot.concurrency_limit
        state.gatekeeper_session = snapshot.gatekeeper
        state.total_agent_spawns = snapshot.total_agent_spawns
        self.workflow_state_store.save(state)
        self.consensus_store.set_status_projection(workflow_to_consensus_status(snapshot.status))
        return self._project_state(state)

    def _project_state(self, state: WorkflowState) -> WorkflowSessionSnapshot:
        gatekeeper_run_record = (
            self.agent_run_store.get(state.gatekeeper_session.run_id)
            if state.gatekeeper_session.run_id is not None
            else None
        )
        return WorkflowSessionSnapshot(
            session_id=state.session_id,
            started_at=state.started_at,
            status=state.workflow_status,
            resume_status=state.resume_status,
            concurrency_limit=state.concurrency_limit,
            gatekeeper=project_gatekeeper_session(
                state.gatekeeper_session,
                run_record=gatekeeper_run_record,
            ),
            total_agent_spawns=state.total_agent_spawns,
            pending_question_ids=tuple(question.question_id for question in self.question_store.list_pending()),
            active_attempt_ids=tuple(attempt.attempt_id for attempt in self.attempt_store.list_active()),
        )


@dataclass(slots=True)
class WorkflowPolicy:
    """Own workflow lifecycle transitions and projections."""

    workflow_state_store: WorkflowStateStore
    agent_run_store: AgentRunStore
    consensus_store: ConsensusStore
    question_store: QuestionStore
    attempt_store: AttemptStore
    roadmap_store: RoadmapStore | None = None

    def session(self) -> WorkflowSessionResource:
        return WorkflowSessionResource(
            workflow_state_store=self.workflow_state_store,
            agent_run_store=self.agent_run_store,
            consensus_store=self.consensus_store,
            roadmap_store=self.roadmap_store,
            question_store=self.question_store,
            attempt_store=self.attempt_store,
        )

    def snapshot(self) -> WorkflowSnapshot:
        return build_workflow_snapshot(
            workflow_state_store=self.workflow_state_store,
            agent_run_store=self.agent_run_store,
            question_store=self.question_store,
            attempt_store=self.attempt_store,
        )

    def set_status(self, status: WorkflowStatus) -> WorkflowSnapshot:
        if status is WorkflowStatus.PAUSED:
            self.session().pause()
        else:
            self.session().set_status(status, resume_status=None)
        return self.snapshot()

    def begin_planning(self) -> WorkflowSnapshot:
        return self.set_status(WorkflowStatus.PLANNING)

    def end_planning(self) -> WorkflowSnapshot:
        return self.set_status(WorkflowStatus.EXECUTING)

    def resume(self) -> WorkflowSnapshot:
        self.session().resume()
        return self.snapshot()

    def projected_consensus_status(self) -> ConsensusStatus:
        return workflow_to_consensus_status(self.workflow_state_store.load().workflow_status)

    def project_consensus_document(self, document: ConsensusDocument) -> ConsensusDocument:
        projected = document.model_copy(deep=True)
        projected.status = self.projected_consensus_status()
        return projected


def apply_workflow_status(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
    status: WorkflowStatus,
) -> WorkflowSnapshot:
    return WorkflowPolicy(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        question_store=question_store,
        attempt_store=attempt_store,
    ).set_status(status)


def resume_workflow(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    consensus_store: ConsensusStore,
    roadmap_store: RoadmapStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
) -> WorkflowSnapshot:
    return WorkflowPolicy(
        workflow_state_store=workflow_state_store,
        agent_run_store=agent_run_store,
        consensus_store=consensus_store,
        roadmap_store=roadmap_store,
        question_store=question_store,
        attempt_store=attempt_store,
    ).resume()


def is_execution_workflow_status(status: WorkflowStatus) -> bool:
    return status is WorkflowStatus.EXECUTING


def is_terminal_workflow_status(status: WorkflowStatus) -> bool:
    return status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}


__all__ = [
    "WorkflowPolicy",
    "WorkflowSessionResource",
    "apply_workflow_status",
    "infer_resume_workflow_status",
    "is_execution_workflow_status",
    "is_terminal_workflow_status",
    "resume_workflow",
    "workflow_to_consensus_status",
]
