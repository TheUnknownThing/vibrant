"""Consensus document store."""

from __future__ import annotations

from pathlib import Path
import re

from vibrant.consensus.parser import ConsensusParser
from vibrant.consensus.writer import ConsensusWriter
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus, DEFAULT_CONSENSUS_CONTEXT


_DECISIONS_BLOCK = re.compile(r"(<!-- DECISIONS:START -->\n)(.*?)(\n<!-- DECISIONS:END -->)", re.DOTALL)


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

    def append_decision(
        self,
        *,
        title: str,
        resolution: str,
        context: str,
        made_by: str = "gatekeeper",
        impact: str = "",
        when: str | None = None,
    ) -> ConsensusDocument:
        document = self.load() or self._default_document()
        decision_number = self._next_decision_number(document.context)
        decision_block = "\n".join(
            [
                f"### Decision {decision_number}: {title.strip()}",
                f"- **Date**: {when or self._current_date()}",
                f"- **Made By**: `{made_by.strip() or 'gatekeeper'}`",
                f"- **Context**: {context.strip()}",
                f"- **Resolution**: {resolution.strip()}",
                f"- **Impact**: {impact.strip() or 'None recorded.'}",
            ]
        )
        document.context = self._append_to_decisions(document.context, decision_block)
        return self.write(document)

    def set_status_projection(self, status: ConsensusStatus) -> ConsensusDocument:
        document = self.load() or self._default_document()
        document.status = status
        return self.write(document)

    def _default_document(self) -> ConsensusDocument:
        return ConsensusDocument(project=self.project_name, context=DEFAULT_CONSENSUS_CONTEXT)

    def _append_to_decisions(self, context: str, block: str) -> str:
        source = context.strip() or DEFAULT_CONSENSUS_CONTEXT
        match = _DECISIONS_BLOCK.search(source)
        if match is None:
            return f"{source.rstrip()}\n\n## Design Choices\n<!-- DECISIONS:START -->\n{block}\n<!-- DECISIONS:END -->"
        middle = match.group(2).strip()
        updated_middle = f"{middle}\n\n{block}".strip()
        return f"{source[:match.start()]}{match.group(1)}{updated_middle}{match.group(3)}{source[match.end():]}"

    def _next_decision_number(self, context: str) -> int:
        matches = re.findall(r"### Decision (\d+):", context)
        if not matches:
            return 1
        return max(int(item) for item in matches) + 1

    def _current_date(self) -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).date().isoformat()
