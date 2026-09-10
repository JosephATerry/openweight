#!/usr/bin/env python3
"""Create the deterministic, allowlisted OpenWeight Docker Space export."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# Docker builds from this deliberately small context. The Space card replaces
# the engineering repository README at the export root.
EXACT_FILES: dict[str, str] = {
    ".dockerignore": ".dockerignore",
    "Dockerfile": "Dockerfile",
    "README.md": "deploy/huggingface/README.template.md",
    "requirements-container.txt": "requirements-container.txt",
    "requirements.txt": "requirements.txt",
    "deploy/huggingface/policy_index.json": (
        "deploy/huggingface/policy_index.json"
    ),
    "deploy/huggingface/policy_index.npz": (
        "deploy/huggingface/policy_index.npz"
    ),
    "docker/api/healthcheck.py": "docker/api/healthcheck.py",
    "scripts/index_policy_corpus.py": "scripts/index_policy_corpus.py",
    "scripts/setup_operations.py": "scripts/setup_operations.py",
    "scripts/setup_security.py": "scripts/setup_security.py",
}

ALLOWLISTED_TREES: tuple[str, ...] = (
    "data/policies",
    "frontend",
    "src",
)

IGNORED_TREE_PARTS = frozenset(
    {
        ".cache",
        ".pytest_cache",
        ".vite",
        "__pycache__",
        "coverage",
        "dist",
        "node_modules",
        "playwright-report",
        "test-results",
    }
)

FORBIDDEN_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".github/",
    ".huggingface/",
    ".venv/",
    "data/evals/",
    "data/training/",
    "mlruns/",
    "models/",
    "results/",
)


@dataclass(frozen=True, order=True)
class ExportEntry:
    destination: str
    source: str


def _normalized_relative(path: Path) -> str:
    return path.as_posix()


def _is_forbidden(path: str) -> bool:
    normalized = path.rstrip("/") + ("/" if path.endswith("/") else "")
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in FORBIDDEN_PREFIXES
    )


def collect_export_entries(
    repository_root: Path = REPOSITORY_ROOT,
) -> tuple[ExportEntry, ...]:
    """Return the complete export manifest without traversing excluded data."""

    entries: list[ExportEntry] = []

    for destination, source in EXACT_FILES.items():
        source_path = repository_root / source
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError(f"required export file is missing or unsafe: {source}")
        entries.append(ExportEntry(destination=destination, source=source))

    for tree in ALLOWLISTED_TREES:
        tree_path = repository_root / tree
        if not tree_path.is_dir() or tree_path.is_symlink():
            raise ValueError(f"required export tree is missing or unsafe: {tree}")
        for source_path in sorted(tree_path.rglob("*")):
            relative = source_path.relative_to(repository_root)
            if any(part in IGNORED_TREE_PARTS for part in relative.parts):
                continue
            if source_path.is_symlink():
                raise ValueError(
                    f"symbolic links are not allowed in the export: {relative}"
                )
            if source_path.is_file():
                normalized = _normalized_relative(relative)
                entries.append(
                    ExportEntry(destination=normalized, source=normalized)
                )

    entries = sorted(set(entries))
    destinations = [entry.destination for entry in entries]
    if len(destinations) != len(set(destinations)):
        raise ValueError("duplicate destination in Space export manifest")
    if any(_is_forbidden(path) for path in destinations):
        raise ValueError("forbidden path entered Space export manifest")
    return tuple(entries)


def validate_export(
    export_root: Path,
    entries: tuple[ExportEntry, ...],
) -> None:
    actual = {
        _normalized_relative(path.relative_to(export_root))
        for path in export_root.rglob("*")
        if path.is_file()
    }
    expected = {entry.destination for entry in entries}
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            f"Space export does not match allowlist; missing={missing}, "
            f"unexpected={unexpected}"
        )
    if any(_is_forbidden(path) for path in actual):
        raise ValueError("Space export contains a forbidden path")


def export_space(
    output: Path,
    repository_root: Path = REPOSITORY_ROOT,
) -> tuple[ExportEntry, ...]:
    repository_root = repository_root.resolve()
    output = output.resolve()
    if output == repository_root or repository_root in output.parents:
        raise ValueError("export destination must be outside the source repository")
    if output.exists() and any(output.iterdir()):
        raise ValueError("export destination must not exist or must be empty")
    output.mkdir(parents=True, exist_ok=True)

    entries = collect_export_entries(repository_root)
    for entry in entries:
        destination = output / entry.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository_root / entry.source, destination)

    validate_export(output, entries)
    return entries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Empty directory outside the repository to receive the export",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = export_space(args.output)
    print(
        json.dumps(
            {
                "destination": str(args.output.resolve()),
                "file_count": len(entries),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
