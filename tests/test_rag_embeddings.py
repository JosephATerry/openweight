from langchain_core.embeddings import Embeddings

from openweight_platform.rag.embeddings import (
    DEFAULT_QUERY_INSTRUCTION,
    EMBEDDING_DIMENSION,
    QwenEmbeddings,
)


class FakeEncoder:
    device = "cpu"

    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return [
            [float(index == 0) for index in range(EMBEDDING_DIMENSION)]
            for _ in texts
        ]


def test_embedding_adapter_implements_langchain_interface():
    embeddings = QwenEmbeddings(encoder=FakeEncoder())

    assert isinstance(embeddings, Embeddings)
    assert embeddings.dimension == 1024


def test_documents_are_normalized_without_query_instruction():
    encoder = FakeEncoder()
    embeddings = QwenEmbeddings(encoder=encoder, batch_size=4)

    vectors = embeddings.embed_documents(["first document", "second document"])

    assert len(vectors) == 2
    texts, options = encoder.calls[0]
    assert texts == ["first document", "second document"]
    assert options["normalize_embeddings"] is True
    assert options["batch_size"] == 4
    assert "prompt" not in options
    assert "prompt_name" not in options


def test_query_uses_project_instruction_and_normalization():
    encoder = FakeEncoder()
    embeddings = QwenEmbeddings(encoder=encoder)

    vector = embeddings.embed_query("Who can access production systems?")

    assert len(vector) == EMBEDDING_DIMENSION
    texts, options = encoder.calls[0]
    assert texts == ["Who can access production systems?"]
    assert options["normalize_embeddings"] is True
    assert options["prompt"] == (
        f"Instruct: {DEFAULT_QUERY_INSTRUCTION}\nQuery:"
    )


def test_model_loading_is_lazy_and_uses_selected_device():
    calls = []

    def factory(model_id, device):
        calls.append((model_id, device))
        return FakeEncoder()

    embeddings = QwenEmbeddings(
        model_id="fake/qwen",
        device="cpu",
        encoder_factory=factory,
    )

    assert calls == []

    embeddings.embed_query("policy query")

    assert calls == [("fake/qwen", "cpu")]
