"""Attempt execution session projection helpers."""

from __future__ import annotations

from vibrant.models.agent import ProviderResumeHandle

from ...types import AttemptExecutionSnapshot, AttemptRecord, AttemptStatus, InputRequest

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


def active_run_id_for_attempt(attempt: AttemptRecord) -> str | None:
    """Resolve the run id that currently represents attempt execution."""

    if attempt.status in _MERGE_PHASE_STATUSES:
        return attempt.merge_run_id
    if attempt.status in _VALIDATION_PHASE_STATUSES:
        return _latest_run_id(attempt.validation_run_ids) or attempt.code_run_id
    if attempt.status in _CODE_PHASE_STATUSES:
        return attempt.code_run_id
    return attempt.code_run_id or _latest_run_id(attempt.validation_run_ids) or attempt.merge_run_id


def project_attempt_execution(
    attempt: AttemptRecord,
    *,
    run_store,
    workspace_service,
    runtime_service,
) -> AttemptExecutionSnapshot:
    """Project an execution-session view from attempt, run, and workspace state."""

    run_id = active_run_id_for_attempt(attempt)
    run_record = run_store.get(run_id) if run_id is not None else None
    resume_handle = (
        ProviderResumeHandle.from_provider_metadata(run_record.provider)
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
        conversation_id=attempt.conversation_id,
        run_id=run_id,
        run_status=run_record.lifecycle.status.value if run_record is not None else None,
        workspace_path=workspace_path,
        provider_kind=run_record.provider.kind if run_record is not None else None,
        provider_thread_id=(
            runtime_snapshot.provider_thread_id
            if runtime_snapshot is not None
            else (resume_handle.thread_id if resume_handle is not None else None)
        ),
        provider_thread_path=resume_handle.thread_path if resume_handle is not None else None,
        provider_resume_cursor=resume_handle.resume_cursor if resume_handle is not None else None,
        resumable=bool(resume_handle and resume_handle.resumable),
        live=live,
        awaiting_input=awaiting_input,
        input_requests=input_requests,
        updated_at=attempt.updated_at,
    )


def attempt_needs_recovery(snapshot: AttemptExecutionSnapshot) -> bool:
    """Return whether the attempt should be recovered on the next execution tick."""

    return (
        snapshot.status in _RECOVERABLE_ATTEMPT_STATUSES
        and not snapshot.live
        and snapshot.workspace_path is not None
    )


def _latest_run_id(run_ids: list[str]) -> str | None:
    return run_ids[-1] if run_ids else None
