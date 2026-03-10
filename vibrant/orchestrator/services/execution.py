"""Task execution service."""

from __future__ import annotations

from dataclasses import dataclass

from vibrant.agents.runtime import AgentHandle
from vibrant.models.agent import AgentRecord
from vibrant.models.task import TaskInfo
from vibrant.orchestrator.git_manager import GitWorktreeInfo

from ..types import CodeAgentLifecycleResult, RuntimeExecutionResult
from .agents import AgentRegistry
from .git_workspace import GitWorkspaceService, format_merge_error
from .prompts import PromptService
from .retries import RetryPolicyService
from .review import ReviewService
from .roadmap import RoadmapService
from .runtime import AgentRuntimeService
from .state_store import StateStore
from .workflow import WorkflowService


@dataclass(slots=True)
class TaskExecutionAttempt:
    """Prepared and started task execution attempt.

    This is the internal orchestration object that MCP-facing code can later
    use to start a task, inspect the agent handle, and await the normalized
    runtime result separately.
    """

    task: TaskInfo
    worktree: GitWorktreeInfo
    prompt: str
    agent_record: AgentRecord
    handle: AgentHandle


class TaskExecutionService:
    """Run task execution end-to-end using specialized services."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        roadmap_service: RoadmapService,
        workflow_service: WorkflowService,
        git_service: GitWorkspaceService,
        prompt_service: PromptService,
        agent_registry: AgentRegistry,
        runtime_service: AgentRuntimeService,
        review_service: ReviewService,
        retry_service: RetryPolicyService,
    ) -> None:
        self.state_store = state_store
        self.roadmap_service = roadmap_service
        self.workflow_service = workflow_service
        self.git_service = git_service
        self.prompt_service = prompt_service
        self.agent_registry = agent_registry
        self.runtime_service = runtime_service
        self.review_service = review_service
        self.retry_service = retry_service

    async def execute_until_blocked(self) -> list[CodeAgentLifecycleResult]:
        results: list[CodeAgentLifecycleResult] = []
        while True:
            result = await self.execute_next_task()
            if result is None:
                break

            results.append(result)
            self.workflow_service.maybe_complete_workflow()

            if result.outcome not in {"accepted", "retried"}:
                break
            if self.state_store.state.pending_questions:
                break
            if self.state_store.state.status.value in {"paused", "completed"}:
                break

        self.workflow_service.maybe_complete_workflow()
        return results

    async def execute_next_task(self) -> CodeAgentLifecycleResult | None:
        if self.state_store.state.pending_questions:
            return None

        dispatcher = self.roadmap_service.dispatcher
        if dispatcher is None:
            return None

        self.workflow_service.begin_execution_if_needed()
        task = dispatcher.dispatch_next_task()
        if task is None:
            self.workflow_service.maybe_complete_workflow()
            self.roadmap_service.persist()
            return None

        self.roadmap_service.persist()
        attempt = await self.start_task_attempt(task)
        return await self.wait_for_task_attempt(attempt)

    async def start_task_attempt(
        self,
        task: TaskInfo,
        *,
        resume_thread_id: str | None = None,
    ) -> TaskExecutionAttempt:
        """Prepare a task run and start its agent handle.

        This gives the orchestrator a clear two-phase API:
        start the run, then wait or observe it later.  The method is intended
        to become the internal basis for future MCP task-spawn endpoints.
        """
        worktree = self.git_service.create_fresh_worktree(task.id)
        task.branch = worktree.branch or task.branch or self.git_service.branch_name(task.id)
        self.roadmap_service.persist()

        prompt = self.prompt_service.build_task_prompt(task, worktree)
        agent_record = self.agent_registry.create_code_agent_record(task=task, worktree=worktree, prompt=prompt)
        handle = await self.runtime_service.start_task(
            worktree=worktree,
            prompt=prompt,
            agent_record=agent_record,
            resume_thread_id=resume_thread_id,
        )
        return TaskExecutionAttempt(
            task=task,
            worktree=worktree,
            prompt=prompt,
            agent_record=agent_record,
            handle=handle,
        )

    async def wait_for_task_attempt(self, attempt: TaskExecutionAttempt) -> CodeAgentLifecycleResult:
        """Resolve a started task attempt through runtime, review, and merge."""
        dispatcher = self.roadmap_service.dispatcher
        assert dispatcher is not None

        runtime_result = await self.runtime_service.wait_for_run(handle=attempt.handle)
        agent_record = runtime_result.agent_record

        if runtime_result.awaiting_input:
            self.roadmap_service.persist()
            return CodeAgentLifecycleResult(
                task_id=attempt.task.id,
                outcome="awaiting_user",
                task_status=attempt.task.status,
                agent_record=agent_record,
                events=runtime_result.events,
                summary=runtime_result.summary,
                error=runtime_result.error,
                worktree_path=str(attempt.worktree.path),
            )

        if runtime_result.error:
            return await self.retry_service.handle_failure(
                task=attempt.task,
                agent_record=agent_record,
                worktree=attempt.worktree,
                events=runtime_result.events,
                reason=runtime_result.error,
                summary=runtime_result.summary,
                notify_gatekeeper_on_retry=True,
            )

        completed_task = dispatcher.mark_completed(attempt.task.id)
        self.roadmap_service.persist()

        gatekeeper_result, decision = await self.review_service.review_completion(completed_task, agent_record, attempt.worktree)
        if decision in self.review_service.AWAITING_INPUT_VERDICTS:
            self.roadmap_service.persist()
            return CodeAgentLifecycleResult(
                task_id=completed_task.id,
                outcome="awaiting_user",
                task_status=completed_task.status,
                agent_record=agent_record,
                gatekeeper_result=gatekeeper_result,
                events=runtime_result.events,
                summary=agent_record.summary,
                worktree_path=str(attempt.worktree.path),
            )

        if decision in self.review_service.ACCEPTED_VERDICTS:
            merge_result = self.git_service.merge_task(completed_task.id)
            if merge_result.merged and not merge_result.has_conflicts:
                accepted_task = dispatcher.accept_task(completed_task.id)
                self.roadmap_service.persist()
                self.git_service.cleanup_worktree(completed_task.id)
                self.workflow_service.maybe_complete_workflow()
                return CodeAgentLifecycleResult(
                    task_id=accepted_task.id,
                    outcome="accepted",
                    task_status=accepted_task.status,
                    agent_record=agent_record,
                    gatekeeper_result=gatekeeper_result,
                    merge_result=merge_result,
                    events=runtime_result.events,
                    summary=agent_record.summary,
                )

            self.git_service.abort_merge_if_needed()
            merge_error = format_merge_error(merge_result)
            return await self.retry_service.handle_failure(
                task=completed_task,
                agent_record=agent_record,
                worktree=attempt.worktree,
                events=runtime_result.events,
                reason=merge_error,
                summary=agent_record.summary,
                prior_gatekeeper_result=gatekeeper_result,
                notify_gatekeeper_on_retry=True,
            )

        rejection_reason = gatekeeper_result.error or f"Gatekeeper verdict: {decision or 'rejected'}"
        return await self.retry_service.handle_failure(
            task=completed_task,
            agent_record=agent_record,
            worktree=attempt.worktree,
            events=runtime_result.events,
            reason=rejection_reason,
            summary=agent_record.summary,
            prior_gatekeeper_result=gatekeeper_result,
            notify_gatekeeper_on_retry=False,
        )

    async def _execute_task(self, task: TaskInfo) -> CodeAgentLifecycleResult:
        """Compatibility wrapper for the legacy one-shot flow."""
        attempt = await self.start_task_attempt(task)
        return await self.wait_for_task_attempt(attempt)
