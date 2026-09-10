import time
from dataclasses import dataclass
from typing import Iterable

from openweight_platform.backends.base import ModelBackend


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    prompt: str


@dataclass(frozen=True)
class BenchmarkRecord:
    case_id: str
    model_name: str
    prompt: str
    response: str
    input_tokens: int | None
    output_tokens: int | None
    generation_seconds: float
    tokens_per_second: float | None
    peak_vram_gib: float | None
    generation_error_type: str | None = None
    generation_error_message: str | None = None


_GENERATION_ERROR_MESSAGE = (
    "Backend generation failed before producing a usable response."
)


def run_benchmark(
    backend: ModelBackend,
    cases: Iterable[BenchmarkCase],
) -> list[BenchmarkRecord]:
    records = []

    backend.load()

    try:
        for case in cases:
            started_at = time.perf_counter()
            try:
                result = backend.generate(case.prompt)
            except Exception as error:
                records.append(
                    BenchmarkRecord(
                        case_id=case.case_id,
                        model_name=backend.model_name,
                        prompt=case.prompt,
                        response="",
                        input_tokens=None,
                        output_tokens=None,
                        generation_seconds=(
                            time.perf_counter() - started_at
                        ),
                        tokens_per_second=None,
                        peak_vram_gib=None,
                        generation_error_type=type(error).__name__,
                        generation_error_message=(
                            _GENERATION_ERROR_MESSAGE
                        ),
                    )
                )
                continue

            if result.generation_seconds > 0:
                tokens_per_second = (
                    result.output_tokens
                    / result.generation_seconds
                )
            else:
                tokens_per_second = 0.0

            record = BenchmarkRecord(
                case_id=case.case_id,
                model_name=backend.model_name,
                prompt=case.prompt,
                response=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                generation_seconds=result.generation_seconds,
                tokens_per_second=tokens_per_second,
                peak_vram_gib=result.peak_vram_gib,
            )

            records.append(record)

    finally:
        backend.unload()

    return records
