"""Attempt execution session helpers and resources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from vibrant.models.agent import ProviderResumeHandle

from ...basic.session import authoritative_resume_handle
from ...types import (
    AttemptCompletion,
    AttemptExecutionSnapshot,
    AttemptExecutionView,
    AttemptRecoveryState,
    AttemptRecord,
    AttemptStatus,
    InputRequest,
)
from .models import WORKER_INPUT_UNSUPPORTED_ERROR

_CODE_PHASE_STATUSES = {
    AttemptStatus.LEASED,
    AttemptStatus.RUNNING,
    AttemptStatus.AWAITING_INPUT,
}

_VALIDATION_PHASE_STATUSES = {
    AttemptStatus.VALIDATION_PENDING,
    AttemptStatus.VALIDATING,
}

_MERGE_PHASE_STATUSES = {
    AttemptStatus.MERGE_PENDING,
}

_RECOVERABLE_ATTEMPT_STATUSES = {
    AttemptStatus.LEASED,
    AttemptStatus.RUNNING,
}

_NON_RECOVERABLE_RUN_STATUSES = {"awaiting_input", "completed", "failed", "killed"}


def active_run_id_for_attempt(attempt: AttemptRecord) -> str | None:
    """Resolve the run id that currently represents attempt execution."""

    if attempt.status in _MERGE_PHASE_STATUSES:
        return attempt.merge_run_id
    if attempt.status in _VALIDATION_PHASE_STATUSES:
        return _latest_run_id(attempt.validation_run_ids) or attempt.code_run_id
    if attempt.status in _CODE_PHASE_STATUSES:
        return attempt.code_run_id
    return attempt.code_run_id or _latest_run_id(attempt.validation_run_ids) or attempt.merge_run_id


@dataclass(slots=True)
class AttemptExecutionSessionResource:
    attempt_store: Any
    run_store: Any
    workspace_service: Any
    runtime_service: Any
    resume_callback: Callable[[str, AttemptExecutionSnapshot, ProviderResumeHandle | None, Any], Any]

    def get(self, attempt_id: str) -> AttemptExecutionSnapshot | None:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            return None
        return _project_snapshot(
            attempt,
            run_store=self.run_store,
            workspace_service=self.workspace_service,
            runtime_service=self.runtime_service,
        )

    def get_view(self, attempt_id: str) -> AttemptExecutionView | None:
        snapshot = self.get(attempt_id)
        if snapshot is None:
            return None
        return _view(snapshot)

    def get_recovery_state(self, attempt_id: str) -> AttemptRecoveryState | None:
        snapshot = self.get(attempt_id)
        if snapshot is None:
            return None
        return _recovery(snapshot)

    def set_status(
        self,
        attempt_id: str,
        status: AttemptStatus,
    ) -> AttemptExecutionSnapshot:
        snapshot = self.get(attempt_id)
        if snapshot is None:
            raise KeyError(f"Unknown attempt session: {attempt_id}")
        return self._freeze_snapshot(replace(snapshot, status=status))

    def bind_run(
        self,
        attempt_id: str,
        *,
        run_id: str,
        conversation_id: str | None = None,
        status: AttemptStatus | None = None,
    ) -> AttemptExecutionSnapshot:
        snapshot = self.get(attempt_id)
        if snapshot is None:
            raise KeyError(f"Unknown attempt session: {attempt_id}")
        return self._freeze_snapshot(
            replace(
                snapshot,
                run_id=run_id,
                conversation_id=conversation_id or snapshot.conversation_id,
                status=status or snapshot.status,
                live=False,
                awaiting_input=False,
                input_requests=[],
                run_stop_reason=None,
                provider_resume_handle=None,
                provider_thread_id=None,
                resumable=False,
                run_status=None,
            )
        )

    async def resume(
        self,
        attempt_id: str,
        *,
        prepared: Any,
    ) -> AttemptExecutionSnapshot:
        snapshot = self.get(attempt_id)
        if snapshot is None:
            raise KeyError(f"Unknown attempt session: {attempt_id}")
        resumed = await self.resume_callback(
            attempt_id,
            snapshot,
            authoritative_resume_handle(snapshot.provider_resume_handle),
            prepared,
        )
        return self._freeze_snapshot(resumed)

    def list_active(self) -> list[AttemptExecutionSnapshot]:
        snapshots: list[AttemptExecutionSnapshot] = []
        for attempt in self.attempt_store.list_active():
            snapshot = self.get(attempt.attempt_id)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def list_active_views(self) -> list[AttemptExecutionView]:
        return [_view(snapshot) for snapshot in self.list_active()]

    def list_active_recovery_states(self) -> list[AttemptRecoveryState]:
        return [_recovery(snapshot) for snapshot in self.list_active()]

    def next_recoverable(self) -> AttemptExecutionSnapshot | None:
        for snapshot in self.list_active():
            if attempt_needs_recovery(snapshot):
                return snapshot
        return None

    def next_recoverable_state(self) -> AttemptRecoveryState | None:
        snapshot = self.next_recoverable()
        return None if snapshot is None else _recovery(snapshot)

    def durable_completion(self, attempt_id: str) -> AttemptCompletion | None:
        attempt = self.attempt_store.get(attempt_id)
        if attempt is None:
            return None
        if attempt.status not in {AttemptStatus.LEASED, AttemptStatus.RUNNING}:
            return None
        snapshot = self.get(attempt_id)
        if snapshot is None or snapshot.live or snapshot.run_id is None:
            return None
        run_record = self.run_store.get(snapshot.run_id)
        if run_record is None:
            return None

        status = run_record.lifecycle.status
        stop_reason = run_record.lifecycle.stop_reason
        completion_error = run_record.outcome.error
        if stop_reason == "paused":
            return None
        if status.value == "awaiting_input":
            completion_status = "failed"
            completion_error = completion_error or WORKER_INPUT_UNSUPPORTED_ERROR
        elif status.value == "completed":
            completion_status = "succeeded"
        elif status.value == "killed":
            completion_status = "cancelled"
        elif status.value == "failed":
            completion_status = "failed"
        else:
            return None

        return AttemptCompletion(
            attempt_id=attempt.attempt_id,
            task_id=attempt.task_id,
            status=completion_status,
            code_run_id=snapshot.run_id,
            workspace_ref=attempt.workspace_id,
            diff_ref=None,
            validation=None,
            summary=run_record.outcome.summary,
            error=completion_error,
            conversation_ref=attempt.conversation_id,
            provider_events_ref=run_record.provider.canonical_event_log,
        )

    def reconcile_active(self) -> list[AttemptExecutionSnapshot]:
        snapshots: list[AttemptExecutionSnapshot] = []
        for attempt in self.attempt_store.list_active():
            snapshot = self.get(attempt.attempt_id)
            if snapshot is None:
                continue
            snapshots.append(self._freeze_snapshot(self._reconcile_snapshot(snapshot)))
        return snapshots

    def _freeze_snapshot(self, snapshot: AttemptExecutionSnapshot) -> AttemptExecutionSnapshot:
        attempt = self.attempt_store.get(snapshot.attempt_id)
        if attempt is None:
            raise KeyError(f"Unknown attempt session: {snapshot.attempt_id}")

        update_kwargs: dict[str, object] = {}
        if snapshot.status is not attempt.status:
            update_kwargs["status"] = snapshot.status
        if snapshot.conversation_id != attempt.conversation_id:
            update_kwargs["conversation_id"] = snapshot.conversation_id
        if snapshot.run_id is not None:
            run_updates = _attempt_run_updates(attempt, snapshot)
            update_kwargs.update(run_updates)

        if update_kwargs:
            self.attempt_store.update(snapshot.attempt_id, **update_kwargs)
        reloaded = self.get(snapshot.attempt_id)
        if reloaded is None:
            raise KeyError(f"Unknown attempt session after freeze: {snapshot.attempt_id}")
        return reloaded

    @staticmethod
    def _reconcile_snapshot(snapshot: AttemptExecutionSnapshot) -> AttemptExecutionSnapshot:
        if snapshot.live:
            return snapshot
        if snapshot.status is AttemptStatus.AWAITING_INPUT:
            return replace(snapshot, status=AttemptStatus.FAILED)
        if snapshot.status in _RECOVERABLE_ATTEMPT_STATUSES and snapshot.workspace_path is None:
            return replace(snapshot, status=AttemptStatus.FAILED)
        if snapshot.status is AttemptStatus.RUNNING and snapshot.run_status == "awaiting_input":
            return replace(snapshot, status=AttemptStatus.FAILED)
        if snapshot.status in _RECOVERABLE_ATTEMPT_STATUSES and snapshot.run_stop_reason == "paused":
            return snapshot
        if snapshot.status in _RECOVERABLE_ATTEMPT_STATUSES and snapshot.run_status == "failed":
            return replace(snapshot, status=AttemptStatus.FAILED)
        if snapshot.status in _RECOVERABLE_ATTEMPT_STATUSES and snapshot.run_status == "killed":
            return replace(snapshot, status=AttemptStatus.CANCELLED)
        return snapshot


def attempt_needs_recovery(snapshot: AttemptRecoveryState | AttemptExecutionSnapshot) -> bool:
    """Return whether the attempt should be recovered on the next execution tick."""

    return (
        snapshot.status in _RECOVERABLE_ATTEMPT_STATUSES
        and (snapshot.run_stop_reason == "paused" or snapshot.run_status not in _NON_RECOVERABLE_RUN_STATUSES)
        and not snapshot.live
        and snapshot.workspace_path is not None
    )


def _project_snapshot(
    attempt: AttemptRecord,
    *,
    run_store: Any,
    workspace_service: Any,
    runtime_service: Any,
) -> AttemptExecutionSnapshot:
    run_id = active_run_id_for_attempt(attempt)
    run_record = run_store.get(run_id) if run_id is not None else None
    resume_handle = (
        authoritative_resume_handle(ProviderResumeHandle.from_provider_metadata(run_record.provider))
        if run_record is not None
        else None
    )
    workspace_path = None
    try:
        workspace = workspace_service.get_workspace(task_id=attempt.task_id, workspace_id=attempt.workspace_id)
    except Exception:
        workspace = None
    if workspace is not None:
        workspace_path = workspace.path

    live = False
    awaiting_input = attempt.status is AttemptStatus.AWAITING_INPUT
    input_requests: list[InputRequest] = []
    runtime_snapshot = None
    if run_id is not None:
        try:
            runtime_snapshot = runtime_service.snapshot_handle(run_id)
        except Exception:
            runtime_snapshot = None
    if runtime_snapshot is not None:
        live = True
        awaiting_input = runtime_snapshot.awaiting_input
        input_requests = list(runtime_snapshot.input_requests)

    return AttemptExecutionSnapshot(
        attempt_id=attempt.attempt_id,
        task_id=attempt.task_id,
        status=attempt.status,
        workspace_id=attempt.workspace_id,
        workspace_path=workspace_path,
        conversation_id=attempt.conversation_id,
        run_id=run_id,
        run_status=run_record.lifecycle.status.value if run_record is not None else None,
        run_stop_reason=run_record.lifecycle.stop_reason if run_record is not None else None,
        provider_resume_handle=resume_handle,
        provider_thread_id=(
            runtime_snapshot.provider_thread_id
            if runtime_snapshot is not None
            else (resume_handle.thread_id if resume_handle is not None else None)
        ),
        resumable=bool(resume_handle and resume_handle.resumable),
        live=live,
        awaiting_input=awaiting_input,
        input_requests=input_requests,
        updated_at=attempt.updated_at,
    )


def _view(snapshot: AttemptExecutionSnapshot) -> AttemptExecutionView:
    return AttemptExecutionView(
        attempt_id=snapshot.attempt_id,
        task_id=snapshot.task_id,
        status=snapshot.status,
        workspace_id=snapshot.workspace_id,
        conversation_id=snapshot.conversation_id,
        run_id=snapshot.run_id,
        run_status=snapshot.run_status,
        run_stop_reason=snapshot.run_stop_reason,
        provider_thread_id=snapshot.provider_thread_id,
        resumable=snapshot.resumable,
        live=snapshot.live,
        awaiting_input=snapshot.awaiting_input,
        input_requests=list(snapshot.input_requests),
        updated_at=snapshot.updated_at,
    )


def _recovery(snapshot: AttemptExecutionSnapshot) -> AttemptRecoveryState:
    return AttemptRecoveryState(
        attempt_id=snapshot.attempt_id,
        task_id=snapshot.task_id,
        status=snapshot.status,
        run_id=snapshot.run_id,
        run_status=snapshot.run_status,
        run_stop_reason=snapshot.run_stop_reason,
        workspace_path=snapshot.workspace_path,
        live=snapshot.live,
    )


def _latest_run_id(run_ids: list[str]) -> str | None:
    return run_ids[-1] if run_ids else None


def _attempt_run_updates(
    attempt: AttemptRecord,
    snapshot: AttemptExecutionSnapshot,
) -> dict[str, object]:
    if snapshot.status in _MERGE_PHASE_STATUSES:
        if snapshot.run_id != attempt.merge_run_id:
            return {"merge_run_id": snapshot.run_id}
        return {}

    if snapshot.status in _VALIDATION_PHASE_STATUSES:
        validation_run_ids = list(attempt.validation_run_ids)
        if not validation_run_ids or validation_run_ids[-1] != snapshot.run_id:
            validation_run_ids.append(snapshot.run_id)
            return {"validation_run_ids": validation_run_ids}
        return {}

    if snapshot.run_id != attempt.code_run_id:
        return {"code_run_id": snapshot.run_id}
    return {}
