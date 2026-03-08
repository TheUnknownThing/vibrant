"""Chat panel wrapper around the existing conversation view."""

from __future__ import annotations

from .conversation_view import ConversationView


class ChatPanel(ConversationView):
    """Compatibility wrapper for the eventual panel-D chat widget."""

