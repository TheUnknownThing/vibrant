"""Consensus markdown parsing helpers."""

from __future__ import annotations

import re
from pathlib import Path

from vibrant.models.consensus import ConsensusDocument, ConsensusPool


class ConsensusParser:
    """Parse the machine-readable metadata in ``consensus.md``."""

    META_PATTERN = re.compile(r"<!-- META:START -->\n(?P<body>.*?)\n<!-- META:END -->", re.DOTALL)
    BULLET_PATTERN = re.compile(r"- \*\*(?P<key>[^*]+)\*\*: (?P<value>.*)")

    def parse(self, markdown_text: str) -> ConsensusPool:
        meta_match = self.META_PATTERN.search(markdown_text)
        if meta_match is None:
            raise ValueError("Consensus META section not found")

        meta = self._parse_meta(meta_match)
        context = markdown_text[meta_match.end() :].removeprefix("\n").removesuffix("\n")

        return ConsensusPool(
            project=meta.get("Project", "Vibrant"),
            created_at=meta.get("Created"),
            updated_at=meta.get("Last Updated"),
            version=int(meta.get("Version", 0)),
            status=meta.get("Status", "PLANNING"),
            context=context,
        )

    def parse_file(self, path: str | Path) -> ConsensusDocument:
        return self.parse(Path(path).read_text(encoding="utf-8"))

    def _parse_meta(self, match: re.Match[str]) -> dict[str, str]:
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
