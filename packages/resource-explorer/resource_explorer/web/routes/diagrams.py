"""Server-side diagram rendering — proxies to the shared Kroki container so
the browser doesn't need to run mermaid.js (or any future Kroki-supported
diagram language) client-side. See config.py's KrokiConfig for why this has
to be a backend proxy rather than the frontend calling Kroki directly:
resource-explorer isn't on the egeria_network docker network, so it reaches
Kroki via its published host port, which the frontend has no business
knowing about — same reasoning as every other internal-infra call (Postgres,
Egeria) always going through a backend route, never straight from the
browser.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from resource_explorer.config import get_config

router = APIRouter()


class MermaidRenderRequest(BaseModel):
    source: str


class GraphvizRenderRequest(BaseModel):
    source: str


@router.post("/graphviz")
async def render_graphviz(req: GraphvizRenderRequest) -> Response:
    """Renders Graphviz DOT source to SVG via Kroki. Mirrors `render_mermaid`
    below exactly — same shared Kroki container, same raw-SVG response
    shape, same "no fallback, a clear 502" failure behaviour — except it
    posts to Kroki's `/graphviz/svg` endpoint instead of `/mermaid/svg`.

    Added for the relationship-graph rendering (design note "the
    relationship graph, drawn from the real AdventureWorks edges",
    2026-09-28): Graphviz's `dot` layout has cluster support Mermaid has no
    equivalent for, which is why that diagram is emitted as DOT rather than
    reusing the Mermaid path above.
    """
    kroki_cfg = get_config().kroki
    kroki_url = kroki_cfg.url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=kroki_cfg.timeout_seconds) as client:
            resp = await client.post(
                f"{kroki_url}/graphviz/svg",
                content=req.source.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Kroki at {kroki_url}: {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Kroki returned {resp.status_code}: {resp.text[:500]}",
        )

    return Response(content=resp.content, media_type="image/svg+xml")


@router.post("/mermaid")
async def render_mermaid(req: MermaidRenderRequest) -> Response:
    """Renders mermaid diagram source to SVG via Kroki. Returns the raw SVG
    body (image/svg+xml) — the frontend injects it directly into the DOM the
    same way it already did with mermaid.render()'s returned svg string, so
    no response-shape change on the caller's side.

    No fallback if Kroki is unreachable — this raises a clear 502 instead
    (see docs/kroki-diagram-rendering.md for why that's a deliberate choice,
    not an oversight: the alternative is re-vendoring mermaid.js client-side
    just for this failure path, undoing the bundle-size win of moving
    rendering server-side in the first place). The frontend already
    degrades gracefully around that error — one diagram panel shows an
    error message, the rest of the page is unaffected.
    """
    kroki_cfg = get_config().kroki
    kroki_url = kroki_cfg.url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=kroki_cfg.timeout_seconds) as client:
            resp = await client.post(
                f"{kroki_url}/mermaid/svg",
                content=req.source.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Kroki at {kroki_url}: {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Kroki returned {resp.status_code}: {resp.text[:500]}",
        )

    return Response(content=resp.content, media_type="image/svg+xml")
