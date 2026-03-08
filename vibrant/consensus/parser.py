"""Consensus markdown parsing helpers."""

from __future__ import annotations

from vibrant.models.consensus import ConsensusPool


class ConsensusParser:
    """Very small parser seam for future structured parsing work."""

    def parse(self, markdown_text: str) -> ConsensusPool:
        return ConsensusPool(getting_started=markdown_text.strip())

