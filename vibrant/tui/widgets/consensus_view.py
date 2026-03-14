"""Consensus editor/viewer widget for orchestrator-owned consensus state."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from textual.app import ComposeResult
from textual.containers import Center, Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Markdown, Static, TextArea

from ...models.consensus import DEFAULT_CONSENSUS_CONTEXT, ConsensusDocument, ConsensusStatus
from ...models.task import TaskInfo

if TYPE_CHECKING:
    from ...orchestrator.facade import OrchestratorFacade


class ConsensusView(Static):
    """Shared consensus metadata + content preview/editor component."""

    class SaveRequested(Message):
        """Raised when the user wants to persist consensus edits."""

        def __init__(self, document: ConsensusDocument, *, already_saved: bool = False) -> None:
            super().__init__()
            self.document = document
            self.already_saved = already_saved

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
        self._document: ConsensusDocument | None = None
        self._source_path: Path | None = None
        self._tasks: tuple[TaskInfo, ...] = ()
        self._is_editing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="consensus-empty-state"):
            with Vertical(id="consensus-empty-copy"):
                yield Static("", id="consensus-empty-message")
                with Center():
                    yield Button("+ Create File", id="consensus-empty-action", compact=True, flat=True)

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

    @property
    def is_editing(self) -> bool:
        """Return whether edit mode is active."""

        return self._is_editing

    @property
    def has_unsaved_changes(self) -> bool:
        """Return whether the editor differs from the saved baseline."""

        if not self._is_editing or self._document is None:
            return False
        return _normalize_markdown(self.current_editable_markdown) != self._load_markdown_from_disk()

    @property
    def current_editable_markdown(self) -> str:
        """Return the current editor contents."""

        editor = self._editor_widget()
        if editor is not None:
            return editor.text
        return self._load_markdown_from_disk()

    def clear_summary(self) -> None:
        """Show the missing-file state with a short summary."""

        self._document = None
        self._tasks = ()
        self._source_path = None
        self._is_editing = False
        self._refresh_view()

    def update_consensus(
        self,
        document: ConsensusDocument | None,
        *,
        tasks: Sequence[TaskInfo] = (),
        source_path: str | Path | None = None,
        raw_markdown: str | None = None,
    ) -> None:
        """Refresh the widget from the latest consensus document."""

        if document is None:
            self.clear_summary()
            return

        self._document = document.model_copy(deep=True)
        self._tasks = tuple(tasks)
        if source_path is not None:
            self._source_path = Path(source_path)
        elif self._source_path is None and self._orchestrator_facade is not None:
            self._source_path = self._orchestrator_facade.get_consensus_source_path()

        if not self._is_editing:
            self._reload_preview_from_disk(fallback=raw_markdown)

        self._refresh_view()

    def action_toggle_edit_mode(self) -> None:
        """Switch between preview and edit modes."""

        if self._document is None:
            return

        if self._is_editing:
            if self.has_unsaved_changes and not self._save_current_edits():
                return
            self._is_editing = False
            self._reload_preview_from_disk()
        else:
            self._is_editing = True
            self._load_editor_from_disk()
            self._refresh_preview(self.current_editable_markdown)

        self._refresh_view()

    def action_revert_edits(self) -> None:
        """Discard local edits and return to the latest saved document."""

        self._is_editing = False
        self._reload_preview_from_disk()
        self._refresh_view()

    def action_save_edits(self) -> None:
        """Emit a save request for the current editor contents."""

        if self._document is None:
            return
        if not self._save_current_edits():
            return
        self._is_editing = False
        self._reload_preview_from_disk()
        self._refresh_view()

    def action_create_file(self) -> None:
        """Create a new consensus document using the default scaffold."""

        document = self._build_default_document()
        self._document = document
        if self._save_document(document):
            self._is_editing = False
            self._reload_preview_from_disk()
            self._refresh_view()

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

    def assert_facade(self) -> OrchestratorFacade:
        """Return the orchestrator facade, asserting the view is displayable."""

        facade = self._orchestrator_facade
        assert facade is not None, "ConsensusView requires an orchestrator facade before it can be displayed"
        return facade

    def load_document(self) -> None:
        """Load the consensus document from disk and refresh the view."""

        self._document = self._get_consensus()
        self._reload_preview_from_disk()
        self._refresh_view()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "consensus-editor":
            return
        if self._is_editing:
            self._refresh_preview(event.text_area.text)
        self._refresh_view()

    def _refresh_view(self) -> None:
        if not self.is_mounted:
            return

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

        create_message.update("consensus.md does not exist.")
        title.update(self._source_label())

        toggle.label = "Preview" if self._is_editing else "Edit"
        toggle.display = has_document
        preview.display = has_document and not self._is_editing
        editor.display = has_document and self._is_editing
        meta.display = has_document and not self._is_editing
        actions.display = has_document and self._is_editing
        save_button.disabled = not self.has_unsaved_changes

        if has_document:
            meta.update(_format_metadata(self._document))
            markdown_text = self.current_editable_markdown if self._is_editing else self._load_markdown_from_disk()
            self._refresh_preview(markdown_text)

    def _source_label(self) -> str:
        if self._source_path is not None:
            return self._source_path.name
        return "consensus.md"

    def _editor_widget(self) -> TextArea | None:
        try:
            return self.query_one("#consensus-editor", TextArea)
        except Exception:
            return None

    def _set_editor_text(self, text: str) -> None:
        normalized = _normalize_markdown(text)
        editor = self._editor_widget()
        if editor is not None and editor.text != normalized:
            editor.load_text(normalized)

    def _load_markdown_from_disk(self, *, fallback: str | None = None) -> str:
        if self._source_path is not None:
            try:
                raw_markdown = self._source_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                pass
            else:
                return _extract_editable_markdown(raw_markdown, self._document)

        return _extract_editable_markdown(fallback, self._document)

    def _load_editor_from_disk(self) -> None:
        self._set_editor_text(self._load_markdown_from_disk())

    def _reload_preview_from_disk(self, *, fallback: str | None = None) -> None:
        # TODO: shouldn't load from disk.

        self._document = self._get_consensus() or self._document
        if not self.is_mounted:
            return
        self._refresh_preview(self._load_markdown_from_disk(fallback=fallback))

    def _refresh_preview(self, markdown_text: str) -> None:
        if not self.is_mounted:
            return
        preview = self.query_one("#consensus-preview", Markdown)
        preview.update(markdown_text or "_Consensus is empty._")

    def _save_current_edits(self) -> bool:
        return self._save_document(self._parse_editor_document(self.current_editable_markdown))

    def _save_document(self, document: ConsensusDocument) -> bool:
        try:
            written = self.assert_facade().write_consensus_document(document)
        except Exception as exc:
            self.app.notify(f"Failed to save consensus edits: {exc}", severity="error")
            return False

        self._document = written.model_copy(deep=True)
        self._source_path = self.assert_facade().get_consensus_source_path()
        self.post_message(self.SaveRequested(written, already_saved=True))
        return True

    def _parse_editor_document(self, editable_markdown: str) -> ConsensusDocument:
        if self._document is None:
            raise ValueError("Consensus document is unavailable")
        updated = self._document.model_copy(deep=True)
        updated.context = _normalize_markdown(editable_markdown).rstrip("\n")
        return updated

    def _build_default_document(self) -> ConsensusDocument:
        return ConsensusDocument(
            project=_infer_project_name(self._source_path),
            status=ConsensusStatus.INIT,
            context=DEFAULT_CONSENSUS_CONTEXT,
        )

    @property
    def _orchestrator_facade(self) -> OrchestratorFacade | None:
        """Access the app's orchestrator facade."""

        app = self.app
        if app is None:
            return None
        facade = getattr(app, "orchestrator_facade", None)
        assert facade is not None
        return facade

    def _get_consensus(self) -> ConsensusDocument | None:
        """Get the current consensus from the orchestrator."""

        facade = self._orchestrator_facade
        if facade is None:
            return None
        return facade.get_consensus_document()


def _extract_editable_markdown(raw_markdown: str | None, document: ConsensusDocument | None) -> str:
    if raw_markdown:
        meta_end_marker = "<!-- META:END -->"
        if meta_end_marker in raw_markdown:
            body = raw_markdown.split(meta_end_marker, maxsplit=1)[1].lstrip("\n")
            return _normalize_markdown(body)
        return _normalize_markdown(raw_markdown)
    if document is None:
        return ""
    return _normalize_markdown(document.context)


def _format_metadata(document: ConsensusDocument) -> str:
    status_color = {
        ConsensusStatus.INIT: "white",
        ConsensusStatus.PLANNING: "cyan",
        ConsensusStatus.EXECUTING: "purple",
        ConsensusStatus.PAUSED: "yellow",
        ConsensusStatus.COMPLETED: "blue",
        ConsensusStatus.FAILED: "red",
    }.get(document.status, "white")
    metadata = f"[b]Project[/b]: {document.project} v{document.version} (Status [{status_color}]{document.status.value}[/])"

    if document.updated_at is not None:
        metadata += f"\n[b]Updated[/b]: {document.updated_at.astimezone(timezone.utc).isoformat()}"
    return metadata


def _infer_project_name(source_path: Path | None) -> str:
    if source_path is None:
        return "Vibrant"
    if source_path.parent.name == ".vibrant" and source_path.parent.parent.name:
        return source_path.parent.parent.name
    if source_path.parent.name:
        return source_path.parent.name
    return "Vibrant"


def _normalize_markdown(text: str) -> str:
    return text
