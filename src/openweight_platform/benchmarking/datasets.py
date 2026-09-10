import json
from dataclasses import dataclass
from pathlib import Path

from openweight_platform.benchmarking.runner import BenchmarkCase


@dataclass(frozen=True)
class PolicyEvaluationCase:
    case_id: str
    category: str
    policy: str
    request: str
    expected_decision: str


def load_policy_cases(
    path: str | Path,
) -> list[PolicyEvaluationCase]:
    cases = []

    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as file:
        for line_number, line in enumerate(
            file,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            data = json.loads(line)

            case = PolicyEvaluationCase(
                case_id=data["case_id"],
                category=data["category"],
                policy=data["policy"],
                request=data["request"],
                expected_decision=data["expected_decision"],
            )

            cases.append(case)

    return cases


def build_benchmark_case(
    case: PolicyEvaluationCase,
) -> BenchmarkCase:
    prompt = f"""You are evaluating an enterprise access request.

POLICY:
{case.policy}

REQUEST:
{case.request}

Return exactly one decision:
APPROVE
DENY
NEEDS_INFO

Decision:"""

    return BenchmarkCase(
        case_id=case.case_id,
        prompt=prompt,
    )
