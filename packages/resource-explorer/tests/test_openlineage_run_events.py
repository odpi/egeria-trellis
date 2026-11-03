"""R21: every step run emits an OpenLineage run event from the one place all
steps pass through, `step_cost_observer.observe()`."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from resource_explorer.observability import openlineage
from resource_explorer.surveyors import step_cost_observer as sco

DB = SimpleNamespace(
    host="db.example", port=5432, database_name="coco", db_type="postgresql",
    db_user="surveyor", db_password="hunter2-SECRET", connection_ref="vault://x",
)
REPO = SimpleNamespace(github_url="https://user:ghp_SECRETTOKEN@github.com/odpi/egeria?tab=x")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("RE_OPENLINEAGE_TRANSPORT", "RE_OPENLINEAGE_DIR", "RE_OPENLINEAGE_URL",
              "RE_OPENLINEAGE_TOKEN", "RE_OPENLINEAGE_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)


def _run(step="s1", *, raise_=None, entity_type="database", resource=DB, note=None):
    with pytest.raises(type(raise_)) if raise_ else _null():
        with sco.observe(step, "none", "low", entity_type=entity_type, slug="coco",
                         resource=resource, job_family="analysis") as obs:
            if note:
                openlineage.note_failure(note)
            if raise_:
                raise raise_
    return obs[0]


class _null:
    def __enter__(self): return None
    def __exit__(self, *a): return False


def _events(path):
    return [json.loads(l) for l in (path / "openlineage-events.jsonl").read_text().splitlines()]


def test_complete_emits_start_and_complete_same_run_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_OPENLINEAGE_TRANSPORT", "file")
    monkeypatch.setenv("RE_OPENLINEAGE_DIR", str(tmp_path))
    obs = _run()
    start, done = _events(tmp_path)
    assert (start["eventType"], done["eventType"]) == ("START", "COMPLETE")
    assert start["run"]["runId"] == done["run"]["runId"] == obs.openlineage["run_id"]
    pe = done["run"]["facets"]["processing_engine"]
    assert pe["name"] == "resource-explorer" and pe["version"] == openlineage.engine_version()
    assert done["job"] == {"namespace": "resource-explorer", "name": "analysis.s1"}
    assert done["inputs"] == [{"namespace": "postgres://db.example:5432", "name": "coco"}]
    assert done["run"]["facets"]["nominalTime"]["nominalStartTime"]
    blob = (tmp_path / "openlineage-events.jsonl").read_text()
    for secret in ("hunter2", "SECRET", "surveyor", "vault://"):
        assert secret not in blob
    assert obs.vector()["openlineage"]["emitted"] == 2


def test_repo_url_is_stripped_of_userinfo_and_query(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_OPENLINEAGE_TRANSPORT", "file")
    monkeypatch.setenv("RE_OPENLINEAGE_DIR", str(tmp_path))
    _run(entity_type="repo", resource=REPO)
    ev = _events(tmp_path)[0]
    assert ev["inputs"] == [{"namespace": "https://github.com", "name": "odpi/egeria"}]
    assert "ghp_" not in json.dumps(ev)


def test_raising_step_emits_start_and_fail_with_error_message(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_OPENLINEAGE_TRANSPORT", "file")
    monkeypatch.setenv("RE_OPENLINEAGE_DIR", str(tmp_path))
    _run(raise_=ValueError("boom password=hunter2 at https://u:pw@h/x"))
    start, fail = _events(tmp_path)
    assert (start["eventType"], fail["eventType"]) == ("START", "FAIL")
    msg = fail["run"]["facets"]["errorMessage"]["message"]
    assert "boom" in msg and "hunter2" not in msg and "u:pw" not in msg


def test_step_that_catches_its_own_error_can_still_report_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_OPENLINEAGE_TRANSPORT", "file")
    monkeypatch.setenv("RE_OPENLINEAGE_DIR", str(tmp_path))
    _run(note="surveyor raised unexpectedly: nope")
    assert [e["eventType"] for e in _events(tmp_path)] == ["START", "FAIL"]


def test_off_emits_nothing_but_run_id_is_still_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_OPENLINEAGE_DIR", str(tmp_path))  # dir set, transport not
    obs = _run()
    assert not list(tmp_path.iterdir())
    assert obs.openlineage["transport"] == "off" and obs.openlineage["emitted"] == 0
    assert obs.openlineage["run_id"]


class _Handler(BaseHTTPRequestHandler):
    got: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Handler.got.append((self.headers.get("Authorization"), json.loads(body)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_http_posts_to_local_fake_server(monkeypatch):
    _Handler.got = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("RE_OPENLINEAGE_TRANSPORT", "http")
        monkeypatch.setenv("RE_OPENLINEAGE_URL", f"http://127.0.0.1:{srv.server_port}/x")
        monkeypatch.setenv("RE_OPENLINEAGE_TOKEN", "tok")
        obs = _run()
    finally:
        srv.shutdown()
    assert [e["eventType"] for _, e in _Handler.got] == ["START", "COMPLETE"]
    assert _Handler.got[0][0] == "Bearer tok"
    assert obs.openlineage["emitted"] == 2 and obs.openlineage["failed"] == 0


def test_unreachable_url_is_recorded_and_the_step_still_succeeds(monkeypatch, caplog):
    monkeypatch.setenv("RE_OPENLINEAGE_TRANSPORT", "http")
    monkeypatch.setenv("RE_OPENLINEAGE_URL", "http://127.0.0.1:1/x")
    monkeypatch.setenv("RE_OPENLINEAGE_TIMEOUT_S", "0.5")
    obs = _run()
    assert obs.openlineage["failed"] == 2 and obs.openlineage["emitted"] == 0
    assert obs.openlineage["last_error"]
    assert "not delivered" in caplog.text


def test_file_mode_without_dir_is_a_counted_failure_not_a_crash(monkeypatch):
    monkeypatch.setenv("RE_OPENLINEAGE_TRANSPORT", "file")
    obs = _run()
    assert obs.openlineage["failed"] == 2
    assert "RE_OPENLINEAGE_DIR" in obs.openlineage["last_error"]
