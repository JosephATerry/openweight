import pytest

from openweight_platform.backends.base import (
    GenerationResult,
    ModelBackend,
)


class FakeBackend(ModelBackend):
    def __init__(self):
        self.loaded = False

    @property
    def model_name(self) -> str:
        return "fake-model"

    def load(self) -> None:
        self.loaded = True

    def generate(self, prompt: str) -> GenerationResult:
        if not self.loaded:
            raise RuntimeError("Model is not loaded.")

        return GenerationResult(
            text=f"Fake response to: {prompt}",
            input_tokens=10,
            output_tokens=5,
            generation_seconds=0.25,
            peak_vram_gib=0.0,
        )

    def unload(self) -> None:
        self.loaded = False


def test_backend_lifecycle():
    backend = FakeBackend()

    assert backend.model_name == "fake-model"
    assert backend.loaded is False

    backend.load()

    assert backend.loaded is True

    backend.unload()

    assert backend.loaded is False


def test_generate_returns_generation_result():
    backend = FakeBackend()
    backend.load()

    result = backend.generate("hello")

    assert isinstance(result, GenerationResult)
    assert result.text == "Fake response to: hello"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.generation_seconds == 0.25
    assert result.peak_vram_gib == 0.0


def test_generate_before_load_fails():
    backend = FakeBackend()

    with pytest.raises(RuntimeError):
        backend.generate("hello")
