"""Retry and escalation policy service."""

from __future__ import annotations

from vibrant.gatekeeper import GatekeeperRunResult
from vibrant.models.agent import AgentRecord
from vibrant.models.task import TaskInfo, TaskStatus
from vibrant.orchestrator.git_manager import GitWorktreeInfo

from ..types import CodeAgentLifecycleResult
from .git_workspace import GitWorkspaceService
from .review import ReviewService
from .roadmap import RoadmapService


class RetryPolicyService:
    """Own retry, requeue, and escalation behavior for failed tasks."""

    def __init__(
        self,
        *,
        roadmap_service: RoadmapService,
        review_service: ReviewService,
        git_service: GitWorkspaceService,
    ) -> None:
        self.roadmap_service = roadmap_service
        self.review_service = review_service
        self.git_service = git_service

    async def handle_failure(
        self,
        *,
        task: TaskInfo,
        agent_record: AgentRecord,
        worktree: GitWorktreeInfo,
        events: list[dict[str, object]],
        reason: str,
        summary: str | None,
        prior_gatekeeper_result: GatekeeperRunResult | None = None,
        notify_gatekeeper_on_retry: bool,
    ) -> CodeAgentLifecycleResult:
        dispatcher = self.roadmap_service.dispatcher
        assert dispatcher is not None

        updated_task = dispatcher.fail_task(task.id, failure_reason=reason)
        gatekeeper_result = prior_gatekeeper_result
        if updated_task.status is TaskStatus.ESCALATED:
            gatekeeper_result = await self.review_service.review_escalation(updated_task, agent_record, worktree, reason)
            outcome = "escalated"
        elif notify_gatekeeper_on_retry:
            gatekeeper_result = await self.review_service.review_failure(updated_task, agent_record, worktree, reason)
            outcome = "retried"
        else:
            outcome = "retried"

        self.roadmap_service.persist()
        self.git_service.cleanup_worktree(updated_task.id)
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
