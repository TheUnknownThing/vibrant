"""Helpers for simple bold key/value field bullets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re

from .errors import FieldParseError

_FIELD_RE = re.compile(r"- \*\*(?P<key>.+?)\*\*:(?P<value>.*)")


def _validate_text(text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("text must be a string")


def _validate_key(key: str) -> None:
    if not isinstance(key, str):
        raise TypeError("field keys must be strings")
    if not key:
        raise ValueError("field keys must not be empty")
    if "\n" in key or "\r" in key:
        raise ValueError("field keys must be single-line")
    if "**" in key:
        raise ValueError("field keys must not contain markdown bold markers")


def _validate_value(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("field values must be strings")
    if "\n" in value or "\r" in value:
        raise ValueError("field values must be single-line")


def parse_bold_kv_lines(text: str) -> dict[str, str]:
    """Parse `- **Key**: value` lines into an insertion-ordered dict."""

    _validate_text(text)

    items: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue

        match = _FIELD_RE.fullmatch(raw_line)
        if match is None:
            raise FieldParseError(
                f"invalid field line at line {line_number}: {raw_line!r}"
            )

        key = match.group("key")
        value = match.group("value")
        if value.startswith(" "):
            value = value[1:]

        try:
            _validate_key(key)
            _validate_value(value)
        except (TypeError, ValueError) as exc:
            raise FieldParseError(
                f"invalid field line at line {line_number}: {raw_line!r}"
            ) from exc

        if key in items:
            raise FieldParseError(f"duplicate field key {key!r} at line {line_number}")

        items[key] = value

    return items


def render_bold_kv_lines(items: Mapping[str, str] | Iterable[tuple[str, str]]) -> str:
    """Render key/value items using the supported bold bullet format."""

    if isinstance(items, Mapping):
        entries = items.items()
    else:
        entries = items

    seen_keys: set[str] = set()
    rendered_lines: list[str] = []
    for key, value in entries:
        _validate_key(key)
        _validate_value(value)

        if key in seen_keys:
            raise ValueError(f"duplicate field key {key!r}")
        seen_keys.add(key)

        if value:
            rendered_lines.append(f"- **{key}**: {value}")
        else:
            rendered_lines.append(f"- **{key}**:")

    return "\n".join(rendered_lines)
