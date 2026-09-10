"""Audited streaming loader for the pinned Muse Glimmer checkpoint."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import torch
from accelerate import init_empty_weights
from transformers import BitsAndBytesConfig, MuseGlimmerConfig

from openweight_platform.training.muse_text import (
    MuseGlimmerTextForCausalLM,
    map_full_muse_checkpoint_key,
)


EXPECTED_MUSE_REVISION = "a4e59da52a7bc87ae7251dd5545c0dd437c44b68"
EXPECTED_SHARD_SIZES = {
    "model-00001-of-00002.safetensors": 49_950_112_952,
    "model-00002-of-00002.safetensors": 9_603_322_320,
}


@dataclass(frozen=True)
class MuseCheckpointAudit:
    snapshot_path: Path
    text_tensor_count: int
    lm_head_tensor_count: int
    skipped_multimodal_tensor_count: int
    shard_tensor_counts: dict[str, int]
    required_text_shards: tuple[str, ...]
    key_mapping: dict[str, str]


def audit_muse_checkpoint(snapshot_path: str | Path) -> MuseCheckpointAudit:
    """Validate the pinned index before any real tensor is materialized."""

    snapshot = Path(snapshot_path).resolve()
    if snapshot.name != EXPECTED_MUSE_REVISION:
        raise ValueError(
            f"Muse snapshot must resolve to revision {EXPECTED_MUSE_REVISION}"
        )
    for filename, expected_size in EXPECTED_SHARD_SIZES.items():
        path = snapshot / filename
        if not path.is_file() or path.stat().st_size != expected_size:
            raise ValueError(f"Muse checkpoint shard failed size check: {filename}")
    cache_root = (
        snapshot.parents[2]
        if snapshot.parent.name == "snapshots"
        else snapshot.parent
    )
    if any(cache_root.rglob("*.incomplete")):
        raise ValueError("Muse Hugging Face cache contains incomplete files")

    full_config = MuseGlimmerConfig.from_pretrained(
        snapshot,
        local_files_only=True,
        trust_remote_code=False,
    )
    with init_empty_weights():
        empty_model = MuseGlimmerTextForCausalLM(full_config.text_config)
    target_keys = set(empty_model.state_dict())
    del empty_model

    index_path = snapshot / "model.safetensors.index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    weight_map = index.get("weight_map")
    if not isinstance(weight_map, dict):
        raise ValueError("Muse safetensors index has no weight_map")

    key_mapping: dict[str, str] = {}
    mapped_targets: set[str] = set()
    skipped = 0
    text_count = 0
    head_count = 0
    text_shards: set[str] = set()
    shard_counts: Counter[str] = Counter()
    for source_key, shard_name in sorted(weight_map.items()):
        shard_counts[shard_name] += 1
        target_key = map_full_muse_checkpoint_key(source_key, target_keys)
        if target_key is None:
            skipped += 1
            continue
        if target_key in mapped_targets:
            raise ValueError(f"Duplicate mapped Muse tensor: {target_key}")
        mapped_targets.add(target_key)
        text_shards.add(shard_name)
        if source_key.startswith("model.language_model."):
            text_count += 1
        else:
            head_count += 1
        if source_key != target_key:
            key_mapping[source_key] = target_key

    missing_targets = target_keys - mapped_targets
    if missing_targets:
        missing = ", ".join(sorted(missing_targets)[:5])
        raise ValueError(f"Muse checkpoint is missing text tensors: {missing}")
    expected_shards = set(EXPECTED_SHARD_SIZES)
    if text_shards != expected_shards:
        raise ValueError("Muse text weights do not require both expected shards")

    return MuseCheckpointAudit(
        snapshot_path=snapshot,
        text_tensor_count=text_count,
        lm_head_tensor_count=head_count,
        skipped_multimodal_tensor_count=skipped,
        shard_tensor_counts=dict(sorted(shard_counts.items())),
        required_text_shards=tuple(sorted(text_shards)),
        key_mapping=key_mapping,
    )


def load_muse_text_nf4(
    audit: MuseCheckpointAudit,
) -> tuple[MuseGlimmerTextForCausalLM, dict[str, object]]:
    """Stream the audited text checkpoint directly into CUDA NF4 modules."""

    full_config = MuseGlimmerConfig.from_pretrained(
        audit.snapshot_path,
        local_files_only=True,
        trust_remote_code=False,
    )
    full_config.text_config.use_cache = False
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model, loading_info = MuseGlimmerTextForCausalLM.from_pretrained(
        audit.snapshot_path,
        config=full_config.text_config,
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
        disable_mmap=False,
        device_map={"": 0},
        dtype=torch.bfloat16,
        quantization_config=quantization,
        key_mapping=audit.key_mapping,
        output_loading_info=True,
    )
    unexpected = set(loading_info["unexpected_keys"])
    if len(unexpected) != audit.skipped_multimodal_tensor_count:
        raise RuntimeError("Quantized load did not skip the audited key set")
    if loading_info["missing_keys"] or loading_info["mismatched_keys"]:
        raise RuntimeError(f"Quantized Muse load was incomplete: {loading_info}")
    return model, loading_info


__all__ = [
    "EXPECTED_MUSE_REVISION",
    "EXPECTED_SHARD_SIZES",
    "MuseCheckpointAudit",
    "audit_muse_checkpoint",
    "load_muse_text_nf4",
]
