"""Request-correlation context shared by HTTP and mounted protocol surfaces."""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4


REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CURRENT_REQUEST_ID: ContextVar[str | None] = ContextVar(
    "openweight_request_id",
    default=None,
)


def choose_request_id(value: str | None) -> str:
    """Retain one valid caller ID or create an opaque application ID."""

    if value is not None and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid4().hex


def set_request_id(value: str) -> Token[str | None]:
    return _CURRENT_REQUEST_ID.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _CURRENT_REQUEST_ID.reset(token)


def current_request_id() -> str:
    """Return the active HTTP ID or a fresh ID for an in-memory MCP call."""

    return _CURRENT_REQUEST_ID.get() or uuid4().hex


__all__ = [
    "REQUEST_ID_HEADER",
    "REQUEST_ID_PATTERN",
    "choose_request_id",
    "current_request_id",
    "reset_request_id",
    "set_request_id",
]
