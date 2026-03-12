from __future__ import annotations

from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult
from textual.widgets import TextArea

from vibrant.consensus import ConsensusWriter
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus
from vibrant.tui.widgets.consensus_view import ConsensusView, _extract_editable_markdown


class ConsensusViewHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield ConsensusView()


def _context(objectives: str) -> str:
    return (
        "## Objectives\n"
        "<!-- OBJECTIVES:START -->\n"
        f"{objectives}\n"
        "<!-- OBJECTIVES:END -->\n"
        "## Design Choices\n"
        "<!-- DECISIONS:START -->\n"
        "<!-- DECISIONS:END -->\n"
        "## Getting Started\n"
        "Read `docs/tui.md`."
    )


def _document(*, version: int = 1, objectives: str = "Ship the first plan.") -> ConsensusDocument:
    return ConsensusDocument(
        project="Vibrant",
        created_at=datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc),
        version=version,
        status=ConsensusStatus.PLANNING,
        context=_context(objectives),
    )


def test_extract_editable_markdown_strips_only_meta_block():
    markdown = ConsensusWriter().render(_document())

    editable = _extract_editable_markdown(markdown)

    assert "<!-- META:START -->" not in editable
    assert editable.startswith("## Objectives")
    assert "<!-- OBJECTIVES:START -->" in editable
    assert "Read `docs/tui.md`." in editable


@pytest.mark.asyncio
async def test_consensus_view_tracks_unsaved_and_external_updates():
    app = ConsensusViewHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(ConsensusView)
        initial = _document(version=1, objectives="Ship the first plan.")
        view.update_consensus(initial, raw_markdown=ConsensusWriter().render(initial))
        await pilot.pause()

        editor = app.query_one(TextArea)
        editor.load_text(
            "## Objectives\n"
            "<!-- OBJECTIVES:START -->\n"
            "Refine the plan.\n"
            "<!-- OBJECTIVES:END -->\n"
            "## Design Choices\n"
            "<!-- DECISIONS:START -->\n"
            "<!-- DECISIONS:END -->\n"
            "## Getting Started\n"
            "Read `docs/tui.md`.\n"
        )
        await pilot.pause()

        assert view.has_unsaved_changes is True
        assert view.has_external_update is False

        refreshed = _document(version=2, objectives="Gatekeeper changed the plan.")
        view.update_consensus(refreshed, raw_markdown=ConsensusWriter().render(refreshed))
        await pilot.pause()

        assert view.has_unsaved_changes is True
        assert view.has_external_update is True

        view.action_revert_edits()
        await pilot.pause()

        assert view.has_unsaved_changes is False
        assert view.has_external_update is False
        assert "Gatekeeper changed the plan." in view.current_editable_markdown
