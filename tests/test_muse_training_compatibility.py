from __future__ import annotations

import importlib.metadata

import pytest


if tuple(
    int(part) for part in importlib.metadata.version("transformers").split(".")[:2]
) < (5, 15):
    pytest.skip(
        "Muse training compatibility requires requirements-training.txt",
        allow_module_level=True,
    )

import torch
from datasets import Dataset
from peft import (
    LoraConfig,
    get_peft_model,
    prepare_model_for_kbit_training,
)
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import WhitespaceSplit
from transformers import (
    MuseGlimmerConfig,
    MuseGlimmerForConditionalGeneration,
    MuseGlimmerTextConfig,
    MuseGlimmerTextModel,
    PreTrainedTokenizerFast,
)
from transformers.models.muse_glimmer.modeling_muse_glimmer import (
    MuseGlimmerTextAttention,
    MuseGlimmerTextDecoderLayer,
    MuseGlimmerTextMLP,
)
from trl import SFTConfig, SFTTrainer
from trl.trainer.sft_trainer import DataCollatorForLanguageModeling

from openweight_platform.training.muse_text import (
    MUSE_TEXT_ATTENTION_LORA_TARGET_PATTERN,
    MUSE_TEXT_LORA_TARGET_PATTERN,
    REAL_ATTENTION_LORA_TARGET_MODULE_COUNT,
    REAL_LORA_TARGET_MODULE_COUNT,
    REAL_RANK_8_ATTENTION_LORA_PARAMETER_COUNT,
    REAL_RANK_8_LORA_PARAMETER_COUNT,
    MuseGlimmerTextForCausalLM,
    atem_decision_completion,
    completion_only_example,
    prepare_muse_text_for_kbit_training,
    remap_full_muse_state_dict,
    selected_lora_module_names,
    tiny_muse_text_config,
)
from openweight_platform.training.muse_checkpoint import (
    EXPECTED_MUSE_REVISION,
    EXPECTED_SHARD_SIZES,
    audit_muse_checkpoint,
)


def _model() -> MuseGlimmerTextForCausalLM:
    torch.manual_seed(7)
    return MuseGlimmerTextForCausalLM(tiny_muse_text_config())


class Params4bit(torch.nn.Parameter):
    """CPU-only test double matching PEFT's class-based k-bit check."""


def _synthetic_nf4_model() -> MuseGlimmerTextForCausalLM:
    model = _model().to(torch.bfloat16)
    for module_name in selected_lora_module_names(model):
        module = model.get_submodule(module_name)
        module.weight = Params4bit(
            module.weight.detach(),
            requires_grad=False,
        )
    model.is_loaded_in_4bit = True
    return model


def _lora_model():
    return get_peft_model(
        _model(),
        LoraConfig(
            r=8,
            lora_alpha=16,
            lora_dropout=0.0,
            target_modules=MUSE_TEXT_LORA_TARGET_PATTERN,
            bias="none",
            task_type="CAUSAL_LM",
        ),
    )


def _synthetic_batch() -> tuple[torch.Tensor, torch.Tensor]:
    input_ids = torch.tensor([[1, 7, 8, 9, 10, 11, 2]])
    labels = input_ids.clone()
    labels[:, :4] = -100
    return input_ids, labels


def _tiny_tokenizer() -> PreTrainedTokenizerFast:
    vocabulary = {
        "<pad>": 0,
        "<unk>": 1,
        "<bos>": 2,
        "<eos>": 3,
        "synthetic": 4,
        "policy": 5,
        "APPROVE": 6,
        "DENY": 7,
        "NEEDS_INFO": 8,
    }
    tokenizer = Tokenizer(WordLevel(vocabulary, unk_token="<unk>"))
    tokenizer.pre_tokenizer = WhitespaceSplit()
    return PreTrainedTokenizerFast(
        tokenizer_object=tokenizer,
        pad_token="<pad>",
        unk_token="<unk>",
        bos_token="<bos>",
        eos_token="<eos>",
    )


def test_pinned_transformers_resolves_all_muse_classes() -> None:
    assert MuseGlimmerConfig.model_type == "muse_glimmer"
    assert MuseGlimmerTextConfig.model_type == "muse_glimmer_text"
    assert MuseGlimmerTextModel.__name__ == "MuseGlimmerTextModel"
    assert MuseGlimmerForConditionalGeneration.__name__.endswith(
        "ConditionalGeneration"
    )
    assert MuseGlimmerTextAttention.__name__.endswith("Attention")
    assert MuseGlimmerTextMLP.__name__.endswith("MLP")
    assert MuseGlimmerTextDecoderLayer.__name__.endswith("DecoderLayer")


def test_tiny_wrapper_has_no_vision_and_runs_loss_backward() -> None:
    model = _model()
    input_ids, labels = _synthetic_batch()

    output = model(input_ids=input_ids, labels=labels, use_cache=False)

    assert output.logits.shape == (1, input_ids.shape[1], 64)
    assert output.loss is not None and torch.isfinite(output.loss)
    output.loss.backward()
    assert model.lm_head.weight.grad is not None
    assert not any("vision" in name for name, _ in model.named_modules())
    assert model.config.tie_word_embeddings is False
    assert model.model.embed_tokens.weight is not model.lm_head.weight

    cached = model(input_ids=input_ids[:, :2], use_cache=True)
    assert cached.past_key_values is not None


def test_state_dict_remapping_skips_only_known_multimodal_keys() -> None:
    model = _model()
    source = {
        "model.language_model.embed_tokens.weight": torch.zeros(64, 32),
        "model.language_model.norm.weight": torch.zeros(32),
        "lm_head.weight": torch.zeros(64, 32),
        "model.vision_tower.synthetic.weight": torch.zeros(1),
        "model.vision_adapter.synthetic.weight": torch.zeros(1),
        "model.vision_projection.weight": torch.zeros(1),
        "model.perception_emb_norm.synthetic": torch.zeros(1),
    }

    result = remap_full_muse_state_dict(source, set(model.state_dict()))

    assert tuple(result.state_dict) == (
        "lm_head.weight",
        "model.embed_tokens.weight",
        "model.norm.weight",
    )
    assert result.skipped_keys == (
        "model.perception_emb_norm.synthetic",
        "model.vision_adapter.synthetic.weight",
        "model.vision_projection.weight",
        "model.vision_tower.synthetic.weight",
    )


def test_state_dict_remapping_rejects_unknown_or_unmapped_keys() -> None:
    model = _model()
    with pytest.raises(ValueError, match="Unexpected Muse checkpoint key"):
        remap_full_muse_state_dict(
            {"unreviewed.weight": torch.zeros(1)},
            set(model.state_dict()),
        )
    with pytest.raises(ValueError, match="does not map"):
        remap_full_muse_state_dict(
            {"model.language_model.unknown.weight": torch.zeros(1)},
            set(model.state_dict()),
        )


def test_path_aware_rank_8_lora_targets_exactly_eight_modules_per_layer() -> None:
    model = _model()
    names = selected_lora_module_names(model)
    assert len(names) == model.config.num_hidden_layers * 8 == 16
    assert all("embed_tokens" not in name for name in names)
    assert "lm_head" not in names

    peft_model = _lora_model()
    trainable = {
        name: parameter
        for name, parameter in peft_model.named_parameters()
        if parameter.requires_grad
    }
    assert sum(parameter.numel() for parameter in trainable.values()) == 9_216
    assert all("lora_" in name for name in trainable)
    assert REAL_LORA_TARGET_MODULE_COUNT == 52 * 8 == 416
    assert REAL_RANK_8_LORA_PARAMETER_COUNT == 52_396_032
    for name, parameter in peft_model.named_parameters():
        if any(part in name for part in ("embed_tokens", "lm_head", "norm")):
            assert not parameter.requires_grad


def test_attention_only_lora_targets_exactly_five_modules_per_layer() -> None:
    model = _model()
    names = selected_lora_module_names(
        model,
        MUSE_TEXT_ATTENTION_LORA_TARGET_PATTERN,
    )

    assert len(names) == model.config.num_hidden_layers * 5 == 10
    assert all(".self_attn." in name for name in names)
    assert all(".mlp." not in name for name in names)
    assert REAL_ATTENTION_LORA_TARGET_MODULE_COUNT == 52 * 5 == 260
    assert REAL_RANK_8_ATTENTION_LORA_PARAMETER_COUNT == 19_169_280


def test_tiny_lora_optimizer_step_updates_adapter_only() -> None:
    model = _lora_model()
    input_ids, labels = _synthetic_batch()
    adapter_name, adapter_parameter = next(
        (name, parameter)
        for name, parameter in model.named_parameters()
        if "lora_B" in name
    )
    base_name, base_parameter = next(
        (name, parameter)
        for name, parameter in model.named_parameters()
        if name.endswith("embed_tokens.weight")
    )
    adapter_before = adapter_parameter.detach().clone()
    base_before = base_parameter.detach().clone()
    optimizer = torch.optim.AdamW(
        (parameter for parameter in model.parameters() if parameter.requires_grad),
        lr=1e-2,
    )

    loss = model(input_ids=input_ids, labels=labels, use_cache=False).loss
    assert loss is not None and torch.isfinite(loss)
    loss.backward()
    optimizer.step()

    parameters = dict(model.named_parameters())
    assert not torch.equal(adapter_before, parameters[adapter_name])
    assert torch.equal(base_before, parameters[base_name])


def test_kbit_preparation_api_enables_checkpointing_and_input_gradients() -> None:
    model = _model()
    model.is_loaded_in_4bit = True
    prepared = prepare_model_for_kbit_training(
        model,
        use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
    )

    assert prepared.is_gradient_checkpointing
    assert prepared.config.use_cache is False
    input_ids, _ = _synthetic_batch()
    output = prepared(input_ids=input_ids, use_cache=False)
    assert output.logits.requires_grad
    assert all(not parameter.requires_grad for parameter in prepared.parameters())


def test_selective_muse_kbit_preparation_preserves_large_bf16_matrices() -> None:
    model = _synthetic_nf4_model()
    quantized_parameter = model.model.layers[0].self_attn.q_proj.weight

    classification = prepare_muse_text_for_kbit_training(
        model,
        use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
    )

    assert len(classification.quantized_params4bit) == 16
    assert classification.frozen_bf16_embedding == "model.embed_tokens.weight"
    assert classification.frozen_bf16_lm_head == "lm_head.weight"
    assert len(classification.small_non_quantized) == 9
    assert model.model.embed_tokens.weight.dtype == torch.bfloat16
    assert model.lm_head.weight.dtype == torch.bfloat16
    assert model.model.layers[0].input_layernorm.weight.dtype == torch.float32
    assert quantized_parameter.__class__.__name__ == "Params4bit"
    assert quantized_parameter.dtype == torch.bfloat16
    assert model.is_gradient_checkpointing
    assert model.config.use_cache is False
    assert all(not parameter.requires_grad for parameter in model.parameters())


def test_selective_muse_kbit_preparation_supports_lora_backward() -> None:
    model = _synthetic_nf4_model()
    prepare_muse_text_for_kbit_training(
        model,
        use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
    )
    embedding = model.model.embed_tokens.weight
    lm_head = model.lm_head.weight
    norm = model.model.layers[0].input_layernorm.weight
    quantized = model.model.layers[0].self_attn.q_proj.weight
    assert len(selected_lora_module_names(model)) == 16
    model = get_peft_model(
        model,
        LoraConfig(
            r=8,
            lora_alpha=16,
            lora_dropout=0.0,
            target_modules=MUSE_TEXT_LORA_TARGET_PATTERN,
            bias="none",
            task_type="CAUSAL_LM",
        ),
    )
    input_ids, labels = _synthetic_batch()

    output = model(input_ids=input_ids, labels=labels, use_cache=False)
    assert output.loss is not None and torch.isfinite(output.loss)
    output.loss.backward()

    lora_gradients = [
        parameter.grad
        for name, parameter in model.named_parameters()
        if "lora_" in name and parameter.grad is not None
    ]
    assert lora_gradients
    assert all(torch.isfinite(gradient).all() for gradient in lora_gradients)
    assert embedding.grad is None
    assert lm_head.grad is None
    assert norm.grad is None
    assert quantized.grad is None
    assert sum(
        parameter.numel()
        for parameter in model.parameters()
        if parameter.requires_grad
    ) == 9_216


def test_selective_muse_kbit_preparation_enables_reentrant_input_gradients() -> None:
    model = _synthetic_nf4_model()
    prepare_muse_text_for_kbit_training(
        model,
        use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": True},
    )

    embeddings = model.get_input_embeddings()(torch.tensor([[1, 2]]))
    assert embeddings.requires_grad
    assert not model.get_input_embeddings().weight.requires_grad


def test_selective_muse_kbit_preparation_rejects_unknown_parameters() -> None:
    model = _synthetic_nf4_model()
    model.register_parameter(
        "unreviewed_parameter",
        torch.nn.Parameter(torch.ones(1, dtype=torch.bfloat16)),
    )

    with pytest.raises(ValueError, match="Unclassified Muse k-bit parameter"):
        prepare_muse_text_for_kbit_training(model)


def test_trl_preloaded_model_and_explicit_completion_mask(tmp_path) -> None:
    example = completion_only_example([1, 4, 5], [6, 3])
    tokenizer = _tiny_tokenizer()
    model = _model()
    trainer = SFTTrainer(
        model=model,
        args=SFTConfig(
            output_dir=str(tmp_path),
            per_device_train_batch_size=1,
            max_steps=1,
            max_length=8,
            use_cpu=True,
            report_to="none",
            completion_only_loss=True,
            dataset_kwargs={"skip_prepare_dataset": True},
        ),
        train_dataset=Dataset.from_list([example]),
        processing_class=tokenizer,
    )

    batch = trainer.data_collator([example])
    assert trainer.model is model
    assert batch["labels"].tolist() == [[-100, -100, -100, 6, 3]]
    assert batch["attention_mask"].tolist() == [[1, 1, 1, 1, 1]]


def test_atem_target_contains_only_public_final_decision() -> None:
    assert atem_decision_completion(" approve ") == (
        " to=user<|message|>APPROVE<|eot|>"
    )
    assert "analysis" not in atem_decision_completion("DENY")
    assert "reasoning" not in atem_decision_completion("NEEDS_INFO")
    with pytest.raises(ValueError, match="Unsupported policy decision"):
        atem_decision_completion("EXPLAIN")


def test_trl_collator_masks_prompt_without_a_tokenizer() -> None:
    collator = DataCollatorForLanguageModeling(
        pad_token_id=0,
        completion_only_loss=True,
    )
    example = completion_only_example([1, 9, 10], [11, 2])
    batch = collator([example])
    assert batch["labels"].tolist() == [[-100, -100, -100, 11, 2]]


def test_checkpoint_audit_accepts_only_complete_text_and_allowlisted_vision(
    tmp_path,
) -> None:
    snapshot = tmp_path / EXPECTED_MUSE_REVISION
    snapshot.mkdir()
    model = _model()
    weight_map = {}
    for target_key in model.state_dict():
        source_key = (
            "model.language_model." + target_key.removeprefix("model.")
            if target_key.startswith("model.")
            else target_key
        )
        weight_map[source_key] = "model-00001-of-00002.safetensors"
    weight_map["model.language_model.norm.weight"] = (
        "model-00002-of-00002.safetensors"
    )
    weight_map["model.vision_tower.fake.weight"] = (
        "model-00001-of-00002.safetensors"
    )
    (snapshot / "model.safetensors.index.json").write_text(
        __import__("json").dumps({"weight_map": weight_map}),
        encoding="utf-8",
    )
    for filename, size in EXPECTED_SHARD_SIZES.items():
        with (snapshot / filename).open("wb") as shard:
            shard.truncate(size)
    original = MuseGlimmerConfig.from_dict(
        {"text_config": tiny_muse_text_config().to_dict()}
    )
    original.save_pretrained(snapshot)

    audit = audit_muse_checkpoint(snapshot)

    assert audit.text_tensor_count == len(model.state_dict()) - 1
    assert audit.lm_head_tensor_count == 1
    assert audit.skipped_multimodal_tensor_count == 1
    assert audit.required_text_shards == tuple(sorted(EXPECTED_SHARD_SIZES))


def test_checkpoint_audit_rejects_unknown_tensor(tmp_path) -> None:
    snapshot = tmp_path / EXPECTED_MUSE_REVISION
    snapshot.mkdir()
    model = _model()
    weight_map = {}
    for target_key in model.state_dict():
        source_key = (
            "model.language_model." + target_key.removeprefix("model.")
            if target_key.startswith("model.")
            else target_key
        )
        weight_map[source_key] = "model-00001-of-00002.safetensors"
    weight_map["model.language_model.norm.weight"] = (
        "model-00002-of-00002.safetensors"
    )
    weight_map["unreviewed.weight"] = "model-00001-of-00002.safetensors"
    (snapshot / "model.safetensors.index.json").write_text(
        __import__("json").dumps({"weight_map": weight_map}),
        encoding="utf-8",
    )
    for filename, size in EXPECTED_SHARD_SIZES.items():
        with (snapshot / filename).open("wb") as shard:
            shard.truncate(size)
    MuseGlimmerConfig.from_dict(
        {"text_config": tiny_muse_text_config().to_dict()}
    ).save_pretrained(snapshot)

    with pytest.raises(ValueError, match="Unexpected Muse checkpoint key"):
        audit_muse_checkpoint(snapshot)
