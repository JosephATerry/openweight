from openweight_platform.backends.base import (
    GenerationResult,
    ModelBackend,
)
from openweight_platform.benchmarking.runner import (
    BenchmarkCase,
    run_benchmark,
)


class FakeBenchmarkBackend(ModelBackend):
    def __init__(self):
        self.loaded = False
        self.load_calls = 0
        self.unload_calls = 0

    @property
    def model_name(self) -> str:
        return "fake-benchmark-model"

    def load(self) -> None:
        self.loaded = True
        self.load_calls += 1

    def generate(self, prompt: str) -> GenerationResult:
        if not self.loaded:
            raise RuntimeError("Model is not loaded.")

        return GenerationResult(
            text=f"Response to: {prompt}",
            input_tokens=20,
            output_tokens=10,
            generation_seconds=2.0,
            peak_vram_gib=1.5,
        )

    def unload(self) -> None:
        self.loaded = False
        self.unload_calls += 1


def test_benchmark_runs_multiple_cases():
    backend = FakeBenchmarkBackend()

    cases = [
        BenchmarkCase(
            case_id="case_001",
            prompt="First question",
        ),
        BenchmarkCase(
            case_id="case_002",
            prompt="Second question",
        ),
    ]

    records = run_benchmark(
        backend=backend,
        cases=cases,
    )

    assert len(records) == 2

    assert records[0].case_id == "case_001"
    assert records[1].case_id == "case_002"

    assert records[0].model_name == "fake-benchmark-model"

    assert records[0].response == "Response to: First question"

    assert records[0].tokens_per_second == 5.0


def test_benchmark_loads_and_unloads_once():
    backend = FakeBenchmarkBackend()

    cases = [
        BenchmarkCase(
            case_id="case_001",
            prompt="First question",
        ),
        BenchmarkCase(
            case_id="case_002",
            prompt="Second question",
        ),
    ]

    run_benchmark(
        backend=backend,
        cases=cases,
    )

    assert backend.load_calls == 1
    assert backend.unload_calls == 1
    assert backend.loaded is False
