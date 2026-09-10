"""Minimal structured logging with a strict safe-field allowlist."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


ACCESS_LOGGER_NAME = "openweight_platform.api.access"
SAFE_RECORD_FIELDS = (
    "request_id",
    "route",
    "method",
    "status_code",
    "latency_ms",
    "backend",
    "operation",
    "tool_name",
    "approval_state",
    "action_type",
    "dependency",
    "result",
    "error_type",
    "trace_id",
    "span_id",
)


class SafeJsonFormatter(logging.Formatter):
    """Render only deliberately allowlisted metadata as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for name in SAFE_RECORD_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure_api_logging(level: str) -> logging.Logger:
    """Configure the API access logger without changing application root logs."""

    logger = logging.getLogger(ACCESS_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    configured = any(
        getattr(handler, "_openweight_api", False)
        for handler in logger.handlers
    )
    if not configured:
        handler = logging.StreamHandler()
        handler.setFormatter(SafeJsonFormatter())
        handler._openweight_api = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


__all__ = [
    "ACCESS_LOGGER_NAME",
    "SAFE_RECORD_FIELDS",
    "SafeJsonFormatter",
    "configure_api_logging",
]
