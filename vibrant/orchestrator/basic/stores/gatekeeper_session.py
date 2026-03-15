"""Helpers for projecting Gatekeeper session resume state."""

from __future__ import annotations

from dataclasses import replace

from vibrant.models.agent import AgentRunRecord, ProviderResumeHandle

from ..session import authoritative_resume_handle
from ...types import GatekeeperSessionSnapshot


def resume_handle_from_run(record: AgentRunRecord | None) -> ProviderResumeHandle | None:
    """Return the canonical resume handle for a Gatekeeper run, if any."""

    if record is None:
        return None
    return authoritative_resume_handle(
        ProviderResumeHandle.from_provider_metadata(record.provider)
    )


def project_gatekeeper_session(
    session: GatekeeperSessionSnapshot,
    *,
    run_record: AgentRunRecord | None = None,
) -> GatekeeperSessionSnapshot:
    """Project derived resume metadata onto a session snapshot."""

    resume_handle = resume_handle_from_run(run_record)
    provider_thread_id = (
        resume_handle.thread_id
        if resume_handle is not None
        else session.provider_thread_id
    )
    resumable = (
        resume_handle.resumable
        if resume_handle is not None
        else bool(session.resumable or provider_thread_id)
    )
    agent_id = session.agent_id
    if agent_id is None and run_record is not None:
        agent_id = run_record.identity.agent_id
    return replace(
        session,
        agent_id=agent_id,
        provider_thread_id=provider_thread_id,
        resumable=resumable,
    )
