"""Curate — the plan, the record, the commit, and the population rule.

Repo Handoff item 6 and the Curate wireframe: three columns answering
one question, candidates with evidence never auto-applied, testimony
copied and measurements linked, only worthy things curated.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resource_explorer.curate_plan import CURATE_POPULATION, Curations, build_plan
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    # Brief I: a Curate commit runs as the person, so the signed-in user carries an Egeria token.
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": "peterprofile", "egeria_token": "tok-peterprofile"})
    from resource_explorer.web.app import app
    return TestClient(app)


def _seed(registry):
    """A repo with a declared distribution, implied interfaces, Dockerfiles,
    sub-resources, and one enrichment judgement."""
    registry.upsert_finding("p", "distribution", [{
        "check_name": "python:pyegeria", "label": "published",
        "summary": "pyegeria — python distribution declared in pyproject.toml; 3 command-line entry point(s): a, b, c; publish workflow: release.yml.",
        "detail": {"name": "pyegeria", "ecosystem": "python", "scripts": ["a", "b", "c"], "packages": [], "manifest": "pyproject.toml", "publish_workflow": "release.yml"}}])
    # deployment_evidence, not the raw `distribution` finding, is what
    # curate_plan reads for "what it is" (docs/Backlog.md, "Catalogue in
    # layers" -- SoftwareLibrary per distribution was a type error). Three
    # console scripts is deployment evidence, so the verdict is "application".
    registry.upsert_finding("p", "deployment_evidence", [
        {"check_name": "distribution", "label": "application",
         "summary": "pyegeria — application (console_script)",
         "detail": {"name": "pyegeria", "ecosystem": "python",
                    "evidence": [{"kind": "console_script", "path": "pyproject.toml", "detail": "a"}],
                    "consumers_in_repo": [], "could_not_check": []}},
        {"check_name": "coverage", "label": "checked",
         "summary": "1 application(s), 0 libraries, 0 unknown",
         "detail": {"application": 1, "library": 0, "unknown": 0, "distribution_count": 1}}])
    registry.upsert_finding("p", "interface_surface", [
        {"check_name": "cli", "label": "implied", "summary": "Depends on click — suggests a cli"},
        {"check_name": "http_api", "label": "implied", "summary": "Depends on fastapi — suggests a http api"},
        {"check_name": "published_spec", "label": "no", "summary": "No specification file found"}])
    registry.upsert_finding("p", "repo_conventions", [
        {"check_name": "deployment_docker", "label": "pass", "summary": "Deployment/container evidence found", "detail": {"files": ["Dockerfile", "a/Dockerfile"]}}])
    registry.upsert_finding("p", "repo_sub_resource_survey", [
        {"check_name": "docs", "label": "worthy", "summary": "top_level_structural_folder", "detail": {"path": "docs", "kind": "folder"}},
        {"check_name": "docs/guide.md", "label": "worthy", "summary": "well_known_file", "detail": {"path": "docs/guide.md", "kind": "file"}},
        {"check_name": ".idea", "label": "not_worthy", "summary": "generated", "detail": {"path": ".idea", "kind": "folder"}}])
    # The fact layer credits an analysis only for a recorded run; without
    # these, everything above reads as never_run and the plan stays honest
    # about it -- which is the third test below.
    from resource_explorer.activity_logger import log_analysis_run
    for aid in ("interface_surface", "repo_conventions", "sub_resource_survey", "data_file_profiling",
                "architecture_recovery", "dependency_analysis", "manifest_parse"):
        log_analysis_run(registry, "repo", "p", "P repo", "success", f"{aid} ran", aid)
    registry.save_context("repo", "p", {"enrichment": {
        "sensitivity": {"value": "internal", "kind": "judgement", "author": "peterprofile", "set_at": "2026-09-01T00:00:00+00:00"},
        "owner": {"value": "peterprofile", "kind": "judgement", "author": "peterprofile", "set_at": "2026-09-01T00:00:00+00:00", "interim": True},
    }})


class TestWhatWeKnowIsWhatGetsWritten:
    """Brief S1b: tab 1's "What we know" block (plan["know"]) and "what gets written" (plan["writes"]) are built from
    ONE function (curate_plan.context_facts); they must never disagree."""

    def _declare(self, registry):
        registry.save_context("repo", "p", {"enrichment": {
            "sensitivity": {"value": "internal", "kind": "judgement", "author": "mandy", "set_at": "2026-10-03T09:00:00+00:00"},
            "criticality": {"value": "high", "kind": "judgement", "author": "", "set_at": ""},
            "retention": {"value": "keep forever", "kind": "judgement", "author": "mandy", "set_at": "2026-10-03T09:00:00+00:00"},
            "owner": {"value": "dwolfson", "kind": "judgement", "author": "dwolfson", "set_at": "2026-10-02T09:00:00+00:00", "interim": True},
            "licence": {"value": "Apache-2.0", "kind": "judgement", "author": "dwolfson", "set_at": "2026-10-02T09:00:00+00:00"},
        }})
        registry.add_doc_source("repo", "p", "https://x.example/runbook", label="Runbook", source_type="runbook", added_by="dwolfson")

    def test_the_block_and_the_write_plan_agree(self, registry):
        self._declare(registry)
        plan = build_plan(registry, "p")
        know = {f["key"]: f for f in plan["know"]}
        w = plan["writes"]
        # every classification the commit writes is the same fact the block shows
        assert {c["key"] for c in w["classifications"]} == {"sensitivity", "criticality", "retention"}
        for c in w["classifications"]:
            f = know[c["key"]]
            assert (c["value"], c["author"], c["set_at"], c["interim"], c["skipped"], c["classification"]) == \
                   (f["shown"], f["author"], f["set_at"], f["interim"], f["skipped"], f["label"])
        assert (w["owner"]["value"], w["owner"]["author"], w["owner"]["interim"]) == \
               (know["owner"]["shown"], know["owner"]["author"], know["owner"]["interim"]) == ("dwolfson", "dwolfson", True)
        assert w["licence"] == know["licence"]["shown"] == "Apache-2.0"
        # retention is shown in the commit's own words (basis carried from the existing text), not the raw value
        assert know["retention"]["shown"].endswith("(from existing text)") and know["retention"]["skipped"] is False
        # an unsigned fact is flagged unsigned, a signed one is not
        assert know["criticality"]["unsigned"] is True and know["sensitivity"]["unsigned"] is False
        assert know["documentation"]["declared"] and know["documentation"]["sources"][0]["author"] == "dwolfson"

    def test_a_fact_that_is_not_set_is_not_declared_and_is_not_written(self, registry):
        plan = build_plan(registry, "p")
        know = {f["key"]: f for f in plan["know"]}
        assert [f["key"] for f in plan["know"]] == ["owner", "sensitivity", "criticality", "retention", "licence", "documentation"]
        assert not any(f["declared"] for f in plan["know"])
        assert plan["writes"]["classifications"] == [] and plan["writes"]["owner"]["value"] == "" and plan["writes"]["licence"] == ""
        assert know["owner"]["shown"] == "" and know["documentation"]["sources"] == []


class TestThePlan:
    def test_the_three_columns_are_candidates_with_evidence(self, registry):
        _seed(registry)
        plan = build_plan(registry, "p")
        kinds = {r["kind"]: r for r in plan["what_it_is"]}
        assert kinds["SoftwareCapability::pyegeria"]["label"] == "Software Capability · pyegeria"
        assert kinds["SoftwareCapability::pyegeria"]["source"] == "deployment_evidence"
        assert kinds["Endpoint"]["label"] == "Endpoint × 2 · cli, http_api · implied, no contract"
        assert kinds["InfrastructureAsset"]["label"] == "Infrastructure Asset? 2 Dockerfiles"   # a question mark, not "(suggested)"
        assert kinds["SoftwareCapability"]["state"] == "needs_human"                            # no intended use recorded
        assert all(r["candidate"] for r in plan["what_it_is"])
        assert all(r["source"] and r["evidence"] for r in plan["what_it_is"])
        holds = {r["kind"]: r for r in plan["what_it_holds"]}
        assert holds["SubResource"]["count"] == 2 and holds["SubResource"]["label"].startswith("2 of 3 files and folders")
        assert holds["DataFile"]["candidate"] is False        # none profiled: a count of zero is not a thing to confirm
        assert plan["relates"][0]["candidate"] is False        # already-catalogued deps: not looked up, said so

    def test_the_manifest_copies_testimony_and_links_measurements(self, registry):
        _seed(registry)
        w = build_plan(registry, "p")["writes"]
        assert w["classifications"] == [{"key": "sensitivity", "classification": "Confidentiality", "value": "internal", "skipped": False,
                                         "author": "peterprofile", "set_at": "2026-09-01T00:00:00+00:00", "interim": False, "review": False}]
        assert w["owner"] == {"value": "peterprofile", "interim": True, "author": "peterprofile"}
        assert w["catalogued"] is False and w["survey_reports_linked"] == 0

    def test_the_population_is_tracking_or_using_and_the_plan_says_which(self, registry):
        plan = build_plan(registry, "p")
        assert plan["disposition"] == "undecided" and plan["in_population"] is False
        registry.set_disposition("https://github.com/x/p", "tracking", resource_slug="p")
        assert build_plan(registry, "p")["in_population"] is True
        assert tuple(CURATE_POPULATION) == ("tracking", "using")

    def test_a_name_not_yet_read_is_said_not_guessed(self, registry):
        registry.upsert_finding("p", "manifest_parse", [])
        registry.upsert_metric("p", "manifest_parse", {"dependency_count": 3}, detail={"dependencies": {"manifests": ["pyproject.toml"]}})
        row = next(r for r in build_plan(registry, "p")["what_it_is"] if r["kind"] == "SoftwareCapabilityCandidate")
        assert "not yet measured" in row["label"] or "Software Capability?" in row["label"]


class TestTheRecord:
    def test_the_record_is_append_only_and_tracks_each_step(self, registry):
        c = Curations(registry)
        rec = c.create("repo", "p", author="peterprofile", selection={"confirm": ["SoftwareLibrary"]},
                       manifest={"entities": ["SoftwareLibrary"]}, steps=["publish_asset", "classifications"])
        assert rec["state"] == "queued" and [s["state"] for s in rec["steps"]] == ["pending", "pending"]
        c.set_step(rec["id"], "publish_asset", "done", "asset 123")
        c.set_step(rec["id"], "classifications", "failed", "no client")
        out = c.finish(rec["id"])
        assert out["state"] == "failed" and out["finished_at"]
        assert [s["state"] for s in out["steps"]] == ["done", "failed"]
        assert c.for_resource("repo", "p")[0]["id"] == rec["id"]
        with pytest.raises(ValueError):
            c.create("repo", "p", author="", selection={}, manifest={}, steps=[])


def _keep_a_survey(registry):
    """A kept survey: the commit publishes it, it does not run one (brief section 1)."""
    from resource_explorer.surveyors import survey_snapshot
    from resource_explorer.surveyors.survey_report import ClassificationAnnotation
    survey_snapshot.record_step(registry, "p", "repo_language", "2026-10-07T01:00:00", [
        ClassificationAnnotation(summary=t, analysis_step="repo_language", check_name="c", item_key=t) for t in "abc"])


class TestTheCommitRoute:
    def test_it_refuses_outside_the_population(self, client, registry):
        _seed(registry)
        r = client.post("/api/projects/p/curate/commit", json={"confirm": ["SoftwareLibrary"]})
        assert r.status_code == 409 and "tracking or using" in r.json()["detail"]

    def test_it_records_the_act_and_enqueues_the_run(self, client, registry):
        _seed(registry)
        _keep_a_survey(registry)
        registry.set_disposition("https://github.com/x/p", "using", resource_slug="p")
        # the press publishes what the selection RECORD says (brief 2a); a list in the request is not read
        registry.append_resource_scope_event("repo", "p", locator="docs", kind="folder", choice="include", author="peterprofile")
        r = client.post("/api/projects/p/curate/commit",
                        json={"confirm": ["SoftwareCapability::pyegeria", "Endpoint"], "sub_resources": [".idea"], "note": "go"})
        assert r.status_code == 200, r.text
        body = r.json()
        rec = body["curation"]
        assert rec["author"] == "peterprofile" and rec["state"] == "queued"
        assert rec["manifest"]["entities"] == ["SoftwareCapability::pyegeria", "Endpoint"]
        assert rec["manifest"]["contained"] == {"data_files": 0, "sub_resources": 1, "containers": 0, "files": 0, "folders": 1}
        assert [s["name"] for s in rec["steps"]] == ["publish_asset", "classifications", "sub_resources", "components"]
        run = registry.get_run(body["run_id"]) if hasattr(registry, "get_run") else None
        if run is not None:
            assert run["kind"] == "curate_commit" and json.loads(run["target"])["curation_id"] == rec["id"]
        assert client.get(f"/api/projects/p/curate/commits/{rec['id']}").json()["id"] == rec["id"]

    def test_a_non_candidate_is_refused(self, client, registry):
        _seed(registry)
        registry.set_disposition("https://github.com/x/p", "using", resource_slug="p")
        r = client.post("/api/projects/p/curate/commit", json={"confirm": ["Spaceship"]})
        assert r.status_code == 400

    def test_anonymous_is_refused(self, client, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
        assert client.post("/api/projects/p/curate/commit", json={"confirm": []}).status_code == 401


class TestTheCommitSteps:
    """The workflow with Egeria faked: each step lands on the record, a
    failure is a failed step and the rest still run, and the
    classifications carry the author and date."""

    def test_each_step_writes_its_outcome(self, registry, monkeypatch):
        _seed(registry)
        from resource_explorer.workflows import curate_commit as wf
        registry.set_project_context("repo", "p", status="personal")
        calls = []

        _keep_a_survey(registry)

        class FakeOrch:
            def __init__(self, registry=None): pass
            def run(self, slug, steps=None): raise AssertionError("the commit must not run a survey")

        class FakePublisher:
            report_reused = False
            def __init__(self, registry=None):
                self._asset_maker = type("AM", (), {"get_asset_by_guid": staticmethod(lambda g, **k: {"guid": g})})()
            def publish(self, result, zone_names=None, *, defer_drain=None):    # the real signature (pinned below)
                calls.append(("publish", defer_drain))
                registry.set_egeria_asset_guid("p", "asset-1") if hasattr(registry, "set_egeria_asset_guid") else None
                return "report-9"
            def get_survey_reports_by_guid(self, guid):
                return [{"guid": "report-9", "qualified_name": "x", "annotation_count": 3}]
            def publish_sub_resources(self, slug, url, guid, locators):
                calls.append(("subs", guid, list(locators)))
                return {l: f"g-{l}" for l in locators}

        class FakeClient:
            def set_confidentiality_classification(self, guid, body):
                calls.append(("conf", guid, body["properties"]["confidentialityLevel"], body["properties"]["notes"]))

        _same_publish_signature(FakePublisher)
        monkeypatch.setattr("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator", FakeOrch)
        monkeypatch.setattr("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", FakePublisher)
        monkeypatch.setattr("resource_explorer.egeria_identity.classification_client", lambda *a, **k: FakeClient())
        monkeypatch.setattr(registry, "get_egeria_asset_guid", lambda slug: "asset-1")

        rec = Curations(registry).create("repo", "p", author="peterprofile",
                                         selection={"confirm": ["SoftwareLibrary"], "sub_resources": ["docs/guide.md"]},
                                         manifest={}, steps=list(wf.STEPS))
        out = wf.execute_curation(registry, rec["id"])
        by = {s["name"]: s for s in out["steps"]}
        # The commit publishes the kept survey (brief section 1); it surveyed nothing (FakeOrch raises).
        assert by["publish_asset"]["state"] == "done"
        assert "asset-1" in by["publish_asset"]["detail"]
        assert "published · from the survey of 2026-10-07" in by["publish_asset"]["detail"]
        assert "3 annotations linked" in by["publish_asset"]["detail"]
        assert by["classifications"]["state"] == "done" and "Confidentiality · internal" in by["classifications"]["detail"]
        assert by["sub_resources"]["state"] == "done"
        assert by["sub_resources"]["detail"] == "1 of 1 published · 2 read back · plus 1 ancestor folder"   # not "2 of 1"
        assert by["components"]["state"] == "skipped"
        assert out["state"] == "done"
        assert ("publish", False) in calls, "Curate's commit is an explicit publish: it waits (defer_drain=False)"
        conf = next(c for c in calls if c[0] == "conf")
        assert conf[2] == 1 and "set by peterprofile on 2026-09-01" in conf[3]
        subs = next(c for c in calls if c[0] == "subs")
        assert subs[2] == ["docs", "docs/guide.md"]          # the ancestor folder was catalogued too
        assert {r["locator"] for r in registry.list_sub_resources("repo", "p")} == {"docs", "docs/guide.md"}

    def test_a_failed_publish_is_a_failed_step_and_the_rest_say_why(self, registry, monkeypatch):
        _seed(registry)
        from resource_explorer.workflows import curate_commit as wf
        registry.set_project_context("repo", "p", status="personal")

        _keep_a_survey(registry)

        class Boom:
            def __init__(self, registry=None): pass
            def publish(self, result, zone_names=None, *, defer_drain=None): raise RuntimeError("Egeria unreachable")

        _same_publish_signature(Boom)
        monkeypatch.setattr("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", Boom)
        rec = Curations(registry).create("repo", "p", author="peterprofile",
                                         selection={"sub_resources": ["docs"]}, manifest={}, steps=list(wf.STEPS))
        out = wf.execute_curation(registry, rec["id"])
        by = {s["name"]: s for s in out["steps"]}
        assert by["publish_asset"]["state"] == "failed" and "Egeria unreachable" in by["publish_asset"]["detail"]
        assert by["classifications"]["state"] == "failed" and "no asset" in by["classifications"]["detail"]
        assert by["sub_resources"]["state"] == "failed" and "no parent asset" in by["sub_resources"]["detail"]
        assert out["state"] == "failed"

    def test_no_project_context_stops_before_egeria_and_names_the_remedy(self, registry, monkeypatch):
        from resource_explorer.workflows import curate_commit as wf
        rec = Curations(registry).create("repo", "p", author="a", selection={}, manifest={}, steps=list(wf.STEPS))
        out = wf.execute_curation(registry, rec["id"])
        assert "no Egeria Project context" in out["steps"][0]["detail"]


class TestTheDistributionParser:
    def test_it_reads_what_the_manifests_declare(self, tmp_path):
        from resource_explorer.ingestion.distribution_parser import DistributionParser
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "pyegeria"\nversion = "1.0"\n[project.scripts]\nhey = "x:main"\n[tool.setuptools]\npackages = ["pyegeria"]\n')
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / ".github" / "workflows" / "release.yml").write_text("uses: pypa/gh-action-pypi-publish@release/v1\n")
        (tmp_path / "ui").mkdir()
        (tmp_path / "ui" / "package.json").write_text('{"name": "@odpi/ui", "bin": {"ui": "cli.js"}}')
        (tmp_path / "node_modules" / "left-pad").mkdir(parents=True)
        (tmp_path / "node_modules" / "left-pad" / "package.json").write_text('{"name": "left-pad"}')
        found = {f["check_name"]: f for f in DistributionParser().parse(tmp_path)}
        assert set(found) == {"python:pyegeria", "javascript:@odpi/ui"}       # the vendored one is not this repo's
        py = found["python:pyegeria"]
        assert py["label"] == "published" and py["detail"]["publish_workflow"] == "release.yml"
        assert py["detail"]["scripts"] == ["hey"] and py["detail"]["packages"] == ["pyegeria"]
        assert found["javascript:@odpi/ui"]["label"] == "declared"
        assert "1 command-line entry point(s): hey" in py["summary"]


class TestTheMembersBehindTheCounts:
    """Every count on the Curate screen is a link to its members (the design:
    "you cannot confirm ninety contained datasets without reading them").
    The sub-resource count opened an empty rail because the findings are
    stored as `repo_sub_resource_survey`, not `sub_resource_survey`."""

    def test_the_sub_resource_count_opens_its_members(self, registry):
        _seed(registry)
        from resource_explorer.members import members_for
        ms = members_for(registry, "p", "sub_resource_survey", scope="all")
        assert ms.total == 3
        assert {g.name: g.count for g in ms.groups} == {"worthy": 2, "not_worthy": 1}


class TestTheStructureCountComesFromOneFunction:
    """The Structure tab's "N awaiting you" is the plan's `awaiting`; it is computed by the SAME function
    (`awaiting_counts`) the made-of row carries in its detail, and absence of a verdict is "awaiting"."""

    def _components(self, registry, names):
        for n in names:
            registry.upsert_finding("p", "architecture_recovery", [{
                "check_name": "component", "label": "x",
                "detail": {"name": n, "slug": n, "admission": "built_here"}}],
                surveyed_at="2026-09-03T00:00:00", scope_locator=f"src/{n}")

    def test_awaiting_is_the_unreviewed_components_and_the_made_of_row_carries_the_same_figure(self, registry):
        from resource_explorer.curate_plan import awaiting_counts
        _seed(registry)
        self._components(registry, ["a", "b", "c"])
        registry.record_component_verdict("repo", "p", "src/a", "accepted", decided_by="peterprofile")
        plan = build_plan(registry, "p")
        assert plan["awaiting"] == {"components": 2}
        assert plan["made_of"][0]["detail"]["awaiting"] == plan["awaiting"]
        # the plan's figure IS awaiting_counts of the plan's own inputs
        comps = [{"path": "src/a"}, {"path": "src/b"}, {"path": "src/c"}]
        assert awaiting_counts(comps, {"src/a": {}}) == plan["awaiting"]

    def test_no_components_is_zero_awaiting_and_a_rejection_is_a_decision(self, registry):
        from resource_explorer.curate_plan import awaiting_counts
        assert awaiting_counts([], {}) == {"components": 0}
        assert awaiting_counts([{"path": "x"}], {"x": {"verdict": "rejected"}}) == {"components": 0}

    def test_latest_commit_route_returns_the_newest_record_or_none(self, client, registry):
        assert client.get("/api/projects/p/curate/commits/latest").json() == {"curation": None}
        Curations(registry).create("repo", "p", author="peterprofile", selection={}, manifest={}, steps=["publish_asset"])
        got = client.get("/api/projects/p/curate/commits/latest").json()["curation"]
        assert got["entity_slug"] == "p" and "proof_summary" in got
        assert client.get("/api/projects/nope/curate/commits/latest").status_code == 404


def _same_publish_signature(fake) -> None:
    """A fake publisher binds the real `EgeriaPublisher.publish` signature: a fake that drifts from it hides a
    TypeError the real code would raise (2026-10-14: the fakes lacked `defer_drain`)."""
    import inspect

    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    shape = lambda f: [(p.name, p.kind, p.default) for p in inspect.signature(f).parameters.values()]  # noqa: E731
    assert shape(fake.publish) == shape(EgeriaPublisher.publish), fake
