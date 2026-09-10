"""Optional static React delivery without intercepting operational routes."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, Response


SPA_ROUTES = (
    "/",
    "/overview",
    "/assistant",
    "/access-requests",
    "/approvals",
    "/system",
)


def register_frontend_routes(application: FastAPI, dist_dir: str) -> None:
    """Register only known SPA routes so API, MCP, and probes win routing."""

    distribution = Path(dist_dir).expanduser().resolve()
    index = distribution / "index.html"
    assets = distribution / "assets"
    if not index.is_file() or not assets.is_dir():
        raise RuntimeError("Configured frontend distribution is unavailable")
    index_html = index.read_text(encoding="utf-8")
    asset_payloads = {
        path.relative_to(assets).as_posix(): (
            path.read_bytes(),
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )
        for path in assets.rglob("*")
        if path.is_file()
    }
    if not asset_payloads:
        raise RuntimeError("Configured frontend distribution is unavailable")

    @application.get(
        "/assets/{asset_path:path}",
        include_in_schema=False,
        name="frontend-assets",
    )
    async def frontend_asset(asset_path: str) -> Response:
        payload = asset_payloads.get(asset_path)
        if payload is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        content, media_type = payload
        return Response(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    async def frontend_index() -> HTMLResponse:
        return HTMLResponse(
            index_html,
            headers={"Cache-Control": "no-cache"},
        )

    for route in SPA_ROUTES:
        application.add_api_route(
            route,
            frontend_index,
            methods=["GET"],
            include_in_schema=False,
            name=f"frontend-{route.strip('/') or 'root'}",
        )


__all__ = ["SPA_ROUTES", "register_frontend_routes"]
