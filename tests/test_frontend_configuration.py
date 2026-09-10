from __future__ import annotations

import json
from pathlib import Path

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.frontend import SPA_ROUTES


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_frontend_defaults_off_and_requires_a_nonblank_distribution() -> None:
    defaults = ServiceSettings.from_env({})
    assert defaults.frontend_enabled is False
    assert defaults.frontend_dist_dir == "frontend/dist"

    try:
        ServiceSettings.from_env(
            {
                "OPENWEIGHT_FRONTEND_ENABLED": "true",
                "OPENWEIGHT_FRONTEND_DIST_DIR": " ",
            }
        )
    except ValueError as error:
        assert "OPENWEIGHT_FRONTEND_DIST_DIR" in str(error)
    else:
        raise AssertionError("blank enabled frontend distribution was accepted")


def test_frontend_dependency_tree_is_exact_locked_and_not_a_template() -> None:
    package = json.loads(read("frontend/package.json"))
    lock = json.loads(read("frontend/package-lock.json"))

    assert package["dependencies"] == {
        "lucide-react": "1.39.0",
        "react": "19.2.8",
        "react-dom": "19.2.8",
        "react-router-dom": "7.18.3",
    }
    assert package["devDependencies"]["typescript"] == "6.0.3"
    assert package["devDependencies"]["vite"] == "8.2.2"
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert "streamlit" not in read("frontend/package.json").lower()
    assert "gradio" not in read("frontend/package.json").lower()


def test_spa_routes_are_explicit_and_never_shadow_service_surfaces() -> None:
    assert SPA_ROUTES == (
        "/",
        "/overview",
        "/assistant",
        "/access-requests",
        "/approvals",
        "/system",
    )
    forbidden = {"/mcp", "/v1", "/healthz", "/readyz", "/metrics", "/docs"}
    assert not forbidden.intersection(SPA_ROUTES)
    frontend_server = read("src/openweight_platform/api/frontend.py")
    application = read("src/openweight_platform/api/app.py")
    assert "{path:path}" not in frontend_server
    assert '"/assets/{asset_path:path}"' in frontend_server
    assert "max-age=31536000, immutable" in frontend_server
    assert application.rfind("register_frontend_routes(") < application.rfind(
        'application.mount("/", mcp_asgi_app, name="mcp")'
    )


def test_frontend_client_has_no_persistent_token_or_generic_write_surface() -> None:
    client = read("frontend/src/api/client.ts")
    all_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "frontend/src").rglob("*.ts*"))
    )

    assert "localStorage" not in all_source
    assert "sessionStorage" not in all_source
    assert "execute_sql" not in all_source.lower()
    assert "run_python" not in all_source.lower()
    assert "VITE_JWT" not in all_source
    assert "VITE_DATABASE" not in all_source
    assert "VITE_AZURE_CLIENT_SECRET" not in all_source
    assert "Authorization" in client
    assert "setSessionAccessToken" in client


def test_docker_builds_static_assets_without_node_in_the_runtime_stage() -> None:
    dockerfile = read("Dockerfile")
    builder, runtime = dockerfile.split(
        "FROM python:3.12.11-slim-bookworm AS runtime",
        maxsplit=1,
    )

    assert "node:22.23.2-bookworm-slim" in builder
    assert "npm ci --ignore-scripts" in builder
    assert "ARG VITE_DEMO_MODE=false" in builder
    assert "npm run build" in builder
    assert "COPY --from=frontend-build" in runtime
    assert "npm " not in runtime
    assert "node_modules" not in runtime
    assert "USER openweight" in runtime


def test_frontend_documentation_states_the_security_and_demo_boundaries() -> None:
    documentation = read("docs/frontend.md")

    assert "not an authorization boundary" in documentation
    assert "server still validates" in documentation
    assert "Never place signing keys" in documentation
    assert "module memory" in documentation
    assert "`localStorage`" in documentation
    assert "D17 owns Hugging Face deployment" in documentation
