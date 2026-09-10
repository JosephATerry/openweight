"""Canonical environment-driven local API launcher."""

from __future__ import annotations

import uvicorn

from openweight_platform.api.config import ServiceSettings


def main() -> None:
    settings = ServiceSettings.from_env()
    uvicorn.run(
        "openweight_platform.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=False,
        reload=False,
    )


if __name__ == "__main__":
    main()
