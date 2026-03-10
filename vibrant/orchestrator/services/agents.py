"""Agent registry service."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.git_manager import GitWorktreeInfo
from vibrant.orchestrator.engine import OrchestratorEngine


class AgentRegistry:
    """Own durable agent metadata and helper construction."""

    def __init__(self, *, engine: OrchestratorEngine, vibrant_dir: str | Path) -> None:
        self.engine = engine
        self.vibrant_dir = Path(vibrant_dir)

    def upsert(self, record: AgentRecord, *, increment_spawn: bool = False) -> Path:
        return self.engine.upsert_agent_record(record, increment_spawn=increment_spawn)

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
