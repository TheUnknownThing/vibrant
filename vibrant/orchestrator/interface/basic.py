"""Read/query adapter over basic capabilities and policy snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..basic import ArtifactsCapability, ConversationCapability, EventLogCapability
from ..basic.runtime import AgentRuntimeService
from ..policy.gatekeeper_loop import GatekeeperUserLoop
from ..policy.task_loop import TaskLoop
from ..types import ConversationSummary


@dataclass(slots=True)
class BasicQueryAdapter:
    """Expose coherent read models for first-party consumers."""

    artifacts: ArtifactsCapability
    conversations: ConversationCapability
    runtime_service: AgentRuntimeService
    event_log: EventLogCapability
    gatekeeper_loop: GatekeeperUserLoop
    task_loop: TaskLoop

    def workflow_snapshot(self):
        return self.artifacts.workflow_snapshot()

    def get_workflow_status(self):
        return self.artifacts.workflow_state_store.load().workflow_status

    def gatekeeper_state(self):
        return self.gatekeeper_loop.snapshot()

    def task_loop_state(self):
        return self.task_loop.snapshot()

    def gatekeeper_conversation_id(self) -> str | None:
        return self.gatekeeper_loop.snapshot().conversation_id

    def conversation(self, conversation_id: str):
        return self.gatekeeper_loop.conversation(conversation_id)

    def list_conversation_summaries(self) -> list[ConversationSummary]:
        return [
            ConversationSummary(
                conversation_id=manifest.conversation_id,
                agent_ids=list(manifest.agent_ids),
                task_ids=list(manifest.task_ids),
                provider_thread_id=manifest.provider_thread_id,
                active_turn_id=manifest.active_turn_id,
                latest_run_id=manifest.latest_run_id,
                updated_at=manifest.updated_at,
            )
            for manifest in self.conversations.list_manifests()
        ]

    def conversation_frames(self, conversation_id: str):
        return self.conversations.load_frames(conversation_id)

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
        return self.runtime_service.subscribe_canonical_events(
            callback,
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            event_types=event_types,
        )

    def list_recent_events(self, *, limit: int = 20):
        return self.event_log.list_recent_events(limit=limit)

    def get_consensus_document(self):
        return self.artifacts.consensus_store.load()

    def get_roadmap(self):
        return self.artifacts.roadmap_store.load()

    def get_task(self, task_id: str):
        return self.artifacts.roadmap_store.get_task(task_id)

    def list_agent_instances(self):
        return self.artifacts.agent_instance_store.list()

    def list_agent_runs(self):
        return self.artifacts.agent_run_store.list()

    def list_active_agent_runs(self):
        return self.artifacts.agent_run_store.list_active()

    def get_agent_instance(self, agent_id: str):
        return self.artifacts.agent_instance_store.get(agent_id)

    def get_agent_run(self, run_id: str):
        return self.artifacts.agent_run_store.get(run_id)

    def list_agent_records(self):
        return self.list_agent_runs()

    def list_active_agents(self):
        return self.list_active_agent_runs()

    def get_agent_record(self, run_id: str):
        return self.get_agent_run(run_id)

    def list_question_records(self):
        return self.artifacts.question_store.list()

    def list_pending_question_records(self):
        return self.artifacts.question_store.list_pending()

    def list_active_attempts(self):
        return self.artifacts.attempt_store.list_active()

    def get_review_ticket(self, ticket_id: str):
        return self.task_loop.get_review_ticket(ticket_id)

    def list_pending_review_tickets(self):
        return self.task_loop.list_pending_review_tickets()

    def gatekeeper_busy(self) -> bool:
        return self.gatekeeper_loop.snapshot().busy

    def runtime_handle(self, run_id: str):
        return self.runtime_service.snapshot_handle(run_id)
