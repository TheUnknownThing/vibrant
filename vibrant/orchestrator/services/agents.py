"""Agent registry service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.git_manager import GitWorktreeInfo
from vibrant.orchestrator.engine import OrchestratorEngine

logger = logging.getLogger(__name__)

AgentRecordCallback = Callable[[AgentRecord], Any]
"""Callback type matching ``vibrant.agents.runtime.AgentRecordCallback``."""


class AgentRegistry:
    """Own durable agent metadata and helper construction."""

    def __init__(self, *, engine: OrchestratorEngine, vibrant_dir: str | Path) -> None:
        self.engine = engine
        self.vibrant_dir = Path(vibrant_dir)

    def upsert(self, record: AgentRecord, *, increment_spawn: bool = False) -> Path:
        return self.engine.upsert_agent_record(record, increment_spawn=increment_spawn)

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
        return self.engine.agents.get(agent_id)

    def create_code_agent_record(self, *, task: TaskInfo, worktree: GitWorktreeInfo, prompt: str) -> AgentRecord:
        agent_id = f"agent-{task.id}-{uuid4().hex[:8]}"
        native_log = self.vibrant_dir / "logs" / "providers" / "native" / f"{agent_id}.ndjson"
        canonical_log = self.vibrant_dir / "logs" / "providers" / "canonical" / f"{agent_id}.ndjson"
        return AgentRecord(
            agent_id=agent_id,
            task_id=task.id,
            type=AgentType.CODE,
            status=AgentStatus.SPAWNING,
            branch=task.branch,
            worktree_path=str(worktree.path),
            prompt_used=prompt,
            skills_loaded=list(task.skills),
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            provider=AgentProviderMetadata(
                native_event_log=str(native_log),
                canonical_event_log=str(canonical_log),
            ),
        )
