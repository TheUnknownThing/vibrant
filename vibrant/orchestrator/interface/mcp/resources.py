"""Typed read resources for the orchestrator MCP surface."""

from __future__ import annotations

from vibrant.consensus.roadmap import RoadmapDocument
from vibrant.models.consensus import ConsensusDocument
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.interface.basic import BasicQueryAdapter
from vibrant.orchestrator.types import (
    AgentConversationView,
    AgentRunSnapshot,
    AttemptExecutionView,
    GatekeeperSessionSnapshot,
    QuestionView,
    ReviewTicket,
    WorkflowSessionSnapshot,
    WorkflowStatus,
)
from vibrant.providers.base import CanonicalEvent


class OrchestratorMCPResources:
    """Read-only resource projection over the internal MCP query surface."""

    def __init__(self, queries: BasicQueryAdapter) -> None:
        self.queries = queries

    def get_consensus(self) -> ConsensusDocument:
        return self.queries.get_consensus_document()

    def get_roadmap(self) -> RoadmapDocument:
        return self.queries.get_roadmap()

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self.queries.get_task(task_id)

    def get_workflow_status(self) -> WorkflowStatus:
        return self.queries.get_workflow_status()

    def get_workflow_session(self) -> WorkflowSessionSnapshot:
        return self.queries.workflow_session()

    def get_gatekeeper_session(self) -> GatekeeperSessionSnapshot:
        return self.queries.gatekeeper_session()

    def list_pending_questions(self) -> list[QuestionView]:
        return self.queries.list_pending_question_records()

    def list_active_runs(self) -> list[AgentRunSnapshot]:
        return self.queries.list_active_runs()

    def list_active_attempts(self) -> list[AttemptExecutionView]:
        return self.queries.list_active_attempts()

    def get_attempt_execution(self, attempt_id: str) -> AttemptExecutionView | None:
        return self.queries.get_attempt_execution(attempt_id)

    def get_conversation(self, conversation_id: str) -> AgentConversationView | None:
        return self.queries.conversation_session(conversation_id)

    def get_review_ticket(self, ticket_id: str) -> ReviewTicket | None:
        return self.queries.get_review_ticket(ticket_id)

    def list_pending_review_tickets(self) -> list[ReviewTicket]:
        return self.queries.list_pending_review_tickets()

    def list_recent_events(self, limit: int = 20) -> list[CanonicalEvent]:
        return self.queries.list_recent_events(limit=limit)
