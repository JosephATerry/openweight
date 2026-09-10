#!/usr/bin/env python3
"""Upload one validated OpenWeight export to the fixed Docker Space."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
from pathlib import Path

from export_huggingface_space import (
    collect_export_entries,
    validate_export,
)


SPACE_ID = "josephaterry/openweight"
OIDC_RESOURCE = "spaces/josephaterry/openweight"
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def build_upload_command(export_dir: Path, source_sha: str) -> tuple[str, ...]:
    if not COMMIT_SHA.fullmatch(source_sha):
        raise ValueError("source SHA must be a full lowercase Git commit SHA")
    return (
        "hf",
        "upload",
        SPACE_ID,
        str(export_dir.resolve()),
        ".",
        "--repo-type",
        "space",
        "--delete",
        "*",
        "--commit-message",
        f"Deploy OpenWeight from GitHub {source_sha[:12]}",
    )


def deploy(export_dir: Path, source_sha: str) -> None:
    if os.environ.get("HF_OIDC_RESOURCE") != OIDC_RESOURCE:
        raise ValueError(
            "HF_OIDC_RESOURCE must identify the configured destination Space"
        )
    validate_export(export_dir.resolve(), collect_export_entries())
    subprocess.run(
        build_upload_command(export_dir, source_sha),
        check=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the token-free upload command",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    export_dir = args.export_dir.resolve()
    validate_export(export_dir, collect_export_entries())
    command = build_upload_command(export_dir, args.source_sha)
    if args.dry_run:
        print(shlex.join(command))
        return 0

    deploy(export_dir, args.source_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
