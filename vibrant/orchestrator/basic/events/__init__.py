"""Runtime event log service."""

from __future__ import annotations

import inspect
from collections import deque
from dataclasses import dataclass, field

from vibrant.providers.base import CanonicalEvent, CanonicalEventHandler


@dataclass(slots=True)
class EventLogService:
    """Track recent canonical runtime events for interface consumers."""

    on_canonical_event: CanonicalEventHandler | None = None
    max_events: int = 200
    _recent_events: deque[CanonicalEvent] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._recent_events = deque(maxlen=self.max_events)

    async def record_runtime_event(self, event: CanonicalEvent) -> None:
        self._recent_events.append(dict(event))
        if self.on_canonical_event is None:
            return
        result = self.on_canonical_event(event)
        if inspect.isawaitable(result):
            await result

    def list_recent_events(self, limit: int = 20) -> list[CanonicalEvent]:
        return list(self._recent_events)[-limit:]
