from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from openweight_platform.api.app import create_app
from openweight_platform.api.config import ServiceSettings


class FrontendRuntime:
    backend_alias = "fake"
    inference_state = "not_initialized"

    @staticmethod
    def _record(request_id: str) -> dict[str, object]:
        return {
            "request_id": request_id,
            "subject_type": "employee",
            "subject_id": "fic-emp-001",
            "system_name": "Fictional Finance",
            "requested_role": "reader",
            "approval_status": "pending",
            "requested_start_date": "2026-01-01",
            "requested_end_date": None,
        }

    async def readiness(self):
        return []

    async def list_access_requests(
        self,
        *,
        approval_status: str | None,
        limit: int,
        request_id: str,
    ):
        del request_id
        records = [self._record("fic-req-001")]
        if approval_status:
            records = [
                record
                for record in records
                if record["approval_status"] == approval_status
            ]
        return records[:limit]

    async def lookup_access_request(
        self,
        access_request_id: str,
        *,
        request_id: str,
    ):
        del request_id
        return self._record(access_request_id)

    async def close(self) -> None:
        return None


def settings(**overrides: str) -> ServiceSettings:
    return ServiceSettings.from_env(
        {
            "OPENWEIGHT_DATABASE_REQUIRED": "false",
            "OPENWEIGHT_ENVIRONMENT": "test",
            **overrides,
        }
    )


def request(application, method: str, path: str, **kwargs) -> httpx.Response:
    async def perform() -> httpx.Response:
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

    return asyncio.run(perform())


def get_many(application, paths: list[str]) -> list[httpx.Response]:
    async def perform() -> list[httpx.Response]:
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="http://testserver",
            ) as client:
                responses = []
                for path in paths:
                    try:
                        response = await asyncio.wait_for(
                            client.get(path),
                            timeout=5,
                        )
                    except TimeoutError as error:
                        raise AssertionError(f"request timed out: {path}") from error
                    responses.append(response)
                return responses

    return asyncio.run(perform())


def test_access_request_read_endpoints_are_typed_and_bounded() -> None:
    application = create_app(
        settings=settings(),
        runtime=FrontendRuntime(),
    )

    listed = request(application, "GET", "/v1/access-requests?limit=10")
    detail = request(application, "GET", "/v1/access-requests/fic-req-001")
    invalid = request(application, "GET", "/v1/access-requests?limit=101")

    assert listed.status_code == 200
    assert listed.json()["access_requests"][0] == {
        "request_id": "fic-req-001",
        "subject_type": "employee",
        "subject_id": "fic-emp-001",
        "system_name": "Fictional Finance",
        "requested_role": "reader",
        "approval_status": "pending",
        "requested_start_date": "2026-01-01",
        "requested_end_date": None,
    }
    assert detail.status_code == 200
    assert detail.json()["access_request"]["request_id"] == "fic-req-001"
    assert invalid.status_code == 422


def test_frontend_routes_require_a_complete_distribution(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    application_settings = settings(
        OPENWEIGHT_FRONTEND_ENABLED="true",
        OPENWEIGHT_FRONTEND_DIST_DIR=str(missing),
    )

    try:
        create_app(settings=application_settings, runtime=FrontendRuntime())
    except RuntimeError as error:
        assert str(error) == "Configured frontend distribution is unavailable"
    else:
        raise AssertionError("missing frontend distribution was accepted")


def test_static_spa_routes_do_not_swallow_api_or_mcp(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    assets = distribution / "assets"
    assets.mkdir(parents=True)
    (distribution / "index.html").write_text(
        "<!doctype html><title>OpenWeight UI marker</title>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("export {};", encoding="utf-8")
    application = create_app(
        settings=settings(
            OPENWEIGHT_FRONTEND_ENABLED="true",
            OPENWEIGHT_FRONTEND_DIST_DIR=str(distribution),
        ),
        runtime=FrontendRuntime(),
    )

    root, assistant, asset, service = get_many(
        application,
        ["/", "/assistant", "/assets/app.js", "/v1/service-info"],
    )

    assert root.status_code == 200
    assert assistant.status_code == 200
    assert "OpenWeight UI marker" in root.text
    assert asset.status_code == 200
    assert service.status_code == 200
    assert service.headers["content-type"].startswith("application/json")


def test_openapi_documents_only_bounded_access_request_reads() -> None:
    application = create_app(settings=settings(), runtime=FrontendRuntime())
    schema = request(application, "GET", "/openapi.json").json()

    assert "/v1/access-requests" in schema["paths"]
    assert "/v1/access-requests/{access_request_id}" in schema["paths"]
    rendered = " ".join(schema["paths"])
    assert "sql" not in rendered.lower()
    assert "arbitrary" not in rendered.lower()
