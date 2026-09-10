"""Direct text-only Muse NF4 inference for frozen policy validation.

This experimental backend loads the audited Hugging Face text checkpoint in
NF4 and optionally attaches a specifically requested local PEFT adapter. The
explicit base-only control mode verifies that no PEFT or LoRA state is present.
Both modes constrain the raw ATEM completion to the same public policy-decision
contract used by the llama.cpp Muse backend. Private analysis tokens are never
returned to benchmark consumers.
"""

from __future__ import annotations

import gc
import itertools
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from transformers import LogitsProcessor

from openweight_platform.backends.base import GenerationResult, ModelBackend


DEFAULT_MODEL_ALIAS = "meta-models/Muse-Glimmer-30B"
DEFAULT_MAX_NEW_TOKENS = 512
DEFAULT_REASONING_STRENGTH = "low"
POLICY_DECISIONS = ("APPROVE", "DENY", "NEEDS_INFO")


class MuseQloraError(RuntimeError):
    """Sanitized base exception for direct adapter inference."""


class MuseQloraResponseError(MuseQloraError):
    """Raised when generation does not produce a usable final decision."""


class MuseQloraTokenBudgetError(MuseQloraResponseError):
    """Raised only when the directly observed generated count hits the cap."""


@dataclass(frozen=True)
class AtemConstraintTokens:
    """Token-level representation of the policy-decision ATEM grammar."""

    analysis_prefix: tuple[int, ...]
    final_prefix: tuple[int, ...]
    direct_final_prefix: tuple[int, ...]
    restart_prefix: tuple[int, ...]
    decision_sequences: tuple[tuple[int, ...], ...]
    whitespace_ids: frozenset[int]
    control_ids: frozenset[int]
    eom_id: int
    eot_id: int

    @classmethod
    def from_tokenizer(cls, tokenizer: Any) -> AtemConstraintTokens:
        encode = lambda text: tuple(
            tokenizer(text, add_special_tokens=False)["input_ids"]
        )
        added = tokenizer.get_added_vocab()
        control_ids = frozenset(
            token_id
            for token, token_id in added.items()
            if token.startswith("<|") and token.endswith("|>")
        )
        eom = encode("<|eom|>")
        eot = encode("<|eot|>")
        message = encode("<|message|>")
        if len(eom) != 1 or len(eot) != 1 or len(message) != 1:
            raise ValueError("Muse ATEM control tokens must be atomic")

        decisions: set[tuple[int, ...]] = set()
        for decision in POLICY_DECISIONS:
            letter_positions = [
                index for index, value in enumerate(decision) if value.isalpha()
            ]
            for lowercase in itertools.product((False, True), repeat=len(letter_positions)):
                characters = list(decision)
                for index, use_lower in zip(letter_positions, lowercase, strict=True):
                    if use_lower:
                        characters[index] = characters[index].lower()
                decisions.add(encode("".join(characters)))
        if not decisions or any(not sequence for sequence in decisions):
            raise ValueError("Muse decision tokenization is incomplete")

        whitespace_ids = frozenset(
            token_id
            for token_id in range(len(tokenizer))
            if (
                (decoded := tokenizer.decode(
                    [token_id],
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                ))
                and decoded.isspace()
            )
        )
        return cls(
            analysis_prefix=encode(" to=self<|message|>"),
            final_prefix=encode(" to=user<|message|>"),
            direct_final_prefix=message,
            restart_prefix=encode("<|start|>assistant"),
            decision_sequences=tuple(sorted(decisions)),
            whitespace_ids=whitespace_ids,
            control_ids=control_ids,
            eom_id=eom[0],
            eot_id=eot[0],
        )


_State = tuple[str, object]


class AtemPolicyDecisionLogitsProcessor(LogitsProcessor):
    """Constrain one batch-one completion to policy-decision-v1."""

    def __init__(
        self,
        *,
        prompt_length: int,
        tokens: AtemConstraintTokens,
    ) -> None:
        if prompt_length <= 0:
            raise ValueError("prompt_length must be positive")
        self.prompt_length = prompt_length
        self.tokens = tokens

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        if input_ids.shape[0] != 1 or scores.shape[0] != 1:
            raise ValueError("Muse policy constraint requires batch size one")
        generated = tuple(int(value) for value in input_ids[0, self.prompt_length :])
        states = self._states_after(generated)
        if not states:
            raise MuseQloraResponseError("Constrained Muse generation entered an invalid state")
        allowed, content_mode = self._allowed(states)
        original = scores.clone()
        if content_mode:
            forbidden = self.tokens.control_ids - {self.tokens.eom_id}
            if forbidden:
                scores[:, list(forbidden)] = -torch.inf
            if allowed:
                scores[:, list(allowed)] = original[:, list(allowed)]
            return scores
        if not allowed:
            raise MuseQloraResponseError("Constrained Muse generation has no valid next token")
        scores.fill_(-torch.inf)
        scores[:, list(allowed)] = original[:, list(allowed)]
        return scores

    def _states_after(self, generated: tuple[int, ...]) -> set[_State]:
        states = self._assistant_states()
        for token in generated:
            next_states: set[_State] = set()
            for state in states:
                next_states.update(self._transition(state, token))
            states = next_states
            if not states:
                break
        return states

    def _assistant_states(self) -> set[_State]:
        return {
            ("analysis_prefix", 0),
            ("final_prefix", 0),
            ("direct_final_prefix", 0),
        }

    def _transition(self, state: _State, token: int) -> set[_State]:
        kind, value = state
        if kind in {"analysis_prefix", "final_prefix", "direct_final_prefix", "restart_prefix"}:
            sequence = getattr(self.tokens, kind)
            position = int(value)
            if token != sequence[position]:
                return set()
            if position + 1 < len(sequence):
                return {(kind, position + 1)}
            if kind == "analysis_prefix":
                return {("analysis_content", 0)}
            if kind in {"final_prefix", "direct_final_prefix"}:
                return {("decision", ())}
            return self._assistant_states()

        if kind == "analysis_content":
            if token == self.tokens.eom_id:
                return {("restart_prefix", 0)}
            if token in self.tokens.control_ids:
                return set()
            return {state}

        if kind == "decision":
            prefix = tuple(value)
            complete = prefix in self.tokens.decision_sequences
            if complete and token == self.tokens.eot_id:
                return {("done", 0)}
            if complete and token in self.tokens.whitespace_ids:
                return {("after_decision", 0)}
            if not prefix and token in self.tokens.whitespace_ids:
                return {state}
            extended = prefix + (token,)
            if any(sequence[: len(extended)] == extended for sequence in self.tokens.decision_sequences):
                return {("decision", extended)}
            return set()

        if kind == "after_decision":
            if token == self.tokens.eot_id:
                return {("done", 0)}
            if token in self.tokens.whitespace_ids:
                return {state}
            return set()
        return set()

    def _allowed(self, states: set[_State]) -> tuple[set[int], bool]:
        allowed: set[int] = set()
        content_mode = False
        for kind, value in states:
            if kind in {"analysis_prefix", "final_prefix", "direct_final_prefix", "restart_prefix"}:
                allowed.add(getattr(self.tokens, kind)[int(value)])
            elif kind == "analysis_content":
                content_mode = True
                allowed.add(self.tokens.eom_id)
            elif kind == "decision":
                prefix = tuple(value)
                if not prefix:
                    allowed.update(self.tokens.whitespace_ids)
                for sequence in self.tokens.decision_sequences:
                    if sequence[: len(prefix)] == prefix and len(sequence) > len(prefix):
                        allowed.add(sequence[len(prefix)])
                if prefix in self.tokens.decision_sequences:
                    allowed.update(self.tokens.whitespace_ids)
                    allowed.add(self.tokens.eot_id)
            elif kind == "after_decision":
                allowed.update(self.tokens.whitespace_ids)
                allowed.add(self.tokens.eot_id)
            elif kind == "done":
                allowed.add(self.tokens.eot_id)
        return allowed, content_mode


_FINAL_DECISION = re.compile(
    r"(?: to=user)?<\|message\|>\s*(APPROVE|DENY|NEEDS_INFO)\s*<\|eot\|>\Z",
    re.IGNORECASE,
)


class MuseQloraBackend(ModelBackend):
    """Text-only local NF4 Muse backend with explicit adapter/base-only modes."""

    def __init__(
        self,
        *,
        snapshot_path: str | Path,
        adapter_path: str | Path | None,
        base_only: bool = False,
        model_alias: str = DEFAULT_MODEL_ALIAS,
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
        reasoning_strength: str = DEFAULT_REASONING_STRENGTH,
        current_date: str = "2026-08-24",
        knowledge_cutoff: str = "2026-01-04",
        seed: int = 3407,
    ) -> None:
        if max_new_tokens != DEFAULT_MAX_NEW_TOKENS:
            raise ValueError("Frozen D3 max_new_tokens must be 512")
        if reasoning_strength != DEFAULT_REASONING_STRENGTH:
            raise ValueError("Frozen D3 reasoning strength must be low")
        if base_only and adapter_path is not None:
            raise ValueError("Base-only Muse NF4 mode forbids an adapter path")
        if not base_only and adapter_path is None:
            raise ValueError("Adapter-backed Muse NF4 mode requires an adapter path")
        self.snapshot_path = Path(snapshot_path).resolve()
        self.adapter_path = (
            None if adapter_path is None else Path(adapter_path).resolve()
        )
        self.base_only = base_only
        self.model_alias = model_alias
        self.max_new_tokens = max_new_tokens
        self.reasoning_strength = reasoning_strength
        self.current_date = current_date
        self.knowledge_cutoff = knowledge_cutoff
        self.seed = seed
        self.model: Any | None = None
        self.tokenizer: Any | None = None
        self.constraint_tokens: AtemConstraintTokens | None = None
        self.adapter_evidence: dict[str, object] | None = None
        self.generation_diagnostics: list[dict[str, object]] = []
        self.peak_torch_allocated_bytes = 0
        self.peak_torch_reserved_bytes = 0

    @property
    def model_name(self) -> str:
        if self.base_only:
            return f"{self.model_alias} [HF/NF4 base-only]"
        return f"{self.model_alias} + muse-policy-qlora-v1"

    def load(self) -> None:
        if self.model is not None:
            raise RuntimeError("Muse QLoRA backend is already loaded")
        from transformers import AutoTokenizer

        from openweight_platform.training.muse_checkpoint import (
            audit_muse_checkpoint,
            load_muse_text_nf4,
        )

        audit = audit_muse_checkpoint(self.snapshot_path)
        tokenizer = AutoTokenizer.from_pretrained(
            self.snapshot_path,
            local_files_only=True,
            trust_remote_code=False,
        )
        base_model, loading_info = load_muse_text_nf4(audit)
        base_model.config.use_cache = True
        unexpected = loading_info.get("unexpected_keys", [])
        if self.base_only:
            model = base_model
            for parameter in model.parameters():
                parameter.requires_grad_(False)
            attachment_evidence = verify_base_only_model(model)
        else:
            from peft import PeftModel

            model = PeftModel.from_pretrained(
                base_model,
                self.adapter_path,
                is_trainable=False,
                local_files_only=True,
                autocast_adapter_dtype=False,
            )
            adapter_parameters = [
                (name, parameter)
                for name, parameter in model.named_parameters()
                if ".lora_A." in name or ".lora_B." in name
            ]
            adapter_parameter_count = sum(
                parameter.numel() for _, parameter in adapter_parameters
            )
            if len(adapter_parameters) != 832 or adapter_parameter_count != 52_396_032:
                raise RuntimeError("Loaded Muse adapter structure is inconsistent")
            if any(parameter.requires_grad for parameter in model.parameters()):
                raise RuntimeError("Inference model unexpectedly has trainable parameters")
            active_adapter = model.active_adapter
            if active_adapter != "default":
                raise RuntimeError("Muse QLoRA adapter is not active")
            attachment_evidence = {
                "evaluation_mode": "adapter",
                "adapter_attached": True,
                "active_adapter": active_adapter,
                "adapter_tensor_count": len(adapter_parameters),
                "adapter_parameter_count": adapter_parameter_count,
                "peft_model": True,
            }
        model.eval()

        self.model = model
        self.tokenizer = tokenizer
        self.constraint_tokens = AtemConstraintTokens.from_tokenizer(tokenizer)
        self.adapter_evidence = {
            **attachment_evidence,
            "checkpoint_text_tensors": audit.text_tensor_count,
            "checkpoint_lm_head_tensors": audit.lm_head_tensor_count,
            "checkpoint_skipped_multimodal_tensors": audit.skipped_multimodal_tensor_count,
            "loading_unexpected_key_count": len(unexpected),
        }
        self._update_torch_peaks()

    def generate(self, prompt: str) -> GenerationResult:
        if self.model is None or self.tokenizer is None or self.constraint_tokens is None:
            raise RuntimeError("Muse QLoRA backend must be loaded before generation")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        prompt_text = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
            reasoning_strength=self.reasoning_strength,
            current_date=self.current_date,
            knowledge_cutoff=self.knowledge_cutoff,
        )
        encoded = self.tokenizer(
            prompt_text,
            add_special_tokens=False,
            return_tensors="pt",
        )
        input_ids = encoded["input_ids"].to("cuda:0")
        attention_mask = encoded["attention_mask"].to("cuda:0")
        prompt_length = int(input_ids.shape[1])
        processor = AtemPolicyDecisionLogitsProcessor(
            prompt_length=prompt_length,
            tokens=self.constraint_tokens,
        )
        torch.manual_seed(self.seed)
        torch.cuda.manual_seed_all(self.seed)
        started = time.perf_counter()
        with torch.inference_mode():
            generated = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                use_cache=True,
                logits_processor=[processor],
                eos_token_id=self.constraint_tokens.eot_id,
                pad_token_id=self.tokenizer.pad_token_id,
            )
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        self._update_torch_peaks()
        output_ids = generated[0, prompt_length:]
        output_count = int(output_ids.numel())
        raw_completion = self.tokenizer.decode(
            output_ids,
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )
        match = _FINAL_DECISION.search(raw_completion)
        direct_exhaustion = output_count == self.max_new_tokens and match is None
        self.generation_diagnostics.append(
            {
                "input_tokens": prompt_length,
                "output_tokens": output_count,
                "finish_classification": (
                    "token_budget_exhaustion" if direct_exhaustion else "structured_decision"
                ),
                "direct_token_budget_exhaustion": direct_exhaustion,
            }
        )
        print(
            json.dumps(
                {
                    "stage": "case_generated",
                    "case_index": len(self.generation_diagnostics),
                    "output_tokens": output_count,
                    "generation_seconds": elapsed,
                    "direct_token_budget_exhaustion": direct_exhaustion,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        del generated, output_ids, input_ids, attention_mask, encoded
        if match is None:
            if direct_exhaustion:
                raise MuseQloraTokenBudgetError(
                    "Muse QLoRA generation reached the configured token budget"
                )
            raise MuseQloraResponseError("Muse QLoRA generation produced no final decision")
        decision = match.group(1).upper()
        return GenerationResult(
            text=decision,
            input_tokens=prompt_length,
            output_tokens=output_count,
            generation_seconds=elapsed,
            peak_vram_gib=torch.cuda.max_memory_allocated() / (1024**3),
        )

    def unload(self) -> None:
        self._update_torch_peaks()
        if self.adapter_evidence is not None:
            self.adapter_evidence.update(
                peak_torch_allocated_bytes=self.peak_torch_allocated_bytes,
                peak_torch_reserved_bytes=self.peak_torch_reserved_bytes,
            )
        self.model = None
        self.tokenizer = None
        self.constraint_tokens = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

    def _update_torch_peaks(self) -> None:
        if not torch.cuda.is_available():
            return
        self.peak_torch_allocated_bytes = max(
            self.peak_torch_allocated_bytes,
            int(torch.cuda.max_memory_allocated()),
        )
        self.peak_torch_reserved_bytes = max(
            self.peak_torch_reserved_bytes,
            int(torch.cuda.max_memory_reserved()),
        )


def verify_base_only_model(model: Any) -> dict[str, object]:
    """Fail closed unless a loaded inference model has no PEFT/LoRA state."""

    adapter_parameters = [
        (name, parameter)
        for name, parameter in model.named_parameters()
        if "lora_A." in name or "lora_B." in name
    ]
    peft_modules = [
        name
        for name, module in model.named_modules()
        if type(module).__module__.startswith("peft.")
    ]
    peft_config = getattr(model, "peft_config", None)
    if adapter_parameters or peft_modules or peft_config not in (None, {}):
        raise RuntimeError("Base-only Muse NF4 model contains PEFT/LoRA state")
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise RuntimeError("Base-only inference model has trainable parameters")
    return {
        "evaluation_mode": "base_only",
        "adapter_attached": False,
        "active_adapter": None,
        "adapter_tensor_count": 0,
        "adapter_parameter_count": 0,
        "peft_model": False,
    }


__all__ = [
    "AtemConstraintTokens",
    "AtemPolicyDecisionLogitsProcessor",
    "MuseQloraBackend",
    "MuseQloraError",
    "MuseQloraResponseError",
    "MuseQloraTokenBudgetError",
    "verify_base_only_model",
]
