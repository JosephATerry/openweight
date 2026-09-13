from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def compose() -> dict[str, object]:
    return yaml.safe_load(read("compose.yaml"))


def test_compose_has_only_api_and_postgres_runtime_services() -> None:
    services = compose()["services"]

    assert set(services) == {"api", "postgres"}
    assert "privileged" not in services["api"]
    assert "volumes" not in services["api"]
    assert services["api"]["cap_drop"] == ["ALL"]
    assert services["api"]["security_opt"] == ["no-new-privileges:true"]
    assert services["api"]["depends_on"]["postgres"]["condition"] == (
        "service_healthy"
    )


def test_compose_uses_environment_contract_and_loopback_ports() -> None:
    services = compose()["services"]
    api = services["api"]
    postgres = services["postgres"]

    assert api["environment"]["OPENWEIGHT_API_HOST"] == "0.0.0.0"
    assert api["environment"]["POSTGRES_HOST"] == "postgres"
    assert api["environment"]["POSTGRES_PORT"] == 5432
    assert api["environment"]["OPENWEIGHT_MAX_NEW_TOKENS"] == (
        "${OPENWEIGHT_MAX_NEW_TOKENS:-768}"
    )
    assert api["environment"]["OPENWEIGHT_WEB_ENABLED"] == (
        "${OPENWEIGHT_WEB_ENABLED:-false}"
    )
    assert str(api["ports"][0]).startswith("127.0.0.1:")
    assert str(postgres["ports"][0]).startswith("127.0.0.1:")
    assert "POSTGRES_PASSWORD" in api["environment"]
    assert "POSTGRES_PASSWORD" in postgres["environment"]
    assert "change-me" not in read("compose.yaml")


def test_compose_health_checks_are_non_consequential() -> None:
    services = compose()["services"]
    api_probe = " ".join(services["api"]["healthcheck"]["test"])
    postgres_probe = " ".join(services["postgres"]["healthcheck"]["test"])
    healthcheck_source = read("docker/api/healthcheck.py")

    assert "healthcheck.py" in api_probe
    assert "/healthz" in healthcheck_source
    assert "/readyz" not in healthcheck_source
    assert "/v1/agent/query" not in healthcheck_source
    assert "pg_isready" in postgres_probe


def test_postgres_reuses_existing_init_and_persistent_volume() -> None:
    configuration = compose()
    postgres = configuration["services"]["postgres"]

    assert "postgres_data" in configuration["volumes"]
    assert "postgres_data:/var/lib/postgresql/data" in postgres["volumes"]
    assert (
        "./docker/postgres/init:/docker-entrypoint-initdb.d:ro"
        in postgres["volumes"]
    )
    assert "CREATE EXTENSION IF NOT EXISTS vector" in read(
        "docker/postgres/init/001_enable_vector.sql"
    )


def test_dockerfile_is_non_root_lazy_runtime_image() -> None:
    dockerfile = read("Dockerfile")

    assert dockerfile.startswith(
        "FROM node:22.23.2-bookworm-slim AS frontend-build\n"
    )
    assert "FROM python:3.12.11-slim-bookworm AS runtime" in dockerfile
    assert "frontend/package.json frontend/package-lock.json" in dockerfile
    assert "npm ci --ignore-scripts" in dockerfile
    assert "npm run build" in dockerfile
    assert "/build/frontend/dist ./frontend/dist" in dockerfile
    assert "COPY requirements.txt requirements-container.txt" in dockerfile
    assert "--requirement requirements-container.txt" in dockerfile
    assert "requirements-training" not in dockerfile
    assert "COPY . " not in dockerfile
    assert "USER openweight" in dockerfile
    assert 'CMD ["python", "-m", "openweight_platform.api.run"]' in dockerfile
    assert "openweight_platform.api.run" in dockerfile
    assert "pytest" not in dockerfile
    assert "COPY models" not in dockerfile
    assert "COPY .cache" not in dockerfile
    assert "EXPOSE 8000 7860" in dockerfile
    assert "OPENWEIGHT_PRELOAD_DEMO_EMBEDDINGS=false" in dockerfile
    assert "snapshot_download('Qwen/Qwen3-Embedding-0.6B'" in dockerfile
    assert "allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt']" in dockerfile
    assert "shutil.rmtree(path + '/.cache'" in dockerfile
    assert "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3" in dockerfile
    assert "deploy/huggingface/policy_index.npz" in dockerfile


def test_container_dependency_overlay_requires_cpu_torch() -> None:
    requirements = read("requirements-container.txt")

    assert "-r requirements.txt" in requirements
    assert "https://download.pytorch.org/whl/cpu" in requirements
    assert "torch==2.13.0+cpu" in requirements
    assert "nvidia-" not in requirements.lower()
    assert "cuda-toolkit" not in requirements.lower()


def test_huggingface_space_template_is_docker_only_and_secret_free() -> None:
    template = read("deploy/huggingface/README.template.md")
    environment = read("deploy/huggingface/env.example")

    assert "sdk: docker" in template
    assert "app_port: 7860" in template
    assert "OPENWEIGHT_DEPLOYMENT_PROFILE=huggingface" in environment
    assert "OPENWEIGHT_API_PORT=7860" in environment
    assert "OPENWEIGHT_WEB_ENABLED=false" in environment
    assert "OPENWEIGHT_DATABASE_REQUIRED=false" in environment
    assert "HF_TOKEN=" not in environment
    assert "openai/gpt-oss-20b" in environment
    assert "OPENWEIGHT_HF_INFERENCE_CONCURRENCY_LIMIT=2" in environment
    assert "OPENWEIGHT_HF_RATE_LIMIT_REQUESTS=5" in environment
    assert "OPENWEIGHT_HF_PUBLIC_ORIGIN=" in environment
    assert "*.hf.space" not in environment

    demo_compose = yaml.safe_load(read("deploy/huggingface/compose.yaml"))
    services = demo_compose["services"]
    assert set(services) == {"openweight-space"}
    service = services["openweight-space"]
    assert service["ports"] == ["127.0.0.1:7860:7860"]
    assert service["build"]["args"]["OPENWEIGHT_PRELOAD_DEMO_EMBEDDINGS"] == "true"
    assert service["cap_drop"] == ["ALL"]
    assert "volumes" not in service


def test_dockerignore_excludes_local_secrets_models_and_experiments() -> None:
    patterns = {
        line.strip()
        for line in read(".dockerignore").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert {
        ".git/",
        ".env",
        ".venv/",
        ".venv-train/",
        ".cache/",
        ".huggingface/",
        "meta-models/",
        "outputs/",
        "mlruns/",
        "data/evals/",
        "data/training/",
        "results/",
        "requirements-training.txt",
        "*.safetensors",
        "*.gguf",
    }.issubset(patterns)
    assert "!scripts/setup_operations.py" in patterns
    assert "!scripts/setup_security.py" in patterns
    assert "!scripts/migrate_database.py" in patterns
    assert "!scripts/index_policy_corpus.py" in patterns


def test_container_docs_explain_local_and_durable_approval_modes() -> None:
    documentation = read("docs/containers.md")

    assert "process-local memory checkpoint backend" in documentation
    assert "OPENWEIGHT_CHECKPOINT_BACKEND=postgres" in documentation
    assert "no silent fallback" in documentation
    assert "does not bundle or launch a language model" in documentation
    assert "docker compose down --volumes" in documentation
    assert "destructive" in documentation
