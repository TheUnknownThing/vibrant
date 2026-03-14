"""Durable artifact capability bundle."""

from __future__ import annotations

from dataclasses import dataclass

from vibrant.models.agent import AgentInstanceRecord, AgentRunRecord

from ..stores import (
    AgentInstanceStore,
    AgentRunStore,
    AttemptStore,
    ConsensusStore,
    QuestionStore,
    ReviewTicketStore,
    RoadmapStore,
    WorkflowStateStore,
)
from ..stores.gatekeeper_session import project_gatekeeper_session
from ...types import WorkflowSnapshot


@dataclass(slots=True)
class ArtifactsCapability:
    """Group the durable orchestrator stores behind one typed bundle."""

    workflow_state_store: WorkflowStateStore
    attempt_store: AttemptStore
    question_store: QuestionStore
    consensus_store: ConsensusStore
    roadmap_store: RoadmapStore
    review_ticket_store: ReviewTicketStore
    agent_instance_store: AgentInstanceStore
    agent_run_store: AgentRunStore

    def workflow_snapshot(self) -> WorkflowSnapshot:
        state = self.workflow_state_store.load()
        gatekeeper_run = (
            self.agent_run_store.get(state.gatekeeper_session.run_id)
            if state.gatekeeper_session.run_id is not None
            else None
        )
        return WorkflowSnapshot(
            status=state.workflow_status,
            concurrency_limit=state.concurrency_limit,
            gatekeeper=project_gatekeeper_session(
                state.gatekeeper_session,
                run_record=gatekeeper_run,
            ),
            pending_question_ids=tuple(question.question_id for question in self.question_store.list_pending()),
            active_attempt_ids=tuple(attempt.attempt_id for attempt in self.attempt_store.list_active()),
            active_agent_ids=tuple(record.identity.agent_id for record in self.agent_run_store.list_active()),
        )

    def list_agent_instances(self) -> list[AgentInstanceRecord]:
        return self.agent_instance_store.list()

    def list_agent_runs(self) -> list[AgentRunRecord]:
        return self.agent_run_store.list()

    def list_active_agent_runs(self) -> list[AgentRunRecord]:
        return self.agent_run_store.list_active()
