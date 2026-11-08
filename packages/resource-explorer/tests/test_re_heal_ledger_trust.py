"""RE-HEAL-LEDGER-TRUST (brief 2026-11-07): after an Egeria reset the heal trusted its own ledger, hid per-command
failures behind exit 0, and linked executors before the engine existed. One test group per decision, in §3 order.

Egeria is never contacted: the canary read and element lookups are monkeypatched, the pyegeria boundary is
autospecced (test_dr_egeria_runner's Fakes), the `dr_egeria` child is faked at `bootstrap.subprocess.run`, and the
registry is a temp SQLite one.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from resource_explorer import bootstrap as bs
from resource_explorer import executor_links as xl
from tests.test_dr_egeria_runner import IDENT, OK_OUTPUT_ROWS, Fakes, _rows, _summary, reg, root  # noqa: F401
from tests.test_engine_host_self_heal import OPT, _canary, _engine_batch

FIXTURES = Path(__file__).parent / "fixtures" / "executor_links"
X_QN = "ResourceExplorerSurvey::re-x"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from resource_explorer import engine_host

    monkeypatch.delenv(OPT, raising=False)
    monkeypatch.setattr(engine_host, "_engine_answer", {"present": None, "at": ""})
    monkeypatch.setattr(bs, "_repository_seen", {"id": "", "at": ""})
    with bs._status_lock:
        bs._status.clear()
    yield
    with bs._status_lock:
        bs._status.clear()


# ── decision 3: per-command failure is failure ─────────────────────────────────

#: database-steps.md's first run on 2026-11-07 (engine-host-heal3-1107.txt, executor-links-run-1107.txt): the types
#: were created, every executor link failed, and the child still exited 0 ("OK | ok").
NOT_FOUND = "Referenced element 'ResourceExplorerSurvey' for attribute 'Governance Engine' not found."
RESET_ROWS = (
    [("Create Governance Action Type", "SUCCESS", f"Database step {n}", f"GovActionProcessStep::Db::{n}",
      "Created Governance Action Type") for n in ("a", "b", "c")]
    + [("Link Action to Action Executor", "FAILURE", "", "", NOT_FOUND) for _ in range(3)])


def _child(monkeypatch, stdout: str, returncode: int = 0):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(kwargs.get("cwd"))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(bs.subprocess, "run", fake_run)
    return calls


def test_exit_zero_with_a_failed_command_is_not_ok_and_names_it(tmp_path, monkeypatch):
    _child(monkeypatch, _summary(RESET_ROWS, errors={
        "Link Action to Action Executor (GovActionProcessStep::Db::a)": [NOT_FOUND]}))
    doc = tmp_path / "database-steps.md"
    doc.write_text("x")
    out = bs.run_dr_egeria_document(doc, identity=IDENT)
    assert out.ok is False
    assert out.detail.startswith("3 of 6 commands failed (3 failure, 3 success); first: Link Action to Action Executor")
    assert NOT_FOUND in out.detail


def test_exit_zero_with_every_command_ok_is_ok(tmp_path, monkeypatch):
    _child(monkeypatch, _summary(OK_OUTPUT_ROWS))
    doc = tmp_path / "d.md"
    doc.write_text("x")
    out = bs.run_dr_egeria_document(doc, identity=IDENT)
    assert out.ok is True and out.detail == "2 commands: 2 success"


def test_exit_zero_with_no_summary_is_not_known_so_not_ok(tmp_path, monkeypatch):
    _child(monkeypatch, "OK | ok\n")
    doc = tmp_path / "d.md"
    doc.write_text("x")
    out = bs.run_dr_egeria_document(doc, identity=IDENT)
    assert out.ok is False and "no per-command summary" in out.detail and "results not known" in out.detail


def test_a_plain_batch_heal_stops_at_the_document_whose_commands_failed(tmp_path, monkeypatch):
    docs = tmp_path / "dr-egeria"
    (docs / "plainb").mkdir(parents=True)
    for name in ("one.md", "two.md"):
        (docs / "plainb" / name).write_text("x")
    (docs / "plainb" / bs.BATCH_MANIFEST_FILE).write_text(json.dumps(
        {"canary": {"qualified_name": "Q"}, "files": ["one.md", "two.md"]}))
    monkeypatch.setenv("EGERIA_USER_ID", "svc")
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "pw")
    calls = _child(monkeypatch, _summary(RESET_ROWS))
    ok, detail = bs.heal_batch(bs.discover_batches(docs)[0])
    assert ok is False and detail.startswith("one.md: 3 of 6 commands failed")
    assert len(calls) == 1, "two.md must not run after one.md's commands failed"


# ── decision 2: rows carry the repository id; another repository's rows are not ok rows ──

def _ok_row(reg, doc: str, *, repo: str = "", row_id: str = "", ts: str = "2026-11-01T10:00:00+00:00"):
    from resource_explorer.registry import ActivityEntry

    detail = {"document": doc, "sha256": "s", "dry_run": False}
    if repo:
        detail["metadata_collection_id"] = repo
    reg.write_activity(ActivityEntry(
        id=row_id or f"row-{doc}-{repo}", ts=ts, operation="dr_egeria", intent="enrichment",
        entity_type="dr_egeria_document", entity_slug=doc, entity_name=doc, entity_location="", status="ok",
        summary="ok", detail=json.dumps(detail), items=[]))


def _two_document_batch(root):
    (root / "engine-host" / "library").mkdir()
    (root / "engine-host" / "library" / "x.md").write_text(
        (root / "plain" / "only.md").read_text().replace("ResourceExplorerSurvey::re-db-derived", X_QN))
    _engine_batch(root, files=("decl.md", "library/x.md"))


def test_rows_from_another_repository_are_not_ok_rows(root, reg):
    _two_document_batch(root)
    batch = bs.discover_batches(root)[0]
    _ok_row(reg, "engine-host/decl.md", repo="repo-A")
    _ok_row(reg, "engine-host/library/x.md", repo="repo-B")
    wanted, rows = bs.applied_documents(batch, reg, collection_id="repo-A")
    assert {json.loads(r["detail"])["document"] for r in rows} == {"engine-host/decl.md"}
    count = bs.applied_count(batch, reg, collection_id="repo-A")
    assert count == {"applied": 1, "total": 2, "pending": ["library/x.md"], "other_repository": 1}
    assert bs.applied_words(count) == "1 of 2 documents applied · 1 run row from another repository ignored"
    # The other repository sees the mirror image; an unknown current id filters nothing (a hint either way).
    assert bs.applied_count(batch, reg, collection_id="repo-B")["pending"] == ["decl.md"]
    assert bs.applied_count(batch, reg, collection_id="")["applied"] == 2


def test_an_unstamped_row_is_kept_as_a_hint(root, reg):
    _engine_batch(root)
    _ok_row(reg, "engine-host/decl.md")
    assert bs.applied_count(bs.discover_batches(root)[0], reg, collection_id="repo-A")["applied"] == 1


def test_the_price_line_filters_on_the_last_id_a_heal_read_without_an_egeria_call(root, reg, monkeypatch):
    _engine_batch(root)
    _ok_row(reg, "engine-host/decl.md", repo="repo-old")
    monkeypatch.setattr(bs, "_repository_seen", {"id": "repo-new", "at": "T"})
    price = bs.batch_price("engine-host", root, registry=reg)
    assert price["documents_applied_words"] == "0 of 1 documents applied · 1 run row from another repository ignored"


def test_repository_id_is_read_from_an_element_header():
    header = {"origin": {"homeMetadataCollectionId": "9905c3cb-94c5-4494-9229-0d6f69c0b842"}}
    assert bs.repository_id_of({"elementHeader": header}) == "9905c3cb-94c5-4494-9229-0d6f69c0b842"
    assert bs.repository_id_of(header) == "9905c3cb-94c5-4494-9229-0d6f69c0b842"
    recorded = json.loads((FIXTURES / "related-postgres_schema_and_stats-1107.json").read_text())
    assert bs.repository_id_of(recorded["elementList"][0]) == "9905c3cb-94c5-4494-9229-0d6f69c0b842"
    assert bs.repository_id_of("No elements found") == "" and bs.repository_id_of({}) == ""


def test_the_runner_stamps_its_row_with_the_repository_it_wrote_to(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    fakes.expert.get_metadata_element_by_unique_name.side_effect = lambda *a, **k: {
        "elementHeader": {"origin": {"homeMetadataCollectionId": "repo-A"}}}
    out = runner.run_document("plain/only.md", force=True, identity=IDENT, registry=reg)
    detail = json.loads(next(r for r in _rows(reg) if r["id"] == out["activity_id"])["detail"])
    assert detail["metadata_collection_id"] == "repo-A"


def test_a_stamp_that_cannot_be_read_leaves_the_row_unstamped(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    fakes.expert.get_metadata_element_by_unique_name.side_effect = RuntimeError("Egeria down")
    out = runner.run_document("plain/only.md", force=True, identity=IDENT, registry=reg)
    assert out["status"] == "ok"
    assert "metadata_collection_id" not in json.loads(_rows(reg)[0]["detail"])


# ── decision 1: the ledger is a hint, Egeria is the truth ─────────────────────

def _lookups(monkeypatch, answers: dict):
    asked = []

    def fake(qualified_name, expert=None):
        asked.append(qualified_name)
        return answers.get(qualified_name)

    monkeypatch.setattr(bs, "created_element_present", fake)
    return asked


def _press(root):
    return bs.check_and_heal(root, only="engine-host", requested_by="dan")["batches"]["engine-host"]


def test_an_ok_row_whose_element_is_absent_is_re_applied_with_words(root, reg, monkeypatch):
    """The 2026-11-07 case: the ledger survived the reset, Egeria did not. "8 of 8 applied" must not stop a run."""
    from resource_explorer import dr_egeria_runner as runner

    _two_document_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    _ok_row(reg, "engine-host/decl.md", ts="2026-11-07T09:00:00+00:00")
    _ok_row(reg, "engine-host/library/x.md", ts="2026-11-07T09:00:00+00:00")
    assert bs.applied_count(bs.discover_batches(root)[0], reg)["applied"] == 2          # the ledger: "2 of 2"
    Fakes(monkeypatch)
    asked = _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": True, X_QN: False})
    seen = []
    real = runner.run_document
    monkeypatch.setattr(runner, "run_document", lambda path, **kw: seen.append(path.name) or real(path, **kw))
    out = _press(root)
    assert out["action"] == "completed", out
    assert seen == ["x.md"]
    assert asked == ["ResourceExplorerSurvey::re-db-derived", X_QN]
    assert ("ledger says library/x.md applied on 2026-11-07 but ResourceExplorerSurvey::re-x is absent: "
            "re-applying") in out["reason"]
    assert "skipped 1 verified present: decl.md" in out["reason"]


def test_an_ok_row_whose_elements_are_present_is_skipped_and_said_verified(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _two_document_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    _ok_row(reg, "engine-host/decl.md", ts="2026-11-07T09:00:00+00:00")
    _ok_row(reg, "engine-host/library/x.md", ts="2026-11-07T09:00:00+00:00")
    _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": True, X_QN: True})
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("verified present is never re-run"))
    out = _press(root)
    assert out["action"] == "present"
    assert out["reason"].startswith("already declared · content applied on 2026-11-07 · verified present · "
                                    "2 of 2 documents applied · nothing left to run")


def test_undetermined_refuses_the_whole_pass_even_with_ok_rows(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _two_document_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    _ok_row(reg, "engine-host/decl.md", ts="2026-11-07T09:00:00+00:00")
    _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": True})        # x.md's element: None
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("undetermined is never absent"))
    before = len(_rows(reg))
    out = _press(root)
    assert out["action"] == "present"
    assert "· NOT verified ·" in out["reason"]
    assert out["reason"].endswith("could not verify library/x.md's elements in Egeria (ResourceExplorerSurvey::re-x "
                                  "could not be looked up) · nothing run")
    assert len(_rows(reg)) == before


def test_a_partly_present_document_is_not_re_run_and_is_named(root, reg, monkeypatch):
    """Some elements present, some absent: a re-run would repeat its non-idempotent links; a person decides."""
    from resource_explorer import dr_egeria_runner as runner

    doc = (root / "plain" / "only.md").read_text()
    (root / "engine-host" / "decl.md").write_text(doc + doc.replace("re-db-derived", "re-second"))
    _engine_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": True, "ResourceExplorerSurvey::re-second": False})
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("a partly present document is not re-run"))
    out = _press(root)
    assert out["action"] == "present" and "· NOT verified ·" in out["reason"]
    assert ("NOT run, partly present (links are not idempotent; run by hand): decl.md "
            "(ResourceExplorerSurvey::re-db-derived present; ResourceExplorerSurvey::re-second absent)") in out["reason"]


# ── decision 4: requires, ordering, blocked_by; the default flips on ───────────

def _dependent_batch(docs, name="survey-definitions-database"):
    (docs / name).mkdir()
    (docs / name / "def.md").write_text("x")
    (docs / name / bs.BATCH_MANIFEST_FILE).write_text(json.dumps({
        "idempotent": False, "requires": ["engine-host"], "canary": {"qualified_name": "GovActionProcess::D"},
        "files": ["def.md"]}))
    # Folder order puts the dependent FIRST: `requires` must still heal engine-host before it.
    (docs / bs.FOLDER_ORDER_FILE).write_text(json.dumps({"folders": [name, "engine-host"]}))


def test_an_unset_setting_heals_the_engine_by_default_and_only_false_opts_out(root, monkeypatch):
    _engine_batch(root)
    assert bs.discover_batches(root)[0].opted_in is True
    for off in ("0", "false", "No", "OFF"):
        monkeypatch.setenv(OPT, off)
        assert bs.discover_batches(root)[0].opted_in is False, off
    monkeypatch.setenv(OPT, "true")
    assert bs.discover_batches(root)[0].opted_in is True


def test_requires_orders_the_requirement_first():
    a = bs.Batch("a", Path("."), "a", {}, [], requires=["c"])
    b = bs.Batch("b", Path("."), "b", {}, [])
    c = bs.Batch("c", Path("."), "c", {}, [])
    assert [x.batch_id for x in bs.order_by_requires([a, b, c])] == ["c", "a", "b"]
    cyc1 = bs.Batch("x", Path("."), "x", {}, [], requires=["y"])
    cyc2 = bs.Batch("y", Path("."), "y", {}, [], requires=["x"])
    assert {x.batch_id for x in bs.order_by_requires([cyc1, cyc2])} == {"x", "y"}


def test_a_dependent_is_blocked_while_its_requirement_is_opted_out_and_missing(root, monkeypatch):
    _engine_batch(root)
    _dependent_batch(root)
    monkeypatch.setenv(OPT, "false")
    _canary(monkeypatch, False)
    monkeypatch.setattr(bs, "heal_batch", lambda *a, **k: pytest.fail("nothing of a blocked batch runs"))
    out = bs.check_and_heal(root)["batches"]
    assert list(out) == ["engine-host", "survey-definitions-database"]
    assert out["engine-host"]["action"] == "opted_out"
    dep = out["survey-definitions-database"]
    assert dep["action"] == "blocked" and dep["blocked_by"] == "engine-host"
    assert dep["reason"].startswith("blocked by engine-host (opted_out: RE_ENGINE_HOST_HEAL is off and it is missing)")
    assert dep["reason"].endswith("nothing of this batch was run")


def test_a_dependent_heals_once_its_requirement_is_present_in_the_same_pass(root, monkeypatch):
    _engine_batch(root)
    _dependent_batch(root)
    answers = {"engine-host": [False, True], "survey-definitions-database": [False, True]}
    monkeypatch.setattr(bs, "canary_present", lambda b, expert=None: answers[b.batch_id].pop(0))
    monkeypatch.setattr(bs, "heal_identity", lambda: IDENT)
    healed = []
    monkeypatch.setattr(bs, "heal_batch", lambda b, **k: healed.append(b.batch_id) or (True, "ok"))
    out = bs.check_and_heal(root)["batches"]
    assert healed == ["engine-host", "survey-definitions-database"]
    assert out["survey-definitions-database"]["action"] == "healed"


def test_a_dependent_is_blocked_when_its_requirement_failed_to_heal(root, monkeypatch):
    _engine_batch(root)
    _dependent_batch(root)
    monkeypatch.setattr(bs, "canary_present", lambda b, expert=None: False)
    monkeypatch.setattr(bs, "heal_identity", lambda: IDENT)
    healed = []
    monkeypatch.setattr(bs, "heal_batch", lambda b, **k: healed.append(b.batch_id) or (False, "database-steps.md: …"))
    out = bs.check_and_heal(root)["batches"]
    assert healed == ["engine-host"]
    assert out["survey-definitions-database"]["action"] == "blocked"
    assert out["survey-definitions-database"]["reason"].startswith("blocked by engine-host (failed)")


def test_a_one_batch_run_of_a_dependent_reads_its_requirement_live(root, monkeypatch):
    _engine_batch(root)
    _dependent_batch(root)
    monkeypatch.setattr(bs, "canary_present", lambda b, expert=None: None if b.batch_id == "engine-host" else False)
    monkeypatch.setattr(bs, "heal_batch", lambda *a, **k: pytest.fail("not with the engine undetermined"))
    out = bs.check_and_heal(root, only="survey-definitions-database")["batches"]
    assert out == {"survey-definitions-database": {
        "action": "blocked", "blocked_by": "engine-host",
        "reason": "blocked by engine-host (unknown): its steps link to what engine-host declares, so nothing of this "
                  "batch was run"}}


def test_a_present_dependent_is_not_blocked(root, monkeypatch):
    _engine_batch(root)
    _dependent_batch(root)
    monkeypatch.setenv(OPT, "false")
    monkeypatch.setattr(bs, "canary_present", lambda b, expert=None: b.batch_id != "engine-host")
    out = bs.check_and_heal(root)["batches"]
    assert out["survey-definitions-database"] == {"action": "ok", "reason": "canary present"}


def test_the_real_survey_definition_batches_require_engine_host_and_run_after_it():
    batches = bs.discover_batches()
    ids = [b.batch_id for b in batches]
    by = {b.batch_id: b for b in batches}
    for dep in ("survey-definitions", "survey-definitions-database"):
        assert by[dep].requires == ["engine-host"], dep
        assert ids.index("engine-host") < ids.index(dep)
    # Every batch whose documents link executors to RE's engine declares the requirement (the builder's list).
    for b in batches:
        if b.batch_id == "engine-host":
            continue
        links = xl.declared_links_of([b.path / f for f in b.files])
        assert bool(links) == ("engine-host" in b.requires), b.batch_id


# ── decision 5: the executor-link reconciler ─────────────────────────────────

RECORDED = json.loads((FIXTURES / "related-postgres_schema_and_stats-1107.json").read_text())
ENGINE_GUID = RECORDED["elementList"][0]["element"]["elementGUID"]


def _executor_entry(rel_guid: str, request_type: str, created: str) -> dict:
    e = json.loads(json.dumps(RECORDED["elementList"][0]))
    e["relationshipGUID"] = rel_guid
    e["relationshipProperties"]["propertiesAsStrings"]["requestType"] = request_type
    e["versions"]["createTime"] = created
    return e


def _g(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


class Expert:
    """MetadataExpert's two reads (and the delete), answering from the recorded 2026-11-07 shape."""

    def __init__(self, declared, *, missing=(), several=(), absent=(), unreadable=(), engine=True):
        self.guids = {xl.ENGINE_QUALIFIED_NAME: ENGINE_GUID} if engine else {}
        self.related: dict[str, object] = {}
        self.deleted: list[str] = []
        self.unreadable = set(unreadable)
        for n, d in enumerate(declared):
            gat = d.governance_action_type
            if gat in absent:
                continue
            guid = _g(n)
            self.guids[gat] = guid
            if gat in missing:
                self.related[guid] = {"elementList": [x for x in RECORDED["elementList"]
                                                      if x["type"]["typeName"] != "GovernanceActionExecutor"]}
            elif gat in several:
                self.related[guid] = {"elementList": [
                    _executor_entry(f"rel-{n}-new", d.request_type, "2026-11-07T17:00:00+00:00"),
                    _executor_entry(f"rel-{n}-old", d.request_type, "2026-11-07T16:22:50+00:00")]}
            else:
                self.related[guid] = {"elementList": [_executor_entry(f"rel-{n}", d.request_type,
                                                                      "2026-11-07T16:22:50+00:00")]}

    def get_metadata_element_by_unique_name(self, name, *a, **k):
        if name in self.unreadable:
            raise RuntimeError("Egeria answered 503")
        guid = self.guids.get(name)
        return {"elementGUID": guid} if guid else "No elements found"

    def get_all_related_elements(self, guid, graph_query_depth=1, **k):
        return self.related[guid]

    def delete_related_elements(self, relationship_guid, body=None):
        self.deleted.append(relationship_guid)
        for answer in self.related.values():
            answer["elementList"] = [x for x in answer["elementList"] if x.get("relationshipGUID") != relationship_guid]


class Author:
    def __init__(self, expert):
        self.expert = expert
        self.links: list[tuple[str, str, dict]] = []

    def link_governance_action_executor(self, type_guid, engine_guid, body):
        self.links.append((type_guid, engine_guid, body))
        rt = body["properties"]["requestType"]
        self.expert.related[type_guid]["elementList"].append(_executor_entry("rel-added", rt, "2026-11-07T18:00:00Z"))


def _subset():
    return xl.declared_links_of([FIXTURES / "executor-links-subset-1107.md"])


def test_the_recorded_subset_declares_the_fifteen_links():
    links = _subset()
    assert len(links) == 15
    assert links[0] == xl.DeclaredLink("GovActionProcessStep::DatabaseScoutingSurvey::postgres_schema_and_stats",
                                       "ResourceExplorerSurvey", "re-postgres-schema-and-stats",
                                       "executor-links-subset-1107.md")
    assert all(d.governance_engine == "ResourceExplorerSurvey" and d.request_type.startswith("re-") for d in links)


def test_the_recorded_answer_reads_as_exactly_one_executor():
    d = _subset()[0]
    expert = Expert([d])
    expert.related[_g(0)] = RECORDED
    st = xl.read_state(expert, d, ENGINE_GUID)
    assert st.problem == xl.OK and st.executor_guids == ["c71d6a75-fdbd-44bc-87c6-5a86330903ce"]


def test_dry_run_reports_drift_and_writes_nothing():
    links = _subset()
    names = [d.governance_action_type for d in links]
    expert = Expert(links, missing=names[1:4], several=names[4:5], absent=names[5:6], unreadable=names[6:7])
    author = Author(expert)
    report = xl.reconcile(expert, links, author=author, apply=False)
    assert report.counts() == {"ok": 9, "missing": 3, "several": 1, "type_absent": 1, "unreadable": 1}
    assert report.drift == 6
    assert author.links == [] and expert.deleted == []
    assert report.words() == ("executor links: 15 declared to ResourceExplorerSurvey · 3 missing, 9 ok, 1 several, "
                              "1 type absent, 1 unreadable · drift 6")


def test_apply_adds_the_missing_and_keeps_the_oldest_of_several():
    links = _subset()
    names = [d.governance_action_type for d in links]
    expert = Expert(links, missing=names[1:3], several=names[3:4])
    author = Author(expert)
    report = xl.reconcile(expert, links, author=author, apply=True)
    assert [(t, e, b["properties"]) for t, e, b in author.links] == [
        (_g(1), ENGINE_GUID, {"class": "GovernanceActionExecutorProperties", "requestType": links[1].request_type}),
        (_g(2), ENGINE_GUID, {"class": "GovernanceActionExecutorProperties", "requestType": links[2].request_type})]
    assert expert.deleted == ["rel-3-new"], "the oldest link is kept"
    assert [s.action for s in report.states if s.action] == ["added · now ok", "added · now ok", "removed 1 · now ok"]
    again = xl.reconcile(expert, links, author=author, apply=False)
    assert again.drift == 0, "reconciled to one, whatever happened before"


def test_an_absent_engine_refuses_the_pass_without_a_write():
    links = _subset()
    expert = Expert(links, engine=False)
    author = Author(expert)
    report = xl.reconcile(expert, links, author=author, apply=True)
    assert report.refused.startswith("the engine ResourceExplorerSurvey is not in Egeria: every link would fail")
    assert report.states == [] and author.links == []


def test_the_reconciler_is_engine_hosts_post_heal_and_each_dependents_check():
    by = {b.batch_id: b for b in bs.discover_batches()}
    assert by["engine-host"].post_heal == {
        "script": "scripts/reconcile_executor_links.py", "args": ["--apply"],
        "reason": by["engine-host"].post_heal["reason"]}
    for dep in ("survey-definitions", "survey-definitions-database"):
        checks = [c for c in by[dep].post_heal_checks if c["script"] == "scripts/reconcile_executor_links.py"]
        assert checks and checks[0]["args"] == ["--batch", dep] and "--apply" not in checks[0]["args"], dep
    assert (Path(bs.__file__).resolve().parent.parent / "scripts" / "reconcile_executor_links.py").is_file()


def test_post_heal_args_reach_the_script(monkeypatch):
    seen = []
    monkeypatch.setattr(bs.subprocess, "run", lambda argv, **k: seen.append(argv) or SimpleNamespace(
        returncode=0, stdout="executor links: 0 declared\n", stderr=""))
    batch = next(b for b in bs.discover_batches() if b.batch_id == "engine-host")
    ok, words = bs._run_post_heal(batch)
    assert ok and seen[0][-2:] == [str(Path(bs.__file__).resolve().parent.parent
                                       / "scripts" / "reconcile_executor_links.py"), "--apply"]
    assert words == "post_heal ok — executor links: 0 declared"


def test_the_runner_path_now_runs_post_heal(root, monkeypatch):
    """engine-host is a runner batch: its heal used to return before post_heal, so the reconciler never ran."""
    _engine_batch(root)
    manifest = root / "engine-host" / bs.BATCH_MANIFEST_FILE
    raw = json.loads(manifest.read_text())
    raw["post_heal"] = {"script": "scripts/reconcile_executor_links.py", "args": ["--apply"]}
    manifest.write_text(json.dumps(raw))
    monkeypatch.setattr(bs, "_heal_with_runner", lambda *a, **k: (True, "1 document run · 2 ok"))
    monkeypatch.setattr(bs, "_run_post_heal", lambda b: (True, "post_heal ok — executor links: drift 0"))
    ok, detail = bs.heal_batch(bs.discover_batches(root)[0], waiver="w")
    assert ok and detail == "1 document run · 2 ok; post_heal ok — executor links: drift 0"


def test_a_verified_press_with_nothing_to_run_still_reconciles_the_links(root, reg, monkeypatch):
    """2026-11-07: every type present (made by hand), every link missing. The press must reach the reconciler."""
    _engine_batch(root)
    manifest = root / "engine-host" / bs.BATCH_MANIFEST_FILE
    raw = json.loads(manifest.read_text())
    raw["post_heal"] = {"script": "scripts/reconcile_executor_links.py", "args": ["--apply"]}
    manifest.write_text(json.dumps(raw))
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": True})
    ran = []
    monkeypatch.setattr(bs, "_run_post_heal", lambda b: ran.append(b.batch_id) or (True, "post_heal ok — added 15"))
    out = _press(root)
    assert ran == ["engine-host"]
    assert out["action"] == "present" and out["reason"].endswith("nothing left to run · skipped 1 verified present: "
                                                                 "decl.md · post_heal ok — added 15")
