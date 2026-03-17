"""Consensus document writing helpers."""

from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import fcntl

from vibrant.consensus.parser import ConsensusParser
from vibrant.models.consensus import ConsensusDocument, ConsensusPool


class ConsensusLockError(RuntimeError):
    """Raised when a consensus write cannot acquire the file lock."""


class ConsensusVersionError(ValueError):
    """Raised when a consensus write would violate version monotonicity."""


class ConsensusWriter:
    """Renders and writes structured consensus markdown."""

    def __init__(
        self,
        *,
        parser: ConsensusParser | None = None,
        history_dir_name: str = "consensus.history",
    ) -> None:
        self.parser = parser or ConsensusParser()
        self.history_dir_name = history_dir_name

    def render(self, document: ConsensusDocument | ConsensusPool) -> str:
        created_at = _format_timestamp(document.created_at)
        updated_at = _format_timestamp(document.updated_at)

        lines = [
            f"# Consensus Pool — Project {document.project}",
            "<!-- META:START -->",
            f"- **Project**: {document.project}",
            f"- **Created**: {created_at}",
            f"- **Last Updated**: {updated_at}",
            f"- **Version**: {document.version}",
            f"- **Status**: {document.status.value}",
            "<!-- META:END -->",
        ]
        context = document.context.removeprefix("\n").removesuffix("\n")
        if context:
            lines.append(context)

        return "\n".join(lines).rstrip() + "\n"

    def write(
        self,
        path: Path,
        document: ConsensusDocument | ConsensusPool,
        *,
        lock_timeout: float = 5.0,
    ) -> ConsensusDocument:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        with self._file_lock(destination, timeout=lock_timeout):
            previous_text = destination.read_text(encoding="utf-8") if destination.exists() else None
            previous_document = self.parser.parse(previous_text) if previous_text is not None else None
            prepared_document = self._prepare_document(document, previous_document)

            if previous_text is not None:
                self._snapshot_previous(destination, previous_text)

            _atomic_write_text(destination, self.render(prepared_document))

        return prepared_document

    def _prepare_document(
        self,
        document: ConsensusDocument | ConsensusPool,
        previous_document: ConsensusDocument | None,
    ) -> ConsensusDocument:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        created_at = document.created_at
        if previous_document is not None and previous_document.created_at is not None:
            created_at = previous_document.created_at
        elif created_at is None:
            created_at = now

        if previous_document is None:
            next_version = document.version
        elif document.version > previous_document.version:
            next_version = document.version
        else:
            next_version = previous_document.version + 1

        if previous_document is not None and next_version <= previous_document.version:
            raise ConsensusVersionError(
                f"Consensus version must increase monotonically: {previous_document.version} -> {next_version}"
            )

        return ConsensusDocument(
            project=document.project,
            created_at=created_at,
            updated_at=now,
            version=next_version,
            status=document.status,
            context=document.context,
        )

    def _snapshot_previous(self, destination: Path, previous_text: str) -> Path:
        history_dir = destination.parent / self.history_dir_name
        history_dir.mkdir(parents=True, exist_ok=True)
        snapshot_time = datetime.now(timezone.utc)
        snapshot_path = history_dir / f"consensus.{_format_snapshot_timestamp(snapshot_time)}.md"
        counter = 1
        while snapshot_path.exists():
            snapshot_path = history_dir / f"consensus.{_format_snapshot_timestamp(snapshot_time)}.{counter}.md"
            counter += 1
        _atomic_write_text(snapshot_path, previous_text)
        return snapshot_path

    def _file_lock(self, destination: Path, *, timeout: float):
        lock_path = destination.with_suffix(destination.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout

        class _LockContext:
            def __init__(self, path: Path, deadline_seconds: float) -> None:
                self.path = path
                self.deadline_seconds = deadline_seconds
                self.handle = None

            def __enter__(self):
                self.handle = self.path.open("a+", encoding="utf-8")
                while True:
                    try:
                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return self.handle
                    except BlockingIOError as exc:
                        if time.monotonic() >= self.deadline_seconds:
                            self.handle.close()
                            raise ConsensusLockError(
                                f"Timed out acquiring consensus lock: {self.path}"
                            ) from exc
                        time.sleep(0.05)

            def __exit__(self, exc_type, exc, tb) -> None:
                assert self.handle is not None
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()

        return _LockContext(lock_path, deadline)



def _atomic_write_text(path: Path, content: str) -> None:
    descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise



def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")



def _format_snapshot_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    rendered = value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return rendered.replace(":", "-")
