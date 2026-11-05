"""F58: the shell tells an RE-session 401 (`login_required`) from Egeria refusing the person's Egeria token
(`egeria_token_refused`), and shows the sign-in form with the right words for each.

auth.js is a classic script (one `window.fetch` wrapper, a login overlay). Run for real under node with a minimal
window/document/sessionStorage so the wrapper's own branch is what is tested, not a substring of its source."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_HARNESS = r"""
const fs = require('fs');
const [authPath, status, body] = process.argv.slice(-3);
// a token whose exp is in the future, so the wrapper's proactive expiry check does not fire first
const token = 'h.' + Buffer.from(JSON.stringify({sub: 'peterprofile', exp: Math.floor(Date.now()/1000) + 3600})).toString('base64') + '.s';
const els = {};
const el = (id) => (els[id] ||= {id, textContent: '', style: {}, classList: {
  _s: new Set(['hidden']), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, has(c) { return this._s.has(c); } },
  focus() {}, addEventListener() {} });
const document = { getElementById: (id) => el(id), dispatchEvent() {}, addEventListener() {} };
const store = { re_token: token };
const sessionStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
const calls = [];
const window = { location: { href: 'http://re.test/next/', origin: 'http://re.test', pathname: '/next/', search: '' },
  fetch: async (url) => { calls.push(String(url)); return new Response(body, { status: Number(status), headers: { 'content-type': 'application/json' } }); },
  addEventListener() {} };
globalThis.window = window; globalThis.document = document; globalThis.sessionStorage = sessionStorage;
globalThis.setTimeout = (f) => f();
globalThis.history = { replaceState() {} }; globalThis.CustomEvent = class { constructor(t) { this.type = t; } };
eval(fs.readFileSync(authPath, 'utf8') + '\nglobalThis.Auth = Auth;');
(async () => {
  const r = await window.fetch('/api/filesystems/x/file-profiles', { method: 'POST' });
  const echoed = await r.json().catch(() => 'not json');   // the caller's own body read is untouched by the shell's classification
  console.log(JSON.stringify({
    status: r.status, echoed, calls,
    message: el('login-message').textContent, messageHidden: el('login-message').classList.has('hidden'),
    overlay: el('login-overlay').style.display || '', tokenCleared: store.re_token === undefined }));
})();
"""


def _press(status: int, body: dict) -> dict:
    out = subprocess.run(["node", "-e", _HARNESS, "--", str(STATIC / "auth.js"), str(status), json.dumps(body)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


WORDS = "your Egeria sign-in is no longer valid (Egeria restarted since you signed in) · sign in again · nothing was profiled"


def test_the_egeria_token_refused_marker_shows_the_sign_in_form_with_the_egeria_words():
    got = _press(401, {"error": "egeria_token_refused", "detail": WORDS})
    assert got["overlay"] == "flex" and got["messageHidden"] is False
    assert got["message"] == WORDS
    assert got["tokenCleared"] is True, "a new sign-in mints a new RE session with a live Egeria token"
    assert got["echoed"] == {"error": "egeria_token_refused", "detail": WORDS}


def test_the_re_session_401_still_says_the_session_expired():
    got = _press(401, {"error": "login_required"})
    assert got["overlay"] == "flex" and got["message"] == "Your session has expired. Please sign in again."
    assert got["tokenCleared"] is True


def test_a_401_that_is_not_json_is_the_session_401_and_a_200_shows_nothing():
    harness = _HARNESS.replace("new Response(body,", "new Response('not json',")
    out = subprocess.run(["node", "-e", harness, "--", str(STATIC / "auth.js"), "401", "{}"],
                         capture_output=True, text=True, check=True)
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["echoed"] == "not json" and got["message"] == "Your session has expired. Please sign in again."
    ok = _press(200, {"ok": True})
    assert ok["overlay"] == "" and ok["message"] == "" and ok["echoed"] == {"ok": True}


def test_the_shell_branches_on_the_marker_never_on_the_words():
    js = (STATIC / "auth.js").read_text(encoding="utf-8")
    assert "body.error === EGERIA_TOKEN_REFUSED" in js
    assert "const EGERIA_TOKEN_REFUSED = 'egeria_token_refused';" in js
    assert "no longer valid" not in js[js.index("async function classify401"):js.index("return { kind: 'login_required'")]
    api = (STATIC / "re-api.js").read_text(encoding="utf-8")
    assert "code === 'egeria_token_refused'" in api
