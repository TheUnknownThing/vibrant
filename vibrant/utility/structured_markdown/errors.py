"""Exceptions raised by structured markdown helpers."""


class StructuredMarkdownError(Exception):
    """Base error for structured markdown helper failures."""


class SectionError(StructuredMarkdownError):
    """Raised when machine-managed sections are malformed or missing."""


class FieldParseError(StructuredMarkdownError):
    """Raised when a bold key/value field list cannot be parsed."""
