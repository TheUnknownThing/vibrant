"""Question-related orchestration services."""

from __future__ import annotations

from vibrant.gatekeeper import Gatekeeper, GatekeeperRunResult

from .state_store import StateStore


class QuestionService:
    """Manage Gatekeeper questions through a dedicated service boundary."""

    def __init__(self, *, state_store: StateStore, gatekeeper: Gatekeeper) -> None:
        self.state_store = state_store
        self.gatekeeper = gatekeeper

    def pending_questions(self) -> list[str]:
        questions = self.state_store.state.pending_questions
        return [question for question in questions if isinstance(question, str) and question]

    def current_question(self) -> str | None:
        questions = self.pending_questions()
        return questions[0] if questions else None

    async def answer(self, answer: str, *, question: str | None = None) -> GatekeeperRunResult:
        return await self.state_store.engine.answer_pending_question(
            self.gatekeeper,
            answer=answer,
            question=question,
        )
