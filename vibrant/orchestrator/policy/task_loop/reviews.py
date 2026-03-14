"""Review-resolution policy helpers."""

from __future__ import annotations

from ...types import ReviewTicketStatus
from .models import ReviewResolutionCommand


def review_ticket_status_for(command: ReviewResolutionCommand) -> ReviewTicketStatus:
    return {
        "accept": ReviewTicketStatus.ACCEPTED,
        "retry": ReviewTicketStatus.RETRY,
        "escalate": ReviewTicketStatus.ESCALATED,
    }[command.decision]


def review_ticket_status_for_resolution(command: ReviewResolutionCommand) -> ReviewTicketStatus:
    return review_ticket_status_for(command)
