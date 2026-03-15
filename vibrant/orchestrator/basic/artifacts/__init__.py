"""Helpers for projecting durable orchestrator artifact state."""

from __future__ import annotations

from ..stores import AgentRunStore, AttemptStore, QuestionStore, WorkflowStateStore
from ..stores.gatekeeper_session import project_gatekeeper_session
from ...types import WorkflowSnapshot


def build_workflow_snapshot(
    *,
    workflow_state_store: WorkflowStateStore,
    agent_run_store: AgentRunStore,
    question_store: QuestionStore,
    attempt_store: AttemptStore,
) -> WorkflowSnapshot:
    state = workflow_state_store.load()
    gatekeeper_run_record = (
        agent_run_store.get(state.gatekeeper_session.run_id)
        if state.gatekeeper_session.run_id is not None
        else None
    )
    return WorkflowSnapshot(
        status=state.workflow_status,
        concurrency_limit=state.concurrency_limit,
        gatekeeper=project_gatekeeper_session(
            state.gatekeeper_session,
            run_record=gatekeeper_run_record,
        ),
        pending_question_ids=tuple(question.question_id for question in question_store.list_pending()),
        active_attempt_ids=tuple(attempt.attempt_id for attempt in attempt_store.list_active()),
    )


__all__ = ["build_workflow_snapshot"]
