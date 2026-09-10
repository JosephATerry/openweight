from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "deploy-huggingface.yml"
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def workflow() -> dict[str, object]:
    return yaml.safe_load(read(WORKFLOW_PATH))


def deploy_workflow() -> dict[str, object]:
    return yaml.safe_load(read(DEPLOY_WORKFLOW_PATH))


def all_run_commands(configuration: dict[str, object]) -> str:
    jobs = configuration["jobs"]
    return "\n".join(
        step["run"]
        for job in jobs.values()
        for step in job["steps"]
        if "run" in step
    )


def test_ci_has_expected_triggers_and_least_privilege() -> None:
    configuration = workflow()

    assert set(configuration["on"]) == {
        "push",
        "pull_request",
        "workflow_dispatch",
    }
    assert configuration["on"]["push"]["branches"] == ["main"]
    assert configuration["permissions"] == {"contents": "read"}
    assert set(configuration["jobs"]) == {"quality", "container"}
    assert "write-all" not in read(WORKFLOW_PATH).lower()


def test_ci_uses_one_pinned_python_runtime_and_immutable_actions() -> None:
    configuration = workflow()
    quality = configuration["jobs"]["quality"]
    action_references = [
        step["uses"]
        for job in configuration["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]

    assert any(
        step.get("with", {}).get("python-version") == "3.12.11"
        for step in quality["steps"]
    )
    assert any(
        step.get("with", {}).get("node-version") == "22.23.2"
        for step in quality["steps"]
    )
    assert action_references
    assert all(SHA_PIN.fullmatch(reference) for reference in action_references)
    assert all(
        reference.startswith(
            ("actions/checkout@", "actions/setup-python@", "actions/setup-node@")
        )
        for reference in action_references
    )


def test_quality_job_matches_local_model_free_checks() -> None:
    configuration = workflow()
    commands = all_run_commands(configuration)
    quality = configuration["jobs"]["quality"]

    assert "python -m pytest -q" in commands
    assert "--ignore=tests/test_policy_dataset_v3.py" in commands
    assert "python -m pip check" in commands
    assert "pip-audit --requirement requirements-container.txt" in commands
    assert "detect-secrets-hook --baseline .secrets.baseline" in commands
    assert quality["env"]["HF_HUB_OFFLINE"] == "1"
    assert quality["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert quality["env"]["USE_HUB_KERNELS"] == "0"
    assert quality["env"]["CUDA_VISIBLE_DEVICES"] == ""
    assert "requirements-training" not in commands
    assert "npm ci" in commands
    assert "npm run typecheck" in commands
    assert "npm run lint" in commands
    assert "npm test" in commands
    assert "npm run build" in commands
    assert "npm audit --audit-level=high" in commands
    assert "test ! -e data/evals" in commands
    assert "test ! -e data/training" in commands
    assert "scripts/export_huggingface_space.py" in commands


def test_public_secret_scan_uses_only_sanitized_tracked_files() -> None:
    source = read(WORKFLOW_PATH)

    assert (
        "git ls-files -z | xargs -0 detect-secrets-hook "
        "--baseline .secrets.baseline"
    ) in source
    assert "git rev-list" not in source
    assert "git cat-file" not in source
    assert "git log --all" not in source


def test_container_job_builds_and_smokes_only_safe_endpoints() -> None:
    configuration = workflow()
    container = configuration["jobs"]["container"]
    commands = all_run_commands(configuration)

    assert container["needs"] == "quality"
    assert container["env"]["COMPOSE_PROJECT_NAME"] == (
        "openweight-d11-ci-smoke"
    )
    assert "docker compose config --quiet" in commands
    assert "docker compose build api" in commands
    assert "docker compose up --detach --wait" in commands
    assert "/healthz" in commands
    assert "/readyz" in commands
    assert "/v1/service-info" in commands
    assert "/metrics" in commands
    assert "http://127.0.0.1:18011/" in commands
    assert "http://127.0.0.1:18011/system" in commands
    assert "/v1/agent/query" not in commands
    assert "index_policy_corpus" not in commands
    assert "setup_operations" not in commands
    assert "docker compose down --volumes --remove-orphans" in commands
    cleanup = container["steps"][-1]
    assert cleanup["if"] == "${{ always() }}"


def test_workflow_has_no_deployment_or_long_lived_secret_surface() -> None:
    source = read(WORKFLOW_PATH).lower()
    forbidden = (
        "azure/login",
        "az containerapp",
        "terraform",
        "deploy_huggingface_space",
        "hf upload",
        "docker push",
        "git push",
        "${{ secrets.",
        "train_muse",
        "run_muse_qlora_validation",
        "/v1/agent/query",
    )

    assert not any(value in source for value in forbidden)
    assert "--ignore=tests/test_policy_dataset_v3.py" in source


def test_ci_dependencies_do_not_install_training_or_cuda_stack() -> None:
    requirements = read(ROOT / "requirements-ci.txt")

    assert "-r requirements-container.txt" in requirements
    assert "requirements-training" not in requirements
    assert "pip-audit==2.10.1" in requirements
    assert "detect-secrets==1.5.0" in requirements
    assert "nvidia-" not in requirements.lower()


def test_secret_baseline_is_reviewed_and_excludes_frozen_generated_data() -> None:
    baseline = json.loads(read(ROOT / ".secrets.baseline"))
    findings = [
        finding
        for file_findings in baseline["results"].values()
        for finding in file_findings
    ]
    filters = json.dumps(baseline["filters_used"])
    plugin_names = {plugin["name"] for plugin in baseline["plugins_used"]}

    assert findings
    assert all(finding["is_secret"] is False for finding in findings)
    assert "data/training/" in filters
    assert "data/evals/" in filters
    assert "results/validation/" in filters
    assert "HexHighEntropyString" not in plugin_names
    assert {
        "AWSKeyDetector",
        "AzureStorageKeyDetector",
        "GitHubTokenDetector",
        "PrivateKeyDetector",
        "KeywordDetector",
    }.issubset(plugin_names)


def test_dependabot_is_weekly_and_bounded_for_three_ecosystems() -> None:
    configuration = yaml.safe_load(read(DEPENDABOT_PATH))
    updates = configuration["updates"]

    assert configuration["version"] == 2
    assert {update["package-ecosystem"] for update in updates} == {
        "pip",
        "github-actions",
        "docker",
    }
    assert all(update["schedule"]["interval"] == "weekly" for update in updates)
    assert all(update["open-pull-requests-limit"] == 3 for update in updates)


def test_ci_only_files_stay_out_of_runtime_build_context() -> None:
    patterns = set(read(ROOT / ".dockerignore").splitlines())

    assert ".github/" in patterns
    assert "requirements-ci.txt" in patterns
    assert ".secrets.baseline" in patterns


def test_deployment_runs_only_after_successful_main_push_ci() -> None:
    configuration = deploy_workflow()
    trigger = configuration["on"]["workflow_run"]
    deploy = configuration["jobs"]["deploy"]
    condition = deploy["if"]

    assert trigger == {
        "workflows": ["CI"],
        "types": ["completed"],
        "branches": ["main"],
    }
    assert "conclusion == 'success'" in condition
    assert "event == 'push'" in condition
    assert "head_branch == 'main'" in condition
    assert "head_repository.full_name == github.repository" in condition
    assert "pull_request_target" not in read(DEPLOY_WORKFLOW_PATH)


def test_deployment_has_least_privilege_and_serialized_releases() -> None:
    configuration = deploy_workflow()
    deploy = configuration["jobs"]["deploy"]
    action_references = [
        step["uses"] for step in deploy["steps"] if "uses" in step
    ]

    assert configuration["permissions"] == {"contents": "read"}
    assert configuration["concurrency"] == {
        "group": "hugging-face-production",
        "cancel-in-progress": True,
    }
    assert deploy["environment"]["name"] == "hugging-face-production"
    assert deploy["timeout-minutes"] == 15
    assert action_references
    assert all(SHA_PIN.fullmatch(reference) for reference in action_references)


def test_deployment_secret_is_isolated_to_upload_step() -> None:
    configuration = deploy_workflow()
    deploy = configuration["jobs"]["deploy"]
    secret_steps = [
        step
        for step in deploy["steps"]
        if "HF_SPACE_DEPLOY_TOKEN" in json.dumps(step)
    ]
    source = read(DEPLOY_WORKFLOW_PATH)

    assert len(secret_steps) == 1
    assert secret_steps[0]["name"] == "Deploy export to josephterry/openweight"
    assert source.count("secrets.HF_SPACE_DEPLOY_TOKEN") == 1
    assert "secrets.HF_TOKEN" not in source
    assert "/v1/agent/query" not in source
    assert "josephterry/openweight" in source
