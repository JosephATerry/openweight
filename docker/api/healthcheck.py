#!/usr/bin/env python3
"""Dependency-free liveness probe for the API container."""

from __future__ import annotations

import json
import os
from urllib.request import urlopen


def main() -> int:
    port = int(os.environ.get("OPENWEIGHT_API_PORT", "8000"))
    with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
        if response.status != 200:
            return 1
        payload = json.load(response)
    return 0 if payload.get("status") == "alive" else 1


if __name__ == "__main__":
    raise SystemExit(main())
