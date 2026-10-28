"""Classic's "Run bootstrap now" must not report success for a refused or already-running request."""
from __future__ import annotations

from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "resource_explorer/web/static/index.html").read_text()


def _handler() -> str:
    i = HTML.index("async function _runBootstrapNow()")
    return HTML[i:HTML.index("\n}\n", i)]


def test_it_checks_res_ok_before_reading_the_result():
    h = _handler()
    assert "res.ok" in h
    assert h.index("res.ok") < h.index("Everything already present")


def test_a_refusal_and_a_409_are_said_not_swallowed():
    h = _handler()
    assert "res.status === 409" in h and "already running" in h.lower()
    assert "res.status === 403" in h and "admin" in h.lower()


def test_it_sends_the_admin_header_with_the_existing_helper_and_still_no_force():
    h = _handler()
    assert "..._adminAuthHeaders()" in h
    assert "force" not in h.replace("Deliberately does NOT pass force", "").replace("a forced re-run", "")


def test_classic_retry_and_bulk_linkage_calls_send_the_admin_header():
    i = HTML.index("async function retryOutboxElement(")
    assert "..._adminAuthHeaders()" in HTML[i:i + 400]
    j = HTML.index("fetch(_EGERIA_BULK_ENDPOINT[kind]")
    assert "..._adminAuthHeaders()" in HTML[j:j + 300]
