import gc
import time
from collections.abc import Callable
from threading import Thread
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer

from openweight_platform.backends.base import (
    GenerationResult,
    ModelBackend,
)


class GptOssBackend(ModelBackend):
    def __init__(
        self,
        model_id: str = "openai/gpt-oss-20b",
        max_new_tokens: int = 256,
    ):
        self.model_id = model_id
        self.max_new_tokens = max_new_tokens

        self.model = None
        self.tokenizer = None

    @property
    def model_name(self) -> str:
        return self.model_id

    def load(self) -> None:
        if self.model is not None:
            return

        print(f"Loading {self.model_id}...")

        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            dtype="auto",
            device_map="auto",
        )

        self.model.eval()

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_id,
            local_files_only=True,
        )

        print(f"{self.model_id} loaded.")

    def generate(self, prompt: str) -> GenerationResult:
        return self.generate_stream(prompt, lambda _: None)

    def generate_stream(
        self,
        prompt: str,
        on_text: Callable[[str], None],
    ) -> GenerationResult:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError(
                "The backend must be loaded before generate() is called."
            )

        messages = [
            {
                "role": "system",
                "content": "Reasoning: low",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )

        input_device = self.model.get_input_embeddings().weight.device

        inputs = {
            key: value.to(input_device)
            for key, value in inputs.items()
        }

        input_tokens = inputs["input_ids"].shape[-1]

        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()

        start = time.perf_counter()
        streamer = TextIteratorStreamer(
            self.tokenizer,
            skip_prompt=True,
            skip_special_tokens=False,
        )
        outcome: dict[str, Any] = {}

        def run_generation() -> None:
            try:
                with torch.inference_mode():
                    outcome["outputs"] = self.model.generate(
                        **inputs,
                        max_new_tokens=self.max_new_tokens,
                        do_sample=False,
                        streamer=streamer,
                    )
            except BaseException as error:
                outcome["error"] = error
                streamer.on_finalized_text("", stream_end=True)

        worker = Thread(target=run_generation, daemon=True)
        final_filter = _FinalAnswerStreamFilter()
        worker.start()
        for text in streamer:
            filtered = final_filter.feed(text)
            if filtered:
                on_text(filtered)
        remaining = final_filter.finish()
        if remaining:
            on_text(remaining)
        worker.join()
        if "error" in outcome:
            raise outcome["error"]
        outputs = outcome.get("outputs")
        if outputs is None:
            raise RuntimeError("The model did not return generated output.")

        torch.cuda.synchronize()

        generation_seconds = time.perf_counter() - start

        generated_ids = outputs[0, input_tokens:]
        output_tokens = generated_ids.numel()

        decoded = self.tokenizer.decode(
            generated_ids,
            skip_special_tokens=False,
        )

        text = self._extract_final_answer(decoded)

        peak_vram_gib = (
            torch.cuda.max_memory_allocated() / 1024**3
        )

        return GenerationResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            generation_seconds=generation_seconds,
            peak_vram_gib=peak_vram_gib,
        )

    def unload(self) -> None:
        self.model = None
        self.tokenizer = None

        gc.collect()

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    @staticmethod
    def _extract_final_answer(decoded: str) -> str:
        final_marker = "<|channel|>final<|message|>"

        if final_marker not in decoded:
            raise RuntimeError("The model output did not contain a final channel.")

        final_text = decoded.rsplit(final_marker, 1)[1]

        for end_marker in ("<|return|>", "<|end|>"):
            final_text = final_text.split(end_marker, 1)[0]

        return final_text.strip()


class _FinalAnswerStreamFilter:
    """Release only the GPT-OSS final channel, never analysis or control tokens."""

    _START = "<|channel|>final<|message|>"
    _ENDS = ("<|return|>", "<|end|>")

    def __init__(self) -> None:
        self._buffer = ""
        self._started = False
        self._finished = False

    def feed(self, text: str) -> str:
        if self._finished or not text:
            return ""
        self._buffer += text
        if not self._started:
            marker_index = self._buffer.find(self._START)
            if marker_index < 0:
                self._buffer = self._buffer[-(len(self._START) - 1) :]
                return ""
            self._buffer = self._buffer[marker_index + len(self._START) :]
            self._started = True
        return self._release_safe_text()

    def _release_safe_text(self) -> str:
        end_positions = [
            position
            for marker in self._ENDS
            if (position := self._buffer.find(marker)) >= 0
        ]
        if end_positions:
            position = min(end_positions)
            released = self._buffer[:position]
            self._buffer = ""
            self._finished = True
            return released

        retained = 0
        for marker in self._ENDS:
            for length in range(1, min(len(marker), len(self._buffer)) + 1):
                if self._buffer.endswith(marker[:length]):
                    retained = max(retained, length)
        if retained:
            released = self._buffer[:-retained]
            self._buffer = self._buffer[-retained:]
            return released
        released = self._buffer
        self._buffer = ""
        return released

    def finish(self) -> str:
        if not self._started or self._finished:
            self._buffer = ""
            return ""
        # Retained bytes can only be a possible control-marker prefix. Drop
        # that incomplete suffix instead of exposing protocol syntax.
        self._buffer = ""
        self._finished = True
        return ""
