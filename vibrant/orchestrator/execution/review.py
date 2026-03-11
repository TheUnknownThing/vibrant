"""Review orchestration service."""

from __future__ import annotations

import inspect

from vibrant.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.agent import AgentRecord
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.git_manager import GitWorktreeInfo

from .git_workspace import GitWorkspaceService
from ..artifacts.roadmap import RoadmapService
from ..state.store import StateStore


class ReviewService:
    """Route task outcomes through Gatekeeper review and normalize verdicts."""

    ACCEPTED_VERDICTS = {"accept", "accepted", "approve", "approved", "done"}
    AWAITING_INPUT_VERDICTS = {"needs_input", "awaiting_input"}

    def __init__(
        self,
        *,
        gatekeeper: Gatekeeper,
        state_store: StateStore,
        roadmap_service: RoadmapService,
        git_service: GitWorkspaceService,
    ) -> None:
        self.gatekeeper = gatekeeper
        self.state_store = state_store
        self.roadmap_service = roadmap_service
        self.git_service = git_service

    async def run_gatekeeper_request(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        run_gatekeeper = self.gatekeeper.run
        try:
            signature = inspect.signature(run_gatekeeper)
        except (TypeError, ValueError):
            signature = None

        if signature is not None and "resume_latest_thread" in signature.parameters:
            return await run_gatekeeper(request, resume_latest_thread=resume_latest_thread)
        return await run_gatekeeper(request)

    async def review_completion(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
    ) -> tuple[GatekeeperRunResult, str]:
        result = await self.gatekeeper.run(self.build_completion_request(task, agent_record, worktree))
        self.state_store.apply_gatekeeper_result(result)
        self.roadmap_service.merge_result(result.roadmap_document)
        return result, self.resolve_decision(result, task.id)

    async def review_failure(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRunResult:
        result = await self.gatekeeper.run(self.build_failure_request(task, agent_record, worktree, reason))
        self.state_store.apply_gatekeeper_result(result)
        self.roadmap_service.merge_result(result.roadmap_document)
        return result

    async def review_escalation(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRunResult:
        result = await self.gatekeeper.run(self.build_escalation_request(task, agent_record, worktree, reason))
        self.state_store.apply_gatekeeper_result(result)
        self.roadmap_service.merge_result(result.roadmap_document)
        return result

    def build_completion_request(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
    ) -> GatekeeperRequest:
        diff_text = self.git_service.collect_diff(task, worktree)
        trigger_description = "\n".join(
            [
                f"Task {task.id}: {task.title}",
                "Evaluate the completed implementation against the roadmap acceptance criteria.",
                f"Branch: {task.branch or self.git_service.branch_name(task.id)}",
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

    def build_failure_request(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRequest:
        diff_text = self.git_service.collect_diff(task, worktree)
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

    def build_escalation_request(
        self,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRequest:
        diff_text = self.git_service.collect_diff(task, worktree)
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

    def resolve_decision(self, result: GatekeeperRunResult, task_id: str) -> str:
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
