"""Runtime-based Gatekeeper implementation.

The Gatekeeper now rides on the same AgentBase/BaseAgentRuntime stack as the
other agent types. It is a long-lived, read-only conversational identity whose
durable project mutations are intended to happen through MCP requests rather
than transcript parsing or direct file edits.
"""

from __future__ import annotations

import asyncio
import enum
import inspect
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from vibrant.config import DEFAULT_CONFIG_DIR, VibrantConfig, find_project_root, load_config
from vibrant.models.agent import AgentProviderMetadata, AgentRunRecord, AgentStatus, AgentType
from vibrant.providers.base import CanonicalEvent, RuntimeMode
from vibrant.providers.registry import provider_transport, resolve_provider_adapter
from vibrant.prompts import build_gatekeeper_prompt, build_user_answer_trigger_description

from .base import ReadOnlyAgentBase
from .runtime import AgentHandle, BaseAgentRuntime, NormalizedRunResult

PLANNING_COMPLETE_MCP_TOOL = "vibrant.end_planning_phase"
REQUEST_USER_DECISION_MCP_TOOL = "vibrant.request_user_decision"
WITHDRAW_QUESTION_MCP_TOOL = "vibrant.withdraw_question"
UPDATE_CONSENSUS_MCP_TOOL = "vibrant.update_consensus"
ADD_TASK_MCP_TOOL = "vibrant.add_task"
UPDATE_TASK_DEFINITION_MCP_TOOL = "vibrant.update_task_definition"
REORDER_TASKS_MCP_TOOL = "vibrant.reorder_tasks"
PAUSE_WORKFLOW_MCP_TOOL = "vibrant.pause_workflow"
RESUME_WORKFLOW_MCP_TOOL = "vibrant.resume_workflow"
ACCEPT_REVIEW_TICKET_MCP_TOOL = "vibrant.accept_review_ticket"
RETRY_REVIEW_TICKET_MCP_TOOL = "vibrant.retry_review_ticket"
ESCALATE_REVIEW_TICKET_MCP_TOOL = "vibrant.escalate_review_ticket"

MCP_TOOL_NAMES = (
    PLANNING_COMPLETE_MCP_TOOL,
    REQUEST_USER_DECISION_MCP_TOOL,
    WITHDRAW_QUESTION_MCP_TOOL,
    UPDATE_CONSENSUS_MCP_TOOL,
    ADD_TASK_MCP_TOOL,
    UPDATE_TASK_DEFINITION_MCP_TOOL,
    REORDER_TASKS_MCP_TOOL,
    PAUSE_WORKFLOW_MCP_TOOL,
    RESUME_WORKFLOW_MCP_TOOL,
    ACCEPT_REVIEW_TICKET_MCP_TOOL,
    RETRY_REVIEW_TICKET_MCP_TOOL,
    ESCALATE_REVIEW_TICKET_MCP_TOOL,
)

GatekeeperRunHandle = AgentHandle
GatekeeperRunResult = NormalizedRunResult


class GatekeeperTrigger(str, enum.Enum):
    """Supported Gatekeeper invocation triggers."""

    PROJECT_START = "project_start"
    TASK_COMPLETION = "task_completion"
    TASK_FAILURE = "task_failure"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    USER_CONVERSATION = "user_conversation"


@dataclass(slots=True)
class GatekeeperRequest:
    """Inputs required for one Gatekeeper invocation."""

    trigger: GatekeeperTrigger
    trigger_description: str
    agent_summary: str | None = None


class GatekeeperAgent(ReadOnlyAgentBase):
    """Read-only Gatekeeper agent that delegates durable state changes via MCP."""

    def __init__(
        self,
        project_root: str | Path,
        config: VibrantConfig,
        *,
        adapter_factory: Any,
        on_canonical_event: Callable[[CanonicalEvent], Any] | None = None,
        on_agent_record_updated: Callable[[AgentRunRecord], Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            project_root,
            config,
            adapter_factory=adapter_factory,
            on_canonical_event=on_canonical_event,
            on_agent_record_updated=on_agent_record_updated,
            timeout_seconds=timeout_seconds,
        )
        self.vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        self.consensus_path = self.vibrant_dir / "consensus.md"
        self.roadmap_path = self.vibrant_dir / "roadmap.md"

    def get_agent_type(self) -> AgentType:
        return AgentType.GATEKEEPER

    def should_auto_reject_requests(self) -> bool:
        return False

    def get_thread_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_thread_kwargs()
        kwargs["persist_extended_history"] = True
        return kwargs

    def build_run_record(
        self,
        request: GatekeeperRequest,
        *,
        agent_id: str | None = None,
        role: str = AgentType.GATEKEEPER.value,
        run_id: str | None = None,
    ) -> AgentRunRecord:
        resolved_agent_id = agent_id or AgentType.GATEKEEPER.value
        resolved_run_id = run_id or f"gatekeeper-{request.trigger.value}-{uuid4().hex[:8]}"
        native_log = self.vibrant_dir / "logs" / "providers" / "native" / f"{resolved_run_id}.ndjson"
        canonical_log = self.vibrant_dir / "logs" / "providers" / "canonical" / f"{resolved_run_id}.ndjson"

        return AgentRunRecord(
            identity={
                "run_id": resolved_run_id,
                "agent_id": resolved_agent_id,
                "role": role,
                "type": AgentType.GATEKEEPER,
            },
            lifecycle={"status": AgentStatus.SPAWNING},
            context={"worktree_path": str(self.project_root)},
            provider=AgentProviderMetadata(
                kind=self.config.provider_kind.value,
                transport=provider_transport(self.config.provider_kind),
                runtime_mode=RuntimeMode.READ_ONLY.codex_thread_sandbox,
                native_event_log=str(native_log),
                canonical_event_log=str(canonical_log),
            ),
        )

    def build_agent_record(self, request: GatekeeperRequest) -> AgentRunRecord:
        return self.build_run_record(request)

    def render_prompt(self, request: GatekeeperRequest) -> str:
        consensus_text = _read_text(self.consensus_path) or "No consensus document exists yet."
        roadmap_text = _read_text(self.roadmap_path) or "No roadmap document exists yet."
        skills_text = self._render_available_skills()
        return build_gatekeeper_prompt(
            project_name=self.project_root.name,
            consensus_text=consensus_text,
            roadmap_text=roadmap_text,
            trigger_value=request.trigger.value,
            trigger_description=request.trigger_description,
            agent_summary=request.agent_summary,
            skills_text=skills_text,
            mcp_tool_names=MCP_TOOL_NAMES,
        )

    def _render_available_skills(self) -> str:
        skills_dir = self.vibrant_dir / "skills"
        if not skills_dir.exists():
            return "- No project-specific skills available."

        entries: list[str] = []
        for path in sorted(item for item in skills_dir.iterdir() if item.is_file()):
            description = _extract_skill_description(path)
            entries.append(f"- {path.stem}: {description}")
        return "\n".join(entries) if entries else "- No project-specific skills available."


class Gatekeeper:
    """Thin service wrapper that runs the Gatekeeper through BaseAgentRuntime."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        config: VibrantConfig | None = None,
        adapter_factory: Any | None = None,
        on_canonical_event: Callable[[CanonicalEvent], Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.project_root = find_project_root(project_root)
        self.vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        self.agent_runs_dir = self.vibrant_dir / "agent-runs"
        self.config = config or load_config(start_path=self.project_root)

        self.agent = GatekeeperAgent(
            self.project_root,
            self.config,
            adapter_factory=adapter_factory or resolve_provider_adapter(self.config.provider_kind),
            on_canonical_event=on_canonical_event,
            timeout_seconds=timeout_seconds,
        )
        self.runtime = BaseAgentRuntime(self.agent)

    def render_prompt(self, request: GatekeeperRequest) -> str:
        return self.agent.render_prompt(request)

    def build_run_record(
        self,
        request: GatekeeperRequest,
        *,
        agent_id: str | None = None,
        role: str = AgentType.GATEKEEPER.value,
        run_id: str | None = None,
    ) -> AgentRunRecord:
        return self.agent.build_run_record(request, agent_id=agent_id, role=role, run_id=run_id)

    def build_agent_record(self, request: GatekeeperRequest) -> AgentRunRecord:
        return self.build_run_record(request)

    async def run(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        handle = await self.start_run(
            request,
            resume_latest_thread=resume_latest_thread,
        )
        return await handle.wait()

    async def start_run(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
        on_record_updated: Callable[[AgentRunRecord], Any] | None = None,
        on_result: Callable[[GatekeeperRunResult], Any] | None = None,
    ) -> GatekeeperRunHandle:
        prompt = self.render_prompt(request)
        agent_record = self.build_run_record(request)
        agent_record.context.prompt_used = prompt

        should_resume = (
            resume_latest_thread
            if resume_latest_thread is not None
            else request.trigger is GatekeeperTrigger.USER_CONVERSATION
        )
        resume_thread_id = self._find_latest_gatekeeper_thread_id() if should_resume else None
        record_callback = on_record_updated or self._persist_agent_record

        handle = await self.runtime.start(
            agent_record=agent_record,
            prompt=prompt,
            cwd=str(self.project_root),
            resume_thread_id=resume_thread_id,
            on_record_updated=record_callback,
        )
        setattr(handle, "agent_record", agent_record)
        setattr(handle, "request", request)
        setattr(handle, "prompt", prompt)

        if on_result is not None:
            asyncio.create_task(
                self._forward_result(handle, on_result),
                name=f"gatekeeper-result-callback-{agent_record.identity.agent_id}",
            )

        return handle

    async def answer_question(self, question: str, answer: str) -> GatekeeperRunResult:
        request = GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description=build_user_answer_trigger_description(question=question, answer=answer),
            agent_summary=answer,
        )
        return await self.run(request, resume_latest_thread=True)

    async def start_answer_question(
        self,
        question: str,
        answer: str,
        *,
        on_record_updated: Callable[[AgentRunRecord], Any] | None = None,
        on_result: Callable[[GatekeeperRunResult], Any] | None = None,
    ) -> GatekeeperRunHandle:
        request = GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description=build_user_answer_trigger_description(question=question, answer=answer),
            agent_summary=answer,
        )
        return await self.start_run(
            request,
            resume_latest_thread=True,
            on_record_updated=on_record_updated,
            on_result=on_result,
        )

    async def _forward_result(
        self,
        handle: GatekeeperRunHandle,
        callback: Callable[[GatekeeperRunResult], Any],
    ) -> None:
        result = await handle.wait()
        callback_result = callback(result)
        if inspect.isawaitable(callback_result):
            await callback_result

    def _find_latest_gatekeeper_thread_id(self) -> str | None:
        candidate_paths: list[Path] = []
        if self.agent_runs_dir.exists():
            candidate_paths.extend(sorted(self.agent_runs_dir.glob("*.json")))
        if not candidate_paths:
            return None

        latest_record: AgentRunRecord | None = None
        latest_sort_key: tuple[datetime, datetime] | None = None
        for path in candidate_paths:
            try:
                record = AgentRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if record.identity.role != AgentType.GATEKEEPER.value:
                continue

            thread_id = record.provider.provider_thread_id
            if not thread_id:
                continue

            started = record.lifecycle.started_at or datetime.min.replace(tzinfo=timezone.utc)
            finished = record.lifecycle.finished_at or started
            sort_key = (started, finished)
            if latest_sort_key is None or sort_key > latest_sort_key:
                latest_record = record
                latest_sort_key = sort_key

        if latest_record is None:
            return None

        return latest_record.provider.provider_thread_id

    def _persist_agent_record(self, agent_record: AgentRunRecord) -> None:
        self.agent_runs_dir.mkdir(parents=True, exist_ok=True)
        path = self.agent_runs_dir / f"{agent_record.identity.run_id}.json"
        _atomic_write_text(path, agent_record.model_dump_json(indent=2) + "\n")


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _extract_skill_description(path: Path) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        return line
    return "No description provided."


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
