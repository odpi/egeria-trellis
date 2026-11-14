"""S4: the embedded EA panel. The behaviour (origin and source checks, the session hand-over, the sign-in message,
an unreachable EA, the hand-off by reference) is driven under jsdom by tests/js/ea_panel_harness.cjs; jsdom comes
from STUDIO_JSDOM_DIR or EA_JSDOM_DIR (a node_modules directory: RE's frontend-build has it), else the test skips
and says so. The rest checks the page wiring."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
STATIC = HERE.parent / "trellis_studio" / "static"
REPO = HERE.parents[2]


def _jsdom_dir():
    for d in (os.environ.get("STUDIO_JSDOM_DIR"), os.environ.get("EA_JSDOM_DIR"),
              str(REPO / "packages" / "resource-explorer" / "frontend-build" / "node_modules")):
        if d and (Path(d) / "jsdom").is_dir():
            return d
    return None


@pytest.fixture(scope="module")
def harness():
    node, jsd = shutil.which("node"), _jsdom_dir()
    if not node or not jsd:
        if os.environ.get("STUDIO_JS_REQUIRED") == "1":
            pytest.fail("node and jsdom are required here (STUDIO_JS_REQUIRED=1)")
        pytest.skip("node or jsdom not found (set STUDIO_JSDOM_DIR to a node_modules with jsdom)")
    out = subprocess.run([node, str(HERE / "js" / "ea_panel_harness.cjs"), jsd, str(STATIC / "ea_panel.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_iframe_url_is_ea_with_embed_and_nothing_secret(harness):
    assert harness["src"] == "http://localhost:8880/?embed=studio"


def test_a_wrong_origin_or_source_is_ignored(harness):
    assert harness["wrongOriginIgnored"] and harness["wrongSourceIgnored"]


def test_the_session_goes_only_to_eas_exact_origin(harness):
    assert harness["sessionSent"]


def test_no_valid_session_gives_the_sign_in_message(harness):
    assert harness["noTokenNothingSent"]
    assert harness["noTokenSays"] == "Sign in to Studio to use EA here."


def test_an_unreachable_ea_is_said_and_studio_carries_on(harness):
    assert harness["unreachableSays"].startswith("EA isn't reachable at http://localhost:8880/")
    assert harness["askWhenUnreachable"] is False and harness["postedWhenUnreachable"] == 0


def test_the_hand_off_is_a_reference_only_from_ea(harness):
    assert harness["opened"] == [{"kind": "spec", "name": "Open Glossaries"}]   # bad kind and wrong origin dropped
    assert harness["askSent"]


def test_page_opens_drafts_by_reference_through_studios_api():
    js = (STATIC / "studio.js").read_text()
    assert "onOpenDraft: (ref) => studioDrafts.openDraft(ref.kind, ref.name)" in js
    drafts = (STATIC / "studio_drafts.js").read_text()
    assert "api(`/api/drafts/${kind}/${encodeURIComponent(name)}`)" in drafts


def test_every_post_names_an_exact_origin():
    js = (STATIC / "ea_panel.js").read_text()
    posts = re.findall(r"postMessage\(([^;]*)\);", js)
    assert posts and all(p.rstrip().endswith("origin)") or p.rstrip().endswith(", origin") for p in posts), posts
    assert not re.search(r"postMessage\([^)]*['\"]\*['\"]", js)


def test_the_panel_is_resizable_and_optional():
    js = (STATIC / "studio.js").read_text()
    assert "makeSplit('studio-ea', [{ el: appHost, grow: true }, { el: ea.root, width: 380, min: 240 }])" in js
    assert 'id="st-ea-toggle"' in (STATIC / "index.html").read_text()


def test_config_gives_the_page_eas_origin(authed):
    body = authed.get("/api/config").json()
    assert body["advisor_url"] == "http://localhost:8880/" and body["advisor_origin"] == "http://localhost:8880"


def test_sign_out_unloads_the_frame_and_a_new_session_reloads_it(harness):
    assert harness["firstUserJwt"]
    assert harness["unloadedFrameSrc"] == "about:blank" and harness["unloadedReady"] is False
    assert harness["staysUnloadedWithoutSession"]
    assert harness["reloadedSrc"] == "http://localhost:8880/?embed=studio"
    assert harness["secondUserJwt"]


def test_page_unloads_ea_when_the_session_ends():
    js = (STATIC / "studio.js").read_text()
    assert "document.addEventListener('studio:session-ended', () => ea.unload());" in js
    auth = (STATIC / "studio_auth.js").read_text()
    assert auth.count("new CustomEvent('studio:session-ended')") == 2      # sign-out and an expired session
    assert "window.location.replace(window.location.pathname);" in auth
