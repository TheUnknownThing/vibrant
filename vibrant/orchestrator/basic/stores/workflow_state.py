"""Durable workflow state store."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ..json_store import read_json, write_json
from ...types import (
    GatekeeperLifecycleStatus,
    GatekeeperSessionSnapshot,
    WorkflowState,
    WorkflowStatus,
    utc_now,
)

_UNSET = object()


class WorkflowStateStore:
    """Persist non-derivable workflow session state in ``.vibrant/state.json``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> WorkflowState:
        raw = read_json(self.path, default=None)
        if raw is None:
            state = self._default_state()
            self.save(state)
            return state
        return self._parse_state(raw)

    def save(self, state: WorkflowState) -> None:
        write_json(self.path, self.serialize(state))

    def serialize(self, state: WorkflowState) -> dict[str, object]:
        gatekeeper_payload = {
            **asdict(state.gatekeeper_session),
            "lifecycle_state": state.gatekeeper_session.lifecycle_state.value,
        }
        gatekeeper_payload.pop("provider_thread_id", None)
        return {
            "session_id": state.session_id,
            "started_at": state.started_at,
            "workflow_status": state.workflow_status.value,
            "resume_status": state.resume_status.value if state.resume_status is not None else None,
            "concurrency_limit": state.concurrency_limit,
            "gatekeeper_session": gatekeeper_payload,
            "total_agent_spawns": state.total_agent_spawns,
        }

    def update_workflow_status(
        self,
        status: WorkflowStatus,
        *,
        resume_status: WorkflowStatus | None | object = _UNSET,
    ) -> WorkflowState:
        state = self.load()
        state.workflow_status = status
        if resume_status is not _UNSET:
            state.resume_status = resume_status
        self.save(state)
        return state

    def update_gatekeeper_session(self, session: GatekeeperSessionSnapshot) -> WorkflowState:
        state = self.load()
        session.updated_at = utc_now()
        state.gatekeeper_session = session
        self.save(state)
        return state

    def set_concurrency_limit(self, limit: int) -> WorkflowState:
        if limit < 1:
            raise ValueError("concurrency_limit must be >= 1")
        state = self.load()
        state.concurrency_limit = limit
        self.save(state)
        return state

    def increment_agent_spawns(self, amount: int = 1) -> WorkflowState:
        if amount < 0:
            raise ValueError("amount must be >= 0")
        state = self.load()
        state.total_agent_spawns += amount
        self.save(state)
        return state

    def _default_state(self) -> WorkflowState:
        return WorkflowState(
            session_id=str(uuid4()),
            started_at=utc_now(),
            workflow_status=WorkflowStatus.INIT,
            concurrency_limit=4,
            gatekeeper_session=GatekeeperSessionSnapshot(),
            resume_status=None,
            total_agent_spawns=0,
        )

    def _parse_state(self, raw: object) -> WorkflowState:
        if not isinstance(raw, dict):
            return self._default_state()

        gatekeeper_raw = raw.get("gatekeeper_session")
        gatekeeper_session = self._parse_gatekeeper_session(
            gatekeeper_raw if isinstance(gatekeeper_raw, dict) else {}
        )
        return WorkflowState(
            session_id=_as_non_empty_string(raw.get("session_id")) or str(uuid4()),
            started_at=_as_non_empty_string(raw.get("started_at")) or utc_now(),
            workflow_status=_parse_workflow_status(raw.get("workflow_status")),
            concurrency_limit=_as_positive_int(raw.get("concurrency_limit"), default=4),
            gatekeeper_session=gatekeeper_session,
            resume_status=_parse_optional_workflow_status(raw.get("resume_status")),
            total_agent_spawns=_as_non_negative_int(raw.get("total_agent_spawns"), default=0),
        )

    def _parse_gatekeeper_session(self, raw: dict[str, object]) -> GatekeeperSessionSnapshot:
        lifecycle = GatekeeperLifecycleStatus.NOT_STARTED
        raw_lifecycle = raw.get("lifecycle_state")
        if isinstance(raw_lifecycle, str):
            try:
                lifecycle = GatekeeperLifecycleStatus(raw_lifecycle.strip().lower())
            except ValueError:
                lifecycle = GatekeeperLifecycleStatus.NOT_STARTED
        return GatekeeperSessionSnapshot(
            agent_id=_as_non_empty_string(raw.get("agent_id")),
            run_id=_as_non_empty_string(raw.get("run_id")),
            conversation_id=_as_non_empty_string(raw.get("conversation_id")),
            lifecycle_state=lifecycle,
            provider_thread_id=None,
            active_turn_id=_as_non_empty_string(raw.get("active_turn_id")),
            resumable=raw.get("resumable") is True,
            last_error=_as_non_empty_string(raw.get("last_error")),
            updated_at=_as_non_empty_string(raw.get("updated_at")) or utc_now(),
        )


def _parse_workflow_status(value: object) -> WorkflowStatus:
    if isinstance(value, WorkflowStatus):
        return value
    if isinstance(value, str):
        try:
            return WorkflowStatus(value.strip().lower())
        except ValueError:
            return WorkflowStatus.INIT
    return WorkflowStatus.INIT


def _parse_optional_workflow_status(value: object) -> WorkflowStatus | None:
    if value is None:
        return None
    if isinstance(value, WorkflowStatus):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return None
        try:
            return WorkflowStatus(normalized)
        except ValueError:
            return None
    return None


def _as_non_empty_string(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _as_positive_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _as_non_negative_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    return default
