"""Review ticket persistence."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ..json_store import read_json, write_json
from ...types import ReviewResolutionRecord, ReviewTicket, ReviewTicketStatus, utc_now


class ReviewTicketStore:
    """Persist attempt-scoped review tickets in ``.vibrant/reviews.json``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create(
        self,
        *,
        task_id: str,
        attempt_id: str,
        agent_id: str,
        review_kind: str,
        conversation_id: str | None,
        ticket_id: str | None = None,
        summary: str | None = None,
        diff_ref: str | None = None,
    ) -> ReviewTicket:
        tickets = self._load_tickets()
        ticket = ReviewTicket(
            ticket_id=ticket_id or f"review-{uuid4()}",
            task_id=task_id,
            attempt_id=attempt_id,
            agent_id=agent_id,
            review_kind=_normalize_review_kind(review_kind),
            conversation_id=_optional_string(conversation_id),
            summary=_optional_string(summary),
            diff_ref=_optional_string(diff_ref),
        )
        tickets[ticket.ticket_id] = ticket
        self._save_tickets(tickets)
        return ticket

    def get(self, ticket_id: str) -> ReviewTicket | None:
        return self._load_tickets().get(ticket_id)

    def list_pending(self) -> list[ReviewTicket]:
        return [ticket for ticket in self._load_tickets().values() if ticket.status is ReviewTicketStatus.PENDING]

    def list_by_task(self, task_id: str) -> list[ReviewTicket]:
        return [ticket for ticket in self._load_tickets().values() if ticket.task_id == task_id]

    def list_by_attempt(self, attempt_id: str) -> list[ReviewTicket]:
        return [ticket for ticket in self._load_tickets().values() if ticket.attempt_id == attempt_id]

    def resolve(
        self,
        ticket_id: str,
        resolution: ReviewResolutionRecord | None = None,
        *,
        status: ReviewTicketStatus | None = None,
        reason: str | None = None,
    ) -> ReviewTicket:
        tickets = self._load_tickets()
        try:
            ticket = tickets[ticket_id]
        except KeyError as exc:
            raise KeyError(f"Unknown review ticket: {ticket_id}") from exc

        if status is None:
            raise ValueError("resolve() requires an explicit status")

        resolution_message = None
        if resolution is not None and resolution.merge_outcome is not None:
            resolution_message = resolution.merge_outcome.message
        ticket.status = status
        ticket.resolution_reason = _optional_string(reason) or _optional_string(resolution_message)
        ticket.resolved_at = utc_now()
        tickets[ticket_id] = ticket
        self._save_tickets(tickets)
        return ticket

    def _load_tickets(self) -> dict[str, ReviewTicket]:
        raw = read_json(self.path, default={})
        if not isinstance(raw, dict):
            return {}

        tickets: dict[str, ReviewTicket] = {}
        for ticket_id, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            try:
                tickets[ticket_id] = ReviewTicket(
                    ticket_id=str(payload.get("ticket_id") or ticket_id),
                    task_id=str(payload["task_id"]),
                    attempt_id=str(payload["attempt_id"]),
                    agent_id=str(payload["agent_id"]),
                    review_kind=_normalize_review_kind(payload.get("review_kind", "task_result")),
                    conversation_id=_optional_string(payload.get("conversation_id")),
                    status=ReviewTicketStatus(str(payload.get("status", ReviewTicketStatus.PENDING.value))),
                    summary=_optional_string(payload.get("summary")),
                    diff_ref=_optional_string(payload.get("diff_ref")),
                    created_at=str(payload.get("created_at") or utc_now()),
                    resolved_at=_optional_string(payload.get("resolved_at")),
                    resolution_reason=_optional_string(payload.get("resolution_reason")),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return tickets

    def _save_tickets(self, tickets: dict[str, ReviewTicket]) -> None:
        write_json(
            self.path,
            {
                ticket_id: asdict(ticket)
                | {"status": ticket.status.value}
                for ticket_id, ticket in tickets.items()
            },
        )


def _normalize_review_kind(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"task_result", "merge_failure"}:
            return normalized
    return "task_result"
def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None
