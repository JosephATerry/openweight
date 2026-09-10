"""Local LangChain-compatible embeddings for Qwen3 Embedding."""

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.embeddings import Embeddings


DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_DIMENSION = 1024
DEFAULT_QUERY_INSTRUCTION = (
    "Given an enterprise policy question, retrieve relevant policy passages "
    "that provide the rules needed to answer the question"
)


def _available_device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _create_sentence_transformer(model_id: str, device: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_id, device=device)


class QwenEmbeddings(Embeddings):
    """Lazy local adapter for Qwen3 document and query embeddings."""

    def __init__(
        self,
        model_id: str = DEFAULT_EMBEDDING_MODEL_ID,
        *,
        dimension: int = EMBEDDING_DIMENSION,
        query_instruction: str = DEFAULT_QUERY_INSTRUCTION,
        device: str | None = None,
        batch_size: int = 16,
        encoder: Any | None = None,
        encoder_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.model_id = model_id
        self.dimension = dimension
        self.query_instruction = query_instruction
        self.batch_size = batch_size
        self._device = device
        self._encoder = encoder
        self._encoder_factory = encoder_factory

        if encoder is not None and self._device is None:
            self._device = str(getattr(encoder, "device", "cpu"))

    @property
    def device(self) -> str:
        """Return the selected device, resolving it without loading the model."""

        if self._device is None:
            self._device = _available_device()

        return self._device

    @property
    def query_prompt(self) -> str:
        return f"Instruct: {self.query_instruction}\nQuery:"

    def _get_encoder(self):
        if self._encoder is None:
            factory = self._encoder_factory or _create_sentence_transformer
            self._encoder = factory(self.model_id, self.device)

        return self._encoder

    def _encode(
        self,
        texts: Sequence[str],
        *,
        prompt: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []

        encode_options = {
            "batch_size": self.batch_size,
            "normalize_embeddings": True,
            "convert_to_numpy": True,
            "show_progress_bar": False,
        }

        if prompt is not None:
            encode_options["prompt"] = prompt

        encoded = self._get_encoder().encode(
            list(texts),
            **encode_options,
        )
        vectors = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        result = [
            [float(value) for value in vector]
            for vector in vectors
        ]

        for vector in result:
            if len(vector) != self.dimension:
                raise ValueError(
                    f"Expected {self.dimension} embedding dimensions, "
                    f"received {len(vector)}"
                )

        return result

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents without applying a retrieval query instruction."""

        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        """Embed a query with the project-specific Qwen retrieval prompt."""

        return self._encode([text], prompt=self.query_prompt)[0]
