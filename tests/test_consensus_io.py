"""Unit tests for the Phase 3 consensus parser and writer."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

import pytest

from vibrant.consensus.parser import ConsensusParser
from vibrant.consensus.writer import ConsensusLockError, ConsensusWriter
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus


SAMPLE_CONTEXT = """## Objectives
<!-- OBJECTIVES:START -->
Ship the orchestrator core.

Keep the consensus concise.
<!-- OBJECTIVES:END -->
## Design Choices
<!-- DECISIONS:START -->
### Decision 1: Use Markdown sections
- **Date**: 2026-03-07T22:30:00Z
- **Made By**: `gatekeeper`
- **Context**: Agents need shared context.
- **Resolution**: Keep consensus structured.
- **Impact**: Parser and writer depend on delimiters.

### Decision 2: Pause support in state machine
- **Date**: 2026-03-08T00:00:00Z
- **Made By**: `user`
- **Context**: Operator needs control.
- **Resolution**: Add PAUSED state.
- **Impact**: TUI and engine both surface pause.
<!-- DECISIONS:END -->
## Getting Started
Read `docs/spec.md` first, then `.vibrant/roadmap.md`."""

SAMPLE_CONSENSUS = f"""# Consensus Pool — Project Vibrant
<!-- META:START -->
- **Project**: Vibrant
- **Created**: 2026-03-07T22:00:00Z
- **Last Updated**: 2026-03-08T01:15:00Z
- **Version**: 7
- **Status**: EXECUTING
<!-- META:END -->
{SAMPLE_CONTEXT}
"""


class TestConsensusParserWriter:
    def test_parse_sample_consensus_extracts_meta_and_raw_context(self):
        document = ConsensusParser().parse(SAMPLE_CONSENSUS)

        assert document.project == "Vibrant"
        assert document.version == 7
        assert document.status is ConsensusStatus.EXECUTING
        assert document.context == SAMPLE_CONTEXT

    def test_write_updates_increment_version_and_create_snapshot(self, tmp_path):
        consensus_path = tmp_path / ".vibrant" / "consensus.md"
        consensus_path.parent.mkdir(parents=True)
        consensus_path.write_text(SAMPLE_CONSENSUS, encoding="utf-8")

        parser = ConsensusParser()
        writer = ConsensusWriter(parser=parser)
        document = parser.parse_file(consensus_path)
        document.context += "\n\n## Questions\n- [blocking] Track every write."

        written = writer.write(consensus_path, document)
        reparsed = parser.parse_file(consensus_path)
        snapshots = sorted((consensus_path.parent / "consensus.history").glob("consensus.*.md"))

        assert written.version == 8
        assert reparsed.version == 8
        assert reparsed.context.endswith("Track every write.")
        assert len(snapshots) == 1
        assert "- **Version**: 7" in snapshots[0].read_text(encoding="utf-8")

    def test_concurrent_write_attempt_is_blocked_by_file_lock(self, tmp_path):
        consensus_path = tmp_path / ".vibrant" / "consensus.md"
        consensus_path.parent.mkdir(parents=True)
        consensus_path.write_text(SAMPLE_CONSENSUS, encoding="utf-8")

        parser = ConsensusParser()
        writer = ConsensusWriter(parser=parser)
        document = parser.parse_file(consensus_path)
        document.status = ConsensusStatus.PAUSED

        lock_script = """
import fcntl
import sys
from pathlib import Path

consensus_path = Path(sys.argv[1])
lock_path = consensus_path.with_suffix(consensus_path.suffix + '.lock')
lock_path.parent.mkdir(parents=True, exist_ok=True)

with lock_path.open('a+', encoding='utf-8') as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    print('locked', flush=True)
    sys.stdin.read()
"""
        process = subprocess.Popen(
            [sys.executable, "-c", lock_script, str(consensus_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "locked"
            with pytest.raises(ConsensusLockError, match="Timed out acquiring consensus lock"):
                writer.write(consensus_path, document, lock_timeout=0.1)

            reparsed = parser.parse_file(consensus_path)
            assert reparsed.version == 7
            assert reparsed.status is ConsensusStatus.EXECUTING
        finally:
            assert process.stdin is not None
            process.stdin.close()
            process.wait(timeout=5)

        assert process.returncode == 0

    def test_round_trip_parse_modify_write_parse_preserves_context(self, tmp_path):
        consensus_path = tmp_path / ".vibrant" / "consensus.md"
        consensus_path.parent.mkdir(parents=True)

        original = ConsensusDocument(
            project="Vibrant",
            created_at=datetime(2026, 3, 7, 22, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 7, 22, 0, tzinfo=timezone.utc),
            version=0,
            status=ConsensusStatus.PLANNING,
            context="""## Objectives
<!-- OBJECTIVES:START -->
Build the control plane.
<!-- OBJECTIVES:END -->
## Design Choices
<!-- DECISIONS:START -->
### Decision 1: Use atomic writes
- **Date**: 2026-03-07T22:15:00Z
- **Made By**: `gatekeeper`
- **Context**: Consensus is durable state.
- **Resolution**: Always write via temp file + rename.
- **Impact**: Prevents partial writes after crashes.
<!-- DECISIONS:END -->
## Getting Started
Read the roadmap, then inspect the current task.""",
        )

        writer = ConsensusWriter()
        writer.write(consensus_path, original)

        parsed = ConsensusParser().parse_file(consensus_path)
        parsed.context += "\n\n## Questions\n- [blocking] Should snapshots stay enabled?"

        updated = writer.write(consensus_path, parsed)
        reparsed = ConsensusParser().parse_file(consensus_path)

        assert updated.version == 1
        assert reparsed.version == 1
        assert reparsed.project == "Vibrant"
        assert reparsed.status is ConsensusStatus.PLANNING
        assert "### Decision 1: Use atomic writes" in reparsed.context
        assert reparsed.context.endswith("Should snapshots stay enabled?")
