from __future__ import annotations

from pathlib import Path

import pytest
from rich.color import Color
from rich.style import Style
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from vibrant.tui.widgets.input_bar import InputBar, _ChatInput


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
async def test_vibe_command_autocomplete_with_tab() -> None:
    app = InputBarHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        field = app.query_one("#message-input", Input)
        suggestions = app.query_one("#input-suggestions", OptionList)
        bar.focus_input()

        await pilot.press("/", "v", "i")
        await pilot.pause()

        assert suggestions.display is True
        assert [suggestions.get_option_at_index(index).prompt for index in range(suggestions.option_count)] == ["/vibe"]

        await pilot.press("tab")
        await pilot.pause()

        assert field.value == "/vibe "
        assert suggestions.display is False


@pytest.mark.asyncio
async def test_enter_applies_active_slash_command_suggestion() -> None:
    app = InputBarHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        field = app.query_one("#message-input", Input)
        suggestions = app.query_one("#input-suggestions", OptionList)
        bar.focus_input()

        await pilot.press("/", "l", "o", "enter")
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


@pytest.mark.asyncio
async def test_enter_applies_active_file_suggestion(tmp_path: Path) -> None:
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

        await pilot.press(*"check @do", "enter")
        await pilot.pause()

        assert field.value == "check @docs/"
        assert suggestions.display is True
        assert [suggestions.get_option_at_index(index).prompt for index in range(suggestions.option_count)] == ["@docs/tui-todo.md", "@docs/tui.md"]


@pytest.mark.asyncio
async def test_slash_commands_render_in_bold_primary_color() -> None:
    app = InputBarHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        field = app.query_one("#message-input", _ChatInput)
        bar.focus_input()

        await pilot.press(*"/logs details")
        await pilot.pause()

        command_span = next(span for span in field._value.spans if span.start == 0 and span.end == 5)
        assert isinstance(command_span.style, Style)
        assert command_span.style.bold is True
        assert command_span.style.color is not None
        assert command_span.style.color.triplet == Color.parse(app.theme_variables["primary"]).triplet


@pytest.mark.asyncio
async def test_file_tokens_render_underlined_with_primary_background(tmp_path: Path) -> None:
    app = InputBarHarness(base_path=tmp_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        field = app.query_one("#message-input", _ChatInput)
        bar.focus_input()

        await pilot.press(*"check @docs/tui.md")
        await pilot.pause()

        file_start = field.value.index("@docs/tui.md")
        file_end = file_start + len("@docs/tui.md")
        file_span = next(span for span in field._value.spans if span.start == file_start and span.end == file_end)
        assert isinstance(file_span.style, Style)
        assert file_span.style.underline is True
        assert file_span.style.color is not None
        assert file_span.style.bgcolor is not None
        assert file_span.style.color.triplet == Color.parse(app.theme_variables["primary"]).triplet
        assert file_span.style.bgcolor.triplet == Color.parse(app.theme_variables["primary-background"]).triplet


@pytest.mark.asyncio
async def test_file_autocomplete_scrolls_without_wrapping(tmp_path: Path) -> None:
    for index in range(12):
        (tmp_path / f"file-{index:02}.py").write_text("x", encoding="utf-8")

    app = InputBarHarness(base_path=tmp_path)

    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        suggestions = app.query_one("#input-suggestions", OptionList)
        bar.focus_input()

        await pilot.press("@")
        await pilot.pause()

        assert suggestions.option_count == 12
        assert suggestions.highlighted == 0

        for _ in range(8):
            await pilot.press("down")
            await pilot.pause()

        assert suggestions.highlighted == 8
        assert suggestions.scroll_offset.y > 0

        for _ in range(10):
            await pilot.press("down")
            await pilot.pause()

        assert suggestions.highlighted == 11
        assert suggestions.get_option_at_index(suggestions.highlighted).prompt == "@file-11.py"


@pytest.mark.asyncio
async def test_up_and_down_navigate_submitted_history_and_restore_draft() -> None:
    app = InputBarHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        field = app.query_one("#message-input", Input)
        bar.set_history_provider(lambda: ["first prompt", "second prompt"])
        bar.focus_input()

        await pilot.press(*"draft idea")
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()
        assert field.value == "second prompt"

        await pilot.press("up")
        await pilot.pause()
        assert field.value == "first prompt"

        await pilot.press("up")
        await pilot.pause()
        assert field.value == "first prompt"

        await pilot.press("down")
        await pilot.pause()
        assert field.value == "second prompt"

        await pilot.press("down")
        await pilot.pause()
        assert field.value == "draft idea"


@pytest.mark.asyncio
async def test_slash_command_navigation_does_not_wrap() -> None:
    app = InputBarHarness()

    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one(InputBar)
        suggestions = app.query_one("#input-suggestions", OptionList)
        bar.focus_input()

        await pilot.press("/")
        await pilot.pause()

        assert suggestions.option_count == len(InputBar.COMMAND_SUGGESTIONS)

        for _ in range(suggestions.option_count + 3):
            await pilot.press("down")
            await pilot.pause()

        assert suggestions.highlighted == suggestions.option_count - 1
        assert suggestions.get_option_at_index(suggestions.highlighted).prompt == InputBar.COMMAND_SUGGESTIONS[-1]
