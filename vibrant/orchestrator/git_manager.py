"""Git worktree management hooks for future phases."""

from __future__ import annotations

from pathlib import Path


class GitManager:
    """Tracks the root used for transient worktrees."""

    def __init__(self, worktree_root: str = ".vibrant/worktrees") -> None:
        self.worktree_root = Path(worktree_root)

