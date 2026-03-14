"""Typed read resources for the orchestrator MCP surface."""

from __future__ import annotations

from typing import Any


class OrchestratorMCPResources:
    """Read-only resource projection over the internal MCP query surface."""

    def __init__(self, queries: Any) -> None:
        self.queries = queries

    def get_consensus(self) -> Any:
        return self.queries.get_consensus_document()

    def get_roadmap(self) -> Any:
        return self.queries.get_roadmap()

    def get_task(self, task_id: str) -> Any:
        return self.queries.get_task(task_id)

    def get_workflow_status(self) -> Any:
        return self.queries.get_workflow_status()

    def list_pending_questions(self) -> Any:
        return self.queries.list_pending_question_records()

    def list_active_agents(self) -> Any:
        return self.queries.list_active_agents()

    def list_active_attempts(self) -> Any:
        return self.queries.list_active_attempts()

    def get_review_ticket(self, ticket_id: str) -> Any:
        return self.queries.get_review_ticket(ticket_id)

    def list_pending_review_tickets(self) -> Any:
        return self.queries.list_pending_review_tickets()

    def list_recent_events(self, limit: int = 20) -> Any:
        return self.queries.list_recent_events(limit=limit)
