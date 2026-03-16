"""Consensus document store."""

from __future__ import annotations

from pathlib import Path

from vibrant.consensus.parser import ConsensusParser
from vibrant.consensus.writer import ConsensusWriter
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus, DEFAULT_CONSENSUS_CONTEXT


class ConsensusStore:
    """Persist the human-readable consensus document."""

    def __init__(self, path: str | Path, *, project_name: str) -> None:
        self.path = Path(path)
        self.project_name = project_name
        self.root = self.path.parent
        self.parser = ConsensusParser()
        self.writer = ConsensusWriter(parser=self.parser)

    def load(self) -> ConsensusDocument | None:
        if not self.path.exists():
            return None
        return self.parser.parse_file(self.path)

    def write(self, document: ConsensusDocument) -> ConsensusDocument:
        prepared = document.model_copy(deep=True)
        if not prepared.project:
            prepared.project = self.project_name
        return self.writer.write(self.path, prepared)

    def update_context(self, context: str) -> ConsensusDocument:
        document = self.load() or self._default_document()
        document.context = context.strip() or DEFAULT_CONSENSUS_CONTEXT
        return self.write(document)

    def set_status_projection(self, status: ConsensusStatus) -> ConsensusDocument:
        document = self.load() or self._default_document()
        document.status = status
        return self.write(document)

    def _default_document(self) -> ConsensusDocument:
        return ConsensusDocument(project=self.project_name, context=DEFAULT_CONSENSUS_CONTEXT)
