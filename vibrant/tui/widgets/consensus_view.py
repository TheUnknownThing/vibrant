"""Consensus editor/viewer widget for the redesigned TUI."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Center
from textual.message import Message
from textual.widgets import Button, Markdown, Static, TextArea

from ...consensus import ConsensusParser, ConsensusWriter
from ...models.consensus import ConsensusDocument, ConsensusStatus
from ...models.task import TaskInfo

_SECTION_PATTERN = re.compile(
    r"^## (?P<name>Objectives|Design Choices|Getting Started|Questions)\s*$",
    re.MULTILINE,
)
_DEFAULT_GETTING_STARTED = "Start by reviewing `docs/spec.md`, `docs/plan.md`, and `.vibrant/roadmap.md`."


class ConsensusView(Static):
    """Shared consensus metadata + markdown preview/editor component."""

    class SaveRequested(Message):
        """Raised when the user wants to persist consensus edits."""

        def __init__(self, document: ConsensusDocument) -> None:
            super().__init__()
            self.document = document

    DEFAULT_CSS = """
    ConsensusView {
        height: 1fr;
        border: round $primary-background;
        background: $surface;
        padding: 0;
        layout: vertical;
    }

    #consensus-empty-state {
        width: 1fr;
        height: 1fr;
        align: center middle;
    }

    #consensus-empty-copy {
        width: 1fr;
        height: auto;
        align: center middle;
    }

    #consensus-empty-message,
    #consensus-empty-action {
        text-style: bold;
        content-align: center middle;
    }

    #consensus-empty-action {
        color: $accent;
    }

    #consensus-empty-action:hover {
        color: $surface;
    }

    #consensus-content {
        width: 1fr;
        height: 1fr;
    }

    #consensus-header {
        height: 3;
        padding: 1;
        background: $primary-background;
    }

    #consensus-title {
        width: 1fr;
        content-align: left middle;
        text-style: bold;
    }

    #consensus-body {
        height: 1fr;
    }

    #consensus-preview,
    #consensus-editor {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }

    #consensus-preview {
        overflow-y: auto;
    }

    #consensus-editor {
        margin-top: 1;
        border: none;
    }

    #consensus-meta,
    #consensus-actions {
        height: auto;
        padding: 1;
        border-top: solid $primary-background;
    }

    #consensus-meta {
        color: $text-muted;
    }

    #consensus-actions {
        align: right middle;
    }

    #consensus-actions Button {
        margin-left: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._writer = ConsensusWriter()
        self._parser = ConsensusParser()
        self._document: ConsensusDocument | None = None
        self._source_path: Path | None = None
        self._tasks: tuple[TaskInfo, ...] = ()
        self._summary_message = "Consensus.md does not exist."
        self._editor_text_cache = ""
        self._last_saved_editable_markdown = ""
        self._last_saved_raw_markdown: str | None = None
        self._has_external_update = False
        self._is_editing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="consensus-empty-state"):
            with Vertical(id="consensus-empty-copy"):
                yield Static("", id="consensus-empty-message")
                with Center():
                    yield Button("➕ Create File", id="consensus-empty-action", compact=True, flat=True)

        with Vertical(id="consensus-content"):
            with Horizontal(id="consensus-header"):
                yield Static("consensus.md", id="consensus-title")
                yield Button("Edit", id="consensus-edit-toggle", compact=True)
            with Vertical(id="consensus-body"):
                yield Markdown("", id="consensus-preview")
                yield TextArea("", id="consensus-editor", language="markdown", soft_wrap=True, show_line_numbers=True)
            yield Static("", id="consensus-meta", markup=True)
            with Horizontal(id="consensus-actions"):
                yield Button("Cancel", id="consensus-cancel", compact=True)
                yield Button("Save", id="consensus-save", variant="primary", compact=True)

    def on_mount(self) -> None:
        self._refresh_view()

    @property
    def is_editing(self) -> bool:
        """Return whether edit mode is active."""

        return self._is_editing

    @property
    def has_unsaved_changes(self) -> bool:
        """Return whether the editor differs from the saved baseline."""

        return self.current_editable_markdown != self._last_saved_editable_markdown

    @property
    def has_external_update(self) -> bool:
        """Return whether a newer saved document arrived while editing."""

        return self._has_external_update

    @property
    def current_editable_markdown(self) -> str:
        """Return the current editor contents."""

        editor = self._editor_widget()
        if editor is not None:
            self._editor_text_cache = editor.text
        return self._editor_text_cache

    def clear_summary(self, message: str = "File does not exist.") -> None:
        """Show the missing-file state with a short summary."""

        self._document = None
        self._tasks = ()
        self._source_path = None
        self._summary_message = message or "File does not exist."
        self._editor_text_cache = ""
        self._last_saved_editable_markdown = ""
        self._last_saved_raw_markdown = None
        self._has_external_update = False
        self._is_editing = False
        self._refresh_view()

    def update_consensus(
        self,
        document: ConsensusDocument,
        *,
        tasks: Sequence[TaskInfo] = (),
        source_path: str | Path | None = None,
        raw_markdown: str | None = None,
    ) -> None:
        """Refresh the widget from the latest consensus document."""

        editable_markdown = _extract_editable_markdown(raw_markdown or self._writer.render(document))
        current_text = self.current_editable_markdown
        had_unsaved_changes = self.has_unsaved_changes

        self._document = document.model_copy(deep=True)
        self._tasks = tuple(tasks)
        self._source_path = Path(source_path) if source_path is not None else None
        self._summary_message = ""
        self._last_saved_raw_markdown = raw_markdown or self._writer.render(document)
        self._last_saved_editable_markdown = editable_markdown

        if had_unsaved_changes:
            self._has_external_update = current_text != editable_markdown
            self._refresh_preview(current_text)
        else:
            self._set_editor_text(editable_markdown)
            self._refresh_preview(editable_markdown)
            self._has_external_update = False

        self._refresh_view()

    def action_toggle_edit_mode(self) -> None:
        """Switch between preview and edit modes."""

        if self._document is None:
            return
        self._is_editing = not self._is_editing
        self._refresh_preview(self.current_editable_markdown)
        self._refresh_view()

    def action_revert_edits(self) -> None:
        """Discard local edits and return to the latest saved document."""

        self._set_editor_text(self._last_saved_editable_markdown)
        self._refresh_preview(self._last_saved_editable_markdown)
        self._has_external_update = False
        self._is_editing = False
        self._refresh_view()

    def action_save_edits(self) -> None:
        """Emit a save request for the current editor contents."""

        if self._document is None:
            return
        document = self._parse_editor_document(self.current_editable_markdown)
        self._refresh_preview(self.current_editable_markdown)
        self._is_editing = False
        self.post_message(self.SaveRequested(document))
        self._refresh_view()

    def action_create_file(self) -> None:
        """Create a new consensus document using the default scaffold."""

        document = self._build_default_document()
        rendered = self._writer.render(document)
        self.update_consensus(document, source_path=self._source_path, raw_markdown=rendered)
        self.post_message(self.SaveRequested(document))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "consensus-edit-toggle":
            self.action_toggle_edit_mode()
        elif button_id == "consensus-cancel":
            self.action_revert_edits()
        elif button_id == "consensus-save":
            self.action_save_edits()
        elif button_id == "consensus-empty-action":
            self.action_create_file()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "consensus-editor":
            return
        self._editor_text_cache = event.text_area.text
        if self._is_editing:
            self._refresh_preview(self._editor_text_cache)
        self._refresh_view()

    def _refresh_view(self) -> None:
        empty_state = self.query_one("#consensus-empty-state", Vertical)
        content = self.query_one("#consensus-content", Vertical)
        preview = self.query_one("#consensus-preview", Markdown)
        editor = self.query_one("#consensus-editor", TextArea)
        meta = self.query_one("#consensus-meta", Static)
        actions = self.query_one("#consensus-actions", Horizontal)
        title = self.query_one("#consensus-title", Static)
        toggle = self.query_one("#consensus-edit-toggle", Button)
        create_message = self.query_one("#consensus-empty-message", Static)
        save_button = self.query_one("#consensus-save", Button)

        has_document = self._document is not None
        empty_state.display = not has_document
        content.display = has_document

        create_message.update(self._summary_message or "File does not exist.")
        title.update(self._source_label())

        toggle.label = "Preview" if self._is_editing else "Edit"
        toggle.display = has_document
        preview.display = has_document and not self._is_editing
        editor.display = has_document and self._is_editing
        meta.display = has_document and not self._is_editing
        actions.display = has_document and self._is_editing
        save_button.disabled = not self.has_unsaved_changes

        if has_document:
            meta.update(self._metadata_markup())
            self._refresh_preview(self.current_editable_markdown if self._is_editing else self._last_saved_editable_markdown)

    def _metadata_markup(self) -> str:
        if self._document is None:
            return ""

        status_color = {
            ConsensusStatus.INIT: "white",
            ConsensusStatus.PLANNING: "cyan",
            ConsensusStatus.EXECUTING: "purple",
            ConsensusStatus.PAUSED: "yellow",
            ConsensusStatus.COMPLETED: "blue",
            ConsensusStatus.FAILED: "red",
        }.get(self._document.status, "white")
        metadata = f"[b]Project[/b]: {self._document.project} v{self._document.version} (Status [{status_color}]{self._document.status.value}[/])"

        if self._document.updated_at is not None:
            metadata += f"\n[b]Updated[/b]: {self._document.updated_at.astimezone(timezone.utc).isoformat()}"
        return metadata

    def _source_label(self) -> str:
        if self._source_path is not None:
            return self._source_path.name
        return "consensus.md"

    def _editor_widget(self) -> TextArea | None:
        with suppress(Exception):
            return self.query_one("#consensus-editor", TextArea)
        return None

    def _set_editor_text(self, text: str) -> None:
        normalized = _normalize_markdown(text)
        self._editor_text_cache = normalized
        editor = self._editor_widget()
        if editor is not None and editor.text != normalized:
            editor.load_text(normalized)

    def _refresh_preview(self, markdown_text: str) -> None:
        preview = self.query_one("#consensus-preview", Markdown)
        preview.update(markdown_text or "_Consensus is empty._")

    def _parse_editor_document(self, editable_markdown: str) -> ConsensusDocument:
        if self._document is None:
            raise ValueError("Consensus document is unavailable")
        full_markdown = _compose_consensus_markdown(self._writer, self._document, editable_markdown)
        return self._parser.parse(full_markdown)

    def _build_default_document(self) -> ConsensusDocument:
        timestamp = datetime.now(timezone.utc).replace(microsecond=0)
        return ConsensusDocument(
            project=_infer_project_name(self._source_path),
            created_at=timestamp,
            updated_at=timestamp,
            version=0,
            status=ConsensusStatus.INIT,
            objectives="",
            decisions=[],
            getting_started=_DEFAULT_GETTING_STARTED,
        )


def _extract_editable_markdown(markdown_text: str) -> str:
    """Remove the title/meta scaffolding and machine comments from rendered markdown."""

    editable = re.sub(r"\A# .*?\n", "", markdown_text, count=1)
    editable = re.sub(r"<!-- META:START -->\n.*?\n<!-- META:END -->\n?", "", editable, flags=re.DOTALL)
    editable = re.sub(r"<!-- [A-Z:]+ -->\n?", "", editable)
    return _normalize_markdown(editable)


def _compose_consensus_markdown(
    writer: ConsensusWriter,
    document: ConsensusDocument,
    editable_markdown: str,
) -> str:
    sections = _split_editable_sections(editable_markdown)
    title_and_meta = _extract_title_and_meta(writer.render(document))

    lines = [
        title_and_meta,
        "## Objectives",
        "<!-- OBJECTIVES:START -->",
        sections.get("Objectives", ""),
        "<!-- OBJECTIVES:END -->",
        "## Design Choices",
        "<!-- DECISIONS:START -->",
        sections.get("Design Choices", ""),
        "<!-- DECISIONS:END -->",
        "## Getting Started",
        sections.get("Getting Started", ""),
        "",
    ]

    questions = sections.get("Questions", "")
    if questions.strip():
        lines.extend(["## Questions", questions, ""])

    return "\n".join(lines).rstrip() + "\n"


def _extract_title_and_meta(markdown_text: str) -> str:
    before_objectives, _, _ = markdown_text.partition("## Objectives")
    return before_objectives.rstrip()


def _split_editable_sections(markdown_text: str) -> dict[str, str]:
    normalized = _normalize_markdown(markdown_text)
    matches = list(_SECTION_PATTERN.finditer(normalized))
    if not matches:
        raise ValueError("Consensus editor content is missing required section headings")

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        sections[match.group("name")] = normalized[start:end].strip("\n")

    required = {"Objectives", "Design Choices", "Getting Started"}
    missing = required.difference(sections)
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"Consensus editor content is missing sections: {missing_names}")
    return sections


def _normalize_markdown(markdown_text: str) -> str:
    stripped = markdown_text.strip()
    if not stripped:
        return ""
    return stripped + "\n"


def _infer_project_name(source_path: Path | None) -> str:
    if source_path is None:
        return "Vibrant"
    resolved = source_path.expanduser()
    if resolved.name == "consensus.md" and resolved.parent.name == ".vibrant":
        return resolved.parent.parent.name or "Vibrant"
    return resolved.stem or "Vibrant"
