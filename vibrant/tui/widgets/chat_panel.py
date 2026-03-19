"""Gatekeeper conversation panel."""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ...orchestrator.types import AgentConversationView, AgentStreamEvent, QuestionStatus, QuestionView, WorkflowStatus
from .conversation_view import ConversationView


class ChatPanel(Static):
    """Conversation panel backed by orchestrator conversation streams."""

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        padding: 0;
        background: $surface;
    }

    ChatPanel.question-notification {
        border: tall $warning;
    }

    #chat-panel-layout {
        height: 1fr;
    }

    #chat-panel-header {
        height: 2;
        padding: 1 1 0 1;
        margin-top: 1;
        background: $primary-background;
        color: $text;
    }

    #chat-panel-subtitle {
        height: auto;
        padding: 0 1 1 1;
        margin-bottom: 1;
        color: $text-muted;
        background: $primary-background;
    }

    #chat-panel-conversation {
        height: 1fr;
    }
    """

    FLASH_DURATION_SECONDS = 1.5

    def __init__(self, **widget_kwargs: object) -> None:
        super().__init__(**widget_kwargs)
        self._header_text = "[b]Gatekeeper[/b]"
        self._subtitle_text = "Type in the input bar to engage in conversation."
        self._question_records: tuple[QuestionView, ...] = ()
        self._pending_questions: tuple[str, ...] = ()
        self._status: WorkflowStatus | str | None = None
        self._notification_token = 0
        self._conversation: ConversationView | None = None
        self._bound_conversation: AgentConversationView | None = None
        self._conversation_refresh_scheduled = False

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-panel-layout"):
            yield Static(self._header_text, id="chat-panel-header", markup=True)
            yield Static(self._subtitle_text, id="chat-panel-subtitle")
            self._conversation = ConversationView(id="chat-panel-conversation")
            yield self._conversation

    def on_mount(self) -> None:
        self._refresh_widgets()
        self._schedule_conversation_refresh()

    @property
    def current_conversation_id(self) -> str | None:
        """Return the currently displayed orchestrator conversation id."""

        if self._conversation is None:
            return None
        return self._conversation.current_conversation_id

    def bind_conversation(self, conversation: AgentConversationView | None) -> None:
        """Render a conversation view from the orchestrator."""

        self._bound_conversation = conversation
        self._schedule_conversation_refresh()

    def ingest_stream_event(self, event: AgentStreamEvent) -> None:
        """Append one streamed event from the orchestrator conversation bus."""

        if self._conversation is not None:
            self._conversation.ingest_stream_event(event)

    def clear_conversation(self) -> None:
        """Clear the active conversation view."""

        self._bound_conversation = None
        self._schedule_conversation_refresh()

    def clear(self) -> None:
        """Compatibility alias for clearing the conversation view."""

        self.clear_conversation()

    @property
    def notification_active(self) -> bool:
        """Return whether the panel is currently flashing for attention."""

        return self.has_class("question-notification")

    def get_question_summary_text(self) -> str:
        """Return the deprecated standalone question notice text."""

        return ""

    def set_gatekeeper_state(
        self,
        *,
        status: WorkflowStatus | str | None,
        question_records: Sequence[QuestionView],
        flash: bool = False,
    ) -> None:
        """Update panel metadata from orchestrator-owned question state."""

        self._status = status
        self._question_records = tuple(question_records)
        self._pending_questions = tuple(
            record.text for record in self._question_records if record.status is QuestionStatus.PENDING and record.text
        )
        self._subtitle_text = _format_subtitle(status, has_pending_questions=bool(self._pending_questions))
        self._schedule_conversation_refresh()
        self._refresh_widgets()

        if flash and self._pending_questions:
            self.flash_question_notification()

    def flash_question_notification(self) -> None:
        """Temporarily highlight the panel when a new question arrives."""

        self._notification_token += 1
        token = self._notification_token
        self.add_class("question-notification")
        self.set_timer(self.FLASH_DURATION_SECONDS, lambda: self._clear_question_notification(token))

    def _clear_question_notification(self, token: int) -> None:
        if token == self._notification_token:
            self.remove_class("question-notification")

    def _schedule_conversation_refresh(self) -> None:
        if self._conversation is None or self._conversation_refresh_scheduled:
            return
        self._conversation_refresh_scheduled = True
        self.call_after_refresh(self._sync_conversation_view)

    def _sync_conversation_view(self) -> None:
        self._conversation_refresh_scheduled = False
        if self._conversation is None:
            return
        self._conversation.sync_state(
            conversation=self._bound_conversation,
            pending_questions=self._pending_questions,
        )

    def _refresh_widgets(self) -> None:
        if not self.is_mounted:
            return

        self.query_one("#chat-panel-subtitle", Static).update(self._subtitle_text)


def _format_subtitle(status: WorkflowStatus | str | None, *, has_pending_questions: bool) -> str:
    normalized = status.value if isinstance(status, WorkflowStatus) else str(status or "").strip().lower()

    if normalized == WorkflowStatus.PLANNING.value:
        return "Planning · User to Gatekeeper"
    if normalized == WorkflowStatus.EXECUTING.value:
        return "Executing · Gatekeeper escalation" if has_pending_questions else "Executing · Gatekeeper history"
    if normalized == WorkflowStatus.PAUSED.value:
        return "Paused · Review history"
    if normalized == WorkflowStatus.COMPLETED.value:
        return "Completed · Review history"
    if normalized == WorkflowStatus.FAILED.value:
        return "Failed · Review history"
    return "Type in the input bar to engage in conversation."
