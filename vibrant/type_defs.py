"""Shared typing helpers for serializable payloads and callback surfaces."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import TypeAlias, TypeGuard

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONMapping: TypeAlias = Mapping[str, JSONValue]
JSONObject: TypeAlias = dict[str, JSONValue]
RequestId: TypeAlias = int | str
AsyncNone: TypeAlias = Awaitable[None] | None


def is_json_value(value: object) -> TypeGuard[JSONValue]:
    """Return whether ``value`` is representable as JSON data."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and is_json_value(item) for key, item in value.items())
    return False


def is_json_mapping(value: object) -> TypeGuard[JSONMapping]:
    """Return whether ``value`` is a mapping with JSON-compatible values."""

    if not isinstance(value, Mapping):
        return False
    return all(isinstance(key, str) and is_json_value(item) for key, item in value.items())


def is_json_object(value: object) -> TypeGuard[JSONObject]:
    """Return whether ``value`` is a JSON object backed by ``dict``."""

    return isinstance(value, dict) and is_json_mapping(value)
