"""Shared fail-closed PostgreSQL TLS configuration validation."""

from __future__ import annotations

import os
from pathlib import Path


POSTGRES_SSL_MODES = ("disable", "prefer", "require", "verify-ca", "verify-full")


def resolve_postgres_tls_config(
    sslmode: str,
    sslrootcert: str | None,
) -> tuple[str, str | None]:
    """Normalize TLS settings and validate an explicitly configured CA bundle."""

    normalized_mode = sslmode.strip().lower()
    if normalized_mode not in POSTGRES_SSL_MODES:
        raise ValueError("POSTGRES_SSLMODE is not supported")

    normalized_root = None if sslrootcert is None else sslrootcert.strip()
    if not normalized_root:
        if normalized_mode == "verify-full":
            raise ValueError(
                "POSTGRES_SSLROOTCERT must be an explicit readable CA bundle "
                "when POSTGRES_SSLMODE=verify-full"
            )
        return normalized_mode, None

    root_path = Path(normalized_root)
    if not root_path.is_absolute():
        raise ValueError("POSTGRES_SSLROOTCERT must be an absolute path")
    try:
        is_regular_file = root_path.is_file()
        is_nonempty = root_path.stat().st_size > 0
        is_readable = os.access(root_path, os.R_OK)
    except OSError as error:
        raise ValueError(
            "POSTGRES_SSLROOTCERT must name a readable regular CA bundle"
        ) from error
    if not is_regular_file or not is_nonempty or not is_readable:
        raise ValueError(
            "POSTGRES_SSLROOTCERT must name a readable regular CA bundle"
        )

    return normalized_mode, str(root_path)
