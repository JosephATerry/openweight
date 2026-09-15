from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "deploy-huggingface.yml"
AZURE_DEPLOY_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "deploy-azure.yml"
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def workflow() -> dict[str, object]:
    return yaml.safe_load(read(WORKFLOW_PATH))


def deploy_workflow() -> dict[str, object]:
    return yaml.safe_load(read(DEPLOY_WORKFLOW_PATH))


def azure_deploy_workflow() -> dict[str, object]:
    return yaml.safe_load(read(AZURE_DEPLOY_WORKFLOW_PATH))


def all_run_commands(configuration: dict[str, object]) -> str:
    jobs = configuration["jobs"]
    return "\n".join(
        step["run"]
        for job in jobs.values()
        for step in job["steps"]
        if "run" in step
    )


def job_run_commands(job: dict[str, object]) -> str:
    return "\n".join(step["run"] for step in job["steps"] if "run" in step)


def normalized_expression(value: object) -> str:
    return " ".join(str(value).split())


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
    assert "--ignore=tests/test_muse_training_compatibility.py" in commands
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
    assert "--ignore=tests/test_muse_training_compatibility.py" in source


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

    assert configuration["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert configuration["concurrency"] == {
        "group": "hugging-face-production",
        "cancel-in-progress": True,
    }
    assert deploy["environment"]["name"] == "hugging-face-production"
    assert deploy["environment"]["url"] == (
        "https://josephaterry-openweight.hf.space"
    )
    assert deploy["timeout-minutes"] == 15
    assert action_references
    assert all(SHA_PIN.fullmatch(reference) for reference in action_references)


def test_deployment_uses_trusted_publisher_without_a_stored_secret() -> None:
    configuration = deploy_workflow()
    deploy = configuration["jobs"]["deploy"]
    upload_steps = [
        step
        for step in deploy["steps"]
        if step["name"] == "Deploy export to josephaterry/openweight"
    ]
    source = read(DEPLOY_WORKFLOW_PATH)
    obsolete_secret = "HF_SPACE_" "DEPLOY_TOKEN"  # pragma: allowlist secret

    assert len(upload_steps) == 1
    assert upload_steps[0]["env"]["HF_OIDC_RESOURCE"] == (
        "spaces/josephaterry/openweight"
    )
    assert obsolete_secret not in source
    assert "${{ secrets." not in source
    assert "secrets.HF_TOKEN" not in source
    assert "HF_TOKEN" not in source
    assert "/v1/agent/query" not in source
    assert "josephaterry/openweight" in source
    assert DEPLOY_WORKFLOW_PATH.name == "deploy-huggingface.yml"


def test_azure_deployment_is_ci_gated_current_main_and_serialized() -> None:
    configuration = azure_deploy_workflow()
    trigger = configuration["on"]["workflow_run"]
    deploy = configuration["jobs"]["deploy"]
    condition = deploy["if"]
    source = read(AZURE_DEPLOY_WORKFLOW_PATH)

    assert trigger == {
        "workflows": ["CI"],
        "types": ["completed"],
        "branches": ["main"],
    }
    assert "github.event_name == 'workflow_run'" in condition
    assert "AZURE_DEPLOY_ENABLED" not in condition
    assert "AZURE_BUILD_ENABLED" not in condition
    assert "conclusion == 'success'" in condition
    assert "event == 'push'" in condition
    assert "head_repository.full_name == github.repository" in condition
    assert configuration["concurrency"] == {
        "group": "azure-production",
        "cancel-in-progress": False,
    }
    assert deploy["environment"]["name"] == "azure-production"
    assert "git ls-remote" in source
    assert "pull_request_target" not in source


def test_azure_deployment_is_disabled_safely_until_explicitly_enabled() -> None:
    configuration = azure_deploy_workflow()
    deploy = configuration["jobs"]["deploy"]
    condition = deploy["if"]
    source = read(AZURE_DEPLOY_WORKFLOW_PATH)
    authorization = deploy["steps"][0]
    commands = authorization["run"]

    assert authorization["id"] == "authorization"
    assert authorization["name"] == "Authorize the requested Azure operation"
    assert authorization["env"] == {
        "AUTH_ONLY_REQUESTED": "${{ inputs.auth_only }}",
        "BUILD_ONLY_REQUESTED": "${{ inputs.build_only }}",
        "AZURE_BUILD_ENABLED": "${{ vars.AZURE_BUILD_ENABLED }}",
        "AZURE_DEPLOY_ENABLED": "${{ vars.AZURE_DEPLOY_ENABLED }}",
    }
    assert "AZURE_DEPLOY_ENABLED" not in condition
    assert "AZURE_BUILD_ENABLED" not in condition
    assert "workflow_dispatch' && inputs.build_only" in condition
    assert 'authorized=false' in commands
    assert '"$AZURE_BUILD_ENABLED" == "true"' in commands
    assert '"$AZURE_DEPLOY_ENABLED" == "true"' in commands
    assert commands.count("authorized=true") == 2
    assert 'echo "authorized=$authorized" >> "$GITHUB_OUTPUT"' in commands
    assert "authorization gate: CLOSED" in commands
    assert "secrets.AZURE_DEPLOY_ENABLED" not in source
    assert "secrets.AZURE_BUILD_ENABLED" not in source


def test_azure_auth_only_job_is_manual_isolated_and_least_privilege() -> None:
    configuration = azure_deploy_workflow()
    dispatch_inputs = configuration["on"]["workflow_dispatch"]["inputs"]
    auth = configuration["jobs"]["azure-auth-smoke"]
    deploy = configuration["jobs"]["deploy"]
    auth_condition = " ".join(auth["if"].split())
    deploy_condition = " ".join(deploy["if"].split())

    assert set(dispatch_inputs) == {"auth_only", "build_only"}
    assert dispatch_inputs["auth_only"] == {
        "description": (
            "Verify GitHub OIDC authentication to Azure without building or "
            "deploying"
        ),
        "required": True,
        "default": False,
        "type": "boolean",
    }
    assert dispatch_inputs["build_only"]["default"] is False
    assert dispatch_inputs["build_only"]["type"] == "boolean"
    assert auth_condition == (
        "github.event_name == 'workflow_dispatch' && inputs.auth_only && "
        "!inputs.build_only"
    )
    assert "inputs.build_only && !inputs.auth_only" in deploy_condition
    assert "AZURE_BUILD_ENABLED" not in deploy_condition
    assert "AZURE_DEPLOY_ENABLED" not in deploy_condition
    assert "AZURE_BUILD_ENABLED" not in auth_condition
    assert "AZURE_DEPLOY_ENABLED" not in auth_condition
    assert "needs" not in auth
    assert "needs" not in deploy
    assert auth["environment"] == {"name": "azure-production"}
    assert auth["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert auth["timeout-minutes"] == 5


def test_azure_auth_only_job_uses_oidc_and_read_only_arm_verification() -> None:
    configuration = azure_deploy_workflow()
    auth = configuration["jobs"]["azure-auth-smoke"]
    source = read(AZURE_DEPLOY_WORKFLOW_PATH)
    commands = job_run_commands(auth)
    action_steps = [step for step in auth["steps"] if "uses" in step]

    assert len(action_steps) == 1
    login = action_steps[0]
    assert login["uses"] == (
        "azure/login@7ddb5af1ef8758cf1353cf3b42f940aee27ba21c"
    )
    assert login["with"] == {
        "client-id": "${{ vars.AZURE_CLIENT_ID }}",
        "tenant-id": "${{ vars.AZURE_TENANT_ID }}",
        "subscription-id": "${{ vars.AZURE_SUBSCRIPTION_ID }}",
    }
    assert not any(
        step.get("uses", "").startswith("actions/checkout@")
        for step in auth["steps"]
    )
    assert auth["env"] == {
        "AZURE_CORE_OUTPUT": "none",
        "AZURE_SUBSCRIPTION_ID": "${{ vars.AZURE_SUBSCRIPTION_ID }}",
        "AZURE_RESOURCE_GROUP": "${{ vars.AZURE_RESOURCE_GROUP }}",
    }
    assert "az account list --refresh" in commands
    assert "az account show --query id --output tsv" in commands
    assert "az account show --query user.type --output tsv" in commands
    assert 'test "$AZURE_RESOURCE_GROUP" = "rg-owp-demo-cus"' in commands
    assert "read-only Azure Resource Manager verification: PASS" in commands
    assert "OIDC authentication smoke test: PASS" in commands
    assert "${{ secrets." not in source
    assert "AZURE_CLIENT_SECRET" not in source


def test_azure_auth_only_job_has_no_mutation_or_application_execution_surface() -> None:
    auth = azure_deploy_workflow()["jobs"]["azure-auth-smoke"]
    commands = job_run_commands(auth).lower()
    action_references = [
        step["uses"].lower() for step in auth["steps"] if "uses" in step
    ]
    azure_cli_commands = re.findall(r"\baz\s+([a-z-]+\s+[a-z-]+)", commands)
    forbidden = (
        "docker ",
        "buildx",
        "az acr login",
        "az acr build",
        "az acr import",
        "az acr repository delete",
        "docker push",
        "terraform",
        "az containerapp",
        "containerapp job",
        "postgres",
        "psql",
        "migrate",
        "keyvault",
        "role assignment",
        "az group create",
        "az group update",
        "az deployment",
        "gh ",
        "git push",
        "curl ",
    )

    assert not any(value in commands for value in forbidden)
    assert azure_cli_commands == ["account list", "account show", "account show"]
    assert len(auth["steps"]) == 2
    assert action_references == [
        "azure/login@7ddb5af1ef8758cf1353cf3b42f940aee27ba21c"
    ]


def test_azure_build_only_is_separately_gated_model_free_and_non_deploying() -> None:
    configuration = azure_deploy_workflow()
    deploy = configuration["jobs"]["deploy"]
    condition = deploy["if"]
    source = read(AZURE_DEPLOY_WORKFLOW_PATH)
    commands = all_run_commands(configuration)
    normalized_condition = " ".join(condition.split())
    authorization = deploy["steps"][0]
    authorization_commands = authorization["run"]
    release = next(
        step
        for step in deploy["steps"]
        if step["name"] == "Deploy one digest-pinned Container App revision"
    )

    assert (
        "github.event_name == 'workflow_dispatch' && inputs.build_only && "
        "!inputs.auth_only"
    ) in normalized_condition
    assert "AZURE_BUILD_ENABLED" not in normalized_condition
    assert "secrets.AZURE_BUILD_ENABLED" not in source
    assert authorization["env"]["AZURE_BUILD_ENABLED"] == (
        "${{ vars.AZURE_BUILD_ENABLED }}"
    )
    assert '"$AZURE_BUILD_ENABLED" == "true"' in authorization_commands
    assert "github.event_name == 'workflow_run'" in normalized_expression(
        release["if"]
    )
    assert "workflow_dispatch" not in normalized_expression(release["if"])
    assert "preload_demo_embeddings=false" in commands
    assert '--platform linux/amd64' in commands
    assert "IMAGE_REPOSITORY}:${SOURCE_SHA}" in commands
    assert "@${digest}" in commands
    assert "terraform plan" not in commands
    assert "terraform apply" not in commands
    assert "containerapp job start" not in commands
    assert "keyvault secret" not in commands


def test_azure_environment_gate_is_first_and_guards_every_later_step() -> None:
    deploy = azure_deploy_workflow()["jobs"]["deploy"]
    steps = deploy["steps"]
    authorization = steps[0]
    protected_condition = "steps.authorization.outputs.authorized == 'true'"

    assert authorization["id"] == "authorization"
    assert "uses" not in authorization
    assert steps[1]["name"] == "Check out the CI-validated revision"
    for step in steps[1:]:
        assert protected_condition in normalized_expression(step.get("if"))
        assert "vars.AZURE_BUILD_ENABLED" not in json.dumps(step)
        assert "vars.AZURE_DEPLOY_ENABLED" not in json.dumps(step)

    step_names = [step["name"] for step in steps]
    assert step_names.index("Authorize the requested Azure operation") < step_names.index(
        "Authenticate to Azure with GitHub OIDC"
    )
    assert step_names.index("Authorize the requested Azure operation") < step_names.index(
        "Build and push the production image"
    )
    assert step_names.index("Authorize the requested Azure operation") < step_names.index(
        "Deploy one digest-pinned Container App revision"
    )


def test_azure_pre_runner_conditions_use_only_event_input_and_trust_contexts() -> None:
    configuration = azure_deploy_workflow()
    deploy_condition = normalized_expression(configuration["jobs"]["deploy"]["if"])
    auth_condition = normalized_expression(
        configuration["jobs"]["azure-auth-smoke"]["if"]
    )

    assert "vars." not in deploy_condition
    assert "secrets." not in deploy_condition
    assert "github.event_name == 'workflow_dispatch'" in deploy_condition
    assert "inputs.build_only && !inputs.auth_only" in deploy_condition
    assert "github.event_name == 'workflow_run'" in deploy_condition
    assert "conclusion == 'success'" in deploy_condition
    assert "event == 'push'" in deploy_condition
    assert "head_branch == 'main'" in deploy_condition
    assert "head_repository.full_name == github.repository" in deploy_condition
    assert auth_condition == (
        "github.event_name == 'workflow_dispatch' && inputs.auth_only && "
        "!inputs.build_only"
    )
    assert "pull_request" not in configuration["on"]


def test_azure_deployment_uses_oidc_digest_and_safe_verification_only() -> None:
    configuration = azure_deploy_workflow()
    deploy = configuration["jobs"]["deploy"]
    source = read(AZURE_DEPLOY_WORKFLOW_PATH)
    commands = all_run_commands(configuration)
    action_references = [
        step["uses"] for step in deploy["steps"] if "uses" in step
    ]

    assert configuration["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert deploy["environment"]["name"] == "azure-production"
    assert all(SHA_PIN.fullmatch(reference) for reference in action_references)
    assert any(reference.startswith("azure/login@") for reference in action_references)
    assert "${{ vars.AZURE_CLIENT_ID }}" in source
    assert "${{ secrets." not in source
    assert "AZURE_CLIENT_SECRET" not in source
    assert "docker push" in commands
    assert "az acr repository show" in commands
    assert "@${digest}" in commands
    assert "az containerapp update" in commands
    assert "/healthz" in commands
    assert "/readyz" in commands
    assert "/v1/agent/query" not in commands
    assert "index_policy_corpus" not in commands
    assert "terraform apply" not in commands


def test_hugging_face_and_azure_deployments_remain_independent() -> None:
    huggingface = read(DEPLOY_WORKFLOW_PATH)
    azure = read(AZURE_DEPLOY_WORKFLOW_PATH)

    assert "azure/login" not in huggingface
    assert "deploy_huggingface_space" not in azure
    assert "HF_OIDC_RESOURCE" not in azure
    assert "HF_TOKEN" not in azure
