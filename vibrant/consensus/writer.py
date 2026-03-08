"""Consensus document writing helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from vibrant.models.consensus import ConsensusDocument, ConsensusPool


class ConsensusWriter:
    """Renders and writes structured consensus markdown."""

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
            "## Objectives",
            "<!-- OBJECTIVES:START -->",
            document.objectives,
            "<!-- OBJECTIVES:END -->",
            "## Design Choices",
            "<!-- DECISIONS:START -->",
        ]

        for index, decision in enumerate(document.decisions, start=1):
            decision_date = _format_timestamp(decision.date)
            lines.extend(
                [
                    f"### Decision {index}: {decision.title}",
                    f"- **Date**: {decision_date}",
                    f"- **Made By**: `{decision.made_by.value}`",
                    f"- **Context**: {decision.context}",
                    f"- **Resolution**: {decision.resolution}",
                    f"- **Impact**: {decision.impact}",
                    "",
                ]
            )

        lines.extend(
            [
                "<!-- DECISIONS:END -->",
                "## Getting Started",
                document.getting_started,
                "",
            ]
        )
        return "\n".join(lines)

    def write(self, path: str | Path, document: ConsensusDocument | ConsensusPool) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_suffix(destination.suffix + ".tmp")
        temp_path.write_text(self.render(document), encoding="utf-8")
        temp_path.replace(destination)


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
