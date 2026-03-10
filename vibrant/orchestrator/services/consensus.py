"""Consensus orchestration service."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from vibrant.consensus import ConsensusParser, ConsensusWriter
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus

from .state_store import StateStore


class ConsensusService:
    """Read and update durable consensus state."""

    def __init__(
        self,
        consensus_path: str | Path,
        *,
        state_store: StateStore,
        parser: ConsensusParser | None = None,
        writer: ConsensusWriter | None = None,
    ) -> None:
        self.consensus_path = Path(consensus_path)
        self.state_store = state_store
        self.parser = parser or ConsensusParser()
        self.writer = writer or ConsensusWriter(parser=self.parser)

    def current(self) -> ConsensusDocument | None:
        return self.state_store.consensus

    def load(self) -> ConsensusDocument:
        current = self.current()
        if current is not None:
            return current
        return self.parser.parse_file(self.consensus_path)

    def write(self, document: ConsensusDocument) -> ConsensusDocument:
        written = self.writer.write(self.consensus_path, document)
        self.state_store.engine.consensus = written
        return written

    def set_status(self, status: ConsensusStatus) -> ConsensusDocument | None:
        if not self.consensus_path.exists() and self.current() is None:
            return None

        document = self.load()
        if document.status is status:
            return document

        updated = document.model_copy(deep=True)
        updated.status = status
        written = self.write(updated)
        self.state_store.refresh()
        return written

    def update(
        self,
        *,
        status: ConsensusStatus | str | None = None,
        objectives: str | None = None,
        getting_started: str | None = None,
        questions: Sequence[str] | None = None,
    ) -> ConsensusDocument:
        document = self.load().model_copy(deep=True)

        if status is not None:
            document.status = status if isinstance(status, ConsensusStatus) else ConsensusStatus(str(status).strip().upper())
        if objectives is not None:
            document.objectives = objectives
        if getting_started is not None:
            document.getting_started = getting_started
        if questions is not None:
            document.questions = [question.strip() for question in questions if isinstance(question, str) and question.strip()]

        written = self.write(document)
        self.state_store.refresh()
        return written
