#!/usr/bin/env python3
"""Build the synthetic-policy Qwen index used by the public demo profile."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from huggingface_hub import snapshot_download

from openweight_platform.rag.embeddings import (
    DEFAULT_EMBEDDING_MODEL_ID,
    QwenEmbeddings,
)
from openweight_platform.rag.portable import (
    DEMO_EMBEDDING_MODEL_REVISION,
    build_portable_policy_index,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deploy/huggingface/policy_index.npz"),
    )
    args = parser.parse_args()
    model_path = snapshot_download(
        DEFAULT_EMBEDDING_MODEL_ID,
        revision=DEMO_EMBEDDING_MODEL_REVISION,
        local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1",
    )
    manifest = build_portable_policy_index(
        args.output,
        embeddings=QwenEmbeddings(model_id=model_path, device="cpu"),
    )
    print(
        f"wrote {manifest['chunk_count']} chunks with "
        f"{manifest['embedding_model_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
