"""Text-only Muse Glimmer compatibility for single-GPU QLoRA experiments.

The official conditional-generation class always constructs the vision tower.
This small project-owned wrapper retains only ``MuseGlimmerTextModel`` and the
untied language-model head. It mirrors the official logit scaling, softcap,
and causal-LM loss, and is deliberately limited to text-only training.
"""

from __future__ import annotations

import inspect
import re
from collections import OrderedDict
from collections.abc import Mapping, Set
from dataclasses import dataclass

import torch
from torch import nn
from transformers import MuseGlimmerTextConfig, MuseGlimmerTextModel
from transformers.generation import GenerationMixin
from transformers.models.muse_glimmer.modeling_muse_glimmer import (
    MuseGlimmerCausalLMOutputWithPast,
    MuseGlimmerPreTrainedModel,
)


POLICY_DECISIONS = ("APPROVE", "DENY", "NEEDS_INFO")
REAL_LORA_TARGET_MODULE_COUNT = 416
REAL_RANK_8_LORA_PARAMETER_COUNT = 52_396_032
REAL_ATTENTION_LORA_TARGET_MODULE_COUNT = 260
REAL_RANK_8_ATTENTION_LORA_PARAMETER_COUNT = 19_169_280

# PEFT treats a string target_modules value as a full regular expression.
# Requiring the complete path distinguishes the two gate_proj locations and
# prevents accidental matches in embeddings, heads, vision, or future modules.
MUSE_TEXT_LORA_TARGET_PATTERN = (
    r"^model\.layers\.\d+\."
    r"(?:self_attn\.(?:q_proj|k_proj|v_proj|o_proj|gate_proj)"
    r"|mlp\.(?:gate_proj|up_proj|down_proj))$"
)
MUSE_TEXT_ATTENTION_LORA_TARGET_PATTERN = (
    r"^model\.layers\.\d+\."
    r"self_attn\.(?:q_proj|k_proj|v_proj|o_proj|gate_proj)$"
)

_MUSE_TEXT_NORM_PARAMETER_PATTERN = re.compile(
    r"^model\.(?:norm|layers\.\d+\."
    r"(?:input_layernorm|post_attention_layernorm|"
    r"pre_feedforward_layernorm|post_feedforward_layernorm))\.weight$"
)
_MUSE_TEXT_EMBEDDING_PARAMETER = "model.embed_tokens.weight"
_MUSE_TEXT_LM_HEAD_PARAMETER = "lm_head.weight"

_FULL_TEXT_PREFIX = "model.language_model."
_TEXT_ONLY_PREFIX = "model."
_SKIPPABLE_FULL_MODEL_PREFIXES = (
    "model.vision_tower.",
    "model.vision_adapter.",
    "model.vision_projection.",
    "model.perception_emb_norm.",
)


class MuseGlimmerTextForCausalLM(
    MuseGlimmerPreTrainedModel,
    GenerationMixin,
):
    """Text-only causal-LM wrapper for the official Muse text transformer."""

    config_class = MuseGlimmerTextConfig
    accepts_loss_kwargs = False

    def __init__(self, config: MuseGlimmerTextConfig) -> None:
        super().__init__(config)
        self.model = MuseGlimmerTextModel(config)
        self.lm_head = nn.Linear(
            config.hidden_size,
            config.vocab_size,
            bias=False,
        )
        self.post_init()

    def get_input_embeddings(self) -> nn.Module:
        return self.model.get_input_embeddings()

    def set_input_embeddings(self, value: nn.Module) -> None:
        self.model.set_input_embeddings(value)

    def get_output_embeddings(self) -> nn.Module:
        return self.lm_head

    def set_output_embeddings(self, value: nn.Module) -> None:
        self.lm_head = value

    def forward(
        self,
        input_ids: torch.LongTensor | None = None,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.LongTensor | None = None,
        past_key_values=None,
        inputs_embeds: torch.FloatTensor | None = None,
        labels: torch.LongTensor | None = None,
        use_cache: bool | None = None,
        logits_to_keep: int | torch.Tensor = 0,
        **kwargs,
    ) -> MuseGlimmerCausalLMOutputWithPast:
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
            past_key_values=past_key_values,
            inputs_embeds=inputs_embeds,
            use_cache=use_cache,
            **kwargs,
        )
        slice_indices = (
            slice(-logits_to_keep, None)
            if isinstance(logits_to_keep, int)
            else logits_to_keep
        )
        logits = self.lm_head(outputs.last_hidden_state[:, slice_indices, :])
        logits = logits * self.config.output_multiplier
        logits = torch.tanh(
            logits / self.config.final_logit_softcapping
        )
        logits = logits * self.config.final_logit_softcapping

        loss = None
        if labels is not None:
            loss = self.loss_function(
                logits,
                labels,
                self.config.vocab_size,
                **kwargs,
            )

        return MuseGlimmerCausalLMOutputWithPast(
            loss=loss,
            logits=logits,
            past_key_values=outputs.past_key_values,
            hidden_states=outputs.hidden_states,
            attentions=outputs.attentions,
        )


@dataclass(frozen=True)
class RemappedStateDict:
    """Validated text-only checkpoint mapping and explicitly skipped keys."""

    state_dict: OrderedDict[str, torch.Tensor]
    skipped_keys: tuple[str, ...]


@dataclass(frozen=True)
class MuseKbitParameterClassification:
    """Exhaustive parameter classes for selective Muse k-bit preparation."""

    quantized_params4bit: tuple[str, ...]
    frozen_bf16_embedding: str
    frozen_bf16_lm_head: str
    small_non_quantized: tuple[str, ...]


def classify_muse_text_kbit_parameters(
    model: MuseGlimmerTextForCausalLM,
) -> MuseKbitParameterClassification:
    """Classify every text-wrapper parameter or fail on architecture drift."""

    if not getattr(model, "is_loaded_in_4bit", False):
        raise ValueError("Selective Muse preparation requires an NF4 model")

    quantized: list[str] = []
    embedding: list[str] = []
    lm_head: list[str] = []
    small: list[str] = []
    full_projection_pattern = re.compile(MUSE_TEXT_LORA_TARGET_PATTERN)
    for name, parameter in model.named_parameters():
        if parameter.__class__.__name__ == "Params4bit":
            module_name = name.removesuffix(".weight")
            if not full_projection_pattern.fullmatch(module_name):
                raise ValueError(
                    f"Unexpected Muse Params4bit parameter: {name}"
                )
            quantized.append(name)
        elif name == _MUSE_TEXT_EMBEDDING_PARAMETER:
            embedding.append(name)
        elif name == _MUSE_TEXT_LM_HEAD_PARAMETER:
            lm_head.append(name)
        elif _MUSE_TEXT_NORM_PARAMETER_PATTERN.fullmatch(name):
            small.append(name)
        else:
            raise ValueError(f"Unclassified Muse k-bit parameter: {name}")

    expected_quantized = model.config.num_hidden_layers * 8
    expected_small = model.config.num_hidden_layers * 4 + 1
    if len(quantized) != expected_quantized:
        raise ValueError(
            f"Expected {expected_quantized} Params4bit projections, "
            f"got {len(quantized)}"
        )
    if embedding != [_MUSE_TEXT_EMBEDDING_PARAMETER]:
        raise ValueError("Muse input embedding classification is incomplete")
    if lm_head != [_MUSE_TEXT_LM_HEAD_PARAMETER]:
        raise ValueError("Muse lm_head classification is incomplete")
    if len(small) != expected_small:
        raise ValueError(
            f"Expected {expected_small} Muse norm parameters, got {len(small)}"
        )

    parameters = dict(model.named_parameters())
    for name in (*embedding, *lm_head):
        if parameters[name].dtype != torch.bfloat16:
            raise ValueError(f"Muse frozen matrix must be BF16: {name}")
    for name in small:
        if parameters[name].dtype not in {
            torch.float16,
            torch.bfloat16,
            torch.float32,
        }:
            raise ValueError(f"Unsupported Muse norm dtype: {name}")

    return MuseKbitParameterClassification(
        quantized_params4bit=tuple(quantized),
        frozen_bf16_embedding=embedding[0],
        frozen_bf16_lm_head=lm_head[0],
        small_non_quantized=tuple(small),
    )


def prepare_muse_text_for_kbit_training(
    model: MuseGlimmerTextForCausalLM,
    *,
    use_gradient_checkpointing: bool = True,
    gradient_checkpointing_kwargs: dict[str, object] | None = None,
) -> MuseKbitParameterClassification:
    """Prepare text-only Muse QLoRA without upcasting its huge frozen matrices.

    This mirrors the relevant PEFT preparation responsibilities while keeping
    the untied input embedding and language-model head frozen in BF16. Only
    explicitly classified, small Muse norm parameters are promoted to FP32.
    """

    classification = classify_muse_text_kbit_parameters(model)
    parameters = dict(model.named_parameters())
    for parameter in parameters.values():
        parameter.requires_grad = False
    for name in classification.small_non_quantized:
        parameter = parameters[name]
        if parameter.dtype in {torch.float16, torch.bfloat16}:
            parameter.data = parameter.data.to(torch.float32)

    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    checkpointing_kwargs = gradient_checkpointing_kwargs or {}
    if use_gradient_checkpointing:
        use_reentrant = checkpointing_kwargs.get("use_reentrant", True)
        if use_reentrant:
            if hasattr(model, "enable_input_require_grads"):
                model.enable_input_require_grads()
            else:
                model.get_input_embeddings().register_forward_hook(
                    lambda _module, _inputs, output: output.requires_grad_(True)
                )

        supports_kwargs = "gradient_checkpointing_kwargs" in inspect.signature(
            model.gradient_checkpointing_enable
        ).parameters
        if checkpointing_kwargs and not supports_kwargs:
            raise ValueError(
                "Muse gradient checkpointing does not accept keyword options"
            )
        kwargs = (
            {"gradient_checkpointing_kwargs": checkpointing_kwargs}
            if supports_kwargs
            else {}
        )
        model.gradient_checkpointing_enable(**kwargs)

    return classification


def remap_full_muse_state_dict(
    source: Mapping[str, torch.Tensor],
    target_keys: Set[str],
) -> RemappedStateDict:
    """Map official full-model keys to the text-only wrapper.

    Known vision/projector tensors are explicitly skipped. Any other source
    key that cannot be mapped to a real wrapper key is rejected, so checkpoint
    drift cannot silently discard parameters.
    """

    mapped: OrderedDict[str, torch.Tensor] = OrderedDict()
    skipped: list[str] = []
    for source_key in sorted(source):
        target_key = map_full_muse_checkpoint_key(source_key, target_keys)
        if target_key is None:
            skipped.append(source_key)
            continue
        if target_key in mapped:
            raise ValueError(f"Duplicate mapped Muse checkpoint key: {target_key}")
        mapped[target_key] = source[source_key]

    return RemappedStateDict(mapped, tuple(skipped))


def map_full_muse_checkpoint_key(
    source_key: str,
    target_keys: Set[str],
) -> str | None:
    """Map one full-checkpoint key, or return None for allowed vision keys."""

    if source_key.startswith(_FULL_TEXT_PREFIX):
        target_key = _TEXT_ONLY_PREFIX + source_key.removeprefix(
            _FULL_TEXT_PREFIX
        )
    elif source_key.startswith("lm_head."):
        target_key = source_key
    elif source_key.startswith(_SKIPPABLE_FULL_MODEL_PREFIXES):
        return None
    else:
        raise ValueError(f"Unexpected Muse checkpoint key: {source_key}")

    if target_key not in target_keys:
        raise ValueError(
            "Muse checkpoint key does not map to the text-only wrapper: "
            f"{source_key} -> {target_key}"
        )
    return target_key


def tiny_muse_text_config() -> MuseGlimmerTextConfig:
    """Return a random-weight smoke config preserving Muse invariants."""

    return MuseGlimmerTextConfig(
        vocab_size=64,
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=8,
        max_position_embeddings=64,
        sliding_window=16,
        pad_token_id=0,
        bos_token_id=1,
        eos_token_id=2,
        tie_word_embeddings=False,
        use_cache=False,
    )


def selected_lora_module_names(
    model: nn.Module,
    target_pattern: str = MUSE_TEXT_LORA_TARGET_PATTERN,
) -> tuple[str, ...]:
    """Return precisely the path-approved Muse text projection modules."""

    pattern = re.compile(target_pattern)
    return tuple(
        name
        for name, module in model.named_modules()
        if isinstance(module, nn.Linear) and pattern.fullmatch(name)
    )


def completion_only_example(
    prompt_token_ids: list[int],
    decision_token_ids: list[int],
) -> dict[str, list[int]]:
    """Build deterministic TRL input IDs and a completion-only loss mask."""

    if not prompt_token_ids or not decision_token_ids:
        raise ValueError("Prompt and decision token IDs must both be non-empty")
    return {
        "input_ids": prompt_token_ids + decision_token_ids,
        "completion_mask": (
            [0] * len(prompt_token_ids) + [1] * len(decision_token_ids)
        ),
    }


def atem_decision_completion(decision: str) -> str:
    """Render a final-only Muse ATEM completion with no reasoning target.

    The official chat template already emits ``<|start|>assistant`` at the
    generation boundary. Training supplies only the public final channel and
    decision; it never stores or teaches hidden reasoning content.
    """

    normalized = decision.strip().upper()
    if normalized not in POLICY_DECISIONS:
        raise ValueError(f"Unsupported policy decision: {decision}")
    return f" to=user<|message|>{normalized}<|eot|>"


__all__ = [
    "MUSE_TEXT_ATTENTION_LORA_TARGET_PATTERN",
    "MUSE_TEXT_LORA_TARGET_PATTERN",
    "MuseGlimmerTextForCausalLM",
    "MuseKbitParameterClassification",
    "POLICY_DECISIONS",
    "REAL_ATTENTION_LORA_TARGET_MODULE_COUNT",
    "REAL_LORA_TARGET_MODULE_COUNT",
    "REAL_RANK_8_ATTENTION_LORA_PARAMETER_COUNT",
    "REAL_RANK_8_LORA_PARAMETER_COUNT",
    "RemappedStateDict",
    "atem_decision_completion",
    "completion_only_example",
    "classify_muse_text_kbit_parameters",
    "map_full_muse_checkpoint_key",
    "prepare_muse_text_for_kbit_training",
    "remap_full_muse_state_dict",
    "selected_lora_module_names",
    "tiny_muse_text_config",
]
