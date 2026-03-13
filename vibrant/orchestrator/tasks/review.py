"""Task review orchestration service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect

from vibrant.agents.gatekeeper import Gatekeeper, GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.agents.role_results import GatekeeperRoleResult
from vibrant.models.agent import AgentRunRecord
from vibrant.models.task import TaskInfo
from vibrant.prompts import (
    build_task_completion_trigger_description,
    build_task_escalation_trigger_description,
    build_task_failure_trigger_description,
)

from ..artifacts.roadmap import RoadmapService
from ..execution.git_manager import GitWorktreeInfo
from ..execution.git_workspace import GitWorkspaceService
from ..state.store import StateStore
from .models import TaskReviewDecision
from .store import TaskStore


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
        task_store: TaskStore,
        gatekeeper_runner: Callable[[GatekeeperRequest, bool | None], Awaitable[GatekeeperRunResult]] | None = None,
    ) -> None:
        self.gatekeeper = gatekeeper
        self.state_store = state_store
        self.roadmap_service = roadmap_service
        self.git_service = git_service
        self.task_store = task_store
        self.gatekeeper_runner = gatekeeper_runner

    async def run_gatekeeper_request(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        if self.gatekeeper_runner is not None:
            return await self.gatekeeper_runner(request, resume_latest_thread)

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
        agent_record: AgentRunRecord,
        worktree: GitWorktreeInfo,
    ) -> tuple[GatekeeperRunResult, str]:
        result = await self.run_gatekeeper_request(self.build_completion_request(task, agent_record, worktree))
        self.state_store.apply_gatekeeper_result(result)
        self._reload_roadmap()
        decision = self.resolve_decision(result, task_id=task.id)
        self._record_review(task, result, decision=decision, reason=result.error)
        return result, decision

    async def review_failure(
        self,
        task: TaskInfo,
        agent_record: AgentRunRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRunResult:
        result = await self.run_gatekeeper_request(self.build_failure_request(task, agent_record, worktree, reason))
        self.state_store.apply_gatekeeper_result(result)
        self._reload_roadmap()
        decision = TaskReviewDecision.NEEDS_INPUT if result.awaiting_input or result.input_requests else TaskReviewDecision.RETRY
        self._record_review(task, result, decision=decision, reason=reason)
        return result

    async def review_escalation(
        self,
        task: TaskInfo,
        agent_record: AgentRunRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRunResult:
        result = await self.run_gatekeeper_request(self.build_escalation_request(task, agent_record, worktree, reason))
        self.state_store.apply_gatekeeper_result(result)
        self._reload_roadmap()
        decision = TaskReviewDecision.NEEDS_INPUT if result.awaiting_input or result.input_requests else TaskReviewDecision.ESCALATED
        self._record_review(task, result, decision=decision, reason=reason)
        return result

    def build_completion_request(
        self,
        task: TaskInfo,
        agent_record: AgentRunRecord,
        worktree: GitWorktreeInfo,
    ) -> GatekeeperRequest:
        diff_text = self.git_service.collect_diff(task, worktree)
        trigger_description = build_task_completion_trigger_description(
            task_id=task.id,
            task_title=task.title,
            branch=task.branch or self.git_service.branch_name(task.id),
            acceptance_criteria=task.acceptance_criteria,
            diff_text=diff_text,
        )
        return GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_COMPLETION,
            trigger_description=trigger_description,
            agent_summary=agent_record.outcome.summary,
        )

    def build_failure_request(
        self,
        task: TaskInfo,
        agent_record: AgentRunRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRequest:
        diff_text = self.git_service.collect_diff(task, worktree)
        trigger_description = build_task_failure_trigger_description(
            task_id=task.id,
            task_title=task.title,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            reason=reason,
            diff_text=diff_text,
        )
        return GatekeeperRequest(
            trigger=GatekeeperTrigger.TASK_FAILURE,
            trigger_description=trigger_description,
            agent_summary=agent_record.outcome.summary or reason,
        )

    def build_escalation_request(
        self,
        task: TaskInfo,
        agent_record: AgentRunRecord,
        worktree: GitWorktreeInfo,
        reason: str,
    ) -> GatekeeperRequest:
        diff_text = self.git_service.collect_diff(task, worktree)
        trigger_description = build_task_escalation_trigger_description(
            task_id=task.id,
            task_title=task.title,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            reason=reason,
            diff_text=diff_text,
        )
        return GatekeeperRequest(
            trigger=GatekeeperTrigger.MAX_RETRIES_EXCEEDED,
            trigger_description=trigger_description,
            agent_summary=agent_record.outcome.summary or reason,
        )

    def _reload_roadmap(self) -> None:
        self.roadmap_service.reload(
            project_name=self.roadmap_service.project_name,
            concurrency_limit=self.state_store.state.concurrency_limit,
        )

    def _record_review(
        self,
        task: TaskInfo,
        result: GatekeeperRunResult,
        *,
        decision: TaskReviewDecision | str,
        reason: str | None,
    ) -> None:
        current_task = self.roadmap_service.get_task(task.id) or task
        self.task_store.record_review(
            task=current_task,
            decision=decision,
            reason=reason,
            summary=getattr(getattr(result, "agent_record", None), "outcome", None).summary if result.agent_record else None,
            gatekeeper_agent_id=result.agent_record.identity.agent_id if result.agent_record is not None else None,
        )

    def resolve_decision(self, result: GatekeeperRunResult, *, task_id: str | None = None) -> str:
        if result.awaiting_input or result.input_requests:
            return "needs_input"

        decision_from_state = self._resolve_decision_from_task_state(task_id)
        if decision_from_state is not None:
            return decision_from_state

        role_result = result.role_result
        if not isinstance(role_result, GatekeeperRoleResult):
            if result.error:
                return "rejected"
            raise RuntimeError("Gatekeeper result is missing a GatekeeperRoleResult payload")

        normalized = (role_result.suggested_decision or "").strip().lower()
        if not normalized:
            if result.error:
                return "rejected"
            raise RuntimeError("Gatekeeper result did not provide a suggested decision")

        if normalized in self.ACCEPTED_VERDICTS:
            return "accepted"
        if normalized in self.AWAITING_INPUT_VERDICTS:
            return "needs_input"
        if normalized in {"retry", "retried"}:
            return "retry"
        if normalized in {"escalate", "escalated"}:
            return "escalated"
        if normalized in {"rejected", "reject"}:
            return "rejected"
        if result.error:
            return "rejected"
        raise RuntimeError(f"Unsupported Gatekeeper decision: {normalized}")

    def _resolve_decision_from_task_state(self, task_id: str | None) -> str | None:
        if not task_id:
            return None

        current_task = self.roadmap_service.get_task(task_id)
        if current_task is None and self.roadmap_service.dispatcher is not None:
            current_task = self.roadmap_service.dispatcher.get_task(task_id)
        if current_task is None:
            return None

        if current_task.status.value == "accepted":
            return "accepted"
        if current_task.status.value == "escalated":
            return "escalated"
        if current_task.status.value == "queued":
            return "retry"
        if current_task.status.value == "failed":
            return "rejected"
        return None
