"""Frozen Muse policy QLoRA training orchestration.

The implementation is intentionally narrow: it accepts only an explicitly
selected committed train/dev corpus, uses completion-only ATEM labels, and
enforces the Stage-C5 model-memory and LoRA configuration. It never reads
policy evaluation data.
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil
import torch


MODEL_ALIAS = "meta-models/Muse-Glimmer-30B"
MODEL_REVISION = "a4e59da52a7bc87ae7251dd5545c0dd437c44b68"
TRAIN_SHA256 = "34571d4293ee6e44e490a2fe7e8ac9599cfc5b3f5f3b8b8567adf3888d1462cb"
DEV_SHA256 = "7e9287c2a625b21e6f71fbe35ebacb3e311b7fdc81db138e3fef85a47c93440e"
CORPUS_VERSION = "muse_policy_qlora_v1"
CORPUS_V2_VERSION = "muse_policy_qlora_v2"
V2_TRAIN_SHA256 = "b40d44b8b5cdde0df5ef2986be0b71fc6e9bded38cbf4acc0bd955b9c4e459b7"
V2_DEV_SHA256 = "0b760b27d40191b028e359506357581f81fcf46c8662fd5e57bfe273c7539972"
RUN_NAME = "muse-policy-qlora-v1-cycle-1"
CYCLE2_RUN_NAME = "muse-policy-qlora-v1-cycle-2"
CYCLE3_RUN_NAME = "muse-policy-qlora-v2-cycle-3"
EXPERIMENT_NAME = "openweight-platform-training"
EXPECTED_TRAIN_EXAMPLES = 1296
EXPECTED_DEV_EXAMPLES = 144
EXPECTED_TARGETS = 416
EXPECTED_TRAINABLE = 52_396_032


@dataclass(frozen=True)
class FrozenTrainingConfig:
    epochs: int = 3
    micro_batch_size: int = 1
    gradient_accumulation_steps: int = 8
    effective_batch_size: int = 8
    max_sequence_length: int = 512
    packing: bool = False
    learning_rate: float = 1e-4
    scheduler: str = "cosine"
    warmup_ratio: float = 0.05
    weight_decay: float = 0.0
    max_gradient_norm: float = 1.0
    optimizer: str = "paged_adamw_8bit"
    seed: int = 3407
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.0
    lora_profile: str = "full-projection"
    preparation_profile: str = "selective-muse"
    gradient_checkpointing: bool = True
    use_reentrant: bool = False
    use_cache: bool = False
    save_steps: int = 50
    save_total_limit: int = 2

    @property
    def optimizer_steps_per_epoch(self) -> int:
        return EXPECTED_TRAIN_EXAMPLES // self.gradient_accumulation_steps

    @property
    def total_optimizer_steps(self) -> int:
        return self.epochs * self.optimizer_steps_per_epoch

    @property
    def warmup_steps(self) -> int:
        return math.ceil(self.total_optimizer_steps * self.warmup_ratio)


def cycle2_training_config() -> FrozenTrainingConfig:
    """Return the preregistered lower-dose Cycle-2 schedule."""
    config = FrozenTrainingConfig(epochs=1, learning_rate=2e-5)
    if config.total_optimizer_steps != 162 or config.warmup_steps != 9:
        raise RuntimeError("Cycle-2 optimizer-step accounting changed")
    return config


def cycle3_training_config() -> FrozenTrainingConfig:
    """Return Cycle 2's frozen lower-dose schedule for corpus-v2 Cycle 3."""
    config = cycle2_training_config()
    if config.total_optimizer_steps != 162 or config.warmup_steps != 9:
        raise RuntimeError("Cycle-3 optimizer-step accounting changed")
    return config


@dataclass
class ResourcePeaks:
    process_rss_bytes: int = 0
    system_used_bytes: int = 0
    swap_used_bytes: int = 0
    physical_gpu_used_mib: int = 0
    physical_gpu_free_mib_minimum: int | None = None


@dataclass
class TrainingState:
    global_step: int = 0
    completed_micro_steps: int = 0
    epoch_loss_sum: float = 0.0
    epoch_micro_count: int = 0
    epoch_metrics: list[dict[str, Any]] = field(default_factory=list)
    best_dev_loss: float | None = None
    best_epoch: int | None = None
    best_checkpoint: str | None = None


class ResourceMonitor:
    """Sample host and physical GPU peaks without retaining process details."""

    def __init__(self, interval_seconds: float = 1.0) -> None:
        self.interval_seconds = interval_seconds
        self.peaks = ResourcePeaks()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self) -> ResourceMonitor:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join()

    def _sample(self) -> None:
        process = psutil.Process()
        while not self._stop.wait(self.interval_seconds):
            self.peaks.process_rss_bytes = max(
                self.peaks.process_rss_bytes, process.memory_info().rss
            )
            self.peaks.system_used_bytes = max(
                self.peaks.system_used_bytes, psutil.virtual_memory().used
            )
            self.peaks.swap_used_bytes = max(
                self.peaks.swap_used_bytes, psutil.swap_memory().used
            )
            try:
                used, free = physical_gpu_memory_mib()
            except Exception:
                continue
            self.peaks.physical_gpu_used_mib = max(
                self.peaks.physical_gpu_used_mib, used
            )
            current_minimum = self.peaks.physical_gpu_free_mib_minimum
            self.peaks.physical_gpu_free_mib_minimum = (
                free if current_minimum is None else min(current_minimum, free)
            )


def validate_frozen_inputs(
    repository_root: str | Path,
    *,
    corpus_version: str = CORPUS_VERSION,
) -> tuple[Path, Path]:
    """Validate exact committed train/dev files without evaluation inputs."""
    root = Path(repository_root).resolve()
    corpus_inputs = {
        CORPUS_VERSION: (TRAIN_SHA256, DEV_SHA256),
        CORPUS_V2_VERSION: (V2_TRAIN_SHA256, V2_DEV_SHA256),
    }
    try:
        train_digest, dev_digest = corpus_inputs[corpus_version]
    except KeyError as error:
        raise ValueError(f"Unsupported frozen training corpus: {corpus_version}") from error
    train = root / f"data/training/{corpus_version}_train.jsonl"
    dev = root / f"data/training/{corpus_version}_dev.jsonl"
    expected = (
        (train, train_digest, EXPECTED_TRAIN_EXAMPLES),
        (dev, dev_digest, EXPECTED_DEV_EXAMPLES),
    )
    for path, digest, count in expected:
        if sha256_file(path) != digest:
            raise ValueError(f"Frozen training input hash mismatch: {path.name}")
        actual_count = sum(
            1 for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if actual_count != count:
            raise ValueError(f"Frozen training input count mismatch: {path.name}")
    return train, dev


def encode_frozen_records(
    path: str | Path,
    tokenizer: Any,
    *,
    expected_count: int,
) -> list[dict[str, torch.Tensor]]:
    """Create completion-only tensors using the verified Muse ATEM boundary."""
    encoded: list[dict[str, torch.Tensor]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        completion = record.get("completion")
        if completion not in {"APPROVE", "DENY", "NEEDS_INFO"}:
            raise ValueError("Frozen corpus has an invalid completion")
        messages = [{"role": "user", "content": record["prompt"]}]
        template_kwargs = {
            "reasoning_strength": "low",
            "current_date": "2026-08-25",
            "knowledge_cutoff": "2026-01-04",
        }
        prompt_text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            **template_kwargs,
        )
        full_text = tokenizer.apply_chat_template(
            messages
            + [{"role": "assistant", "recipient": "user", "content": completion}],
            tokenize=False,
            add_generation_prompt=False,
            **template_kwargs,
        )
        prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
        if full_ids[: len(prompt_ids)] != prompt_ids:
            raise ValueError("Muse prompt/completion token boundary is unstable")
        completion_ids = full_ids[len(prompt_ids) :]
        expected_suffix = f" to=user<|message|>{completion}<|eot|>"
        if tokenizer.decode(completion_ids, skip_special_tokens=False) != expected_suffix:
            raise ValueError("Muse completion is not final-only ATEM output")
        features = {
            "input_ids": prompt_ids + completion_ids,
            "completion_mask": [0] * len(prompt_ids) + [1] * len(completion_ids),
        }
        labels = torch.tensor(
            [
                token if active else -100
                for token, active in zip(
                    features["input_ids"], features["completion_mask"], strict=True
                )
            ],
            dtype=torch.long,
        )
        input_ids = torch.tensor(features["input_ids"], dtype=torch.long)
        if input_ids.numel() > 512:
            raise ValueError("Frozen training example exceeds 512 tokens")
        if torch.any(labels[: len(prompt_ids)] != -100):
            raise ValueError("Prompt token contributes to training loss")
        if torch.any(labels[len(prompt_ids) :] == -100):
            raise ValueError("Completion token is masked")
        encoded.append(
            {
                "input_ids": input_ids,
                "attention_mask": torch.ones_like(input_ids),
                "labels": labels,
            }
        )
    if len(encoded) != expected_count:
        raise ValueError(f"Encoded {len(encoded)} examples, expected {expected_count}")
    return encoded


def deterministic_epoch_order(size: int, epoch_index: int, seed: int) -> list[int]:
    generator = torch.Generator()
    generator.manual_seed(seed + epoch_index)
    return torch.randperm(size, generator=generator).tolist()


def append_metric_event(
    path: str | Path,
    *,
    step: int,
    metrics: dict[str, float],
) -> None:
    safe_keys = {
        "train_loss", "learning_rate", "gradient_norm", "epoch",
        "internal_dev_loss", "gpu_memory_used_mib", "gpu_memory_free_mib",
        "torch_allocated_bytes", "torch_reserved_bytes", "examples_per_second",
        "elapsed_seconds",
    }
    if not metrics.keys() <= safe_keys:
        raise ValueError("Unsafe training metric key")
    if not all(math.isfinite(float(value)) for value in metrics.values()):
        raise ValueError("Training metric is not finite")
    event = {
        "step": step,
        "timestamp_ms": int(time.time() * 1000),
        "metrics": metrics,
    }
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")


def save_resume_checkpoint(
    *,
    output_dir: Path,
    model: Any,
    optimizer: Any,
    scheduler: Any,
    state: TrainingState,
    save_total_limit: int,
) -> Path:
    """Save adapter plus optimizer/scheduler/RNG state and prune older saves."""
    checkpoint = output_dir / "checkpoints" / f"checkpoint-{state.global_step:04d}"
    checkpoint.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(checkpoint, safe_serialization=True)
    torch.save(optimizer.state_dict(), checkpoint / "optimizer.pt")
    torch.save(scheduler.state_dict(), checkpoint / "scheduler.pt")
    torch.save(
        {
            "python": random.getstate(),
            "torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all(),
        },
        checkpoint / "rng_state.pt",
    )
    atomic_json(checkpoint / "training_state.json", asdict(state))
    checkpoints = sorted(
        checkpoint.parent.glob("checkpoint-*"),
        key=lambda path: int(path.name.rsplit("-", 1)[1]),
    )
    for old in checkpoints[:-save_total_limit]:
        shutil.rmtree(old)
    return checkpoint


def latest_resume_checkpoint(output_dir: str | Path) -> Path | None:
    checkpoints = list((Path(output_dir) / "checkpoints").glob("checkpoint-*"))
    if not checkpoints:
        return None
    return max(checkpoints, key=lambda path: int(path.name.rsplit("-", 1)[1]))


def load_training_state(checkpoint: Path) -> TrainingState:
    return TrainingState(
        **json.loads((checkpoint / "training_state.json").read_text(encoding="utf-8"))
    )


def evaluate_loss(model: Any, encoded_dev: list[dict[str, torch.Tensor]]) -> float:
    model.eval()
    total = 0.0
    with torch.inference_mode():
        for example in encoded_dev:
            batch = {key: value.unsqueeze(0).to("cuda:0") for key, value in example.items()}
            output = model(**batch, use_cache=False)
            loss = float(output.loss.detach().float().item())
            if not math.isfinite(loss):
                raise RuntimeError("Internal-dev loss is not finite")
            total += loss
            del output, batch
    model.train()
    return total / len(encoded_dev)


def parameter_sample(parameter: torch.nn.Parameter) -> torch.Tensor:
    flat = parameter.detach().reshape(-1)
    indices = torch.linspace(
        0, flat.numel() - 1, steps=min(8, flat.numel()),
        device=flat.device, dtype=torch.float64,
    ).to(torch.long)
    return flat[indices].clone()


def physical_gpu_memory_mib() -> tuple[int, int]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    used, free = (int(value.strip()) for value in result.stdout.strip().split(","))
    return used, free


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_inventory(directory: str | Path) -> list[dict[str, Any]]:
    root = Path(directory)
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def atomic_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def cleanup_training_objects(*objects: Any) -> None:
    for value in objects:
        del value
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


__all__ = [
    "CORPUS_VERSION", "CORPUS_V2_VERSION", "DEV_SHA256", "V2_DEV_SHA256",
    "V2_TRAIN_SHA256", "EXPERIMENT_NAME", "EXPECTED_DEV_EXAMPLES",
    "EXPECTED_TARGETS", "EXPECTED_TRAINABLE", "EXPECTED_TRAIN_EXAMPLES",
    "CYCLE2_RUN_NAME", "CYCLE3_RUN_NAME", "FrozenTrainingConfig", "MODEL_ALIAS", "MODEL_REVISION",
    "RUN_NAME",
    "ResourceMonitor", "TRAIN_SHA256", "TrainingState", "append_metric_event",
    "artifact_inventory", "atomic_json", "deterministic_epoch_order",
    "encode_frozen_records", "evaluate_loss", "latest_resume_checkpoint",
    "load_training_state", "parameter_sample", "physical_gpu_memory_mib",
    "cycle2_training_config", "cycle3_training_config", "save_resume_checkpoint", "sha256_file",
    "validate_frozen_inputs",
]
