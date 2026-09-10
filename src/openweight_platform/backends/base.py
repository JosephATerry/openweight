from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class GenerationResult:
    text: str
    input_tokens: int
    output_tokens: int
    generation_seconds: float
    peak_vram_gib: float


class ModelBackend(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the human-readable name of the model."""

    @abstractmethod
    def load(self) -> None:
        """Load the model and make it ready for inference."""

    @abstractmethod
    def generate(self, prompt: str) -> GenerationResult:
        """Generate a response for one prompt."""

    def generate_stream(
        self,
        prompt: str,
        on_text: Callable[[str], None],
    ) -> GenerationResult:
        """Generate once, optionally exposing employee-safe final-answer text.

        Backends without native streaming preserve compatibility by emitting the
        complete generated answer as one chunk.
        """

        result = self.generate(prompt)
        if result.text:
            on_text(result.text)
        return result

    @abstractmethod
    def unload(self) -> None:
        """Release model resources."""
