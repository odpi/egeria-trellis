"""Tests for GET /{slug}/scouting-questions — the per-phase Question
checklist route (question_catalog_reader.py, Discovery-tier Part 3 plan).
No coverage existed for this route before this change.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(
        slug="myproj",
        display_name="My Project",
        github_url="https://github.com/test/myproj",
        description="A test repo.",
    ))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


class TestScoutingQuestionsRoute:
    def test_unknown_repo_returns_404(self, client):
        resp = client.get("/api/projects/not-a-real-repo/scouting-questions")
        assert resp.status_code == 404

    def test_defaults_to_scouting_phase(self, client):
        resp = client.get("/api/projects/myproj/scouting-questions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "scouting"
        assert data["questions"]
        assert all(
            "scouting" in {part.strip().lower() for part in q["stage"].split("/")}
            for q in data["questions"]
        )

    def test_phase_param_is_not_hardcoded_to_scouting(self, client):
        # Part 3 — the route (and its underlying reader) is phase-agnostic;
        # any of the 7 canonical intents works, not just scouting.
        resp = client.get("/api/projects/myproj/scouting-questions?phase=assessment")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "assessment"
        assert data["questions"]
        assert all(
            "assessment" in {part.strip().lower() for part in q["stage"].split("/")}
            for q in data["questions"]
        )
        questions = {q["question"] for q in data["questions"]}
        assert "How mature is it?" in questions
        assert "What explicit license does this resource use, and are there non-standard or copyleft terms?" in questions

    def test_perspective_filter_narrows_results(self, client):
        unfiltered = client.get("/api/projects/myproj/scouting-questions?phase=assessment").json()
        filtered = client.get("/api/projects/myproj/scouting-questions?phase=assessment&perspectives=Security").json()
        assert len(filtered["questions"]) <= len(unfiltered["questions"])
        assert all("Security" in q["perspectives"] for q in filtered["questions"])

    def test_analysis_kind_question_gets_has_data_computed(self, client, registry):
        # license_classification has no findings yet -> has_data False, not None.
        resp = client.get("/api/projects/myproj/scouting-questions?phase=assessment")
        license_q = next(
            q for q in resp.json()["questions"]
            if q["question"].startswith("What explicit license")
        )
        assert license_q["kind"] == "analysis"
        assert license_q["has_data"] is False

        registry.upsert_finding(
            "myproj", "license_classification",
            [{"check_name": "license_risk_tier", "label": "permissive", "summary": "MIT", "confidence": 90}],
            surveyed_at="2026-08-01T00:00:00",
        )
        resp2 = client.get("/api/projects/myproj/scouting-questions?phase=assessment")
        license_q2 = next(
            q for q in resp2.json()["questions"]
            if q["question"].startswith("What explicit license")
        )
        assert license_q2["has_data"] is True

    def test_gap_kind_question_has_data_is_none(self, client):
        """A question nothing answers reports has_data as None, not False.

        None is "we never looked"; False is "we looked and found nothing". The
        whole point of the kind vocabulary is keeping those apart.

        This used to name "Are there outstanding CVEs?" as its example, and
        broke on 2026-08-28 when cve_scan closed that gap — the test failed for
        the best possible reason, but it read like a regression. Gap questions
        are selected by kind now, so closing one is progress rather than a
        failure. The count assertion is deliberate: if every gap is ever
        closed, this test should be removed on purpose, not pass vacuously.
        """
        resp = client.get("/api/projects/myproj/scouting-questions?phase=analysis")
        gaps = [q for q in resp.json()["questions"] if q["kind"] == "gap"]
        assert gaps, (
            "no gap-kind questions left in the analysis phase — if that is real, "
            "delete this test deliberately rather than letting it pass on an "
            "empty list"
        )
        for q in gaps:
            assert q["has_data"] is None, q["question"]


class TestRationaleReachesTheRoute:
    """The catalog's Rationale/Source column — what an answer can and cannot
    claim — carried through to the UI on 2026-09-11.

    It had existed in docs/dr-egeria/resource_questions.csv since the catalog
    was authored and stopped at the generator: the YAML never held it, so no
    reader, route or screen could show it. These assert the whole chain, not
    just the field's presence, because the interesting failure is a generator
    regenerated without it -- which would silently empty every caveat while
    every other test still passed.
    """

    def test_every_question_carries_its_rationale(self, client):
        resp = client.get("/api/projects/myproj/scouting-questions")
        assert resp.status_code == 200
        questions = resp.json()["questions"]
        assert questions
        missing = [q["question"] for q in questions if not (q.get("rationale") or "").strip()]
        assert not missing, f"questions with no rationale: {missing}"

    def test_rationale_is_distinct_from_the_note(self, client):
        """`note` says HOW a question is answered; `rationale` says what the
        answer is not entitled to mean. Collapsing them would lose the
        caveat, so they must not be the same string."""
        questions = client.get("/api/projects/myproj/scouting-questions").json()["questions"]
        same = [q["question"] for q in questions
                if (q.get("note") or "").strip()
                and q["note"].strip() == (q.get("rationale") or "").strip()]
        assert not same, f"rationale duplicates note on: {same}"

    def test_the_caveat_sentences_survive_the_chain(self):
        """The specific sentences the design round asked for. Named
        explicitly: a generic 'is non-empty' assertion passes on placeholder
        text, and these are the rows where the caveat carries the meaning."""
        from resource_explorer.surveyors.question_catalog_reader import get_questions
        qs = get_questions("repo")
        limits = " ".join((q.get("rationale") or "") for q in qs)
        history = " ".join((q.get("catalog_history") or "") for q in qs)
        # secret_scan never claims "no secrets" -- only no matches against
        # this ruleset, in this snapshot. That is a LIMIT and stays in the
        # rationale.
        assert "no matches against this ruleset" in limits
        # cve_scan reports DECLARED dependencies only.
        assert "DECLARED dependencies only" in limits
        # Which analysis shipped when is HISTORY. Since 2026-09-11 it lives
        # in catalog_history, not beside the limit -- the reader deciding
        # how far to trust an answer is not handed the build's changelog.
        assert "secret_scan shipped" in history
        assert "secret_scan shipped" not in limits


class TestChecksReachTheRoute:
    """`analysis_id:check_name` refs, carried through on 2026-09-11 for the
    dashboard-by-question view. They are the finer key: a question declaring
    `repo_conventions:doc_breadth` can show that one finding from an analysis
    that also answers five other questions, instead of the whole analysis six
    times."""

    def test_checks_are_present_and_well_formed(self, client):
        qs = client.get("/api/projects/myproj/scouting-questions",
                        params={"phase": "analysis"}).json()["questions"]
        with_checks = [q for q in qs if q.get("checks")]
        assert with_checks, "the Analysis stage authors check refs; none reached the route"
        for q in with_checks:
            for ref in q["checks"]:
                analysis, _, check = ref.partition(":")
                assert analysis and check, f"malformed check ref {ref!r}"
                # A check ref implies its analysis, which the generator adds
                # to analysis_ids — so the two can never disagree.
                assert analysis in q["analysis_ids"], (
                    f"{ref!r} names an analysis not in analysis_ids {q['analysis_ids']}")

    def test_empty_checks_means_no_refs_authored_not_no_checks_apply(self, client):
        """Empty is the common case and must stay a list, not None, so a
        consumer can fall back to analysis_ids without a null check."""
        qs = client.get("/api/projects/myproj/scouting-questions").json()["questions"]
        assert all(isinstance(q.get("checks"), list) for q in qs)


class TestLimitAndHistoryAreSeparate:
    """Dashboard Round Three: the rationale mixed two things that need
    different homes. The limit -- what this answer does and does not cover,
    present tense, for a reader -- stays in `rationale`. The history -- what
    used to be wrong, which analysis landed when -- moves to
    `catalog_history`, for a maintainer. These pin the split for the
    sixteen Analysis-stage questions it was made on."""

    def test_no_analysis_stage_limit_reads_as_a_changelog(self, client):
        qs = client.get("/api/projects/myproj/scouting-questions",
                        params={"phase": "analysis"}).json()["questions"]
        measured = [q for q in qs if q["analysis_ids"]]
        # 17 since 2026-09-12: "Do we already support these dependencies?"
        # went from `human` to `mixed` when dependency_support gave it a
        # machine starting point, so it now carries an analysis id.
        # 16 since 2026-09-26 (#296): "Does it fit into our security
        # infrastructure?" moved from Analysis/Enrichment to Enrichment at
        # the project owner's direction (the answer is a person's), taking
        # its security_scan analysis id out of the Analysis phase with it.
        assert len(measured) == 16
        tells = ("shipped 2026", "was marked GAP", "landed 2026", "could not parse",
                 "Closed 2026", "before any question referenced")
        leaked = [q["question"] for q in measured
                  if any(t in (q.get("rationale") or "") for t in tells)]
        assert leaked == [], f"history still in the limit on: {leaked}"

    def test_history_is_carried_and_only_where_authored(self, client):
        qs = client.get("/api/projects/myproj/scouting-questions",
                        params={"phase": "analysis"}).json()["questions"]
        assert all(isinstance(q.get("catalog_history"), str) for q in qs)
        with_history = [q for q in qs if q["analysis_ids"] and q["catalog_history"]]
        # 16 of 16 since 2026-09-26 (#296): the one measured question without
        # build history — the security-infrastructure question, a person's to
        # answer — left the Analysis phase for Enrichment, so every remaining
        # measured question carries history. (Was 16 of 17 from 2026-09-12,
        # and 15 of 16 before that.)
        assert len(with_history) == 16
