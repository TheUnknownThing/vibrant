"""Durable storage for processed conversation frames."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..json_store import read_json, write_json
from ...types import AgentStreamEvent, utc_now


@dataclass(slots=True)
class ConversationManifest:
    conversation_id: str
    run_ids: list[str] = field(default_factory=list)
    active_turn_id: str | None = None
    updated_at: str = field(default_factory=utc_now)
    next_sequence: int = 1


class ConversationStore:
    """Persist conversation manifests and stream frames under `.vibrant/conversations/`."""

    def __init__(self, vibrant_dir: str | Path) -> None:
        self.vibrant_dir = Path(vibrant_dir)
        self.base_dir = self.vibrant_dir / "conversations"
        self.frames_dir = self.base_dir / "frames"
        self.index_path = self.base_dir / "index.json"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.frames_dir.mkdir(parents=True, exist_ok=True)

    def bind_run(
        self,
        *,
        conversation_id: str,
        run_id: str,
    ) -> ConversationManifest:
        normalized_run_id = run_id.strip()
        if not normalized_run_id:
            raise ValueError("bind_run() requires a non-empty run_id")
        manifest = self._ensure_manifest(conversation_id)
        if normalized_run_id not in manifest.run_ids:
            manifest.run_ids.append(normalized_run_id)
        manifest.updated_at = utc_now()
        self._save_manifest(manifest)
        return manifest

    def allocate_sequence(self, conversation_id: str) -> int:
        manifest = self._ensure_manifest(conversation_id)
        sequence = manifest.next_sequence
        manifest.next_sequence += 1
        manifest.updated_at = utc_now()
        self._save_manifest(manifest)
        return sequence

    def append_frame(self, event: AgentStreamEvent) -> AgentStreamEvent:
        manifest = self._ensure_manifest(event.conversation_id)
        manifest.updated_at = event.created_at
        if event.run_id and event.run_id not in manifest.run_ids:
            manifest.run_ids.append(event.run_id)
        if event.type == "conversation.turn.started":
            manifest.active_turn_id = event.turn_id
        elif event.type == "conversation.turn.completed" and manifest.active_turn_id == event.turn_id:
            manifest.active_turn_id = None
        self._save_manifest(manifest)

        payload = asdict(event)
        with self._frames_path(event.conversation_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")
        return event

    def load_frames(self, conversation_id: str) -> list[AgentStreamEvent]:
        path = self._frames_path(conversation_id)
        if not path.exists():
            return []
        frames: list[AgentStreamEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw:
                continue
            payload = json.loads(raw)
            payload.setdefault("run_id", None)
            frames.append(AgentStreamEvent(**payload))
        return frames

    def manifest(self, conversation_id: str) -> ConversationManifest | None:
        raw = self._index().get(conversation_id)
        if raw is None:
            return None
        return _manifest_from_raw(raw)

    def list_manifests(self) -> list[ConversationManifest]:
        manifests: list[ConversationManifest] = []
        for payload in self._index().values():
            try:
                manifests.append(_manifest_from_raw(payload))
            except ValueError:
                continue
        return manifests

    def update_active_turn(self, conversation_id: str, turn_id: str | None) -> ConversationManifest:
        manifest = self._ensure_manifest(conversation_id)
        manifest.active_turn_id = turn_id
        manifest.updated_at = utc_now()
        self._save_manifest(manifest)
        return manifest

    def normalize_manifests(
        self,
        *,
        attempt_records: list[object] | None = None,
        gatekeeper_conversation_id: str | None = None,
        gatekeeper_run_id: str | None = None,
    ) -> list[str]:
        raw_index = self._index()
        normalized_index: dict[str, dict[str, Any]] = {}
        rewritten: list[str] = []
        attempt_records = list(attempt_records or [])

        known_ids = set(raw_index)
        known_ids.update(
            conversation_id
            for conversation_id in (
                gatekeeper_conversation_id,
                *(
                    getattr(record, "conversation_id", None)
                    for record in attempt_records
                ),
            )
            if isinstance(conversation_id, str) and conversation_id
        )

        for conversation_id in sorted(known_ids):
            raw = raw_index.get(conversation_id, {"conversation_id": conversation_id})
            if not isinstance(raw, dict):
                raw = {"conversation_id": conversation_id}
            manifest = _manifest_from_raw(raw)
            derived_run_ids = list(manifest.run_ids)
            if not derived_run_ids:
                derived_run_ids.extend(_frame_run_ids(self.load_frames(conversation_id)))
            if gatekeeper_conversation_id == conversation_id and gatekeeper_run_id:
                derived_run_ids.append(gatekeeper_run_id)
            for attempt in attempt_records:
                if getattr(attempt, "conversation_id", None) != conversation_id:
                    continue
                derived_run_ids.extend(_attempt_run_ids(attempt))
            normalized_manifest = ConversationManifest(
                conversation_id=conversation_id,
                run_ids=_dedupe_strings(derived_run_ids),
                active_turn_id=manifest.active_turn_id,
                updated_at=manifest.updated_at,
                next_sequence=manifest.next_sequence,
            )
            normalized_payload = asdict(normalized_manifest)
            normalized_index[conversation_id] = normalized_payload
            if raw != normalized_payload:
                rewritten.append(conversation_id)

        if rewritten or raw_index != normalized_index:
            write_json(self.index_path, normalized_index)
        return rewritten

    def _ensure_manifest(self, conversation_id: str) -> ConversationManifest:
        manifest = self.manifest(conversation_id)
        if manifest is not None:
            return manifest
        manifest = ConversationManifest(conversation_id=conversation_id)
        self._save_manifest(manifest)
        return manifest

    def _save_manifest(self, manifest: ConversationManifest) -> None:
        index = self._index()
        index[manifest.conversation_id] = asdict(manifest)
        write_json(self.index_path, index)

    def _index(self) -> dict[str, dict[str, Any]]:
        payload = read_json(self.index_path, default={})
        if isinstance(payload, dict):
            return payload
        return {}

    def _frames_path(self, conversation_id: str) -> Path:
        return self.frames_dir / f"{conversation_id}.jsonl"


def _manifest_from_raw(raw: dict[str, Any]) -> ConversationManifest:
    payload = dict(raw)
    binding_ids = _string_list(payload.get("binding_ids"))
    run_ids = _string_list(payload.get("run_ids"))
    payload["run_ids"] = run_ids or binding_ids
    payload.pop("binding_kind", None)
    payload.pop("binding_ids", None)
    payload.pop("agent_ids", None)
    payload.pop("task_ids", None)
    payload.pop("run_task_ids", None)
    payload.setdefault("active_turn_id", None)
    payload.setdefault("updated_at", utc_now())
    payload.setdefault("next_sequence", 1)
    return ConversationManifest(**payload)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        cleaned = value.strip() if isinstance(value, str) else ""
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _frame_run_ids(frames: list[AgentStreamEvent]) -> list[str]:
    return [frame.run_id for frame in frames if isinstance(frame.run_id, str) and frame.run_id]


def _attempt_run_ids(record: object) -> list[str]:
    run_ids: list[str] = []
    for field_name in ("code_run_id", "merge_run_id"):
        value = getattr(record, field_name, None)
        if isinstance(value, str) and value.strip():
            run_ids.append(value.strip())
    validation_run_ids = getattr(record, "validation_run_ids", None)
    if isinstance(validation_run_ids, list):
        run_ids.extend(item.strip() for item in validation_run_ids if isinstance(item, str) and item.strip())
    return run_ids
