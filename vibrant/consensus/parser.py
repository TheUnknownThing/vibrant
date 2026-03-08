"""Consensus markdown parsing helpers."""

from __future__ import annotations

import re
from pathlib import Path

from vibrant.models.consensus import ConsensusDecision, ConsensusDocument, ConsensusPool


class ConsensusParser:
    """Parse the machine-readable sections of ``consensus.md``."""

    META_PATTERN = re.compile(r"<!-- META:START -->\n(?P<body>.*?)\n<!-- META:END -->", re.DOTALL)
    OBJECTIVES_PATTERN = re.compile(
        r"<!-- OBJECTIVES:START -->\n(?P<body>.*?)\n<!-- OBJECTIVES:END -->",
        re.DOTALL,
    )
    DECISIONS_PATTERN = re.compile(
        r"<!-- DECISIONS:START -->\n(?P<body>.*?)\n<!-- DECISIONS:END -->",
        re.DOTALL,
    )
    GETTING_STARTED_PATTERN = re.compile(r"## Getting Started\n(?P<body>.*)\Z", re.DOTALL)
    BULLET_PATTERN = re.compile(r"- \*\*(?P<key>[^*]+)\*\*: (?P<value>.*)")
    DECISION_TITLE_PATTERN = re.compile(r"### Decision \d+: (?P<title>.+)")

    def parse(self, markdown_text: str) -> ConsensusPool:
        meta = self._parse_meta(markdown_text)
        objectives = self._extract_section(self.OBJECTIVES_PATTERN, markdown_text)
        decisions = self._parse_decisions(markdown_text)
        getting_started = self._extract_section(self.GETTING_STARTED_PATTERN, markdown_text)

        return ConsensusPool(
            project=meta.get("Project", "Vibrant"),
            created_at=meta.get("Created"),
            updated_at=meta.get("Last Updated"),
            version=int(meta.get("Version", 0)),
            status=meta.get("Status", "PLANNING"),
            objectives=objectives.strip(),
            decisions=decisions,
            getting_started=getting_started.strip(),
        )

    def parse_file(self, path: str | Path) -> ConsensusDocument:
        return self.parse(Path(path).read_text(encoding="utf-8"))

    def _parse_meta(self, markdown_text: str) -> dict[str, str]:
        match = self.META_PATTERN.search(markdown_text)
        if match is None:
            raise ValueError("Consensus META section not found")

        parsed: dict[str, str] = {}
        for line in match.group("body").splitlines():
            line = line.strip()
            if not line:
                continue
            bullet_match = self.BULLET_PATTERN.fullmatch(line)
            if bullet_match is None:
                raise ValueError(f"Invalid consensus META line: {line}")
            parsed[bullet_match.group("key")] = bullet_match.group("value")
        return parsed

    def _parse_decisions(self, markdown_text: str) -> list[ConsensusDecision]:
        block = self._extract_section(self.DECISIONS_PATTERN, markdown_text)
        if not block.strip():
            return []

        decisions: list[ConsensusDecision] = []
        current: dict[str, str] | None = None
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            title_match = self.DECISION_TITLE_PATTERN.fullmatch(line)
            if title_match is not None:
                if current is not None:
                    decisions.append(self._build_decision(current))
                current = {"title": title_match.group("title")}
                continue

            bullet_match = self.BULLET_PATTERN.fullmatch(line)
            if bullet_match is not None and current is not None:
                current[bullet_match.group("key")] = bullet_match.group("value").strip("`")

        if current is not None:
            decisions.append(self._build_decision(current))
        return decisions

    def _build_decision(self, data: dict[str, str]) -> ConsensusDecision:
        return ConsensusDecision(
            title=data.get("title", "Untitled"),
            date=data.get("Date"),
            made_by=data.get("Made By", "gatekeeper"),
            context=data.get("Context", ""),
            resolution=data.get("Resolution", ""),
            impact=data.get("Impact", ""),
        )

    @staticmethod
    def _extract_section(pattern: re.Pattern[str], markdown_text: str) -> str:
        match = pattern.search(markdown_text)
        if match is None:
            return ""
        return match.group("body")
