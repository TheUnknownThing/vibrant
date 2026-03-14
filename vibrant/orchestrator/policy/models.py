"""Policy-layer loop state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ..types import DispatchLease, GatekeeperSessionSnapshot, QuestionRecord, WorkflowSnapshot


class TaskLoopStage(str, Enum):
    IDLE = "idle"
    CODING = "coding"
    VALIDATING = "validating"
    REVIEW_PENDING = "review_pending"
    MERGE_PENDING = "merge_pending"
    BLOCKED = "blocked"
    COMPLETED = "completed"


@dataclass(slots=True)
class GatekeeperLoopState:
    session: GatekeeperSessionSnapshot
    conversation_id: str | None
    pending_question: QuestionRecord | None
    pending_questions: tuple[QuestionRecord, ...] = ()
    last_submission_id: str | None = None
    last_error: str | None = None
    busy: bool = False


@dataclass(slots=True)
class TaskLoopSnapshot:
    active_lease: DispatchLease | None = None
    active_attempt_id: str | None = None
    stage: TaskLoopStage = TaskLoopStage.IDLE
    pending_review_ticket_ids: tuple[str, ...] = field(default_factory=tuple)
    blocking_reason: str | None = None


class PolicyCommandPort(Protocol):
    async def submit_user_input(self, text: str, question_id: str | None = None): ...
    async def restart_gatekeeper(self, reason: str | None = None): ...
    async def stop_gatekeeper(self): ...
    async def run_next_task(self): ...
    async def run_until_blocked(self): ...


class PolicyQueryPort(Protocol):
    def workflow_snapshot(self) -> WorkflowSnapshot: ...
    def gatekeeper_state(self) -> GatekeeperLoopState: ...
    def task_loop_state(self) -> TaskLoopSnapshot: ...
