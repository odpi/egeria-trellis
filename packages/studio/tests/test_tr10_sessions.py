"""TR-10: short-lived session JWTs, a refresh that re-checks the sign-in with Egeria, and a sign-out that ends
refresh (sessions.py). Egeria is faked at trellis_studio.egeria.check_signin; the refresh decision in the page
(static/session_refresh.js) runs under plain node."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

import trellis_auth
from trellis_studio import egeria as studio_egeria
from trellis_studio import sessions
from trellis_studio.app import create_app
from trellis_studio.config import StudioSettings

from conftest import SECRET, fake_egeria_token

STATIC = Path(__file__).parent.parent / "trellis_studio" / "static"


def _claims(tok):
    return jwt.decode(tok, SECRET, algorithms=["HS256"])


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def checks(monkeypatch):
    """What check_signin was asked, and what it answers: None (accepted), "refused", or an exception to raise."""
    state = {"asked": [], "answer": None}

    def check(user, settings):
        state["asked"].append(user.get("user_id"))
        if state["answer"] == "refused":
            raise studio_egeria.SignInRefused("401")
        if isinstance(state["answer"], Exception):
            raise state["answer"]
    monkeypatch.setattr(studio_egeria, "check_signin", check)
    return state


# ---- the JWT ------------------------------------------------------------------------------------------

def test_a_session_jwt_lives_minutes_and_carries_sid_and_auth_time(app, session_jwt):
    c = _claims(session_jwt)
    assert c["sid"] and c["auth_time"] <= time.time()
    assert c["exp"] - c["iat"] == 15 * 60                     # STUDIO_JWT_TTL_MINUTES default
    assert "password" not in json.dumps(c).lower()


def test_ttl_is_configurable_and_never_past_the_egeria_token(monkeypatch):
    s = StudioSettings(jwt_secret=SECRET, jwt_ttl_minutes=60)
    app = create_app(s)
    c = _claims(sessions.mint("peterprofile", fake_egeria_token(ttl=600), app.state.auth_config, s))
    assert c["exp"] - c["iat"] <= 600                          # capped by Egeria's own token, not 60 min


def test_a_jwt_studio_did_not_mint_is_refused(client):
    plain = trellis_auth.create_access_token("peterprofile", fake_egeria_token(), client.app.state.auth_config)
    assert client.get("/api/auth/me", headers=_h(plain)).status_code == 401      # no sid
    assert client.get("/api/config", headers=_h(plain)).status_code == 401


def test_sign_in_mints_a_tr10_jwt(client, monkeypatch):
    monkeypatch.setattr(trellis_auth, "login_with_password", lambda u, p, cfg: fake_egeria_token(u))
    tok = client.post("/api/auth/login", json={"username": "peterprofile", "password": "x"}).json()["access_token"]
    assert _claims(tok)["sid"]
    assert client.get("/api/auth/me", headers=_h(tok)).status_code == 200


# ---- refresh ------------------------------------------------------------------------------------------

def test_refresh_re_checks_egeria_as_the_user_and_keeps_the_session(client, session_jwt, checks):
    before = _claims(session_jwt)
    time.sleep(1.1)                                            # a later iat/exp, to the second
    r = client.post("/api/auth/refresh", headers=_h(session_jwt))
    assert r.status_code == 200, r.text
    after = _claims(r.json()["access_token"])
    assert checks["asked"] == ["peterprofile"]
    assert after["sid"] == before["sid"] and after["auth_time"] == before["auth_time"]
    assert after["exp"] > before["exp"] and after["egeria_token"] == before["egeria_token"]


def test_egeria_refusing_the_token_ends_the_session(client, session_jwt, checks):
    checks["answer"] = "refused"
    r = client.post("/api/auth/refresh", headers=_h(session_jwt))
    assert r.status_code == 401 and "no longer accepts" in r.json()["detail"]
    assert client.get("/api/auth/me", headers=_h(session_jwt)).status_code == 401   # signed out here too


def test_egeria_unreachable_is_not_a_refusal(client, session_jwt, checks):
    checks["answer"] = ConnectionError("down")
    r = client.post("/api/auth/refresh", headers=_h(session_jwt))
    assert r.status_code == 503 and "carries on" in r.json()["detail"]
    assert client.get("/api/auth/me", headers=_h(session_jwt)).status_code == 200   # the session runs on


def test_no_refresh_past_the_maximum_age(client, app, checks):
    old = int(time.time()) - 8 * 3600 + 30                    # signed in almost 8 hours ago
    tok = sessions.mint("peterprofile", fake_egeria_token(), app.state.auth_config, app.state.settings,
                        auth_time=old)
    assert _claims(tok)["exp"] <= old + 8 * 3600               # never past the session's limit
    later = jwt.encode({**_claims(tok), "auth_time": old - 60}, SECRET, algorithm="HS256")
    r = client.post("/api/auth/refresh", headers=_h(later))
    assert r.status_code == 401 and "8-hour limit" in r.json()["detail"]
    assert checks["asked"] == []                               # refused before Egeria is asked


def test_no_refresh_once_the_egeria_token_has_expired(client, app, checks):
    c = _claims(sessions.mint("peterprofile", fake_egeria_token(), app.state.auth_config, app.state.settings))
    stale = jwt.encode({**c, "egeria_token": fake_egeria_token(ttl=-5)}, SECRET, algorithm="HS256")
    r = client.post("/api/auth/refresh", headers=_h(stale))
    assert r.status_code == 401 and "Egeria sign-in has expired" in r.json()["detail"]
    assert checks["asked"] == []


def test_refresh_needs_a_live_session(client, checks):
    assert client.post("/api/auth/refresh").status_code == 401
    expired = jwt.encode({"sub": "peterprofile", "user_id": "peterprofile", "egeria_token": fake_egeria_token(),
                          "sid": "s", "auth_time": int(time.time()) - 100,
                          "iat": int(time.time()) - 100, "exp": int(time.time()) - 1}, SECRET, algorithm="HS256")
    assert client.post("/api/auth/refresh", headers=_h(expired)).status_code == 401
    assert checks["asked"] == []


# ---- sign-out -----------------------------------------------------------------------------------------

def test_sign_out_refuses_the_session_everywhere_and_ends_refresh(client, session_jwt, checks):
    assert client.post("/api/auth/logout", headers=_h(session_jwt)).json() == {"status": "ok"}
    for method, path in (("get", "/api/auth/me"), ("get", "/api/config"), ("post", "/api/auth/refresh")):
        assert getattr(client, method)(path, headers=_h(session_jwt)).status_code == 401, path
    assert checks["asked"] == []


def test_sign_out_ends_every_jwt_of_the_session(client, session_jwt, checks):
    refreshed = client.post("/api/auth/refresh", headers=_h(session_jwt)).json()["access_token"]
    client.post("/api/auth/logout", headers=_h(refreshed))
    assert client.get("/api/auth/me", headers=_h(session_jwt)).status_code == 401     # the earlier one too


def test_sign_out_without_a_session_is_ok_and_touches_no_one(client, session_jwt):
    assert client.post("/api/auth/logout").json() == {"status": "ok"}
    assert client.get("/api/auth/me", headers=_h(session_jwt)).status_code == 200


def test_after_sign_out_the_old_jwt_is_refused_once_expired_too(client, app):
    """The read-back advisory: re-sending a signed-out JWT after its exp is refused (here and, by exp, at EA)."""
    c = _claims(sessions.mint("peterprofile", fake_egeria_token(), app.state.auth_config, app.state.settings))
    client.post("/api/auth/logout", headers=_h(jwt.encode(c, SECRET, algorithm="HS256")))
    lapsed = jwt.encode({**c, "exp": int(time.time()) - 1}, SECRET, algorithm="HS256")
    assert client.get("/api/auth/me", headers=_h(lapsed)).status_code == 401


def test_revocations_forget_a_session_once_no_jwt_of_it_can_verify():
    rev = sessions.Revocations()
    rev.revoke("a", time.time() + 60)
    rev.revoke("b", time.time() - 1)
    assert rev.is_revoked("a") and not rev.is_revoked("b") and "b" not in rev._until


# ---- check_signin itself: a real client, no password, a refused token is SignInRefused ----------------

def test_check_signin_asks_egeria_as_the_user_with_no_password(settings, monkeypatch):
    import httpx
    from pyegeria.core._base_server_client import BaseServerClient
    from pyegeria.egeria_tech_client import EgeriaTech
    seen = {}

    async def origin(self):
        return "test platform"
    monkeypatch.setattr(BaseServerClient, "async_get_platform_origin", origin)

    def profile(self, *a, **k):
        seen["user"], seen["pwd"] = self.user_id, self.user_pwd
        raise _unauthorized()
    monkeypatch.setattr(EgeriaTech, "get_my_profile", profile, raising=False)
    user = {"sub": "peterprofile", "user_id": "peterprofile", "egeria_token": fake_egeria_token()}
    with pytest.raises(studio_egeria.SignInRefused):
        studio_egeria.check_signin(user, settings)
    assert seen == {"user": "peterprofile", "pwd": None}

    monkeypatch.setattr(EgeriaTech, "get_my_profile", lambda self, *a, **k: {"userId": "peterprofile"}, raising=False)
    studio_egeria.check_signin(user, settings)                 # accepted: returns

    def down(self, *a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(EgeriaTech, "get_my_profile", down, raising=False)
    with pytest.raises(httpx.ConnectError):                    # couldn't ask: not a refusal
        studio_egeria.check_signin(user, settings)


def _unauthorized():
    import httpx
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    return PyegeriaUnauthorizedException(httpx.Response(401, request=httpx.Request("GET", "https://e.test/x")),
                                         context={"caller method": "test"})


# ---- the page's refresh decision (plain node, no jsdom) -------------------------------------------------

CASES = [
    # (payload, now, lastActive, expected)
    (None, 1000, 1000, "none"),
    ({"iat": 0, "exp": 900}, 900, 899, "none"),                # expired by its own exp
    ({"iat": 0, "exp": 900}, 100, 99, "wait"),                 # early in its life
    ({"iat": 0, "exp": 900}, 700, 650, "refresh"),             # in the last 5 min, used since issue
    ({"iat": 0, "exp": 900}, 700, None, "idle"),               # in the last 5 min, untouched: lapses
    ({"iat": 500, "exp": 1400}, 1200, 400, "idle"),            # activity only before this JWT was issued
    ({"iat": 0, "exp": 60}, 25, 10, "wait"),                   # short JWT: leads by 30 s, not a third
    ({"iat": 0, "exp": 60}, 31, 10, "refresh"),
]


def test_the_pages_refresh_decision(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not found")
    mod = tmp_path / "session_refresh.mjs"                     # .mjs: node reads it as the ES module it is
    mod.write_text((STATIC / "session_refresh.js").read_text())
    script = (f"import({json.dumps(mod.as_uri())}).then(m => "
              f"console.log(JSON.stringify({json.dumps([c[:3] for c in CASES])}.map(c => m.refreshDue(...c)))))")
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [c[3] for c in CASES]


def test_the_page_refreshes_signs_out_with_its_jwt_and_ends_on_a_refusal():
    js = (STATIC / "studio_auth.js").read_text()
    assert "import { refreshDue } from '/static/session_refresh.js'" in js
    assert "'/api/auth/refresh'" in js and "setToken(data.access_token)" in js
    assert "fetch('/api/auth/logout', { method: 'POST', headers: t ? { Authorization: `Bearer ${t}` }" in js
    assert "r.status === 401 || r.status === 403" in js and "endSession(" in js


def test_ea_swaps_a_refreshed_jwt_of_the_same_session_quietly():
    ea = (Path(__file__).parents[3] / "packages" / "egeria-advisor" / "advisor" / "web" / "static" / "auth.js").read_text()
    assert "before.sid === after.sid && before.sub === after.sub) return true;" in ea
