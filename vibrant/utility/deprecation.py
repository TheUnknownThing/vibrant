"""Deprecation helpers with compatibility for older Python versions."""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable
import warnings

_stdlib_deprecated = getattr(warnings, "deprecated", None)


def deprecated(func: Callable[..., Any]) -> Callable[..., Any]:
    """Provide a runtime deprecation decorator when ``warnings.deprecated`` is unavailable."""

    message = f"{func.__qualname__} is deprecated and will be removed in a future release."

    if _stdlib_deprecated is not None:
        return _stdlib_deprecated(message)(func)

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(message, category=DeprecationWarning, stacklevel=2)
            return await func(*args, **kwargs)

        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        warnings.warn(message, category=DeprecationWarning, stacklevel=2)
        return func(*args, **kwargs)

    return wrapper
