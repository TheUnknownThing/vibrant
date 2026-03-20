"""Textual UI package for Vibrant."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import VibrantApp

__all__ = ["VibrantApp"]


def __getattr__(name: str) -> object:
    if name == "VibrantApp":
        from .app import VibrantApp

        return VibrantApp
    raise AttributeError(name)
