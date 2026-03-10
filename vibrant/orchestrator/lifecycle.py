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

from vibrant.agents import CodeAgent, MergeAgent
from vibrant.agents.utils import maybe_forward_event
from vibrant.config import DEFAULT_CONFIG_DIR, RoadmapExecutionMode, find_project_root, load_config
from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapDocument, RoadmapParser
from vibrant.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.agent import AgentRecord
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.models.state import GatekeeperStatus, OrchestratorStatus
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.engine import OrchestratorEngine
from vibrant.orchestrator.git_manager import GitManager, GitManagerError, GitMergeResult, GitWorktreeInfo
from vibrant.orchestrator.task_dispatch import TaskDispatcher
from vibrant.project_init import ensure_project_files
from vibrant.providers.base import CanonicalEvent
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
        self.gatekeeper = gatekeeper or Gatekeeper(
            self.project_root,
            on_canonical_event=on_canonical_event,
        )
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
        self._active_gatekeeper_futures: set[asyncio.Future[Any]] = set()

        self.code_agent = CodeAgent(
            self.project_root,
            config=self.config,
            adapter_factory=self.adapter_factory,
            on_canonical_event=on_canonical_event,
            on_agent_record_updated=self._on_agent_record_updated,
        )
        self.merge_agent = MergeAgent(
            self.project_root,
            config=self.config,
            adapter_factory=self.adapter_factory,
            on_canonical_event=on_canonical_event,
            on_agent_record_updated=self._on_agent_record_updated,
        )

        self.reload_from_disk()

    def reload_from_disk(self) -> RoadmapDocument:
        """Refresh consensus, roadmap, and derived dispatcher state from disk."""

        self.config = load_config(start_path=self.project_root)
        self.code_agent.config = self.config
        self.merge_agent.config = self.config
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

    def _on_agent_record_updated(self, agent_record: AgentRecord) -> None:
        """Callback from AgentBase.run() for intermediate record updates."""

        self.engine.upsert_agent_record(agent_record)

    async def _run_gatekeeper_request(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        """Run the Gatekeeper with optional resume support when available."""

        handle = await self._start_gatekeeper_request(
            request,
            resume_latest_thread=resume_latest_thread,
        )
        return await handle.wait()

    async def _start_gatekeeper_request(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
        on_result: Callable[[GatekeeperRunResult], Any] | None = None,
    ) -> Any:
        """Start a Gatekeeper request and return after the provider turn has started."""

        start_gatekeeper = getattr(self.gatekeeper, "start_run", None)
        if callable(start_gatekeeper):
            kwargs: dict[str, Any] = {}
            try:
                signature = inspect.signature(start_gatekeeper)
            except (TypeError, ValueError):
                signature = None

            if signature is not None:
                if "resume_latest_thread" in signature.parameters:
                    kwargs["resume_latest_thread"] = resume_latest_thread
                if "on_result" in signature.parameters:
                    kwargs["on_result"] = on_result

            handle = await start_gatekeeper(request, **kwargs)
            self._track_gatekeeper_future(handle.result_future)
            return handle

        result_future = asyncio.create_task(
            self._await_gatekeeper_request_legacy(request, resume_latest_thread=resume_latest_thread),
            name=f"gatekeeper-legacy-{request.trigger.value}",
        )
        self._track_gatekeeper_future(result_future)
        return _LegacyGatekeeperHandle(result_future)

    async def _await_gatekeeper_request_legacy(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        """Fallback for Gatekeeper implementations that only expose ``run``."""

        run_gatekeeper = self.gatekeeper.run
        try:
            signature = inspect.signature(run_gatekeeper)
        except (TypeError, ValueError):
            signature = None

        if signature is not None and "resume_latest_thread" in signature.parameters:
            return await run_gatekeeper(request, resume_latest_thread=resume_latest_thread)
        return await run_gatekeeper(request)

    def _track_gatekeeper_future(self, future: asyncio.Future[Any]) -> None:
        self._active_gatekeeper_futures.add(future)

        def _discard(_: asyncio.Future[Any]) -> None:
            self._active_gatekeeper_futures.discard(future)

        future.add_done_callback(_discard)

    @property
    def gatekeeper_busy(self) -> bool:
        return any(not future.done() for future in self._active_gatekeeper_futures)

    async def start_gatekeeper_message(self, text: str) -> Any:
        """Start a Gatekeeper planning or Q&A turn and return immediately after the turn starts."""

        self.reload_from_disk()
        message = text.strip()
        if not message:
            raise ValueError("Gatekeeper message cannot be empty")
        if self.gatekeeper_busy:
            raise RuntimeError("Gatekeeper is already running")

        if self.engine.state.pending_questions:
            selected_question = self.engine.state.pending_questions[0]
            self.engine.state.gatekeeper_status = GatekeeperStatus.RUNNING
            self.engine.persist_state()
            request = GatekeeperRequest(
                trigger=GatekeeperTrigger.USER_CONVERSATION,
                trigger_description=f"Question: {selected_question}\nUser Answer: {message}",
                agent_summary=message,
            )
            resume_latest_thread = True
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
            resume_latest_thread = trigger is GatekeeperTrigger.USER_CONVERSATION

        return await self._start_gatekeeper_request(
            request,
            resume_latest_thread=resume_latest_thread,
            on_result=self._apply_gatekeeper_result_async,
        )

    async def submit_gatekeeper_message(self, text: str) -> GatekeeperRunResult:
        """Route planning or escalation input to the Gatekeeper and refresh state."""

        handle = await self.start_gatekeeper_message(text)
        result = await handle.wait()
        if not callable(getattr(self.gatekeeper, "start_run", None)):
            await self._apply_gatekeeper_result_async(result)
        return result

    async def _apply_gatekeeper_result_async(self, result: GatekeeperRunResult) -> None:
        """Apply a completed Gatekeeper result and notify UI consumers."""

        self.engine.apply_gatekeeper_result(result)
        self._merge_roadmap_from_result(result)
        self._persist_roadmap()
        self._maybe_complete_workflow()
        self.engine.refresh_from_disk()
        await self._emit_lifecycle_event(
            "gatekeeper.result.applied",
            agent_id=result.agent_record.agent_id if result.agent_record is not None else None,
            task_id=result.agent_record.task_id if result.agent_record is not None else None,
            request_trigger=result.request.trigger.value,
            verdict=result.verdict,
            questions=list(result.questions),
            error=result.error,
            consensus_updated=result.consensus_updated,
            roadmap_updated=result.roadmap_updated,
            plan_modified=result.plan_modified,
            transcript=result.transcript,
        )

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
        agent_record = self.code_agent.build_agent_record(
            task=task, worktree=worktree, prompt=prompt,
        )
        agent_record.started_at = datetime.now(timezone.utc)
        self.engine.upsert_agent_record(agent_record, increment_spawn=True)

        run_result = await self.code_agent.run(
            prompt=prompt,
            agent_record=agent_record,
            cwd=str(worktree.path),
        )

        self.engine.upsert_agent_record(run_result.agent_record)

        if run_result.error:
            return await self._handle_failure(
                task=task,
                agent_record=run_result.agent_record,
                worktree=worktree,
                events=run_result.events,
                reason=run_result.error,
                summary=run_result.transcript or None,
                notify_gatekeeper_on_retry=True,
            )

        completed_task = self.dispatcher.mark_completed(task.id)
        self._persist_roadmap()

        gatekeeper_result = await self._run_gatekeeper_request(
            self._build_gatekeeper_request_for_completion(completed_task, run_result.agent_record, worktree)
        )
        self.engine.apply_gatekeeper_result(gatekeeper_result)
        self._merge_roadmap_from_result(gatekeeper_result)

        decision = self._resolve_gatekeeper_decision(gatekeeper_result, completed_task.id)
        if decision in self.AWAITING_INPUT_VERDICTS:
            self._persist_roadmap()
            return CodeAgentLifecycleResult(
                task_id=completed_task.id,
                outcome="awaiting_user",
                task_status=completed_task.status,
                agent_record=run_result.agent_record,
                gatekeeper_result=gatekeeper_result,
                events=run_result.events,
                summary=run_result.agent_record.summary,
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
                    agent_record=run_result.agent_record,
                    gatekeeper_result=gatekeeper_result,
                    merge_result=merge_result,
                    events=run_result.events,
                    summary=run_result.agent_record.summary,
                )

            # Merge conflict — attempt resolution via MergeAgent
            merge_resolution = await self._attempt_merge_resolution(
                task=completed_task,
                agent_record=run_result.agent_record,
                worktree=worktree,
                failed_merge=merge_result,
                gatekeeper_result=gatekeeper_result,
            )
            if merge_resolution is not None:
                return merge_resolution

            # MergeAgent failed — fall back to failure handling
            self._abort_merge_if_needed()
            merge_error = _format_merge_error(merge_result)
            return await self._handle_failure(
                task=completed_task,
                agent_record=run_result.agent_record,
                worktree=worktree,
                events=run_result.events,
                reason=merge_error,
                summary=run_result.agent_record.summary,
                prior_gatekeeper_result=gatekeeper_result,
                notify_gatekeeper_on_retry=True,
            )

        rejection_reason = gatekeeper_result.error or f"Gatekeeper verdict: {decision or 'rejected'}"
        return await self._handle_failure(
            task=completed_task,
            agent_record=run_result.agent_record,
            worktree=worktree,
            events=run_result.events,
            reason=rejection_reason,
            summary=run_result.agent_record.summary,
            prior_gatekeeper_result=gatekeeper_result,
            notify_gatekeeper_on_retry=False,
        )

    async def _attempt_merge_resolution(
        self,
        *,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        failed_merge: GitMergeResult,
        gatekeeper_result: GatekeeperRunResult,
    ) -> CodeAgentLifecycleResult | None:
        """Attempt to resolve merge conflicts via the MergeAgent.

        Returns a ``CodeAgentLifecycleResult`` on success, or ``None`` if
        the merge agent fails (caller should fall through to failure handling).
        """

        if not failed_merge.conflicted_files:
            return None

        conflict_diff = _run_git_capture(self.project_root, "diff")
        merge_prompt = self.merge_agent.build_merge_prompt(
            task_id=task.id,
            task_title=task.title,
            branch=task.branch or self.git_manager.branch_name(task.id),
            main_branch=self.git_manager.main_branch,
            conflicted_files=failed_merge.conflicted_files,
            conflict_diff=conflict_diff or "No diff available.",
            task_summary=agent_record.summary,
        )
        merge_record = self.merge_agent.build_agent_record(
            task_id=task.id,
            branch=task.branch,
        )
        merge_record.started_at = datetime.now(timezone.utc)
        self.engine.upsert_agent_record(merge_record, increment_spawn=True)

        merge_run = await self.merge_agent.run(
            prompt=merge_prompt,
            agent_record=merge_record,
            cwd=str(self.project_root),
        )
        self.engine.upsert_agent_record(merge_run.agent_record)

        if merge_run.error:
            # MergeAgent could not resolve — caller handles fallback
            return None

        # Verify that all conflicts are resolved (no remaining conflict markers)
        remaining = _run_git_capture(self.project_root, "diff", "--check")
        unmerged = _run_git_capture(self.project_root, "diff", "--name-only", "--diff-filter=U")
        if unmerged:
            # Still has unmerged files — agent didn't fully resolve
            return None

        # Complete the merge commit
        try:
            subprocess.run(
                ["git", "commit", "--no-edit"],
                cwd=str(self.project_root),
                text=True,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            return None

        accepted_task = self.dispatcher.accept_task(task.id)
        self._persist_roadmap()
        self._cleanup_worktree(task.id)
        self._maybe_complete_workflow()

        # Re-use the original merge result but mark it as resolved
        resolved_merge = GitMergeResult(
            branch=failed_merge.branch,
            merged=True,
            has_conflicts=False,
            conflicted_files=[],
            stdout=f"Merge conflicts resolved by MergeAgent. Files: {', '.join(failed_merge.conflicted_files)}",
            stderr="",
        )

        return CodeAgentLifecycleResult(
            task_id=accepted_task.id,
            outcome="accepted",
            task_status=accepted_task.status,
            agent_record=agent_record,
            gatekeeper_result=gatekeeper_result,
            merge_result=resolved_merge,
            events=merge_run.events,
            summary=merge_run.agent_record.summary or agent_record.summary,
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
            gatekeeper_result = await self._run_gatekeeper_request(
                self._build_gatekeeper_request_for_escalation(updated_task, agent_record, worktree, reason)
            )
            self.engine.apply_gatekeeper_result(gatekeeper_result)
            self._merge_roadmap_from_result(gatekeeper_result)
            outcome = "escalated"
        elif notify_gatekeeper_on_retry:
            gatekeeper_result = await self._run_gatekeeper_request(
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

    async def _emit_lifecycle_event(self, event_type: str, **payload: Any) -> None:
        event: CanonicalEvent = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "provider": "vibrant",
        }
        event.update(payload)
        await maybe_forward_event(self.on_canonical_event, event)


@dataclass(slots=True)
class _LegacyGatekeeperHandle:
    """Compatibility handle for Gatekeeper implementations without ``start_run``."""

    result_future: asyncio.Future[GatekeeperRunResult]

    def done(self) -> bool:
        return self.result_future.done()

    async def wait(self) -> GatekeeperRunResult:
        return await self.result_future


def _apply_task_definition(existing: TaskInfo, incoming: TaskInfo) -> None:
    existing.title = incoming.title
    existing.acceptance_criteria = list(incoming.acceptance_criteria)
    existing.prompt = incoming.prompt
    existing.skills = list(incoming.skills)
    existing.dependencies = list(incoming.dependencies)
    existing.priority = incoming.priority
    existing.branch = incoming.branch or existing.branch


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
