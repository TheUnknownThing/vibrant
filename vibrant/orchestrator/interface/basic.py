"""Read/query adapter over basic services and public snapshots."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Sequence

from ..basic.artifacts import build_workflow_snapshot
from ..basic.events import EventLogService
from ..basic.runtime import AgentRuntimeService
from ..basic.stores import (
    AgentInstanceStore,
    AgentRunStore,
    AttemptStore,
    ConsensusStore,
    QuestionStore,
    RoadmapStore,
    WorkflowStateStore,
)
from ..policy.gatekeeper_loop.roles import GATEKEEPER_ROLE
from ..policy.gatekeeper_loop import GatekeeperUserLoop
from ..policy.workflow import WorkflowSessionResource
from ..policy.task_loop.roles import DEFAULT_TASK_AGENT_ROLE
from ..policy.task_loop import TaskLoop
from ..types import (
    AgentInstanceIdentitySnapshot,
    AgentInstanceProviderSnapshot,
    AgentInstanceScopeSnapshot,
    AgentInstanceSnapshot,
    AgentRunIdentitySnapshot,
    AgentRunOutcomeSnapshot,
    AgentRunProviderSnapshot,
    AgentRunRuntimeSnapshot,
    AgentRunSnapshot,
    AgentRunWorkspaceSnapshot,
    AttemptExecutionView,
    AttemptStatus,
    QuestionView,
    ReviewTicket,
    ReviewTicketStatus,
    RoleSnapshot,
)

_ACTIVE_RUN_STATUSES = frozenset({"spawning", "connecting", "running", "awaiting_input"})
_DONE_RUN_STATUSES = frozenset({"completed", "failed", "killed"})


@dataclass(slots=True)
class BasicQueryAdapter:
    """Expose coherent read models for first-party consumers."""

    workflow_state_store: WorkflowStateStore
    attempt_store: AttemptStore
    question_store: QuestionStore
    consensus_store: ConsensusStore
    roadmap_store: RoadmapStore
    agent_instance_store: AgentInstanceStore
    agent_run_store: AgentRunStore
    runtime_service: AgentRuntimeService
    event_log: EventLogService
    gatekeeper_loop: GatekeeperUserLoop
    task_loop: TaskLoop

    def workflow_snapshot(self):
        return build_workflow_snapshot(
            workflow_state_store=self.workflow_state_store,
            agent_run_store=self.agent_run_store,
            question_store=self.question_store,
            attempt_store=self.attempt_store,
        )

    def workflow_session(self):
        return WorkflowSessionResource(
            workflow_state_store=self.workflow_state_store,
            agent_run_store=self.agent_run_store,
            consensus_store=self.consensus_store,
            roadmap_store=self.roadmap_store,
            question_store=self.question_store,
            attempt_store=self.attempt_store,
        ).get()

    def get_workflow_status(self):
        return self.workflow_state_store.load().workflow_status

    def gatekeeper_state(self):
        return self.gatekeeper_loop.snapshot()

    def gatekeeper_session(self):
        return self.gatekeeper_loop.snapshot().session

    def task_loop_state(self):
        return self.task_loop.snapshot()

    def gatekeeper_conversation_id(self) -> str | None:
        return self.gatekeeper_loop.snapshot().conversation_id

    def conversation(self, conversation_id: str):
        return self.gatekeeper_loop.conversation(conversation_id)

    def conversation_session(self, conversation_id: str):
        return self.gatekeeper_loop.conversation(conversation_id)

    def subscribe_conversation(self, conversation_id: str, callback, *, replay: bool = False):
        return self.gatekeeper_loop.subscribe_conversation(conversation_id, callback, replay=replay)

    def subscribe_runtime_events(
        self,
        callback,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        event_types: Sequence[str] | None = None,
    ):
        if task_id is None:
            return self.runtime_service.subscribe_canonical_events(
                callback,
                agent_id=agent_id,
                run_id=run_id,
                event_types=event_types,
            )

        async def _task_filtered(event):
            event_run_id = event.get("run_id")
            if not isinstance(event_run_id, str) or self.attempt_store.task_id_for_run(event_run_id) != task_id:
                return None
            result = callback(event)
            if inspect.isawaitable(result):
                return await result
            return result

        return self.runtime_service.subscribe_canonical_events(
            _task_filtered,
            agent_id=agent_id,
            run_id=run_id,
            event_types=event_types,
        )

    def list_recent_events(self, *, limit: int = 20):
        return self.event_log.list_recent_events(limit=limit)

    def task_id_for_run(self, run_id: str) -> str | None:
        return self.attempt_store.task_id_for_run(run_id)

    def run_task_ids(self) -> dict[str, str]:
        return self.attempt_store.run_task_ids()

    def get_consensus_document(self):
        return self.consensus_store.load()

    def get_roadmap(self):
        return self.roadmap_store.load()

    def get_task(self, task_id: str):
        return self.roadmap_store.get_task(task_id)

    def list_roles(self) -> list[RoleSnapshot]:
        instances = self.agent_instance_store.list()
        runs = self.agent_run_store.list()
        active_runs = self.agent_run_store.list_active()
        roadmap = self.roadmap_store.load()
        task_roles = {
            task.agent_role or DEFAULT_TASK_AGENT_ROLE
            for task in getattr(roadmap, "tasks", ())
        }

        observed_roles = {GATEKEEPER_ROLE, DEFAULT_TASK_AGENT_ROLE}
        observed_roles.update(task_roles)
        observed_roles.update(record.identity.role for record in instances)
        observed_roles.update(record.identity.role for record in runs)

        snapshots: list[RoleSnapshot] = []
        for role in sorted(observed_roles):
            scope_types = sorted(
                {
                    record.scope.scope_type
                    for record in instances
                    if record.identity.role == role
                }
            )
            if not scope_types:
                if role == GATEKEEPER_ROLE:
                    scope_types = ["project"]
                elif role in task_roles or role == DEFAULT_TASK_AGENT_ROLE:
                    scope_types = ["task"]
            snapshots.append(
                RoleSnapshot(
                    role=role,
                    scope_types=tuple(scope_types),
                    instance_count=sum(1 for record in instances if record.identity.role == role),
                    active_run_count=sum(1 for record in active_runs if record.identity.role == role),
                )
            )
        return snapshots

    def get_role(self, role: str) -> RoleSnapshot | None:
        normalized = role.strip().lower()
        return next((snapshot for snapshot in self.list_roles() if snapshot.role == normalized), None)

    def list_instances(self) -> list[AgentInstanceSnapshot]:
        return [self._project_instance(record) for record in self.agent_instance_store.list()]

    def get_instance(self, agent_id: str) -> AgentInstanceSnapshot | None:
        record = self.agent_instance_store.get(agent_id)
        if record is None:
            return None
        return self._project_instance(record)

    def list_runs(self) -> list[AgentRunSnapshot]:
        return [self._project_run(record) for record in self.agent_run_store.list()]

    def list_active_runs(self) -> list[AgentRunSnapshot]:
        return [self._project_run(record) for record in self.agent_run_store.list_active()]

    def get_run(self, run_id: str) -> AgentRunSnapshot | None:
        record = self.agent_run_store.get(run_id)
        if record is None:
            return None
        return self._project_run(record)

    def list_question_records(self) -> list[QuestionView]:
        return [QuestionView.from_record(record) for record in self.question_store.list()]

    def get_question(self, question_id: str) -> QuestionView | None:
        record = self.question_store.get(question_id)
        if record is None:
            return None
        return QuestionView.from_record(record)

    def list_pending_question_records(self) -> list[QuestionView]:
        return [QuestionView.from_record(record) for record in self.question_store.list_pending()]

    def list_active_attempts(self):
        return self.attempt_store.list_active()

    def list_attempt_executions(
        self,
        *,
        task_id: str | None = None,
        status: AttemptStatus | None = None,
    ) -> list[AttemptExecutionView]:
        return self.task_loop.execution.list_attempt_executions(task_id=task_id, status=status)

    def get_attempt_execution(self, attempt_id: str):
        return self.task_loop.execution.attempt_execution(attempt_id)

    def get_review_ticket(self, ticket_id: str):
        return self.task_loop.get_review_ticket(ticket_id)

    def list_review_tickets(
        self,
        *,
        task_id: str | None = None,
        status: ReviewTicketStatus | None = None,
    ) -> list[ReviewTicket]:
        return self.task_loop.list_review_tickets(task_id=task_id, status=status)

    def list_pending_review_tickets(self):
        return self.task_loop.list_pending_review_tickets()

    def gatekeeper_busy(self) -> bool:
        return self.gatekeeper_loop.snapshot().busy

    def _project_instance(self, record) -> AgentInstanceSnapshot:
        return AgentInstanceSnapshot(
            identity=AgentInstanceIdentitySnapshot(
                agent_id=record.identity.agent_id,
                role=record.identity.role,
            ),
            scope=AgentInstanceScopeSnapshot(
                scope_type=record.scope.scope_type,
                scope_id=record.scope.scope_id,
            ),
            provider=AgentInstanceProviderSnapshot(
                kind=record.provider.kind,
                transport=record.provider.transport,
                runtime_mode=record.provider.runtime_mode,
            ),
            latest_run_id=record.latest_run_id,
            active_run_id=record.active_run_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _project_run(self, record) -> AgentRunSnapshot:
        state = record.lifecycle.status.value
        awaiting_input = False
        input_requests = []
        has_handle = False
        try:
            runtime_snapshot = self.runtime_service.snapshot_handle(record.identity.run_id)
        except KeyError:
            pass
        else:
            state = runtime_snapshot.state
            awaiting_input = runtime_snapshot.awaiting_input
            input_requests = runtime_snapshot.input_requests
            has_handle = True

        return AgentRunSnapshot(
            identity=AgentRunIdentitySnapshot(
                agent_id=record.identity.agent_id,
                run_id=record.identity.run_id,
                role=record.identity.role,
            ),
            runtime=AgentRunRuntimeSnapshot(
                status=record.lifecycle.status.value,
                state=state,
                has_handle=has_handle,
                active=record.lifecycle.status.value in _ACTIVE_RUN_STATUSES,
                done=record.lifecycle.status.value in _DONE_RUN_STATUSES,
                awaiting_input=awaiting_input,
                pid=record.lifecycle.pid,
                started_at=record.lifecycle.started_at,
                finished_at=record.lifecycle.finished_at,
                input_requests=input_requests,
            ),
            workspace=AgentRunWorkspaceSnapshot(
                branch=record.context.branch,
                worktree_path=record.context.worktree_path,
            ),
            outcome=AgentRunOutcomeSnapshot(
                summary=record.outcome.summary,
                error=record.outcome.error,
                output=None,
            ),
            provider=AgentRunProviderSnapshot(
                thread_id=record.provider.provider_thread_id,
                thread_path=record.provider.thread_path,
                resume_cursor=record.provider.resume_cursor,
                native_event_log=record.provider.native_event_log,
                canonical_event_log=record.provider.canonical_event_log,
            ),
        )
