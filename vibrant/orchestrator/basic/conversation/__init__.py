"""Conversation mechanics and capability wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ...types import AgentConversationView, AgentStreamEvent, StreamSubscription
from .store import ConversationManifest, ConversationStore
from .stream import ConversationStreamService


@dataclass(slots=True)
class ConversationCapability:
    """Expose conversation storage and projection mechanics."""

    store: ConversationStore
    stream: ConversationStreamService

    def bind_agent(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str | None,
        task_id: str | None,
        provider_thread_id: str | None = None,
    ) -> None:
        self.stream.bind_agent(
            conversation_id=conversation_id,
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            provider_thread_id=provider_thread_id,
        )

    def record_host_message(
        self,
        *,
        conversation_id: str,
        role: Literal["user", "system"],
        text: str,
        related_question_id: str | None = None,
    ) -> AgentStreamEvent:
        return self.stream.record_host_message(
            conversation_id=conversation_id,
            role=role,
            text=text,
            related_question_id=related_question_id,
        )

    def ingest_canonical(self, event: dict[str, Any]) -> list[AgentStreamEvent]:
        return self.stream.ingest_canonical(event)

    def rebuild(self, conversation_id: str) -> AgentConversationView | None:
        return self.stream.rebuild(conversation_id)

    def subscribe(
        self,
        conversation_id: str,
        callback: Any,
        *,
        replay: bool = False,
    ) -> StreamSubscription:
        return self.stream.subscribe(conversation_id, callback, replay=replay)


__all__ = [
    "ConversationCapability",
    "ConversationManifest",
    "ConversationStore",
    "ConversationStreamService",
]
