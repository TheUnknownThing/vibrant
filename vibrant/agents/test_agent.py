"""TestAgent — read-only agent for validation/testing runs."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from vibrant.models.agent import AgentProviderMetadata, AgentRunRecord, AgentStatus, AgentType
from vibrant.providers.registry import provider_transport

from .base import ReadOnlyAgentBase

PYCUA_SUBMODULE_PATH = "tools/pyCUA"
PYCUA_SERVER_ID = "pycua"
PYCUA_TOOL_NAME = "computer"


class TestAgent(ReadOnlyAgentBase):
    """Agent that validates completed task work in a read-only workspace."""

    __test__ = False

    def get_agent_type(self) -> AgentType:
        return AgentType.TEST

    def build_run_record(
        self,
        *,
        task_id: str,
        branch: str,
        workspace_path: str,
        prompt: str,
        agent_id: str | None = None,
        role: str | None = None,
        run_id: str | None = None,
        vibrant_dir: str | Path | None = None,
    ) -> AgentRunRecord:
        resolved_agent_id = agent_id or f"test-{task_id}"
        resolved_run_id = run_id or f"run-test-{task_id}-{uuid4().hex[:8]}"
        resolved_role = role or AgentType.TEST.value

        provider_kwargs: dict[str, str | None] = {}
        if vibrant_dir is not None:
            vdir = Path(vibrant_dir)
            native_log = vdir / "logs" / "providers" / "native" / f"{resolved_run_id}.ndjson"
            canonical_log = vdir / "logs" / "providers" / "canonical" / f"{resolved_run_id}.ndjson"
            provider_kwargs["native_event_log"] = str(native_log)
            provider_kwargs["canonical_event_log"] = str(canonical_log)

        return AgentRunRecord(
            identity={
                "run_id": resolved_run_id,
                "agent_id": resolved_agent_id,
                "role": resolved_role,
                "type": AgentType.TEST,
            },
            lifecycle={"status": AgentStatus.SPAWNING},
            context={
                "branch": branch,
                "worktree_path": workspace_path,
                "prompt_used": prompt,
            },
            provider=AgentProviderMetadata(
                kind=self.config.provider_kind.value,
                transport=provider_transport(self.config.provider_kind),
                runtime_mode="read-only",
                **provider_kwargs,
            ),
        )
