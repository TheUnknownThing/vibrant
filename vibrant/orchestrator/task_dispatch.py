"""Task queue and dispatch helpers."""

from __future__ import annotations

from collections import deque


class TaskDispatcher:
    """Small queue abstraction used as the future dispatch seam."""

    def __init__(self) -> None:
        self._queue: deque[str] = deque()

    def enqueue(self, task_id: str) -> None:
        self._queue.append(task_id)

    def dequeue(self) -> str | None:
        if not self._queue:
            return None
        return self._queue.popleft()

