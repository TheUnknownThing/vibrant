"""Panel D chat and Gatekeeper Q&A widget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ...models import ThreadInfo
from ...models.state import OrchestratorStatus
from .conversation_view import ConversationView


@dataclass(slots=True)
class GatekeeperExchange:
    """One Gatekeeper question and its optional user answer."""

    question: str
    answer: str | None = None


class ChatPanel(Static):
    """Conversation panel with Gatekeeper escalation context."""

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
        background: $primary-background;
        color: $text;
    }

    #chat-panel-subtitle {
        height: auto;
        padding: 0 1 1 1;
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
        self._header_text = "[b]Chat / Q&A[/b]"
        self._subtitle_text = "Conversation threads"
        self._question_summary_text = ""
        self._pending_questions: tuple[str, ...] = ()
        self._status: OrchestratorStatus | str | None = None
        self._notification_token = 0
        self._gatekeeper_history: list[GatekeeperExchange] = []
        self._conversation: ConversationView | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-panel-layout"):
            yield Static(self._header_text, id="chat-panel-header", markup=True)
            yield Static(self._subtitle_text, id="chat-panel-subtitle")
            yield Static(self._question_summary_text, id="chat-panel-notice", markup=False)
            self._conversation = ConversationView(id="chat-panel-conversation")
            yield self._conversation

    def show_thread(self, thread: ThreadInfo) -> None:
        """Render a conversation thread in the scrollable history area."""

        if self._conversation is not None:
            self._conversation.show_thread(thread)

    def update_streaming_text(self, text: str) -> None:
        """Forward live assistant text into the conversation history view."""

        if self._conversation is not None:
            self._conversation.update_streaming_text(text)

    def clear(self) -> None:
        """Clear the active conversation while preserving Gatekeeper state."""

        if self._conversation is not None:
            self._conversation.clear()

    @property
    def current_thread_id(self) -> str | None:
        """Return the currently displayed conversation thread id."""

        if self._conversation is None:
            return None
        return self._conversation.current_thread_id

    @property
    def notification_active(self) -> bool:
        """Return whether the panel is currently flashing for attention."""

        return self.has_class("question-notification")

    def get_question_summary_text(self) -> str:
        """Return the rendered Gatekeeper Q&A summary text."""

        return self._question_summary_text

    def set_gatekeeper_state(
        self,
        *,
        status: OrchestratorStatus | str | None,
        pending_questions: Sequence[str],
        flash: bool = False,
    ) -> None:
        """Update the panel subtitle and Gatekeeper question summary."""

        self._status = status
        self._pending_questions = tuple(question for question in pending_questions if question)

        known_questions = {exchange.question for exchange in self._gatekeeper_history}
        for question in self._pending_questions:
            if question not in known_questions:
                self._gatekeeper_history.append(GatekeeperExchange(question=question))

        self._subtitle_text = _format_subtitle(status, has_pending_questions=bool(self._pending_questions))
        self._question_summary_text = _render_gatekeeper_summary(
            self._gatekeeper_history,
            pending_questions=self._pending_questions,
        )
        self._refresh_widgets()

        if flash and self._pending_questions:
            self.flash_question_notification()

    def record_gatekeeper_answer(self, question: str, answer: str) -> None:
        """Record the latest user answer for a Gatekeeper escalation."""

        for exchange in reversed(self._gatekeeper_history):
            if exchange.question == question and exchange.answer is None:
                exchange.answer = answer
                break
        else:
            self._gatekeeper_history.append(GatekeeperExchange(question=question, answer=answer))

        self._question_summary_text = _render_gatekeeper_summary(
            self._gatekeeper_history,
            pending_questions=self._pending_questions,
        )
        self._refresh_widgets()

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
        return "Planning · User ↔ Gatekeeper"
    if normalized == OrchestratorStatus.EXECUTING.value:
        return "Executing · Gatekeeper escalation" if has_pending_questions else "Executing · Conversation threads"
    if normalized == OrchestratorStatus.PAUSED.value:
        return "Paused · Review history"
    if normalized == OrchestratorStatus.COMPLETED.value:
        return "Completed · Review history"
    return "Conversation threads"



def _render_gatekeeper_summary(
    history: Sequence[GatekeeperExchange],
    *,
    pending_questions: Sequence[str],
) -> str:
    if not history:
        return ""

    pending = set(pending_questions)
    rendered_blocks: list[str] = []
    for exchange in history[-3:]:
        lines = [
            "Gatekeeper → User",
            f"Q: {exchange.question}",
        ]
        if exchange.answer:
            lines.extend(
                [
                    "You → Gatekeeper",
                    f"A: {exchange.answer}",
                ]
            )
        elif exchange.question in pending:
            lines.append("Status: awaiting your answer")
        rendered_blocks.append("\n".join(lines))

    return "\n\n".join(rendered_blocks)
