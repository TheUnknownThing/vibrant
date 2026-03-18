"""Durable storage for processed conversation frames."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..json_store import read_json, write_json
from ...types import AgentStreamEvent, utc_now
from ....type_defs import JSONObject, is_json_object


@dataclass(slots=True)
class ConversationManifest:
    conversation_id: str
    agent_ids: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    provider_thread_id: str | None = None
    active_turn_id: str | None = None
    latest_run_id: str | None = None
    updated_at: str = field(default_factory=utc_now)
    next_sequence: int = 1


class ConversationStore:
    """Persist conversation manifests and stream frames under `.vibrant/conversations/`."""

    def __init__(self, vibrant_dir: Path) -> None:
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
        if event.agent_id and event.agent_id not in manifest.agent_ids:
            manifest.agent_ids.append(event.agent_id)
        if event.task_id and event.task_id not in manifest.task_ids:
            manifest.task_ids.append(event.task_id)
        if event.run_id and event.run_id not in manifest.run_ids:
            manifest.run_ids.append(event.run_id)
        if event.run_id:
            manifest.latest_run_id = event.run_id
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
            if not isinstance(payload, dict):
                continue
            payload.pop("incarnation_id", None)
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

    def _index(self) -> dict[str, JSONObject]:
        payload = read_json(self.index_path, default={})
        if is_json_object(payload):
            return payload
        return {}

    def _frames_path(self, conversation_id: str) -> Path:
        return self.frames_dir / f"{conversation_id}.jsonl"


def _manifest_from_raw(raw: JSONObject) -> ConversationManifest:
    return ConversationManifest(**raw)
