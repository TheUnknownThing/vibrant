"""UI-facing interface adapter used by the facade and TUI."""

from __future__ import annotations

from dataclasses import dataclass

from .backend import OrchestratorBackend


@dataclass(slots=True)
class InterfaceControlPlane:
    """Compose explicit command and query adapters behind one object."""

    backend: OrchestratorBackend

    async def submit_user_input(self, text: str, question_id: str | None = None):
        return await self.backend.commands.submit_user_input(text, question_id=question_id)

    async def wait_for_gatekeeper_submission(self, submission):
        return await self.backend.commands.wait_for_gatekeeper_submission(submission)

    async def respond_to_gatekeeper_request(
        self,
        run_id: str,
        request_id: int | str,
        *,
        result: object | None = None,
        error: dict[str, object] | None = None,
    ):
        return await self.backend.commands.respond_to_gatekeeper_request(
            run_id,
            request_id,
            result=result,
            error=error,
        )

    def end_planning_phase(self):
        return self.backend.commands.end_planning_phase()

    def pause_workflow(self):
        return self.backend.commands.pause_workflow()

    def resume_workflow(self):
        return self.backend.commands.resume_workflow()

    def set_workflow_status(self, status):
        return self.backend.commands.set_workflow_status(status)

    async def restart_gatekeeper(self, reason: str | None = None):
        return await self.backend.commands.restart_gatekeeper(reason)

    async def stop_gatekeeper(self):
        return await self.backend.commands.stop_gatekeeper()

    async def interrupt_gatekeeper(self):
        return await self.backend.commands.interrupt_gatekeeper()

    async def run_next_task(self):
        return await self.backend.commands.run_next_task()

    async def run_until_blocked(self):
        return await self.backend.commands.run_until_blocked()

    def workflow_snapshot(self):
        return self.backend.queries.workflow_snapshot()

    def workflow_session(self):
        return self.backend.queries.workflow_session()

    def gatekeeper_state(self):
        return self.backend.queries.gatekeeper_state()

    def gatekeeper_session(self):
        return self.backend.queries.gatekeeper_session()

    def task_loop_state(self):
        return self.backend.queries.task_loop_state()

    def gatekeeper_conversation_id(self) -> str | None:
        return self.backend.queries.gatekeeper_conversation_id()

    def conversation(self, conversation_id: str):
        return self.backend.queries.conversation(conversation_id)

    def conversation_session(self, conversation_id: str):
        return self.backend.queries.conversation_session(conversation_id)

    def subscribe_conversation(self, conversation_id: str, callback, *, replay: bool = False):
        return self.backend.queries.subscribe_conversation(conversation_id, callback, replay=replay)

    def subscribe_runtime_events(
        self,
        callback,
        *,
        agent_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        event_types=None,
    ):
        return self.backend.queries.subscribe_runtime_events(
            callback,
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            event_types=event_types,
        )

    def list_recent_events(self, *, limit: int = 20):
        return self.backend.queries.list_recent_events(limit=limit)

    def task_id_for_run(self, run_id: str) -> str | None:
        return self.backend.queries.task_id_for_run(run_id)

    def run_task_ids(self) -> dict[str, str]:
        return self.backend.queries.run_task_ids()

    def get_workflow_status(self):
        return self.backend.queries.get_workflow_status()

    def get_consensus_document(self):
        return self.backend.queries.get_consensus_document()

    def get_roadmap(self):
        return self.backend.queries.get_roadmap()

    def get_task(self, task_id: str):
        return self.backend.queries.get_task(task_id)

    def list_roles(self):
        return self.backend.queries.list_roles()

    def get_role(self, role: str):
        return self.backend.queries.get_role(role)

    def list_instances(self):
        return self.backend.queries.list_instances()

    def get_instance(self, agent_id: str):
        return self.backend.queries.get_instance(agent_id)

    def list_runs(self):
        return self.backend.queries.list_runs()

    def list_active_runs(self):
        return self.backend.queries.list_active_runs()

    def get_run(self, run_id: str):
        return self.backend.queries.get_run(run_id)

    def list_question_records(self):
        return self.backend.queries.list_question_records()

    def get_question(self, question_id: str):
        return self.backend.queries.get_question(question_id)

    def list_pending_question_records(self):
        return self.backend.queries.list_pending_question_records()

    def list_active_attempts(self):
        return self.backend.queries.list_active_attempts()

    def list_attempt_executions(self, *, task_id: str | None = None, status=None):
        return self.backend.queries.list_attempt_executions(task_id=task_id, status=status)

    def get_attempt_execution(self, attempt_id: str):
        return self.backend.queries.get_attempt_execution(attempt_id)

    def get_review_ticket(self, ticket_id: str):
        return self.backend.queries.get_review_ticket(ticket_id)

    def list_review_tickets(self, *, task_id: str | None = None, status=None):
        return self.backend.queries.list_review_tickets(task_id=task_id, status=status)

    def list_pending_review_tickets(self):
        return self.backend.queries.list_pending_review_tickets()

    def gatekeeper_busy(self) -> bool:
        return self.backend.queries.gatekeeper_busy()

    def add_task(self, task, *, index: int | None = None):
        return self.backend.commands.add_task(task, index=index)

    def update_task_definition(self, task_id: str, **patch):
        return self.backend.commands.update_task_definition(task_id, **patch)

    def reorder_tasks(self, ordered_task_ids: list[str]):
        return self.backend.commands.reorder_tasks(ordered_task_ids)

    def replace_roadmap(self, *, tasks, project: str | None = None):
        return self.backend.commands.replace_roadmap(tasks=tasks, project=project)

    def update_consensus(self, *, status=None, context: str | None = None):
        return self.backend.commands.update_consensus(status=status, context=context)

    def write_consensus_document(self, document):
        return self.backend.commands.write_consensus_document(document)

    def request_user_decision(self, text: str, **kwargs):
        return self.backend.commands.request_user_decision(text, **kwargs)

    def withdraw_question(self, question_id: str, *, reason: str | None = None):
        return self.backend.commands.withdraw_question(question_id, reason=reason)

    def accept_review_ticket(self, ticket_id: str):
        return self.backend.commands.accept_review_ticket(ticket_id)

    def retry_review_ticket(self, ticket_id: str, **kwargs):
        return self.backend.commands.retry_review_ticket(ticket_id, **kwargs)

    def escalate_review_ticket(self, ticket_id: str, *, reason: str):
        return self.backend.commands.escalate_review_ticket(ticket_id, reason=reason)
