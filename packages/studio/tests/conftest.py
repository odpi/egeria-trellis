"""Studio tests: no network, no Egeria. A fake Egeria token is a well-formed unsigned JWT with an exp."""
from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from trellis_studio.app import create_app
from trellis_studio.config import StudioSettings

SECRET = "test-shared-trellis-secret-0123456789abcdef"
PORTAL_SECRET = "test-portal-secret-0123456789abcdef"


def _b64(d) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def fake_egeria_token(sub="peterprofile", ttl=3600) -> str:
    now = int(time.time())
    return f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64({'iss': 'self', 'sub': sub, 'iat': now, 'exp': now + ttl})}.sig"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("TRELLIS_REQUIRE_LOGIN", "STUDIO_REQUIRE_LOGIN", "TRELLIS_ANONYMOUS_READ", "STUDIO_ANONYMOUS_READ",
              "TRELLIS_PUBLIC_PATHS", "STUDIO_PUBLIC_PATHS", "EGERIA_USER", "EGERIA_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def settings() -> StudioSettings:
    return StudioSettings(jwt_secret=SECRET, portal_secret=PORTAL_SECRET, egeria_platform_url="https://egeria.test:9443",
                          egeria_view_server="qs-view-server", advisor_url="http://localhost:8880/",
                          public_url="http://localhost:8840", portal_origins_raw="http://localhost:8800")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def session_jwt(app):
    from trellis_studio import sessions
    return sessions.mint("peterprofile", fake_egeria_token(), app.state.auth_config, app.state.settings)


@pytest.fixture
def authed(client, session_jwt):
    client.headers.update({"Authorization": f"Bearer {session_jwt}"})
    return client
