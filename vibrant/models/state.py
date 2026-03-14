"""Durable orchestrator runtime state models."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OrchestratorStatus(str, enum.Enum):
    INIT = "init"
    PLANNING = "planning"
    EXECUTING = "executing"
    VALIDATING = "validating"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class GatekeeperStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"


class ProviderRuntimeState(BaseModel):
    """Provider runtime status used to resume or inspect active sessions."""

    model_config = ConfigDict(extra="forbid")

    status: str = "ready"
    provider_thread_id: str | None = None


class QuestionPriority(str, enum.Enum):
    BLOCKING = "blocking"
    NORMAL = "normal"


class QuestionStatus(str, enum.Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    RESOLVED = "resolved"


class QuestionRecord(BaseModel):
    """Durable record for one user-facing orchestrator question."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    source_agent_id: str | None = None
    source_role: str = "gatekeeper"
    text: str
    priority: QuestionPriority = QuestionPriority.BLOCKING
    status: QuestionStatus = QuestionStatus.PENDING
    answer: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None

    @field_validator("question_id", "text", mode="before")
    @classmethod
    def _normalize_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_record(self) -> QuestionRecord:
        if not self.question_id:
            raise ValueError("question_id must not be empty")
        if not self.text:
            raise ValueError("text must not be empty")
        if self.resolved_at is not None and self.status is QuestionStatus.PENDING:
            raise ValueError("pending questions cannot have resolved_at")
        if self.status is QuestionStatus.PENDING and self.answer is not None:
            raise ValueError("pending questions cannot have an answer")
        return self

    def is_pending(self) -> bool:
        return self.status is QuestionStatus.PENDING

    def resolve(self, *, answer: str | None = None) -> None:
        cleaned_answer = answer.strip() if isinstance(answer, str) else None
        self.answer = cleaned_answer or None
        self.status = QuestionStatus.ANSWERED if self.answer else QuestionStatus.RESOLVED
        self.resolved_at = datetime.now(timezone.utc)


class OrchestratorState(BaseModel):
    """Durable orchestrator state stored in ``.vibrant/state.json``."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: OrchestratorStatus = OrchestratorStatus.INIT
    active_agents: list[str] = Field(default_factory=list)
    gatekeeper_status: GatekeeperStatus = GatekeeperStatus.IDLE
    questions: list[QuestionRecord] = Field(default_factory=list)
    pending_questions: list[str] = Field(default_factory=list)
    last_consensus_version: int = 0
    concurrency_limit: int = 4
    provider_runtime: dict[str, ProviderRuntimeState] = Field(default_factory=dict)
    completed_tasks: list[str] = Field(default_factory=list)
    failed_tasks: list[str] = Field(default_factory=list)
    total_agent_spawns: int = 0

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        data = dict(value)

        if "provider_runtime" not in data:
            legacy_threads = data.pop("provider_threads", None)
            if isinstance(legacy_threads, list):
                data["provider_runtime"] = _legacy_provider_runtime(legacy_threads)
        else:
            data.pop("provider_threads", None)

        if "questions" not in data:
            legacy_pending_questions = data.get("pending_questions")
            if isinstance(legacy_pending_questions, list):
                data["questions"] = _legacy_question_records(legacy_pending_questions)

        data.pop("pending_requests", None)
        return data

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        if isinstance(value, OrchestratorStatus):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "running":
                return OrchestratorStatus.EXECUTING.value
            return normalized
        return value

    @model_validator(mode="after")
    def validate_state(self) -> OrchestratorState:
        if self.last_consensus_version < 0:
            raise ValueError("last_consensus_version must be >= 0")
        if self.concurrency_limit < 1:
            raise ValueError("concurrency_limit must be >= 1")
        if self.total_agent_spawns < 0:
            raise ValueError("total_agent_spawns must be >= 0")
        self.sync_pending_question_projection()
        return self

    def pending_question_records(self) -> list[QuestionRecord]:
        return [record for record in self.questions if record.is_pending()]

    def sync_pending_question_projection(self) -> None:
        self.pending_questions = [record.text for record in self.pending_question_records()]

    def replace_questions(self, questions: list[QuestionRecord]) -> None:
        self.questions = questions
        self.sync_pending_question_projection()


def _legacy_provider_runtime(items: list[object]) -> dict[str, ProviderRuntimeState]:
    runtime: dict[str, ProviderRuntimeState] = {}
    for item in items:
        if not isinstance(item, dict):
            continue

        owner_agent_id = item.get("owner_agent_id")
        if not isinstance(owner_agent_id, str) or not owner_agent_id:
            continue

        runtime[owner_agent_id] = ProviderRuntimeState(
            status=_legacy_runtime_status(item),
            provider_thread_id=_legacy_provider_thread_id(item),
        )
    return runtime


def _legacy_runtime_status(item: dict[str, Any]) -> str:
    value = item.get("runtime_state") or item.get("status") or "ready"
    return value if isinstance(value, str) and value else "ready"


def _legacy_provider_thread_id(item: dict[str, Any]) -> str | None:
    value = item.get("provider_thread_id")
    return value if isinstance(value, str) and value else None


def _legacy_question_records(items: list[object]) -> list[QuestionRecord]:
    records: list[QuestionRecord] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        records.append(
            QuestionRecord(
                question_id=f"legacy-question-{index}",
                text=text,
            )
        )
    return records


def reconcile_question_records(
    existing_records: list[QuestionRecord],
    pending_questions: list[str],
    *,
    source_agent_id: str | None = None,
    source_role: str = "gatekeeper",
    priority: QuestionPriority = QuestionPriority.BLOCKING,
) -> list[QuestionRecord]:
    """Reconcile durable question records against the latest pending text list."""

    pending_by_text: dict[str, list[QuestionRecord]] = {}
    retained_records: list[QuestionRecord] = []

    for record in existing_records:
        if record.is_pending():
            pending_by_text.setdefault(record.text, []).append(record.model_copy(deep=True))
            continue
        retained_records.append(record.model_copy(deep=True))

    next_pending_records: list[QuestionRecord] = []
    for raw_question in pending_questions:
        question = raw_question.strip() if isinstance(raw_question, str) else ""
        if not question:
            continue
        existing = pending_by_text.get(question)
        if existing:
            next_pending_records.append(existing.pop(0))
            continue
        next_pending_records.append(
            QuestionRecord(
                question_id=f"question-{uuid4()}",
                source_agent_id=source_agent_id,
                source_role=source_role,
                text=question,
                priority=priority,
            )
        )

    for leftover_records in pending_by_text.values():
        for record in leftover_records:
            record.resolve()
            retained_records.append(record)

    return retained_records + next_pending_records
