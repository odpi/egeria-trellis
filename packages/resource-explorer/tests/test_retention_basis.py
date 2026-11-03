"""Retention basis: one list, one reading of a stored field, the body the publish builds, and an error
that keeps Egeria's reason. The ordinals come from Dr.Egeria's code, NOT from a read of this platform."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from resource_explorer import retention_basis as rb
from resource_explorer.curate_plan import Curations
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.workflows import curate_commit as wf

EXPECTED = {"UNCLASSIFIED": 0, "TEMPORARY": 1, "PROJECT_LIFETIME": 2, "TEAM_LIFETIME": 3,
            "CONTRACT_LIFETIME": 4, "REGULATED_LIFETIME": 5, "TIMEBOXED_LIFETIME": 6, "OTHER": 99}


def test_mapping_is_every_value_and_unknown_is_refused():
    assert rb.ORDINALS == EXPECTED
    for name, n in EXPECTED.items():
        assert rb.ordinal(name) == n
    for bad in ("Ongoing", "", "project_lifetime", "ACTIVE"):
        with pytest.raises(ValueError):
            rb.ordinal(bad)


def test_the_page_copy_equals_the_python_list():
    js = Path(rb.__file__).parent / "web/static/next/retention-basis.js"
    rows = re.findall(r"name: '(\w+)', label: '([^']+)', ordinal: (\d+), hint: '([^']+)'", js.read_text())
    assert [(n, l, int(o), h) for n, l, o, h in rows] == list(rb.RETENTION_BASES)


class TestResolve:
    def test_legacy_free_text_reads_as_project_lifetime_and_keeps_its_text_as_the_note(self):
        r = rb.resolve({"value": "Ongoing"})
        assert r == {"basis": "PROJECT_LIFETIME", "note": "Ongoing", "carried": True}
        assert rb.ordinal(r["basis"]) == 2

    def test_new_shape_is_unchanged(self):
        assert rb.resolve({"value": "REGULATED_LIFETIME", "note": "SOX"}) == \
            {"basis": "REGULATED_LIFETIME", "note": "SOX", "carried": False}

    def test_nothing_stored_is_unset_never_defaulted(self):
        assert rb.resolve(None)["basis"] == "" and rb.resolve({})["basis"] == ""
        assert rb.resolve({"value": "", "note": "just a note"}) == {"basis": "", "note": "just a note", "carried": False}


def _enrichment(retention):
    return {"retention": {"author": "dan", "set_at": "2026-10-08T00:00:00+00:00", "kind": "observation", **retention}}


@pytest.mark.parametrize("name,n", sorted(EXPECTED.items()))
def test_body_built_for_each_of_the_eight(name, n):
    (method, label, body), = wf._classification_bodies(_enrichment({"value": name, "note": "why"}))
    assert method == "set_retention_classification"
    assert body["class"] == "NewClassificationRequestBody"
    p = body["properties"]
    assert p["class"] == "RetentionClassificationProperties"
    assert p["retentionBasis"] == n and isinstance(p["retentionBasis"], int)
    assert "status" not in p                      # ISSUE-22: ignored by the server; Dr.Egeria's confirmed path omits it
    assert "why" in p["notes"] and rb.LABELS[name] in label


def test_legacy_value_publishes_ordinal_2_with_text_in_the_notes():
    (_m, _l, body), = wf._classification_bodies(_enrichment({"value": "Ongoing"}))
    assert body["properties"]["retentionBasis"] == 2 and "Ongoing" in body["properties"]["notes"]


def test_no_retention_builds_no_body():
    assert wf._classification_bodies(_enrichment({"value": "", "note": "only a note"})) == []
    assert wf._classification_bodies({}) == []


class _Err(Exception):
    def __init__(self, msg, reason):
        super().__init__(msg)
        self.additional_info = {"reason": reason}


def test_error_text_keeps_egeria_reason_and_scrubs_secrets():
    short, full, reason = wf._exc_text(_Err("CLIENT_ERROR_400", '{"exceptionErrorMessage": "retentionBasis not valid", "x": "password=hunter2"}'))
    assert short == "retentionBasis not valid" and reason == "retentionBasis not valid"
    _s, _f, r2 = wf._exc_text(_Err("boom", "bad request password: hunter2"))
    assert "hunter2" not in r2


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    return r


def _run(registry, monkeypatch, enrichment, client):
    registry.save_context("repo", "p", {"enrichment": enrichment})
    from resource_explorer import repo_publish
    from resource_explorer.surveyors import survey_snapshot
    monkeypatch.setattr(repo_publish, "resolve_project_context", lambda *a, **k: object())
    monkeypatch.setattr(survey_snapshot, "latest", lambda *a, **k: object())
    monkeypatch.setattr(repo_publish, "publish_snapshot", lambda *a, **k: {
        "ok": True, "asset_guid": "a-1", "read_back": True, "surveyed_at": "2026-10-07", "reused": False,
        "report_guid": "r", "annotation_count": 0})
    monkeypatch.setattr("resource_explorer.egeria_identity.classification_client", lambda *a, **k: client)
    rec = Curations(registry).create("repo", "p", author="dan", selection={}, manifest={}, steps=list(wf.STEPS))
    out = wf.execute_curation(registry, rec["id"])
    return {s["name"]: s for s in out["steps"]}["classifications"]


class Recorder:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail
    def _do(self, kind, guid, body):
        if self.fail == kind:
            raise _Err("PyegeriaClientException CLIENT_ERROR_400", '{"exceptionErrorMessage": "OMAG-400: retentionBasis is not valid"}')
        self.calls.append((kind, guid, body))
    def set_confidentiality_classification(self, g, b): self._do("conf", g, b)
    def set_criticality_classification(self, g, b): self._do("crit", g, b)
    def set_retention_classification(self, g, b): self._do("ret", g, b)


SENS = {"value": "public", "author": "dan", "set_at": "2026-10-08T00:00:00+00:00"}


def test_no_stored_retention_step_is_silent_about_retention(registry, monkeypatch):
    rec = Recorder()
    step = _run(registry, monkeypatch, {"sensitivity": SENS}, rec)
    assert step["state"] == "done" and "Retention" not in step["detail"]


def test_note_only_retention_is_skipped_visibly_not_written_not_failed(registry, monkeypatch):
    rec = Recorder()
    step = _run(registry, monkeypatch, {"sensitivity": SENS, **_enrichment({"value": "", "note": "x"})}, rec)
    assert [c[0] for c in rec.calls] == ["conf"]
    assert step["state"] == "done"
    assert "done: Confidentiality · public" in step["detail"]
    assert "skipped: Retention · skipped · no retention basis picked" in step["detail"]


def test_only_a_skipped_retention_leaves_the_step_skipped(registry, monkeypatch):
    step = _run(registry, monkeypatch, _enrichment({"value": "", "note": "x"}), Recorder())
    assert step["state"] == "skipped" and "no retention basis picked" in step["detail"]


def test_two_landed_one_failed_says_so_per_item_with_the_reason(registry, monkeypatch):
    rec = Recorder(fail="ret")
    step = _run(registry, monkeypatch, {"sensitivity": SENS, **_enrichment({"value": "TEAM_LIFETIME"})}, rec)
    assert step["state"] == "failed"
    assert "done: Confidentiality · public" in step["detail"]
    assert "failed: Retention · Team Lifetime: OMAG-400: retentionBasis is not valid" in step["detail"]
    assert "full text in the log" in step["detail"]
    assert "retentionBasis is not valid" in step["more"]
    assert rec.calls[0][0] == "conf"


def test_the_reason_is_logged_in_full(registry, monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        _run(registry, monkeypatch, _enrichment({"value": "TEAM_LIFETIME"}), Recorder(fail="ret"))
    assert any("egeria reason: OMAG-400: retentionBasis is not valid" in r.getMessage() for r in caplog.records)


def test_a_confidentiality_failure_gets_the_same_treatment(registry, monkeypatch):
    step = _run(registry, monkeypatch, {"sensitivity": SENS}, Recorder(fail="conf"))
    assert step["state"] == "failed" and "retentionBasis is not valid" in step["detail"]


class TestTheSaveRoute:
    @pytest.fixture
    def client(self, registry, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        monkeypatch.setattr("resource_explorer.web.routes.context.get_current_user", lambda request: {"user_id": "dan"})
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_value_and_note_are_stored_together_without_a_new_column(self, client, registry):
        r = client.patch("/api/context/repo/p/field", json={"key": "retention", "value": "TEAM_LIFETIME",
                                                              "note": "per charter", "kind": "observation"})
        assert r.status_code == 200, r.text
        f = registry.get_context("repo", "p")["enrichment"]["retention"]
        assert f["value"] == "TEAM_LIFETIME" and f["note"] == "per charter"

    def test_a_free_text_retention_value_is_refused_with_the_list(self, client):
        r = client.patch("/api/context/repo/p/field", json={"key": "retention", "value": "Ongoing", "kind": "observation"})
        assert r.status_code == 422 and "PROJECT_LIFETIME" in r.text


# ── secret scrubbing: by shape, before truncation, on every path ───────────────────────────────────
from resource_explorer.secret_redaction import scrub_text  # noqa: E402

SECRETS = [
    ("Authorization: Bearer abc123def", "abc123def"),
    ('{"password": "a b c"}', "b c"),
    ('{"password": "a b c"}', "a b"),
    ("api_key=ZZtop99", "ZZtop99"),
    ("apikey: ZZtop99", "ZZtop99"),
    ("pwd=hunter2 next", "hunter2"),
    ("postgresql://u:pw0rd@h:5432/db", "pw0rd"),
    ("token=tok_123&x=1", "tok_123"),
    ('escaped \\"password\\": \\"a b c\\" end', "b c"),
    ("bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
]


@pytest.mark.parametrize("text,secret", SECRETS)
def test_scrub_text_masks_by_shape(text, secret):
    assert secret not in scrub_text(text)


def test_scrub_keeps_the_rest_readable():
    out = scrub_text("Authorization: Bearer abc123def failed for user u")
    assert "abc123def" not in out and out.endswith("failed for user u") and "Authorization" in out
    assert scrub_text("retentionBasis is not valid") == "retentionBasis is not valid"


def test_scrub_happens_before_truncation():
    reason = "x" * 175 + " postgresql://user:SECRETSECRETSECRET@host/db"
    short, _full, _r = wf._exc_text(_Err("boom", reason))
    assert "SECRET" not in short and len(short) <= 200


@pytest.mark.parametrize("text,secret", SECRETS[:4] + SECRETS[6:7])
def test_scrub_runs_on_every_path_that_stores_or_logs_the_text(registry, monkeypatch, caplog, text, secret):
    import logging

    class Leaky(Recorder):
        def set_retention_classification(self, g, b):
            raise _Err("CLIENT_ERROR_400 " + text, text)

    with caplog.at_level(logging.WARNING):
        step = _run(registry, monkeypatch, _enrichment({"value": "TEAM_LIFETIME"}), Leaky())
    assert step["state"] == "failed"
    assert secret not in step["detail"] and secret not in step["more"]
    assert all(secret not in r.getMessage() for r in caplog.records)
    # the HTTP response is the stored record read back
    from fastapi.testclient import TestClient
    cid = Curations(registry).for_resource("repo", "p")[0]["id"]
    if cid:
        from resource_explorer.web.app import app
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        r = TestClient(app).get(f"/api/projects/p/curate/commits/{cid}")
        assert r.status_code == 200 and secret not in r.text


# ── legacy forms ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("stored,basis", [
    ("Temporary", "TEMPORARY"), ("temporary", "TEMPORARY"),
    ("Time Boxed Lifetime", "TIMEBOXED_LIFETIME"), ("time boxed lifetime", "TIMEBOXED_LIFETIME"),
    ("1", "TEMPORARY"), ("99", "OTHER"), (" 0 ", "UNCLASSIFIED"), ("Regulated Lifetime", "REGULATED_LIFETIME"),
    ("Ongoing", "PROJECT_LIFETIME"), ("7", "PROJECT_LIFETIME"), ("forever and a day", "PROJECT_LIFETIME"),
])
def test_old_values_that_name_a_basis_get_that_basis_others_get_project_lifetime(stored, basis):
    r = rb.resolve({"value": stored})
    assert r["basis"] == basis and r["note"] == stored.strip() and r["carried"] is True
    (_m, _l, body), = wf._classification_bodies(_enrichment({"value": stored}))
    assert body["properties"]["retentionBasis"] == rb.ORDINALS[basis]


def test_the_page_resolves_the_same_forms_as_python(tmp_path):
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = tmp_path / "rb.mjs"
    js.write_text((Path(rb.__file__).parent / "web/static/next/retention-basis.js").read_text())
    cases = [s for s, _b in [("Temporary", 0), ("temporary", 0), ("Time Boxed Lifetime", 0), ("1", 0), ("99", 0), (" 0 ", 0),
                             ("Ongoing", 0), ("7", 0), ("TEAM_LIFETIME", 0), ("  TEMPORARY  ", 0), ("", 0)]]
    prog = (f"import('{js.as_uri()}').then(m => console.log(JSON.stringify({json.dumps(cases)}.map(v => m.resolveRetention({{value: v}})))))")
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [rb.resolve({"value": v}) for v in cases]


# ── LOW items ───────────────────────────────────────────────────────────────────────────────────────
def test_notes_are_the_retention_text_and_class_and_basis_cannot_be_overridden():
    (_m, _l, body), = wf._classification_bodies(_enrichment({"value": "TEAM_LIFETIME", "note": "per charter"}))
    p = body["properties"]
    assert p["notes"].startswith("Team Lifetime — per charter — set by dan on 2026-10-08")
    assert p["class"] == "RetentionClassificationProperties" and p["retentionBasis"] == 3
    assert p["steward"] == "dan"


class TestThePlanPreviewsTheSkip:
    def _plan(self, registry, enrichment):
        from resource_explorer.curate_plan import build_plan
        registry.save_context("repo", "p", {"enrichment": enrichment})
        return {c["key"]: c for c in build_plan(registry, "p")["writes"]["classifications"]}

    def test_a_note_without_a_basis_is_shown_as_skipped(self, registry):
        c = self._plan(registry, _enrichment({"value": "", "note": "x"}))["retention"]
        assert c["skipped"] is True and c["value"] == "skipped · no retention basis picked"

    def test_a_basis_is_shown_as_a_write(self, registry):
        c = self._plan(registry, _enrichment({"value": "TEAM_LIFETIME"}))["retention"]
        assert c["skipped"] is False and c["value"] == "Team Lifetime"

    def test_nothing_stored_adds_no_row(self, registry):
        assert "retention" not in self._plan(registry, {"sensitivity": SENS})


# ── scrub_text, second round (BEST-EFFORT, shape-based) ─────────────────────────────────────────────
MORE_SECRETS = [
    ("Authorization: Token abc12345", "abc12345"),
    ("Authorization: token abc", "abc"),
    ("postgresql://u:p@ss@h/db", "p@ss"),
    ("postgresql://u:p@ss@h/db", "ss@h"),
    ("postgresql://u:p/w@h/db", "p/w"),
    ('password: "a\\"b c"', "b c"),
    ('password: "a\\"b c"', 'a\\"b'),
    ("Cookie: session=abc; theme=dark", "session=abc"),
    ("Set-Cookie: sid=zzz999", "zzz999"),
    ("secretKey=abc9", "abc9"),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUt", "wJalrXUt"),
    ('{"passwords": ["p1", "p2 x"]}', "p2 x"),
    ('{"passwords": ["p1", "p2 x"]}', "p1"),
    ("client_secret=s3cr3t&grant=1", "s3cr3t"),
    ("signingKey: k3y-value", "k3y-value"),
    ("Authorization: Basic dXNlcjpwdw==", "dXNlcjpwdw"),
    ("session token: tok99abc", "tok99abc"),
]


@pytest.mark.parametrize("text,secret", MORE_SECRETS)
def test_scrub_text_second_round_samples(text, secret):
    assert secret not in scrub_text(text), scrub_text(text)


def test_scrub_text_keeps_the_name_and_the_rest():
    assert scrub_text("postgresql://u:p@ss@h/db failed") == "postgresql://u:***@h/db failed"
    assert scrub_text("connect host:8080/path@x ok") == "connect host:8080/path@x ok"
    out = scrub_text("Cookie: session=abc\nnext line stays")
    assert out.endswith("next line stays") and "abc" not in out


@pytest.mark.parametrize("text", [
    "the token expired and the key was not found",
    "Turkey: 5 monkeys: 3 keyboard=qwerty",
    "retentionBasis is not valid",
    "Basic usage of the API failed with status 500",
    "token budget exceeded (limit 4096)",
    "Asset 3f2a-9c with key concepts not found",
    "OMAG-400-012: the sort order is invalid",
])
def test_negative_controls_are_left_alone(text):
    """Rule: only an ASSIGNMENT (name then ':' or '=') whose name carries a credential word is masked,
    plus URL userinfo, Cookie lines and 'Bearer <x>'. Words like token/key in a sentence are untouched.
    'keyboard=qwerty' survives; 'foreign_key=id' would not (documented false positive)."""
    assert scrub_text(text) == text


def test_prose_is_best_effort_not_caught():
    assert "hunter2" in scrub_text("the password is hunter2")        # documented limit


# ── every step's error text is scrubbed, not only classifications ───────────────────────────────────
LEAK = "postgresql://svc:Sup3rS3cret@db/x token=TOPSECRET99"


def _assert_clean(*texts):
    for t in texts:
        assert "Sup3rS3cret" not in t and "TOPSECRET99" not in t, t


def _plain_run(registry, monkeypatch, sub_resources=None, publish_boom=None):
    from resource_explorer import repo_publish
    from resource_explorer.surveyors import survey_snapshot
    registry.save_context("repo", "p", {"enrichment": {}})
    monkeypatch.setattr(repo_publish, "resolve_project_context", lambda *a, **k: object())
    monkeypatch.setattr(survey_snapshot, "latest", lambda *a, **k: object())
    if publish_boom:
        def boom(*a, **k): raise _Err("publish " + LEAK, LEAK)
        monkeypatch.setattr(repo_publish, "publish_snapshot", boom)
    else:
        monkeypatch.setattr(repo_publish, "publish_snapshot", lambda *a, **k: {
            "ok": True, "asset_guid": "a-1", "read_back": True, "surveyed_at": "2026-10-07", "reused": False,
            "report_guid": "r", "annotation_count": 0})
    monkeypatch.setattr("resource_explorer.egeria_identity.classification_client", lambda *a, **k: Recorder())
    monkeypatch.setattr(registry, "get_egeria_asset_guid", lambda slug: "a-1")
    if sub_resources:
        def boom2(*a, **k): raise _Err("subs " + LEAK, LEAK)
        monkeypatch.setattr("resource_explorer.resource_scope.publish_chosen", boom2)
    rec = Curations(registry).create("repo", "p", author="dan", selection={"sub_resources": sub_resources or []},
                                     manifest={}, steps=list(wf.STEPS))
    out = wf.execute_curation(registry, rec["id"])
    return rec["id"], {s["name"]: s for s in out["steps"]}


def _http(registry, monkeypatch, cid):
    from fastapi.testclient import TestClient
    from resource_explorer.web.app import app
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    r = TestClient(app).get(f"/api/projects/p/curate/commits/{cid}")
    assert r.status_code == 200
    return r.text


def test_a_failed_publish_step_is_scrubbed_in_detail_more_log_and_http(registry, monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        cid, by = _plain_run(registry, monkeypatch, publish_boom=True)
    st = by["publish_asset"]
    assert st["state"] == "failed"
    _assert_clean(st["detail"], st.get("more", ""), *[r.getMessage() for r in caplog.records], _http(registry, monkeypatch, cid))


def test_a_failed_sub_resources_step_is_scrubbed_in_detail_more_log_and_http(registry, monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        cid, by = _plain_run(registry, monkeypatch, sub_resources=["docs"])
    st = by["sub_resources"]
    assert st["state"] == "failed"
    _assert_clean(st["detail"], st.get("more", ""), *[r.getMessage() for r in caplog.records], _http(registry, monkeypatch, cid))


def test_the_run_queue_catch_all_scrubs_row_outcome_and_log(registry, monkeypatch, caplog):
    import logging
    import resource_explorer.run_queue as rq
    run_id = registry.enqueue_run("scouting_scan", {"slug": "p"})
    row = registry.claim_next_run("host:1", {"pid": 1})

    def boom(target, ref):
        raise RuntimeError("crash " + LEAK)

    monkeypatch.setitem(rq.HANDLERS, "scouting_scan", boom)
    with caplog.at_level(logging.ERROR):
        outcome = rq.execute_run(row, registry)
    _assert_clean(outcome.error, registry.get_run(run_id)["error"], *[r.getMessage() for r in caplog.records])
    assert "crash" in outcome.error


# ── node parity: more inputs ────────────────────────────────────────────────────────────────────────
def test_page_and_python_agree_on_edge_inputs(tmp_path):
    """'6.0' and '00' are not an ordinal or a name, so they take the owner's rule for other free text:
    Project Lifetime (2) with the text kept as the note. That is acceptable and pinned here."""
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = tmp_path / "rb.mjs"
    js.write_text((Path(rb.__file__).parent / "web/static/next/retention-basis.js").read_text())
    cases = ["  ", "Other", "TIMEBOXED_LIFETIME", "6.0", None, "00", "OTHER", "99", "  Team   Lifetime "]
    prog = (f"import('{js.as_uri()}').then(m => console.log(JSON.stringify({json.dumps(cases)}.map(v => m.resolveRetention({{value: v}})))))")
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [rb.resolve({"value": v}) for v in cases]
    assert rb.resolve({"value": "6.0"}) == {"basis": "PROJECT_LIFETIME", "note": "6.0", "carried": True}
    assert rb.resolve({"value": "00"})["basis"] == "PROJECT_LIFETIME"
    assert rb.resolve({"value": "  "})["basis"] == "" and rb.resolve({"value": None})["basis"] == ""


# ── scrub_text, third round ─────────────────────────────────────────────────────────────────────────
THIRD = [
    ('password: "unterminated body that was cut', "unterminated"),
    ('{"password": "cut off here', "cut off"),
    ("DETAIL:  Key (api_key)=(sk-123abc) already exists.", "sk-123abc"),
    ("Key (client_secret, id)=(s3cr3t, 7) already exists", "s3cr3t"),
    ('"credential": {"user":"u","value":"p4ss"} tail', "p4ss"),
    ('"credential": {"a": {"b": "deep9"}, "c": 1} tail', "deep9"),
    ("auth: {unbalanced p4ss", "p4ss"),
    ("key AKIAABCDEFGHIJKLMNOP leaked", "AKIAABCDEFGHIJKLMNOP"),
    ("used sk-proj_abcdef123456 here", "sk-proj_abcdef123456"),
    ("ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2", "a1B2c3D4e5F6g7H8i9J0k1L2"),
]


@pytest.mark.parametrize("text,secret", THIRD)
def test_scrub_text_third_round(text, secret):
    assert secret not in scrub_text(text), scrub_text(text)


def test_object_value_is_masked_as_a_unit_and_the_tail_survives():
    assert scrub_text('"credential": {"user":"u","value":"p4ss"} tail') == '"credential": *** tail'
    assert scrub_text("Key (id)=(5) already exists") == "Key (id)=(5) already exists"      # not a credential name


def test_documented_limits():
    # an UNQUOTED value ends at whitespace; a quoted one is masked whole
    assert scrub_text("password=a b") == "password=*** b"
    assert scrub_text('password="a b"') == "password=***"
    # Bearer masks 6+ characters only ('Bearer auth failed' is prose); a base64 Basic with no digit or '=' is not seen
    assert scrub_text("Bearer auth failed") == "Bearer auth failed"
    assert "QWxhZGRpbg" in scrub_text("Basic QWxhZGRpbg")


def test_a_quoted_value_does_not_run_across_lines():
    out = scrub_text('password: "cut\nnext line stays and so does password-free text')
    assert "next line stays" in out and "cut" not in out


@pytest.mark.parametrize("text", [
    "a task-force risk-based review", "skip-level sk- only", "AKIA short", "ghp_ short",
    "Key (id)=(5) already exists", "the card was declined: reason=insufficient funds",
])
def test_negative_controls_round_three(text):
    assert scrub_text(text) == text


# ── the run boundary covers every handler ───────────────────────────────────────────────────────────
def test_run_outcome_scrubs_its_error_whatever_handler_built_it():
    from resource_explorer.run_queue import RunOutcome
    out = RunOutcome(state="failed", error="x: " + LEAK)
    _assert_clean(out.error)
    assert out.error.startswith("x: ")


def test_finish_run_scrubs_what_it_stores(registry):
    run_id = registry.enqueue_run("scouting_scan", {"slug": "p"})
    registry.finish_run(run_id, "failed", error="boom " + LEAK)
    _assert_clean(registry.get_run(run_id)["error"])


def test_the_materialise_handler_scrubs_all_three_error_sources(registry, monkeypatch):
    import resource_explorer.run_queue as rq
    from resource_explorer.workflows import curate as wcur
    registry.add(Project(slug="q", display_name="Q", github_url="https://github.com/x/q", description="")) \
        if registry.get("q") is None else None
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)

    def mat(reg, kind, slug, path, verdict):
        if path == "a":
            return {"status": "error", "error": "res " + LEAK}
        if path == "b":
            raise RuntimeError("raised " + LEAK)
        return {"guid": "g"}

    monkeypatch.setattr(wcur, "materialize_component_if_accepted", mat)
    monkeypatch.setattr(wcur, "promote_to_publish_zones", lambda g: {"status": "error", "error": "promo " + LEAK})
    monkeypatch.setattr(wcur, "record_promotion", lambda *a, **k: None)
    out = rq._materialize_components({"slug": "p", "paths": ["a", "b", "c"]})
    assert out.state == "failed"
    _assert_clean(out.error)
    assert "res " in out.error and "raised" in out.error and "promo " in out.error


def test_the_tick_loop_logs_a_scrubbed_traceback(monkeypatch, caplog):
    import logging
    import resource_explorer.run_queue as rq
    import resource_explorer.concurrency as conc
    runner = rq.QueueRunner(poll_interval=0.01)

    def boom(fn):
        runner._stop.set()
        raise RuntimeError("tick " + LEAK)

    monkeypatch.setattr(conc, "run_sync", boom)
    with caplog.at_level(logging.ERROR):
        runner._loop()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("tick failed" in m for m in msgs)
    _assert_clean(*msgs, *[r.exc_text or "" for r in caplog.records])
