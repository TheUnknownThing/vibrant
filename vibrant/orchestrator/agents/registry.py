"""Agent registry service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from vibrant.agents.runtime import ProviderThreadHandle
from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.git_manager import GitWorktreeInfo

from .store import AgentRecordStore

logger = logging.getLogger(__name__)

AgentRecordCallback = Callable[[AgentRecord], Any]
"""Callback type matching ``vibrant.agents.runtime.AgentRecordCallback``."""


class AgentRegistry:
    """Own durable agent metadata and helper construction."""

    def __init__(self, *, agent_store: AgentRecordStore, vibrant_dir: str | Path) -> None:
        self.agent_store = agent_store
        self.vibrant_dir = Path(vibrant_dir)

    def upsert(
        self,
        record: AgentRecord,
        *,
        increment_spawn: bool = False,
        rebuild_state: bool = True,
    ) -> Path:
        return self.agent_store.upsert(
            record,
            increment_spawn=increment_spawn,
            rebuild_state=rebuild_state,
        )

    def make_record_callback(self, *, increment_spawn: bool = False) -> AgentRecordCallback:
        """Return a callback that persists every record mutation.

        This is the canonical way to supply an ``on_record_updated``
        callback to an ``AgentRuntime.start()`` call from orchestrator
        services — it decouples the runtime from persistence details.
        """

        def _persist(record: AgentRecord) -> None:
            try:
                self.upsert(record, increment_spawn=increment_spawn)
            except Exception:
                logger.debug("AgentRegistry.make_record_callback: upsert failed", exc_info=True)

        return _persist

    def get(self, agent_id: str) -> AgentRecord | None:
        """Look up a persisted agent record by id."""
        return self.agent_store.get(agent_id)

    def list_records(self) -> list[AgentRecord]:
        """Return all known records in stable id order."""
        return self.agent_store.list_records()

    def list_active_records(self) -> list[AgentRecord]:
        """Return non-terminal records in stable id order."""
        return [record for record in self.list_records() if record.status not in AgentRecord.TERMINAL_STATUSES]

    def records_for_task(self, task_id: str) -> list[AgentRecord]:
        """Return records for a task ordered by start time then id."""
        return self.agent_store.records_for_task(task_id)

    def latest_for_task(self, task_id: str, *, agent_type: AgentType | None = None) -> AgentRecord | None:
        """Return the latest persisted record for a task, optionally filtered by type."""
        return self.agent_store.latest_for_task(task_id, agent_type=agent_type)

    def provider_thread_handle(self, agent_id: str) -> ProviderThreadHandle | None:
        """Return the persisted provider-thread handle for an agent, if available."""
        return self.agent_store.provider_thread_handle(agent_id)

    def create_code_agent_record(self, *, task: TaskInfo, worktree: GitWorktreeInfo, prompt: str) -> AgentRecord:
        return self.create_task_agent_record(
            agent_type=AgentType.CODE,
            task_id=task.id,
            branch=task.branch,
            worktree_path=str(worktree.path),
            prompt=prompt,
            skills=list(task.skills),
            retry_count=task.retry_count,
            max_retries=task.max_retries,
        )

    def create_task_agent_record(
        self,
        *,
        agent_type: AgentType,
        task_id: str,
        branch: str | None,
        worktree_path: str | None,
        prompt: str | None = None,
        skills: list[str] | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
        runtime_mode: str | None = None,
    ) -> AgentRecord:
        """Build a persisted-friendly record for a task-scoped agent run."""
        prefix = "agent"
        if agent_type is AgentType.MERGE:
            prefix = "merge"
        elif agent_type is AgentType.TEST:
            prefix = "test"
        agent_id = f"{prefix}-{task_id}-{uuid4().hex[:8]}"
        native_log = self.vibrant_dir / "logs" / "providers" / "native" / f"{agent_id}.ndjson"
        canonical_log = self.vibrant_dir / "logs" / "providers" / "canonical" / f"{agent_id}.ndjson"
        provider = AgentProviderMetadata(
            native_event_log=str(native_log),
            canonical_event_log=str(canonical_log),
        )
        if runtime_mode is not None:
            provider.runtime_mode = runtime_mode
        return AgentRecord(
            agent_id=agent_id,
            task_id=task_id,
            type=agent_type,
            status=AgentStatus.SPAWNING,
            branch=branch,
            worktree_path=worktree_path,
            prompt_used=prompt,
            skills_loaded=list(skills or []),
            retry_count=retry_count,
            max_retries=max_retries,
            provider=provider,
        )

    def create_merge_agent_record(self, *, task_id: str, branch: str, worktree_path: str) -> AgentRecord:
        """Build a merge-agent record for conflict-resolution flows."""
        return self.create_task_agent_record(
            agent_type=AgentType.MERGE,
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            runtime_mode="danger-full-access",
        )

    def create_test_agent_record(
        self,
        *,
        task_id: str,
        branch: str | None,
        worktree_path: str,
        prompt: str | None = None,
    ) -> AgentRecord:
        """Build a read-only validation/test agent record."""
        return self.create_task_agent_record(
            agent_type=AgentType.TEST,
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            prompt=prompt,
            runtime_mode="read-only",
        )
