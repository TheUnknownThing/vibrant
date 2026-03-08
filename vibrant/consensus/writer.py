"""Consensus document writing helpers."""

from __future__ import annotations

from pathlib import Path

from vibrant.models.consensus import ConsensusPool


class ConsensusWriter:
    """Writes consensus documents atomically in later phases."""

    def write(self, path: str | Path, document: ConsensusPool) -> None:
        Path(path).write_text(document.model_dump_json(indent=2), encoding="utf-8")

