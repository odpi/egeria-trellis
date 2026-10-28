"""Tests for POST /api/diagrams/mermaid — server-side mermaid rendering via
the shared Kroki container (diagrams.py). The frontend used to render
mermaid client-side (mermaid.js); this route replaces that with a thin
backend proxy so the browser never needs mermaid.js at all."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app

client = TestClient(app)


class TestRenderMermaidRoute:
    def test_success_returns_svg_body_and_content_type(self):
        fake_response = MagicMock(status_code=200, content=b"<svg>...</svg>")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
            resp = client.post("/api/diagrams/mermaid", json={"source": "graph TD; A-->B;"})

        assert resp.status_code == 200
        assert resp.content == b"<svg>...</svg>"
        assert resp.headers["content-type"] == "image/svg+xml"

    def test_kroki_unreachable_returns_502_not_500(self):
        with patch(
            "httpx.AsyncClient.post",
            new=AsyncMock(side_effect=httpx.ConnectError("Connection refused")),
        ):
            resp = client.post("/api/diagrams/mermaid", json={"source": "graph TD; A-->B;"})

        assert resp.status_code == 502
        assert "Could not reach Kroki" in resp.json()["detail"]

    def test_kroki_error_response_surfaced_as_502(self):
        fake_response = MagicMock(status_code=400, text="invalid diagram syntax")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
            resp = client.post("/api/diagrams/mermaid", json={"source": "not a real diagram"})

        assert resp.status_code == 502
        assert "400" in resp.json()["detail"]
        assert "invalid diagram syntax" in resp.json()["detail"]

    def test_missing_source_field_is_a_422(self):
        resp = client.post("/api/diagrams/mermaid", json={})
        assert resp.status_code == 422

    def test_posts_source_as_plain_text_body_to_kroki_mermaid_svg_endpoint(self):
        fake_response = MagicMock(status_code=200, content=b"<svg/>")
        mock_post = AsyncMock(return_value=fake_response)
        with patch("httpx.AsyncClient.post", new=mock_post):
            client.post("/api/diagrams/mermaid", json={"source": "graph TD; A-->B;"})

        args, kwargs = mock_post.call_args
        assert args[0].endswith("/mermaid/svg")
        assert kwargs["content"] == b"graph TD; A-->B;"
        assert kwargs["headers"]["Content-Type"] == "text/plain"

    def test_uses_configured_kroki_url_and_timeout_not_hardcoded_defaults(self):
        """Confirms a remote-Kroki deployment (KROKI_URL/KROKI_TIMEOUT_SECONDS
        pointed at a different host) is actually respected, not silently
        ignored in favor of a hardcoded localhost/15s default."""
        from resource_explorer.config import KrokiConfig

        fake_response = MagicMock(status_code=200, content=b"<svg/>")
        mock_post = AsyncMock(return_value=fake_response)
        fake_config = MagicMock()
        fake_config.kroki = KrokiConfig(url="http://kroki.example.internal:9000", timeout_seconds=60.0)

        with patch("httpx.AsyncClient.post", new=mock_post), \
             patch("resource_explorer.web.routes.diagrams.get_config", return_value=fake_config):
            client.post("/api/diagrams/mermaid", json={"source": "graph TD; A-->B;"})

        args, _ = mock_post.call_args
        assert args[0] == "http://kroki.example.internal:9000/mermaid/svg"


class TestRenderGraphvizRoute:
    """POST /api/diagrams/graphviz — the relationship-graph rendering's own
    Kroki proxy, added alongside /mermaid rather than reusing it (Graphviz's
    `dot` layout has cluster support Mermaid has no equivalent for). Mirrors
    TestRenderMermaidRoute's cases exactly, including the Kroki-down ->
    502-not-500 behaviour the frontend relies on to show "renderer
    unavailable" instead of a broken image."""

    def test_success_returns_svg_body_and_content_type(self):
        fake_response = MagicMock(status_code=200, content=b"<svg>...</svg>")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
            resp = client.post("/api/diagrams/graphviz", json={"source": "digraph G { A -> B; }"})

        assert resp.status_code == 200
        assert resp.content == b"<svg>...</svg>"
        assert resp.headers["content-type"] == "image/svg+xml"

    def test_kroki_unreachable_returns_502_not_500(self):
        with patch(
            "httpx.AsyncClient.post",
            new=AsyncMock(side_effect=httpx.ConnectError("Connection refused")),
        ):
            resp = client.post("/api/diagrams/graphviz", json={"source": "digraph G { A -> B; }"})

        assert resp.status_code == 502
        assert "Could not reach Kroki" in resp.json()["detail"]

    def test_kroki_error_response_surfaced_as_502(self):
        fake_response = MagicMock(status_code=400, text="invalid dot syntax")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
            resp = client.post("/api/diagrams/graphviz", json={"source": "not a real dot file"})

        assert resp.status_code == 502
        assert "400" in resp.json()["detail"]
        assert "invalid dot syntax" in resp.json()["detail"]

    def test_missing_source_field_is_a_422(self):
        resp = client.post("/api/diagrams/graphviz", json={})
        assert resp.status_code == 422

    def test_posts_source_as_plain_text_body_to_kroki_graphviz_svg_endpoint(self):
        fake_response = MagicMock(status_code=200, content=b"<svg/>")
        mock_post = AsyncMock(return_value=fake_response)
        with patch("httpx.AsyncClient.post", new=mock_post):
            client.post("/api/diagrams/graphviz", json={"source": "digraph G { A -> B; }"})

        args, kwargs = mock_post.call_args
        assert args[0].endswith("/graphviz/svg")
        assert kwargs["content"] == b"digraph G { A -> B; }"
        assert kwargs["headers"]["Content-Type"] == "text/plain"
