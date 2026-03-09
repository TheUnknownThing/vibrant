"""Code-agent execution coordinator for the Phase 5.1 lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, find_project_root, load_config
from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapDocument, RoadmapParser
from vibrant.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.gatekeeper.gatekeeper import _extract_error_message, _extract_text_from_progress_item, _stop_adapter_safely
from vibrant.models.agent import AgentProviderMetadata, AgentRecord, AgentStatus, AgentType
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import OrchestratorStatus
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.engine import OrchestratorEngine
from vibrant.orchestrator.git_manager import GitManager, GitManagerError, GitMergeResult, GitWorktreeInfo
from vibrant.orchestrator.task_dispatch import TaskDispatcher
from vibrant.project_init import ensure_project_files
from vibrant.providers.base import CanonicalEvent, RuntimeMode
from vibrant.providers.codex.adapter import CodexProviderAdapter

CanonicalEventCallback = Callable[[CanonicalEvent], Any]


@dataclass(slots=True)
class CodeAgentLifecycleResult:
    """Structured outcome for one code-agent execution attempt."""

    task_id: str | None
    outcome: str
    task_status: TaskStatus | None = None
    agent_record: AgentRecord | None = None
    gatekeeper_result: GatekeeperRunResult | None = None
    merge_result: GitMergeResult | None = None
    events: list[CanonicalEvent] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None
    worktree_path: str | None = None


class CodeAgentLifecycle:
    """Execute roadmap tasks end-to-end with worktrees, provider sessions, and Gatekeeper review."""

    ACCEPTED_VERDICTS = {"accept", "accepted", "approve", "approved", "done"}
    REJECTED_VERDICTS = {
        "reject",
        "rejected",
        "retry",
        "retry_task",
        "replan",
        "failed",
        "failure",
        "escalate",
        "escalated",
    }
    AWAITING_INPUT_VERDICTS = {"needs_input", "awaiting_input"}
    REQUEST_ERROR_MESSAGE = "Interactive provider requests are not supported during autonomous task execution."

    def __init__(
        self,
        project_root: str | Path,
        *,
        engine: OrchestratorEngine | None = None,
        gatekeeper: Gatekeeper | Any | None = None,
        git_manager: GitManager | None = None,
        adapter_factory: Any | None = None,
        on_canonical_event: CanonicalEventCallback | None = None,
    ) -> None:
        self.project_root = find_project_root(project_root)
        self.vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        self.roadmap_path = self.vibrant_dir / "roadmap.md"
        self.consensus_path = self.vibrant_dir / "consensus.md"
        self.skills_dir = self.vibrant_dir / "skills"

        ensure_project_files(self.project_root)
        self.config = load_config(start_path=self.project_root)
        self.engine = engine or OrchestratorEngine.load(self.project_root)
        self.gatekeeper = gatekeeper or Gatekeeper(self.project_root)
        self.git_manager = git_manager or GitManager(
            repo_root=self.project_root,
            worktree_root=_scoped_worktree_root(self.project_root, self.config.worktree_directory),
        )
        self.adapter_factory = adapter_factory or CodexProviderAdapter
        self.on_canonical_event = on_canonical_event
        self.consensus_parser = ConsensusParser()
        self.consensus_writer = ConsensusWriter(parser=self.consensus_parser)
        self.roadmap_parser = RoadmapParser()
        self.roadmap_document: RoadmapDocument | None = None
        self.dispatcher: TaskDispatcher | None = None

        self.reload_from_disk()

    def reload_from_disk(self) -> RoadmapDocument:
        """Refresh consensus, roadmap, and derived dispatcher state from disk."""

        self.config = load_config(start_path=self.project_root)
        self.engine.refresh_from_disk()
        if self.roadmap_path.exists():
            incoming = self.roadmap_parser.parse_file(self.roadmap_path)
        else:
            incoming = RoadmapDocument(project=self.project_root.name, tasks=[])

        if self.roadmap_document is None or self.dispatcher is None:
            self.roadmap_document = incoming
            self.dispatcher = TaskDispatcher(
                incoming.tasks,
                concurrency_limit=self.engine.state.concurrency_limit,
            )
            return self.roadmap_document

        self.dispatcher.concurrency_limit = self.engine.state.concurrency_limit
        self._merge_roadmap_updates(incoming)
        return self.roadmap_document

    @property
    def execution_mode(self) -> RoadmapExecutionMode:
        """Return the configured roadmap execution strategy."""

        return self.config.execution_mode

    async def _run_gatekeeper_request(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        """Run the Gatekeeper with optional resume support when available."""

        run_gatekeeper = self.gatekeeper.run
        try:
            signature = inspect.signature(run_gatekeeper)
        except (TypeError, ValueError):
            signature = None

        if signature is not None and "resume_latest_thread" in signature.parameters:
            return await run_gatekeeper(request, resume_latest_thread=resume_latest_thread)
        return await run_gatekeeper(request)

    async def submit_gatekeeper_message(self, text: str) -> GatekeeperRunResult:
        """Route planning or escalation input to the Gatekeeper and refresh state."""

        self.reload_from_disk()
        message = text.strip()
        if not message:
            raise ValueError("Gatekeeper message cannot be empty")

        if self.engine.state.pending_questions:
            result = await self.engine.answer_pending_question(self.gatekeeper, answer=message)
        else:
            trigger = (
                GatekeeperTrigger.PROJECT_START
                if self.engine.state.status is OrchestratorStatus.INIT
                else GatekeeperTrigger.USER_CONVERSATION
            )
            request = GatekeeperRequest(
                trigger=trigger,
                trigger_description=message,
                agent_summary=message,
            )
            result = await self._run_gatekeeper_request(
                request,
                resume_latest_thread=trigger is GatekeeperTrigger.USER_CONVERSATION,
            )
            self.engine.apply_gatekeeper_result(result)

        self._merge_roadmap_from_result(result)
        self._persist_roadmap()
        self._maybe_complete_workflow()
        self.engine.refresh_from_disk()
        return result

    async def execute_until_blocked(self) -> list[CodeAgentLifecycleResult]:
        """Keep executing ready tasks until user input, pause, completion, or exhaustion."""

        results: list[CodeAgentLifecycleResult] = []
        while True:
            result = await self.execute_next_task()
            if result is None:
                break

            results.append(result)
            self._maybe_complete_workflow()

            if result.outcome not in {"accepted", "retried"}:
                break
            if self.engine.state.pending_questions:
                break
            if self.engine.state.status in {OrchestratorStatus.PAUSED, OrchestratorStatus.COMPLETED}:
                break

        self._maybe_complete_workflow()
        return results

    async def execute_next_task(self) -> CodeAgentLifecycleResult | None:
        """Dispatch and execute the next eligible roadmap task."""

        self.reload_from_disk()
        if self.engine.state.pending_questions:
            return None
        if self.dispatcher is None:
            return None

        if self.engine.state.status is OrchestratorStatus.PAUSED:
            self.engine.transition_to(OrchestratorStatus.EXECUTING)
        elif self.engine.state.status in {OrchestratorStatus.PLANNING, OrchestratorStatus.INIT} and self.engine.can_transition_to(
            OrchestratorStatus.EXECUTING
        ):
            self.engine.transition_to(OrchestratorStatus.EXECUTING)

        task = self.dispatcher.dispatch_next_task()
        if task is None:
            self._maybe_complete_workflow()
            self._persist_roadmap()
            return None

        self._persist_roadmap()
        return await self._execute_task(task)

    async def _execute_task(self, task: TaskInfo) -> CodeAgentLifecycleResult:
        worktree = self._create_fresh_worktree(task.id)
        task.branch = worktree.branch or task.branch or self.git_manager.branch_name(task.id)
        self._persist_roadmap()

        prompt = self._build_task_prompt(task, worktree)
        agent_record = self._build_agent_record(task=task, worktree=worktree, prompt=prompt)
        events: list[CanonicalEvent] = []
        transcript_chunks: list[str] = []
        turn_finished = asyncio.Event()
        runtime_error: str | None = None
        adapter: Any | None = None

        async def handle_event(event: CanonicalEvent) -> None:
            nonlocal runtime_error, adapter

            event_copy = dict(event)
            events.append(event_copy)
            event_type = str(event_copy.get("type") or "")
            if event_type == "content.delta":
                transcript_chunks.append(str(event_copy.get("delta", "")))
            elif event_type == "task.progress":
                text = _extract_text_from_progress_item(event_copy.get("item"))
                if text:
                    transcript_chunks.append(text)
            elif event_type == "runtime.error":
                runtime_error = _extract_error_message(event_copy)
                turn_finished.set()
            elif event_type == "turn.completed":
                turn_finished.set()
            elif event_type == "request.opened":
                request_id = event_copy.get("request_id")
                request_kind = str(event_copy.get("request_kind") or "request")
                runtime_error = f"{self.REQUEST_ERROR_MESSAGE} ({request_kind})"
                if adapter is not None and request_id is not None:
                    await adapter.respond_to_request(
                        request_id,
                        error={"code": -32000, "message": runtime_error},
                    )
                turn_finished.set()

            await _maybe_forward_event(self.on_canonical_event, event_copy)

        agent_record.started_at = datetime.now(timezone.utc)
        self.engine.upsert_agent_record(agent_record, increment_spawn=True)

        thread_runtime_mode = _parse_runtime_mode(self.config.sandbox_mode)
        turn_runtime_mode = _parse_runtime_mode(self.config.turn_sandbox_policy or self.config.sandbox_mode)
        turn_result: Any | None = None

        try:
            agent_record.transition_to(AgentStatus.CONNECTING)
            self.engine.upsert_agent_record(agent_record)

            adapter = self.adapter_factory(
                cwd=str(worktree.path),
                codex_binary=self.config.codex_binary,
                codex_home=self.config.codex_home,
                agent_record=agent_record,
                on_canonical_event=handle_event,
            )
            await adapter.start_session(cwd=str(worktree.path))
            agent_record.pid = _extract_pid(adapter)
            self.engine.upsert_agent_record(agent_record)

            await adapter.start_thread(
                model=self.config.model,
                cwd=str(worktree.path),
                runtime_mode=thread_runtime_mode,
                approval_policy=self.config.approval_policy,
                model_provider=self.config.model_provider,
                reasoning_effort=self.config.reasoning_effort,
                reasoning_summary=self.config.reasoning_summary,
                extra_config=self.config.extra_config,
            )

            agent_record.transition_to(AgentStatus.RUNNING)
            self.engine.upsert_agent_record(agent_record)

            turn_result = await adapter.start_turn(
                input_items=[{"type": "text", "text": prompt, "text_elements": []}],
                runtime_mode=turn_runtime_mode,
                approval_policy=self.config.approval_policy,
            )
            await asyncio.wait_for(turn_finished.wait(), timeout=float(self.config.agent_timeout_seconds))
        except Exception as exc:
            if runtime_error is None:
                runtime_error = str(exc)
        finally:
            if adapter is not None:
                await _stop_adapter_safely(adapter)

        transcript = "".join(transcript_chunks).strip()
        exit_code = _extract_exit_code(adapter)

        if runtime_error:
            agent_record.summary = transcript or agent_record.summary
            _transition_terminal_agent(
                agent_record,
                AgentStatus.FAILED,
                exit_code=exit_code if exit_code is not None else 1,
                error=runtime_error,
            )
            self.engine.upsert_agent_record(agent_record)
            return await self._handle_failure(
                task=task,
                agent_record=agent_record,
                worktree=worktree,
                events=events,
                reason=runtime_error,
                summary=transcript or None,
                notify_gatekeeper_on_retry=True,
            )

        agent_record.summary = transcript or _extract_summary_from_turn_result(turn_result)
        _transition_terminal_agent(
            agent_record,
            AgentStatus.COMPLETED,
            exit_code=exit_code if exit_code is not None else 0,
        )
        self.engine.upsert_agent_record(agent_record)

        completed_task = self.dispatcher.mark_completed(task.id)
        self._persist_roadmap()

        gatekeeper_result = await self.gatekeeper.run(self._build_gatekeeper_request_for_completion(completed_task, agent_record, worktree))
        self.engine.apply_gatekeeper_result(gatekeeper_result)
        self._merge_roadmap_from_result(gatekeeper_result)

        decision = self._resolve_gatekeeper_decision(gatekeeper_result, completed_task.id)
        if decision in self.AWAITING_INPUT_VERDICTS:
            self._persist_roadmap()
            return CodeAgentLifecycleResult(
                task_id=completed_task.id,
                outcome="awaiting_user",
                task_status=completed_task.status,
                agent_record=agent_record,
                gatekeeper_result=gatekeeper_result,
                events=events,
                summary=agent_record.summary,
                worktree_path=str(worktree.path),
            )

        if decision in self.ACCEPTED_VERDICTS:
            merge_result = self.git_manager.merge_task(completed_task.id)
            if merge_result.merged and not merge_result.has_conflicts:
                accepted_task = self.dispatcher.accept_task(completed_task.id)
                self._persist_roadmap()
                self._cleanup_worktree(completed_task.id)
                self._maybe_complete_workflow()
                return CodeAgentLifecycleResult(
                    task_id=accepted_task.id,
                    outcome="accepted",
                    task_status=accepted_task.status,
                    agent_record=agent_record,
                    gatekeeper_result=gatekeeper_result,
                    merge_result=merge_result,
                    events=events,
                    summary=agent_record.summary,
                )

            self._abort_merge_if_needed()
            merge_error = _format_merge_error(merge_result)
            return await self._handle_failure(
                task=completed_task,
                agent_record=agent_record,
                worktree=worktree,
                events=events,
                reason=merge_error,
                summary=agent_record.summary,
                prior_gatekeeper_result=gatekeeper_result,
                notify_gatekeeper_on_retry=True,
            )

        rejection_reason = gatekeeper_result.error or f"Gatekeeper verdict: {decision or 'rejected'}"
        return await self._handle_failure(
            task=completed_task,
            agent_record=agent_record,
            worktree=worktree,
            events=events,
            reason=rejection_reason,
            summary=agent_record.summary,
            prior_gatekeeper_result=gatekeeper_result,
            notify_gatekeeper_on_retry=False,
        )

    async def _handle_failure(
        self,
        *,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        events: list[CanonicalEvent],
        reason: str,
        summary: str | None,
        prior_gatekeeper_result: GatekeeperRunResult | None = None,
        notify_gatekeeper_on_retry: bool,
    ) -> CodeAgentLifecycleResult:
        updated_task = self.dispatcher.fail_task(task.id, failure_reason=reason)

        gatekeeper_result = prior_gatekeeper_result
        if updated_task.status is TaskStatus.ESCALATED:
            gatekeeper_result = await self.gatekeeper.run(
                self._build_gatekeeper_request_for_escalation(updated_task, agent_record, worktree, reason)
            )
            self.engine.apply_gatekeeper_result(gatekeeper_result)
            self._merge_roadmap_from_result(gatekeeper_result)
            outcome = "escalated"
        elif notify_gatekeeper_on_retry:
            gatekeeper_result = await self.gatekeeper.run(
                self._build_gatekeeper_request_for_failure(updated_task, agent_record, worktree, reason)
            )
            self.engine.apply_gatekeeper_result(gatekeeper_result)
            self._merge_roadmap_from_result(gatekeeper_result)
            outcome = "retried"
        else:
            outcome = "retried"

        self._persist_roadmap()
        self._cleanup_worktree(updated_task.id)
        return CodeAgentLifecycleResult(
            task_id=updated_task.id,
            outcome=outcome,
            task_status=updated_task.status,
            agent_record=agent_record,
            gatekeeper_result=gatekeeper_result,
            events=events,
            summary=summary,
            error=reason,
        )

    def _create_fresh_worktree(self, task_id: str) -> GitWorktreeInfo:
        try:
            return self.git_manager.create_worktree(task_id)
        except GitManagerError:
            self._cleanup_worktree(task_id)
            return self.git_manager.create_worktree(task_id)

    def _cleanup_worktree(self, task_id: str) -> None:
        try:
            self.git_manager.remove_worktree(task_id)
        except Exception:
            return

    def _persist_roadmap(self) -> None:
        if self.roadmap_document is None:
            return
        self.roadmap_parser.write(self.roadmap_path, self.roadmap_document)

    def _load_consensus(self) -> ConsensusDocument:
        if self.engine.consensus is not None:
            return self.engine.consensus
        return self.consensus_parser.parse_file(self.consensus_path)

    def _build_task_prompt(self, task: TaskInfo, worktree: GitWorktreeInfo) -> str:
        consensus = self._load_consensus()
        additional_context = "\n".join(
            [
                f"Working Directory: {worktree.path}",
                f"Retry Attempt: {task.retry_count + 1} of {task.max_retries + 1}",
            ]
        )
        skill_contents = self._load_task_skills(task.skills)
        return self.roadmap_parser.build_task_prompt(
            task,
            consensus,
            additional_context=additional_context,
            skill_contents=skill_contents,
        )

    def _load_task_skills(self, skills: list[str]) -> list[str] | None:
        rendered: list[str] = []
        for skill in skills:
            for candidate in _skill_candidates(self.skills_dir, skill):
                if candidate.exists() and candidate.is_file():
                    rendered.append(candidate.read_text(encoding="utf-8").strip())
                    break
        return rendered or None

    def _build_agent_record(self, *, task: TaskInfo, worktree: GitWorktreeInfo, prompt: str) -> AgentRecord:
        agent_id = f"agent-{task.id}-{uuid4().hex[:8]}"
        native_log = self.vibrant_dir / "logs" / "providers" / "native" / f"{agent_id}.ndjson"
        canonical_log = self.vibrant_dir / "logs" / "providers" / "canonical" / f"{agent_id}.ndjson"
        return AgentRecord(
            agent_id=agent_id,
            task_id=task.id,
            type=AgentType.CODE,
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

    def _build_gatekeeper_request_for_completion(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
    ) -> GatekeeperRequest:
        diff_text = self._collect_diff(task, worktree)
        trigger_description = "\n".join(
            [
                f"Task {task.id}: {task.title}",
                "Evaluate the completed implementation against the roadmap acceptance criteria.",
                f"Branch: {task.branch or self.git_manager.branch_name(task.id)}",
                "Acceptance Criteria:",
                *[f"- {criterion}" for criterion in task.acceptance_criteria],
                "Git Diff:",
                diff_text,
            ]
        )
        return GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description=trigger_description,
            agent_summary=agent_record.summary,
        )

    def _build_gatekeeper_request_for_failure(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRequest:
        diff_text = self._collect_diff(task, worktree)
        trigger_description = "\n".join(
            [
                f"Task {task.id}: {task.title}",
                f"Failure Reason: {reason}",
                f"Retry Count: {task.retry_count} / {task.max_retries}",
                "Please adjust the task prompt or acceptance criteria for the next retry.",
                "Current Diff / Status:",
                diff_text,
            ]
        )
        return GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_FAILURE,
            trigger_description=trigger_description,
            agent_summary=agent_record.summary or reason,
        )

    def _build_gatekeeper_request_for_escalation(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRequest:
        diff_text = self._collect_diff(task, worktree)
        trigger_description = "\n".join(
            [
                f"Task {task.id}: {task.title}",
                f"Failure Reason: {reason}",
                f"Max retries exceeded at {task.retry_count} / {task.max_retries}.",
                "Escalate to the user or pivot the plan.",
                "Current Diff / Status:",
                diff_text,
            ]
        )
        return GatekeeperRequest(
            trigger=GatekeeperTrigger.MAX_RETRIES_EXCEEDED,
            trigger_description=trigger_description,
            agent_summary=agent_record.summary or reason,
        )

    def _resolve_gatekeeper_decision(self, result: GatekeeperRunResult, task_id: str) -> str:
        verdict = (result.verdict or "").strip().lower()
        if verdict:
            return verdict
        if result.questions:
            return "needs_input"
        if result.roadmap_document is not None:
            task = next((item for item in result.roadmap_document.tasks if item.id == task_id), None)
            if task is not None and task.status is TaskStatus.ACCEPTED:
                return "accepted"
        return "rejected"

    def _merge_roadmap_from_result(self, result: GatekeeperRunResult | None) -> None:
        if result is None or result.roadmap_document is None:
            return
        self._merge_roadmap_updates(result.roadmap_document)

    def _merge_roadmap_updates(self, incoming: RoadmapDocument) -> None:
        assert self.roadmap_document is not None
        assert self.dispatcher is not None

        existing_by_id = {task.id: task for task in self.roadmap_document.tasks}
        merged_tasks: list[TaskInfo] = []
        incoming_ids: set[str] = set()

        for incoming_task in incoming.tasks:
            incoming_ids.add(incoming_task.id)
            existing = existing_by_id.get(incoming_task.id)
            if existing is None:
                merged_tasks.append(incoming_task)
                self.dispatcher.add_task(incoming_task)
                continue

            _apply_task_definition(existing, incoming_task)
            merged_tasks.append(existing)

        for existing in self.roadmap_document.tasks:
            if existing.id not in incoming_ids:
                merged_tasks.append(existing)

        self.roadmap_document.project = incoming.project
        self.roadmap_document.tasks = merged_tasks

    def _collect_diff(self, task: TaskInfo, worktree: GitWorktreeInfo) -> str:
        branch = task.branch or self.git_manager.branch_name(task.id)
        sections: list[str] = []

        status = _run_git_capture(worktree.path, "status", "--short")
        if status:
            sections.extend(["Git Status:", status])

        worktree_diff = _run_git_capture(worktree.path, "diff", "--find-renames")
        if worktree_diff:
            sections.extend(["Working Tree Diff:", worktree_diff])

        staged_diff = _run_git_capture(worktree.path, "diff", "--cached", "--find-renames")
        if staged_diff:
            sections.extend(["Staged Diff:", staged_diff])

        branch_diff = _run_git_capture(self.project_root, "diff", "--find-renames", f"{self.git_manager.main_branch}...{branch}")
        if branch_diff:
            sections.extend(["Branch Diff:", branch_diff])

        if not sections:
            return "No diff available."
        return "\n".join(sections)

    def _abort_merge_if_needed(self) -> None:
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=str(self.project_root),
            text=True,
            capture_output=True,
            check=False,
        )

    def _maybe_complete_workflow(self) -> bool:
        if self.roadmap_document is None or not self.roadmap_document.tasks:
            return False
        if any(task.status is not TaskStatus.ACCEPTED for task in self.roadmap_document.tasks):
            return False
        if self.engine.state.pending_questions or self.engine.state.active_agents:
            return False

        consensus_document = self.engine.consensus
        if consensus_document is None and self.consensus_path.exists():
            consensus_document = self.consensus_parser.parse_file(self.consensus_path)

        if consensus_document is not None and consensus_document.status is not ConsensusStatus.COMPLETED:
            updated = consensus_document.model_copy(deep=True)
            updated.status = ConsensusStatus.COMPLETED
            self.engine.consensus = self.consensus_writer.write(self.consensus_path, updated)
            self.engine.refresh_from_disk()

        if self.engine.state.status is not OrchestratorStatus.COMPLETED:
            self.engine.transition_to(OrchestratorStatus.COMPLETED)
        return True


def _transition_terminal_agent(
    agent_record: AgentRecord,
    status: AgentStatus,
    *,
    exit_code: int,
    error: str | None = None,
) -> None:
    if agent_record.can_transition_to(status):
        agent_record.transition_to(status, exit_code=exit_code, error=error)
        return

    agent_record.exit_code = exit_code
    agent_record.error = error
    if agent_record.finished_at is None:
        agent_record.finished_at = datetime.now(timezone.utc)


async def _maybe_forward_event(handler: CanonicalEventCallback | None, event: CanonicalEvent) -> None:
    if handler is None:
        return
    callback_result = handler(event)
    if inspect.isawaitable(callback_result):
        await callback_result


def _apply_task_definition(existing: TaskInfo, incoming: TaskInfo) -> None:
    existing.title = incoming.title
    existing.acceptance_criteria = list(incoming.acceptance_criteria)
    existing.prompt = incoming.prompt
    existing.skills = list(incoming.skills)
    existing.dependencies = list(incoming.dependencies)
    existing.priority = incoming.priority
    existing.branch = incoming.branch or existing.branch


def _parse_runtime_mode(value: str | None) -> RuntimeMode:
    normalized = (value or RuntimeMode.WORKSPACE_WRITE.value).strip()
    if not normalized:
        return RuntimeMode.WORKSPACE_WRITE

    key = normalized.replace("-", "_")
    lowered = key.lower()
    mapping = {
        "read_only": RuntimeMode.READ_ONLY,
        "readonly": RuntimeMode.READ_ONLY,
        "workspace_write": RuntimeMode.WORKSPACE_WRITE,
        "workspacewrite": RuntimeMode.WORKSPACE_WRITE,
        "full_access": RuntimeMode.FULL_ACCESS,
        "danger_full_access": RuntimeMode.FULL_ACCESS,
        "dangerfullaccess": RuntimeMode.FULL_ACCESS,
    }
    try:
        return mapping[lowered]
    except KeyError as exc:
        raise ValueError(f"Unsupported runtime mode: {value}") from exc


def _extract_pid(adapter: Any) -> int | None:
    client = getattr(adapter, "client", None)
    process = getattr(client, "_process", None)
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) else None


def _extract_exit_code(adapter: Any | None) -> int | None:
    client = getattr(adapter, "client", None)
    process = getattr(client, "_process", None)
    returncode = getattr(process, "returncode", None)
    return returncode if isinstance(returncode, int) else None


def _extract_summary_from_turn_result(turn_result: Any) -> str | None:
    if not isinstance(turn_result, dict):
        return None

    candidates: list[Any] = [turn_result]
    turn_payload = turn_result.get("turn")
    if isinstance(turn_payload, dict):
        candidates.append(turn_payload)

    for candidate in candidates:
        if isinstance(candidate.get("summary"), str) and candidate["summary"].strip():
            return candidate["summary"].strip()
        output_text = candidate.get("outputText") or candidate.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

    return None


def _skill_candidates(skills_dir: Path, skill: str) -> tuple[Path, ...]:
    return (
        skills_dir / skill,
        skills_dir / f"{skill}.md",
        skills_dir / skill / "SKILL.md",
    )


def _scoped_worktree_root(project_root: Path, configured_root: str) -> Path:
    root = Path(configured_root).expanduser()
    if not root.is_absolute():
        root = project_root / root
    return root / _project_worktree_scope(project_root)


def _project_worktree_scope(project_root: Path) -> str:
    digest = hashlib.sha1(str(project_root).encode("utf-8")).hexdigest()[:12]
    return f"{project_root.name}-{digest}"


def _run_git_capture(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _format_merge_error(result: GitMergeResult) -> str:
    details = (result.stderr or result.stdout).strip()
    if result.conflicted_files:
        suffix = f" Conflicted files: {', '.join(result.conflicted_files)}"
    else:
        suffix = ""
    return f"Merge failed for {result.branch}.{suffix} {details}".strip()


__all__ = ["CodeAgentLifecycle", "CodeAgentLifecycleResult"]
