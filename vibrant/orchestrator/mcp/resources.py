"""Typed read resources for the redesigned MCP surface."""

from __future__ import annotations

from typing import Any

from .common import call_backend, serialize_value


class OrchestratorMCPResources:
    """Read-only resource projection over the orchestrator backend."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    def get_consensus(self) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "get_consensus_document",
                    "get_consensus",
                    "consensus_store.load",
                ),
            )
        )

    def get_roadmap(self) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "get_roadmap",
                    "roadmap_store.load",
                    "roadmap_service.load",
                ),
            )
        )

    def get_task(self, task_id: str) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "get_task",
                    "roadmap_store.get_task",
                    "roadmap_service.get_task",
                ),
                task_id,
            )
        )

    def get_workflow_status(self) -> Any:
        if hasattr(self.backend, "get_workflow_status"):
            return serialize_value(self.backend.get_workflow_status())
        snapshot = call_backend(self.backend, ("snapshot", "control_plane.snapshot"))
        if hasattr(snapshot, "status"):
            return serialize_value(getattr(snapshot, "status"))
        if hasattr(snapshot, "workflow_status"):
            return serialize_value(getattr(snapshot, "workflow_status"))
        return serialize_value(snapshot)

    def list_pending_questions(self) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "list_pending_question_records",
                    "question_store.list_pending",
                    "question_service.list_pending",
                ),
            )
        )

    def list_active_agents(self) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "list_active_agents",
                    "agent_record_store.list_active",
                    "agent_registry.list_active",
                ),
            )
        )

    def list_active_attempts(self) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "list_active_attempts",
                    "attempt_store.list_active",
                ),
            )
        )

    def get_review_ticket(self, ticket_id: str) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "get_review_ticket",
                    "review_control.get_ticket",
                    "review_ticket_store.get",
                ),
                ticket_id,
            )
        )

    def list_pending_review_tickets(self) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "list_pending_review_tickets",
                    "review_control.list_pending",
                    "review_ticket_store.list_pending",
                ),
            )
        )

    def list_recent_events(self, limit: int = 20) -> Any:
        return serialize_value(
            call_backend(
                self.backend,
                (
                    "list_recent_events",
                    "event_log.list_recent",
                ),
                limit=limit,
            )
        )
