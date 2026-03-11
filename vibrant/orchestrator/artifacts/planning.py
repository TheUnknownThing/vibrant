"""Planning orchestration service."""

from __future__ import annotations

import asyncio
from typing import Any

from vibrant.agents.gatekeeper import GatekeeperRequest, GatekeeperRunResult, GatekeeperTrigger
from vibrant.models.state import OrchestratorStatus

from .questions import QuestionService
from .roadmap import RoadmapService
from .workflow import WorkflowService
from ..gatekeeper_runtime import GatekeeperRuntimeService
from ..state.store import StateStore


class PlanningService:
    """Route user planning input and Gatekeeper planning runs."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        question_service: QuestionService,
        gatekeeper_runtime: GatekeeperRuntimeService,
        roadmap_service: RoadmapService,
        workflow_service: WorkflowService,
    ) -> None:
        self.state_store = state_store
        self.question_service = question_service
        self.gatekeeper_runtime = gatekeeper_runtime
        self.roadmap_service = roadmap_service
        self.workflow_service = workflow_service

    async def start_message(self, text: str) -> Any:
        self.state_store.refresh()
        message = text.strip()
        if not message:
            raise ValueError("Gatekeeper message cannot be empty")
        if self.gatekeeper_runtime.busy:
            raise RuntimeError("Gatekeeper is already running")

        pending_question = self.question_service.current_question()
        if pending_question is not None:
            result_future = asyncio.create_task(
                self.question_service.answer(message, question=pending_question),
                name="gatekeeper-answer-question",
            )
            return self.gatekeeper_runtime.future_handle(result_future)

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
        return await self.gatekeeper_runtime.start_request(
            request,
            resume_latest_thread=trigger is GatekeeperTrigger.USER_CONVERSATION,
            on_result=self.gatekeeper_runtime.apply_result_async,
        )

    async def submit_message(self, text: str) -> GatekeeperRunResult:
        handle = await self.start_message(text)
        return await handle.wait()
