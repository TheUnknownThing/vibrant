"""Gatekeeper role and instance policy."""

from __future__ import annotations

from vibrant.models.agent import AgentInstanceProviderConfig, AgentInstanceRecord

from ...basic.stores import AgentInstanceStore

GATEKEEPER_ROLE = "gatekeeper"


def ensure_gatekeeper_instance(
    store: AgentInstanceStore,
    *,
    provider: AgentInstanceProviderConfig,
) -> AgentInstanceRecord:
    existing = store.find(role=GATEKEEPER_ROLE, scope_type="project", scope_id=None)
    if existing is not None:
        return existing

    record = AgentInstanceRecord(
        identity={
            "agent_id": GATEKEEPER_ROLE,
            "role": GATEKEEPER_ROLE,
        },
        scope={
            "scope_type": "project",
            "scope_id": None,
        },
        provider=provider,
    )
    store.upsert(record)
    return record
