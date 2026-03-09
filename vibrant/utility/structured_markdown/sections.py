"""Helpers for exact HTML comment section markers."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import SectionError

_SECTION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_MARKER_RE = re.compile(r"<!--\s*([A-Za-z0-9][A-Za-z0-9_-]*):(START|END)\s*-->")


@dataclass(frozen=True)
class _SectionSpan:
    name: str
    body_start: int
    body_end: int


def _validate_text(text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("text must be a string")


def _validate_name(name: str) -> None:
    if not isinstance(name, str):
        raise TypeError("section name must be a string")
    if not _SECTION_NAME_RE.fullmatch(name):
        raise ValueError(
            "section name must start with an alphanumeric character and contain "
            "only letters, digits, underscores, or hyphens"
        )


def _parse_sections(text: str) -> list[_SectionSpan]:
    _validate_text(text)

    sections: list[_SectionSpan] = []
    open_name: str | None = None
    open_start: int | None = None
    open_body_start: int | None = None
    seen_names: set[str] = set()

    for match in _MARKER_RE.finditer(text):
        name, marker_type = match.groups()

        if marker_type == "START":
            if open_name is not None:
                raise SectionError(
                    f"nested sections are not supported: section {open_name!r} "
                    f"contains a start marker for {name!r}"
                )
            if name in seen_names:
                raise SectionError(f"duplicate section {name!r} found")
            open_name = name
            open_start = match.start()
            open_body_start = match.end()
            continue

        if open_name is None:
            raise SectionError(
                f"end marker for section {name!r} appears without a matching start marker"
            )
        if open_name != name:
            raise SectionError(
                f"end marker for section {name!r} does not match open section {open_name!r}"
            )

        sections.append(
            _SectionSpan(
                name=name,
                body_start=open_body_start,
                body_end=match.start(),
            )
        )
        seen_names.add(name)
        open_name = None
        open_start = None
        open_body_start = None

    if open_name is not None:
        raise SectionError(f"section {open_name!r} is missing an end marker")

    return sections


def find_section(text: str, name: str) -> str | None:
    """Return the raw body for a named section, or None if it is absent."""

    _validate_name(name)

    for section in _parse_sections(text):
        if section.name == name:
            return text[section.body_start : section.body_end]
    return None


def require_section(text: str, name: str) -> str:
    """Return the raw body for a named section, raising if it is absent."""

    body = find_section(text, name)
    if body is None:
        raise SectionError(f"required section {name!r} not found")
    return body


def replace_section(text: str, name: str, body: str) -> str:
    """Replace the raw body for a named section, preserving surrounding text."""

    _validate_name(name)
    _validate_text(body)

    for section in _parse_sections(text):
        if section.name == name:
            return text[: section.body_start] + body + text[section.body_end :]

    raise SectionError(f"cannot replace missing section {name!r}")


def has_section(text: str, name: str) -> bool:
    """Return True when a named section exists."""

    return find_section(text, name) is not None


def list_sections(text: str) -> list[str]:
    """Return section names in document order."""

    return [section.name for section in _parse_sections(text)]
