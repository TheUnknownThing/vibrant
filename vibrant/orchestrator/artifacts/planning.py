"""Planning orchestration service."""

from __future__ import annotations

from vibrant.agents.gatekeeper import GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.state import OrchestratorStatus

from .questions import QuestionService
from .roadmap import RoadmapService
from .workflow import WorkflowService
from ..execution.review import ReviewService
from ..state.store import StateStore


class PlanningService:
    """Route user planning input and Gatekeeper planning runs."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        question_service: QuestionService,
        review_service: ReviewService,
        roadmap_service: RoadmapService,
        workflow_service: WorkflowService,
    ) -> None:
        self.state_store = state_store
        self.question_service = question_service
        self.review_service = review_service
        self.roadmap_service = roadmap_service
        self.workflow_service = workflow_service

    async def submit_message(self, text: str) -> GatekeeperRunResult:
        self.state_store.refresh()
        message = text.strip()
        if not message:
            raise ValueError("Gatekeeper message cannot be empty")

        if self.question_service.has_pending_questions():
            result = await self.question_service.answer(message)
        else:
            trigger = (
                GatekeeperTrigger.PROJECT_START
                if self.state_store.status is OrchestratorStatus.INIT
                else GatekeeperTrigger.USER_CONVERSATION
            )
            request = GatekeeperRequest(
                trigger=trigger,
                trigger_description=message,
                agent_summary=message,
            )
            result = await self.review_service.run_gatekeeper_request(
                request,
                resume_latest_thread=trigger is GatekeeperTrigger.USER_CONVERSATION,
            )
            self.state_store.apply_gatekeeper_result(result)

        self.roadmap_service.reload(
            project_name=self.roadmap_service.project_name,
            concurrency_limit=self.state_store.state.concurrency_limit,
        )
        self.roadmap_service.persist()
        self.workflow_service.maybe_complete_workflow()
        self.state_store.refresh()
        return result
