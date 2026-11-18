"""TR-10: short-lived access JWTs; rotating refresh tokens bound to one user and session (a replay revokes the
session); a refresh that re-checks the sign-in with Egeria; a sign-out that ends refresh (sessions.py). One test
per read-back criterion. Egeria is faked at trellis_studio.egeria.check_signin; the refresh decision in the page
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


def _signin(client, monkeypatch, who="peterprofile", egeria_ttl=3600):
    monkeypatch.setattr(trellis_auth, "login_with_password", lambda u, p, cfg: fake_egeria_token(u, ttl=egeria_ttl))
    r = client.post("/api/auth/login", json={"username": who, "password": "x"})
    assert r.status_code == 200, r.text
    return r.json()


def _refresh(client, rt, access=None):
    return client.post("/api/auth/refresh", json={"refresh_token": rt}, headers=_h(access) if access else {})


def test_sign_in_gives_an_access_jwt_and_a_refresh_token(client, monkeypatch):
    s = _signin(client, monkeypatch)
    assert _claims(s["access_token"])["sid"] and s["refresh_token"]
    assert s["refresh_token"] not in json.dumps(_claims(s["access_token"]))       # never inside the JWT
    assert client.get("/api/auth/me", headers=_h(s["access_token"])).status_code == 200
    store = client.app.state.refresh_store
    assert s["refresh_token"] not in json.dumps({k: vars(v) for k, v in store._by_hash.items()})  # hash only


# ---- refresh: bound to the user and session, re-checks Egeria ------------------------------------------

def test_refresh_re_checks_egeria_as_the_user_and_keeps_the_session(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    before = _claims(s["access_token"])
    time.sleep(1.1)                                            # a later iat/exp, to the second
    r = _refresh(client, s["refresh_token"], s["access_token"])
    assert r.status_code == 200, r.text
    after = _claims(r.json()["access_token"])
    assert checks["asked"] == ["peterprofile"]
    assert (after["sub"], after["sid"], after["auth_time"]) == (before["sub"], before["sid"], before["auth_time"])
    assert after["exp"] > before["exp"] and after["egeria_token"] == before["egeria_token"]


def test_refresh_tokens_rotate_on_use(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    r1 = _refresh(client, s["refresh_token"]).json()
    assert r1["refresh_token"] != s["refresh_token"]
    r2 = _refresh(client, r1["refresh_token"])                 # the new one works, once
    assert r2.status_code == 200 and r2.json()["refresh_token"] not in (s["refresh_token"], r1["refresh_token"])


def test_reusing_an_old_refresh_token_revokes_the_whole_session(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    r1 = _refresh(client, s["refresh_token"]).json()
    replay = _refresh(client, s["refresh_token"])               # the first one, again
    assert replay.status_code == 401 and "already renewed" in replay.json()["detail"]
    assert _refresh(client, r1["refresh_token"]).status_code == 401              # its successor too
    for tok in (s["access_token"], r1["access_token"]):          # and every access token of the session
        assert client.get("/api/auth/me", headers=_h(tok)).status_code == 401


def test_identity_never_changes_across_a_refresh(client, monkeypatch, checks):
    peter = _signin(client, monkeypatch, "peterprofile")
    erin = _signin(client, monkeypatch, "erinoverview")
    # The new access token is the refresh token's own user's, whatever the request says...
    ok = _refresh(client, peter["refresh_token"]).json()
    assert _claims(ok["access_token"])["sub"] == "peterprofile"
    # ...and presenting someone else's access token with it is refused and revokes the session.
    r = _refresh(client, ok["refresh_token"], erin["access_token"])
    assert r.status_code == 401 and "didn't match" in r.json()["detail"]
    assert client.get("/api/auth/me", headers=_h(ok["access_token"])).status_code == 401
    assert client.get("/api/auth/me", headers=_h(erin["access_token"])).status_code == 200  # erin's untouched


def test_egeria_refusing_the_token_means_no_refresh_and_401(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    checks["answer"] = "refused"
    r = _refresh(client, s["refresh_token"])
    assert r.status_code == 401 and "no longer accepts" in r.json()["detail"]
    assert client.get("/api/auth/me", headers=_h(s["access_token"])).status_code == 401   # signed out here too
    checks["answer"] = None
    assert _refresh(client, s["refresh_token"]).status_code == 401


def test_an_expired_egeria_token_means_no_refresh_and_401(client, monkeypatch, checks):
    s = _signin(client, monkeypatch, egeria_ttl=2)
    time.sleep(2.1)
    r = _refresh(client, s["refresh_token"])
    assert r.status_code == 401 and "Egeria sign-in has expired" in r.json()["detail"]
    assert checks["asked"] == []                                # refused before Egeria is asked


def test_egeria_unreachable_leaves_the_refresh_token_usable(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    checks["answer"] = ConnectionError("down")
    r = _refresh(client, s["refresh_token"])
    assert r.status_code == 503 and "carries on" in r.json()["detail"]
    assert client.get("/api/auth/me", headers=_h(s["access_token"])).status_code == 200
    checks["answer"] = None
    assert _refresh(client, s["refresh_token"]).status_code == 200    # not a replay: it was never used


def test_no_refresh_past_the_maximum_age(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    rec = client.app.state.refresh_store.peek(s["refresh_token"])
    rec.auth_time -= 8 * 3600                                   # as if signed in 8 hours ago
    r = _refresh(client, s["refresh_token"])
    assert r.status_code == 401 and "8-hour limit" in r.json()["detail"]
    assert checks["asked"] == []


def test_mint_never_runs_past_the_maximum_age(app):
    old = int(time.time()) - 8 * 3600 + 30
    tok = sessions.mint("peterprofile", fake_egeria_token(), app.state.auth_config, app.state.settings,
                        sid="s", auth_time=old)
    assert _claims(tok)["exp"] <= old + 8 * 3600


def test_an_unknown_or_missing_refresh_token_is_401(client, checks):
    assert _refresh(client, "not-a-token").status_code == 401
    assert client.post("/api/auth/refresh", json={}).status_code in (401, 422)
    assert checks["asked"] == []


# ---- expired access tokens and sign-out ----------------------------------------------------------------

def test_an_expired_access_token_with_no_refresh_is_401_never_anonymous(client, app):
    c = _claims(sessions.mint("peterprofile", fake_egeria_token(), app.state.auth_config, app.state.settings,
                              sid="s1", auth_time=int(time.time()) - 60))
    lapsed = jwt.encode({**c, "exp": int(time.time()) - 1}, SECRET, algorithm="HS256")
    for path in ("/api/auth/me", "/api/config", "/api/drafts"):
        r = client.get(path, headers=_h(lapsed))
        assert r.status_code == 401, path
        assert "user_id" not in r.text                           # nothing answered as anyone
    assert client.get("/api/auth/me").status_code == 401


def test_sign_out_revokes_server_side_so_refresh_fails(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    r1 = _refresh(client, s["refresh_token"]).json()
    out = client.post("/api/auth/logout", json={"refresh_token": r1["refresh_token"]}, headers=_h(r1["access_token"]))
    assert out.json() == {"status": "ok"}
    assert _refresh(client, r1["refresh_token"]).status_code == 401
    for method, path in (("get", "/api/auth/me"), ("get", "/api/config")):
        for tok in (s["access_token"], r1["access_token"]):     # the earlier access token too
            assert getattr(client, method)(path, headers=_h(tok)).status_code == 401, path


def test_sign_out_with_only_the_refresh_token_ends_the_session(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    client.post("/api/auth/logout", json={"refresh_token": s["refresh_token"]})
    assert client.get("/api/auth/me", headers=_h(s["access_token"])).status_code == 401
    assert _refresh(client, s["refresh_token"]).status_code == 401


def test_sign_out_with_an_expired_access_token_still_ends_the_session(client, monkeypatch, checks):
    s = _signin(client, monkeypatch)
    lapsed = jwt.encode({**_claims(s["access_token"]), "exp": int(time.time()) - 1}, SECRET, algorithm="HS256")
    client.post("/api/auth/logout", headers=_h(lapsed))
    assert _refresh(client, s["refresh_token"]).status_code == 401


def test_sign_out_without_a_session_is_ok_and_touches_no_one(client, monkeypatch):
    s = _signin(client, monkeypatch)
    assert client.post("/api/auth/logout").json() == {"status": "ok"}
    assert client.get("/api/auth/me", headers=_h(s["access_token"])).status_code == 200


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


def test_refresh_is_single_flight(tmp_path):
    """Read-back advisory on d8f56034: a double-fire would send one refresh token twice, which Studio reads as a
    replay and ends the session. singleFlight shares one running call; a later call runs afresh."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not found")
    mod = tmp_path / "session_refresh.mjs"
    mod.write_text((STATIC / "session_refresh.js").read_text())
    script = f"""
      const m = await import({json.dumps(mod.as_uri())});
      let calls = 0, release;
      const gate = new Promise(r => {{ release = r; }});
      const once = m.singleFlight(async () => {{ calls++; await gate; return calls; }});
      const a = once(), b = once(), c = once();
      release();
      const got = await Promise.all([a, b, c]);
      const after = await once();
      console.log(JSON.stringify({{ calls, got, after }}));
    """
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"calls": 2, "got": [1, 1, 1], "after": 2}


def test_the_page_refreshes_one_tab_at_a_time_and_tells_its_other_tabs():
    js = (STATIC / "studio_auth.js").read_text()
    assert "const refreshOnce = singleFlight(" in js and "navigator.locks.request('studio-refresh', sendRefresh)" in js
    assert "if (what === 'refresh') await refreshOnce();" in js
    # inside the lock it re-reads, so a tab another tab just rotated doesn't send its stale refresh token
    send = js[js.index("async function sendRefresh()"):js.index("const refreshOnce")]
    assert send.index("refreshDue(payload(t), nowSec(), lastActive) !== 'refresh'") < send.index("fetch('/api/auth/refresh'")
    assert "channel.postMessage({ type: 'rotated', sid:" in js and "channel.postMessage({ type: 'ended', sid:" in js
    assert "if (!mine || d.sid !== mine) return;" in js       # only this session's news


def test_the_page_rotates_signs_out_with_both_tokens_and_ends_on_a_refusal():
    js = (STATIC / "studio_auth.js").read_text()
    assert "import { refreshDue, singleFlight } from '/static/session_refresh.js'" in js
    assert "body: JSON.stringify({ refresh_token: rt })" in js
    assert "setRefresh(data.refresh_token); setToken(data.access_token);" in js        # both replaced
    assert "body: JSON.stringify({ refresh_token: rt || null })" in js                  # sign-out sends it
    assert "s.removeItem(REFRESH_KEY)" in js                                            # cleared with the JWT
    assert "r.status === 401 || r.status === 403" in js and "endSession(" in js
    panel = (STATIC / "ea_panel.js").read_text()
    assert "refresh" not in panel.lower()                                               # never handed to EA


def test_ea_swaps_a_refreshed_jwt_of_the_same_session_quietly():
    ea = (Path(__file__).parents[3] / "packages" / "egeria-advisor" / "advisor" / "web" / "static" / "auth.js").read_text()
    assert "before.sid === after.sid && before.sub === after.sub) return true;" in ea
