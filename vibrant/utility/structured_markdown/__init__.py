"""Small helpers for machine-managed Markdown sections."""

from .errors import FieldParseError, SectionError, StructuredMarkdownError
from .fields import parse_bold_kv_lines, render_bold_kv_lines
from .sections import (
    find_section,
    has_section,
    list_sections,
    replace_section,
    require_section,
)

__all__ = [
    "FieldParseError",
    "SectionError",
    "StructuredMarkdownError",
    "find_section",
    "has_section",
    "list_sections",
    "parse_bold_kv_lines",
    "render_bold_kv_lines",
    "replace_section",
    "require_section",
]
