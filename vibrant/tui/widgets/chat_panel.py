"""Gatekeeper conversation panel."""

from __future__ import annotations

from typing import Any, Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ...models.state import OrchestratorStatus
from ...orchestrator.types import AgentConversationView, AgentStreamEvent, QuestionRecord, QuestionStatus
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
        height: 3;
        padding: 1 1 0 1;
        margin: 0 1;
        background: $primary-background;
        color: $text;
    }

    #chat-panel-subtitle {
        height: auto;
        padding: 0 1 1 1;
        margin: 0 1;
        color: $text-muted;
        background: $primary-background;
    }

    #chat-panel-notice {
        height: auto;
        padding: 1;
        margin: 1;
        background: $surface;
        color: $text-muted;
        display: none;
    }

    #chat-panel-notice.has-pending-question {
        color: $warning;
        background: $warning 12%;
        border-left: tall $warning;
    }

    #chat-panel-conversation {
        height: 1fr;
    }
    """

    FLASH_DURATION_SECONDS = 1.5

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._header_text = "[b]Gatekeeper[/b]"
        self._subtitle_text = "Gatekeeper conversation"
        self._question_summary_text = ""
        self._question_records: tuple[QuestionRecord, ...] = ()
        self._pending_questions: tuple[str, ...] = ()
        self._status: OrchestratorStatus | str | None = None
        self._notification_token = 0
        self._conversation: ConversationView | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-panel-layout"):
            yield Static(self._header_text, id="chat-panel-header", markup=True)
            yield Static(self._subtitle_text, id="chat-panel-subtitle")
            yield Static(self._question_summary_text, id="chat-panel-notice", markup=False)
            self._conversation = ConversationView(id="chat-panel-conversation")
            yield self._conversation

    @property
    def current_conversation_id(self) -> str | None:
        """Return the currently displayed orchestrator conversation id."""

        if self._conversation is None:
            return None
        return self._conversation.current_conversation_id

    def bind_conversation(self, conversation: AgentConversationView | None) -> None:
        """Render a conversation view from the orchestrator."""

        if self._conversation is not None:
            self._conversation.show_conversation(conversation)

    def ingest_stream_event(self, event: AgentStreamEvent) -> None:
        """Append one streamed event from the orchestrator conversation bus."""

        if self._conversation is not None:
            self._conversation.ingest_stream_event(event)

    def clear_conversation(self) -> None:
        """Clear the active conversation view."""

        if self._conversation is not None:
            self._conversation.clear()

    def clear(self) -> None:
        """Compatibility alias for clearing the conversation view."""

        self.clear_conversation()

    @property
    def notification_active(self) -> bool:
        """Return whether the panel is currently flashing for attention."""

        return self.has_class("question-notification")

    def get_question_summary_text(self) -> str:
        """Return the rendered Gatekeeper Q and A summary text."""

        return self._question_summary_text

    def set_gatekeeper_state(
        self,
        *,
        status: OrchestratorStatus | str | None,
        question_records: Sequence[QuestionRecord],
        flash: bool = False,
    ) -> None:
        """Update panel metadata from orchestrator-owned question state."""

        self._status = status
        self._question_records = tuple(question_records)
        self._pending_questions = tuple(
            record.text for record in self._question_records if record.status is QuestionStatus.PENDING and record.text
        )
        self._subtitle_text = _format_subtitle(status, has_pending_questions=bool(self._pending_questions))
        self._question_summary_text = _render_gatekeeper_summary(self._question_records)
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

    def _refresh_widgets(self) -> None:
        if not self.is_mounted:
            return

        self.query_one("#chat-panel-subtitle", Static).update(self._subtitle_text)

        notice = self.query_one("#chat-panel-notice", Static)
        if self._question_summary_text:
            notice.update(self._question_summary_text)
            notice.display = True
        else:
            notice.update("")
            notice.display = False
        notice.set_class(bool(self._pending_questions), "has-pending-question")


def _format_subtitle(status: OrchestratorStatus | str | None, *, has_pending_questions: bool) -> str:
    normalized = status.value if isinstance(status, OrchestratorStatus) else str(status or "").strip().lower()

    if normalized == OrchestratorStatus.PLANNING.value:
        return "Planning · User to Gatekeeper"
    if normalized == OrchestratorStatus.EXECUTING.value:
        return "Executing · Gatekeeper escalation" if has_pending_questions else "Executing · Gatekeeper history"
    if normalized == OrchestratorStatus.PAUSED.value:
        return "Paused · Review history"
    if normalized == OrchestratorStatus.COMPLETED.value:
        return "Completed · Review history"
    return "Gatekeeper conversation"


def _render_gatekeeper_summary(question_records: Sequence[QuestionRecord]) -> str:
    if not question_records:
        return ""

    rendered_blocks: list[str] = []
    for record in question_records[-3:]:
        lines = [
            "Gatekeeper -> User",
            f"Q: {record.text}",
        ]
        if record.status is QuestionStatus.RESOLVED and record.answer:
            lines.extend(
                [
                    "User -> Gatekeeper",
                    f"A: {record.answer}",
                ]
            )
        elif record.status is QuestionStatus.PENDING:
            lines.append("Status: awaiting your answer")
        elif record.status is QuestionStatus.WITHDRAWN:
            lines.append("Status: no longer needed")
        rendered_blocks.append("\n".join(lines))

    return "\n\n".join(rendered_blocks)
