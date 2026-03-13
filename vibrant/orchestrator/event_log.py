"""Small in-memory domain event log used by the redesign bootstrap."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DomainEventLog:
    """Keep a bounded recent event list for read models and debugging."""

    max_items: int = 256
    _events: deque[dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        self._events = deque(maxlen=self.max_items)

    def record(self, event: dict[str, Any]) -> None:
        self._events.append(dict(event))

    def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return list(self._events)[-limit:]
