"""Shared repository helpers for JSON-backed durable state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Generic, TypeVar

from .json_store import read_json, write_json


RecordT = TypeVar("RecordT")


class JsonMappingRepository(Generic[RecordT]):
    """Persist a mapping of keyed records in one JSON file."""

    def __init__(
        self,
        path: str | Path,
        *,
        parse_record: Callable[[str, object], RecordT | None],
        serialize_record: Callable[[RecordT], object],
        key_for: Callable[[RecordT], str],
    ) -> None:
        self.path = Path(path)
        self._parse_record = parse_record
        self._serialize_record = serialize_record
        self._key_for = key_for

    def load_all(self) -> dict[str, RecordT]:
        raw = read_json(self.path, default={})
        if not isinstance(raw, dict):
            return {}
        records: dict[str, RecordT] = {}
        for key, payload in raw.items():
            if not isinstance(key, str):
                continue
            record = self._parse_record(key, payload)
            if record is None:
                continue
            records[key] = record
        return records

    def save_all(self, records: Mapping[str, RecordT]) -> None:
        write_json(
            self.path,
            {
                key: self._serialize_record(record)
                for key, record in records.items()
            },
        )

    def list(self) -> list[RecordT]:
        return list(self.load_all().values())

    def get(self, key: str) -> RecordT | None:
        return self.load_all().get(key)

    def upsert(self, record: RecordT) -> RecordT:
        records = self.load_all()
        records[self._key_for(record)] = record
        self.save_all(records)
        return record

    def delete(self, key: str) -> None:
        records = self.load_all()
        if key not in records:
            return
        records.pop(key, None)
        self.save_all(records)


class JsonDataclassMappingRepository(JsonMappingRepository[RecordT]):
    """Persist dataclass records in one keyed JSON file."""

    def __init__(
        self,
        path: str | Path,
        *,
        record_type: type[RecordT],
        key_for: Callable[[RecordT], str],
        key_field: str | None = None,
        normalize_payload: Callable[[dict[str, object]], dict[str, object] | None] | None = None,
    ) -> None:
        def parse_record(key: str, payload: object) -> RecordT | None:
            normalized = _payload_with_key_field(payload, key_field=key_field, key=key)
            if not isinstance(normalized, dict):
                return None
            if normalize_payload is not None:
                normalized = normalize_payload(normalized)
                if normalized is None:
                    return None
            try:
                return record_type(**normalized)
            except (TypeError, ValueError):
                return None

        super().__init__(
            path,
            parse_record=parse_record,
            serialize_record=dataclass_to_json,
            key_for=key_for,
        )


class JsonDirectoryRepository(Generic[RecordT]):
    """Persist one keyed JSON document per record under a directory."""

    def __init__(
        self,
        path: str | Path,
        *,
        parse_text: Callable[[str], RecordT],
        serialize_record: Callable[[RecordT], str],
        key_for: Callable[[RecordT], str],
    ) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._parse_text = parse_text
        self._serialize_record = serialize_record
        self._key_for = key_for

    def get(self, key: str) -> RecordT | None:
        record_path = self.path / f"{key}.json"
        if not record_path.exists():
            return None
        return self._parse_text(record_path.read_text(encoding="utf-8"))

    def list(self) -> list[RecordT]:
        records: list[RecordT] = []
        for path in sorted(self.path.glob("*.json")):
            try:
                records.append(self._parse_text(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return records

    def upsert(self, record: RecordT) -> RecordT:
        key = self._key_for(record)
        path = self.path / f"{key}.json"
        path.write_text(self._serialize_record(record), encoding="utf-8")
        return record

    def delete(self, key: str) -> None:
        path = self.path / f"{key}.json"
        if path.exists():
            path.unlink()


def dataclass_to_json(value: object) -> object:
    """Recursively convert dataclasses and enums into JSON-compatible values."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: dataclass_to_json(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, list):
        return [dataclass_to_json(item) for item in value]
    if isinstance(value, tuple):
        return [dataclass_to_json(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): dataclass_to_json(item)
            for key, item in value.items()
        }
    return value


def _payload_with_key_field(
    payload: object,
    *,
    key_field: str | None,
    key: str,
) -> object:
    if key_field is None or not isinstance(payload, Mapping) or key_field in payload:
        return payload
    normalized = dict(payload)
    normalized[key_field] = key
    return normalized
