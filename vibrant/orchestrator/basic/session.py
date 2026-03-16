"""Small helpers for runtime-backed durable state."""

from __future__ import annotations

from vibrant.models.agent import ProviderResumeHandle


def authoritative_resume_handle(handle: ProviderResumeHandle | None) -> ProviderResumeHandle | None:
    """Drop empty provider resume metadata from projections."""

    if handle is None or handle.empty:
        return None
    return handle
