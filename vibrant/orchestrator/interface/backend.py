"""Bundle interface command and query adapters."""

from __future__ import annotations

from dataclasses import dataclass

from .basic import BasicQueryAdapter
from .policy import PolicyCommandAdapter


@dataclass(slots=True)
class OrchestratorBackend:
    commands: PolicyCommandAdapter
    queries: BasicQueryAdapter
