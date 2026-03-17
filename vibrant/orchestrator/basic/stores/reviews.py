"""Review ticket persistence."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from ..repository import JsonDataclassMappingRepository
from ...types import ReviewResolutionRecord, ReviewTicket, ReviewTicketStatus, utc_now


class ReviewTicketStore:
    """Persist attempt-scoped review tickets in ``.vibrant/reviews.json``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._repository = JsonDataclassMappingRepository(
            self.path,
            record_type=ReviewTicket,
            key_for=lambda ticket: ticket.ticket_id,
            key_field="ticket_id",
            normalize_payload=_normalize_review_payload,
        )

    def create(
        self,
        *,
        task_id: str,
        attempt_id: str,
        run_id: str,
        review_kind: str,
        conversation_id: str | None,
        ticket_id: str | None = None,
        summary: str | None = None,
        diff_ref: str | None = None,
        base_commit: str | None = None,
        result_commit: str | None = None,
        integration_commit: str | None = None,
    ) -> ReviewTicket:
        tickets = self._load_tickets()
        ticket = ReviewTicket(
            ticket_id=ticket_id or f"review-{uuid4()}",
            task_id=task_id,
            attempt_id=attempt_id,
            run_id=run_id,
            review_kind=_normalize_review_kind(review_kind),
            conversation_id=_optional_string(conversation_id),
            summary=_optional_string(summary),
            diff_ref=_optional_string(diff_ref),
            base_commit=_optional_string(base_commit),
            result_commit=_optional_string(result_commit),
            integration_commit=_optional_string(integration_commit),
        )
        tickets[ticket.ticket_id] = ticket
        self._save_tickets(tickets)
        return ticket

    def get(self, ticket_id: str) -> ReviewTicket | None:
        return self._repository.get(ticket_id)

    def list_pending(self) -> list[ReviewTicket]:
        return [ticket for ticket in self._load_tickets().values() if ticket.status is ReviewTicketStatus.PENDING]

    def list_all(self) -> list[ReviewTicket]:
        return self._repository.list()

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
        return self._repository.load_all()

    def _save_tickets(self, tickets: dict[str, ReviewTicket]) -> None:
        self._repository.save_all(tickets)


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


def _normalize_review_payload(payload: dict[str, object]) -> dict[str, object] | None:
    try:
        return {
            "ticket_id": str(payload["ticket_id"]),
            "task_id": str(payload["task_id"]),
            "attempt_id": str(payload["attempt_id"]),
            "run_id": str(payload["run_id"]),
            "review_kind": _normalize_review_kind(payload.get("review_kind", "task_result")),
            "conversation_id": _optional_string(payload.get("conversation_id")),
            "status": ReviewTicketStatus(str(payload.get("status", ReviewTicketStatus.PENDING.value))),
            "summary": _optional_string(payload.get("summary")),
            "diff_ref": _optional_string(payload.get("diff_ref")),
            "base_commit": _optional_string(payload.get("base_commit")),
            "result_commit": _optional_string(payload.get("result_commit")),
            "integration_commit": _optional_string(payload.get("integration_commit")),
            "created_at": str(payload.get("created_at") or utc_now()),
            "resolved_at": _optional_string(payload.get("resolved_at")),
            "resolution_reason": _optional_string(payload.get("resolution_reason")),
        }
    except (KeyError, TypeError, ValueError):
        return None
