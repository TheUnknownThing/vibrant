"""Question-related orchestration services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Iterable
from uuid import uuid4

from vibrant.agents.gatekeeper import Gatekeeper, GatekeeperRunResult
from vibrant.models.state import GatekeeperStatus, QuestionPriority, QuestionRecord, QuestionStatus, reconcile_question_records
from vibrant.providers.base import CanonicalEvent

from ..state import build_user_input_requested_event
from ..state.store import StateStore


class QuestionService:
    """Manage Gatekeeper questions through a dedicated service boundary."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        gatekeeper: Gatekeeper,
        answer_runner: Callable[[str, str], Awaitable[GatekeeperRunResult]] | None = None,
    ) -> None:
        self.state_store = state_store
        self.gatekeeper = gatekeeper
        self.answer_runner = answer_runner

    def records(self) -> list[QuestionRecord]:
        return [record.model_copy(deep=True) for record in self.state_store.state.questions]

    def pending_records(self) -> list[QuestionRecord]:
        return [record for record in self.records() if record.status is QuestionStatus.PENDING]

    def pending_questions(self) -> list[str]:
        return [record.text for record in self.pending_records()]

    def has_pending_questions(self) -> bool:
        return bool(self.state_store.pending_questions())

    def current_question(self) -> str | None:
        record = self.current_record()
        return record.text if record is not None else None

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

    def sync_pending(
        self,
        questions: Iterable[str],
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        source_run_id: str | None = None,
    ) -> list[QuestionRecord]:
        reconciled = reconcile_question_records(
            self.state_store.state.questions,
            list(questions),
            source_agent_id=source_agent_id,
            source_role=source_role,
            source_run_id=source_run_id,
        )
        self.state_store.state.replace_questions(reconciled)
        self._persist_question_state(emit_request_event=bool(self.state_store.state.pending_questions))
        return self.records()

    def ask(
        self,
        text: str,
        *,
        source_agent_id: str | None = None,
        source_role: str = "gatekeeper",
        source_run_id: str | None = None,
        priority: QuestionPriority = QuestionPriority.BLOCKING,
    ) -> QuestionRecord:
        question_text = text.strip()
        if not question_text:
            raise ValueError("Question text cannot be empty")

        record = QuestionRecord(
            question_id=f"question-{uuid4()}",
            source_agent_id=source_agent_id,
            source_role=source_role,
            source_run_id=source_run_id,
            text=question_text,
            priority=priority,
        )
        self.state_store.state.questions.append(record)
        self.state_store.state.sync_pending_question_projection()
        self._persist_question_state(emit_request_event=True)
        return record.model_copy(deep=True)

    def resolve(
        self,
        question_id: str,
        *,
        answer: str | None = None,
        resolved_by_run_id: str | None = None,
    ) -> QuestionRecord:
        needle = question_id.strip()
        if not needle:
            raise ValueError("Question id cannot be empty")

        for record in self.state_store.state.questions:
            if record.question_id == needle:
                if (
                    record.status is QuestionStatus.PENDING
                    or answer is not None
                    or resolved_by_run_id is not None
                ):
                    record.resolve(answer=answer, resolved_by_run_id=resolved_by_run_id)
                    self.state_store.state.sync_pending_question_projection()
                    self._persist_question_state(emit_request_event=False)
                return record.model_copy(deep=True)
        raise KeyError(f"Unknown question record: {question_id}")

    async def answer(self, answer: str, *, question: str | None = None) -> GatekeeperRunResult:
        selected_question = question or self.current_question()
        if not selected_question:
            raise ValueError("No pending Gatekeeper question to answer")

        self.state_store.set_gatekeeper_status(GatekeeperStatus.RUNNING)

        if self.answer_runner is not None:
            result = await self.answer_runner(selected_question, answer)
        else:
            result = await self.gatekeeper.answer_question(selected_question, answer)
        self.state_store.apply_gatekeeper_result(result)
        event: CanonicalEvent = {
            "type": "user-input.resolved",
            "timestamp": _timestamp_now(),
            "origin": "orchestrator",
            "question": selected_question,
            "answer": answer,
        }
        self.state_store.append_event(event)
        return result

    def _persist_question_state(self, *, emit_request_event: bool) -> None:
        pending = list(self.state_store.state.pending_questions)
        if pending:
            self.state_store.state.gatekeeper_status = GatekeeperStatus.AWAITING_USER
            if emit_request_event:
                self.state_store.engine.emitted_events.append(
                    build_user_input_requested_event(
                        pending,
                        banner_text=self.state_store.user_input_banner(),
                        terminal_bell=self.state_store.notification_bell_enabled(),
                    )
                )
        elif self.state_store.state.gatekeeper_status is not GatekeeperStatus.RUNNING:
            self.state_store.state.gatekeeper_status = GatekeeperStatus.IDLE
        self.state_store.persist()


def _timestamp_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
