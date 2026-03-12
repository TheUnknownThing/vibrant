from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from vibrant.tui.widgets.input_bar import InputBar


class InputBarHarness(App[None]):
    def __init__(self, *, base_path: Path | None = None) -> None:
        super().__init__()
        self._base_path = base_path

    def compose(self) -> ComposeResult:
        yield InputBar(base_path=self._base_path)


@pytest.mark.asyncio
async def test_ctrl_backspace_deletes_the_previous_word() -> None:
    app = InputBarHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        field = app.query_one("#message-input", Input)
        bar.focus_input()

        await pilot.press(*"hello world")
        await pilot.pause()

        assert field.value == "hello world"

        await pilot.press("ctrl+backspace")
        await pilot.pause()

        assert field.value == "hello "


@pytest.mark.asyncio
async def test_slash_commands_autocomplete_with_tab() -> None:
    app = InputBarHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        field = app.query_one("#message-input", Input)
        suggestions = app.query_one("#input-suggestions", OptionList)
        bar.focus_input()

        await pilot.press("/", "l", "o")
        await pilot.pause()

        assert suggestions.display is True
        assert [suggestions.get_option_at_index(index).prompt for index in range(suggestions.option_count)] == ["/logs"]

        await pilot.press("tab")
        await pilot.pause()

        assert field.value == "/logs "
        assert suggestions.display is False


@pytest.mark.asyncio
async def test_at_paths_autocomplete_relative_to_base_path(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "tui.md").write_text("hello", encoding="utf-8")
    (docs_dir / "tui-todo.md").write_text("hello", encoding="utf-8")

    app = InputBarHarness(base_path=tmp_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        field = app.query_one("#message-input", Input)
        suggestions = app.query_one("#input-suggestions", OptionList)
        bar.focus_input()

        await pilot.press(*"check @do")
        await pilot.pause()

        assert suggestions.display is True
        assert [suggestions.get_option_at_index(index).prompt for index in range(suggestions.option_count)] == ["@docs/"]

        await pilot.press("tab")
        await pilot.pause()

        assert field.value == "check @docs/"
        assert suggestions.display is True
        assert [suggestions.get_option_at_index(index).prompt for index in range(suggestions.option_count)] == ["@docs/tui-todo.md", "@docs/tui.md"]
