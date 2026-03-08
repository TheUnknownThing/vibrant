"""Simple file-backed store for conversation history."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import ThreadInfo

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_DIR = "~/.vibrant/history"


class HistoryStore:
    """Simple file-backed store for conversation history."""

    def __init__(self, history_dir: str | None = None) -> None:
        if not history_dir:
            history_dir = DEFAULT_HISTORY_DIR
        self.history_dir = Path(history_dir).expanduser().resolve()
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def save_thread(self, thread: ThreadInfo) -> None:
        """Save a thread to disk."""
        try:
            file_path = self.history_dir / f"{thread.id}.json"
            json_data = thread.model_dump_json(indent=2)
            temp_path = file_path.with_suffix(".temp.json")
            temp_path.write_text(json_data, encoding="utf-8")
            temp_path.replace(file_path)
        except Exception:
            logger.exception("Failed to save thread %s to history", thread.id)

    def load_thread(self, thread_id: str) -> ThreadInfo | None:
        """Load a thread from disk by ID."""
        file_path = self.history_dir / f"{thread_id}.json"
        if not file_path.exists():
            return None
        try:
            data = file_path.read_text(encoding="utf-8")
            return ThreadInfo.model_validate_json(data)
        except Exception:
            logger.exception("Failed to load thread %s from history", thread_id)
            return None

    def list_threads(self) -> list[ThreadInfo]:
        """Load all saved threads, sorted newest first."""
        threads = []
        for file_path in self.history_dir.glob("*.json"):
            if file_path.suffix == ".temp.json":
                continue
            try:
                data = file_path.read_text(encoding="utf-8")
                thread = ThreadInfo.model_validate_json(data)
                threads.append(thread)
            except Exception:
                logger.error("Failed to parse history file %s", file_path)
        # Sort by updated_at descending (newest first)
        threads.sort(key=lambda t: t.updated_at, reverse=True)
        return threads

    def delete_thread(self, thread_id: str) -> None:
        """Delete a thread from history."""
        file_path = self.history_dir / f"{thread_id}.json"
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                logger.exception("Failed to delete thread %s from history", thread_id)
