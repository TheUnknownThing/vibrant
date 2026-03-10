"""Question-related orchestration services."""

from __future__ import annotations

from typing import Iterable
from uuid import uuid4

from vibrant.gatekeeper import Gatekeeper, GatekeeperRunResult
from vibrant.models.state import QuestionPriority, QuestionRecord, QuestionStatus, reconcile_question_records

from .state_store import StateStore


class QuestionService:
    """Manage Gatekeeper questions through a dedicated service boundary."""

    def __init__(self, *, state_store: StateStore, gatekeeper: Gatekeeper) -> None:
        self.state_store = state_store
        self.gatekeeper = gatekeeper

    def records(self) -> list[QuestionRecord]:
        return [record.model_copy(deep=True) for record in self.state_store.state.questions]

    def pending_records(self) -> list[QuestionRecord]:
        return [record for record in self.records() if record.status is QuestionStatus.PENDING]

    def pending_questions(self) -> list[str]:
        return [record.text for record in self.pending_records()]

    def current_question(self) -> str | None:
        questions = self.pending_questions()
        return questions[0] if questions else None

    def current_record(self) -> QuestionRecord | None:
        pending = self.pending_records()
        return pending[0] if pending else None

    def get(self, question_id: str) -> QuestionRecord | None:
        needle = question_id.strip()
        if not needle:
            return None
        for record in self.records():
            if record.question_id == needle:
                return record
        return None

    def sync_pending(self, questions: Iterable[str], *, source_agent_id: str | None = None, source_role: str = "gatekeeper") -> list[QuestionRecord]:
        reconciled = reconcile_question_records(
            self.state_store.state.questions,
            list(questions),
            source_agent_id=source_agent_id,
            source_role=source_role,
        )
        self.state_store.state.replace_questions(reconciled)
        self.state_store.persist()
        return self.records()

    def ask(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        priority: QuestionPriority = QuestionPriority.BLOCKING,
    ) -> QuestionRecord:
        question_text = text.strip()
        if not question_text:
            raise ValueError("Question text cannot be empty")

        record = QuestionRecord(
            question_id=f"question-{uuid4()}",
            source_agent_id=source_agent_id,
            source_role=source_role,
            text=question_text,
            priority=priority,
        )
        self.state_store.state.questions.append(record)
        self.state_store.state.sync_pending_question_projection()
        self.state_store.persist()
        return record.model_copy(deep=True)

    def resolve(self, question_id: str, *, answer: str | None = None) -> QuestionRecord:
        needle = question_id.strip()
        if not needle:
            raise ValueError("Question id cannot be empty")

        for record in self.state_store.state.questions:
            if record.question_id == needle:
                if record.status is QuestionStatus.PENDING:
                    record.resolve(answer=answer)
                    self.state_store.state.sync_pending_question_projection()
                    self.state_store.persist()
                return record.model_copy(deep=True)
        raise KeyError(f"Unknown question record: {question_id}")

    async def answer(self, answer: str, *, question: str | None = None) -> GatekeeperRunResult:
        return await self.state_store.engine.answer_pending_question(
            self.gatekeeper,
            answer=answer,
            question=question,
        )
