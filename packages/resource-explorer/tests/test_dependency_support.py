"""`dependency_support` — which technologies a repo's dependencies indicate,
and whether Egeria knows them.

Built 2026-09-12 as "A now, shaped to seed B" (project owner): a curated
mapping in RE, each entry linked to the Egeria technology type it will promote
into. The design followed a measurement, not an assumption: against 2,894
dependency names Egeria's 213 types matched TWO exactly, and token overlap
matched 373 that were nearly all wrong. The tests here pin the things that
measurement taught — chiefly that "unmatched" must never read as "unsupported",
and that a short or greedy pattern is how `apache` matched Airflow 105 times.
"""
from __future__ import annotations

import json
import textwrap

import pytest

from resource_explorer.surveyors import dependency_support as ds


@pytest.fixture(autouse=True)
def _fresh():
    ds.clear_cache()
    yield
    ds.clear_cache()


def _write_mapping(tmp_path, body: str):
    p = tmp_path / "m.yaml"
    p.write_text(textwrap.dedent(body))
    return p


# ── the mapping file ────────────────────────────────────────────────────────

class TestTheMappingIsValidatedLoudly:
    """A mapping that half-loads silently turns every dependency into
    'unmatched' — so every malformation raises."""

    def test_the_real_mapping_loads(self):
        techs = ds.load_mapping()
        assert len(techs) >= 10
        assert any(t.name == "PostgreSQL" for t in techs)

    def test_unknown_keys_are_rejected(self, tmp_path):
        p = _write_mapping(tmp_path, """
            technologies:
              - name: X
                pattern: [foo]        # typo for `patterns` — would silently drop everything
        """)
        with pytest.raises(ds.DependencySupportMappingError, match="unknown keys"):
            ds.load_mapping(p)

    def test_a_pattern_shorter_than_three_characters_is_rejected(self, tmp_path):
        """`pg` and `io` match half the ecosystem. This is the guard against the
        greedy-token failure, expressed as a floor."""
        p = _write_mapping(tmp_path, """
            technologies:
              - name: X
                patterns: [pg]
        """)
        with pytest.raises(ds.DependencySupportMappingError, match="shorter than 3"):
            ds.load_mapping(p)

    def test_a_prefix_pattern_is_measured_without_its_star(self, tmp_path):
        p = _write_mapping(tmp_path, """
            technologies:
              - name: X
                patterns: ["ab*"]
        """)
        with pytest.raises(ds.DependencySupportMappingError, match="shorter than 3"):
            ds.load_mapping(p)

    def test_duplicate_technology_names_are_rejected(self, tmp_path):
        p = _write_mapping(tmp_path, """
            technologies:
              - name: Redis
                patterns: [redis]
              - name: redis
                patterns: [ioredis]
        """)
        with pytest.raises(ds.DependencySupportMappingError, match="listed twice"):
            ds.load_mapping(p)

    def test_every_real_pattern_meets_the_floor(self):
        for t in ds.load_mapping():
            for pat in t.patterns:
                assert len(pat.rstrip("*")) >= 3, (t.name, pat)


# ── matching ────────────────────────────────────────────────────────────────

class TestMatching:
    def _t(self, *patterns, ecosystems=()):
        return ds.Technology("T", None, tuple(patterns), frozenset(ecosystems))

    def test_exact_is_case_insensitive(self):
        assert self._t("psycopg2").matches("Psycopg2")

    def test_prefix(self):
        t = self._t("apache-airflow-*")
        assert t.matches("apache-airflow-providers-postgres")
        assert not t.matches("apache-airflow")          # prefix needs the dash after

    def test_maven_coordinate_matches_on_artifact_and_on_full(self):
        t = self._t("postgresql")
        assert t.matches("org.postgresql:postgresql")
        t2 = self._t("org.apache.kafka:*")
        assert t2.matches("org.apache.kafka:kafka-clients")

    def test_ecosystem_scoping(self):
        t = self._t("pgx", ecosystems=["go"])
        assert t.matches("pgx", "go")
        assert not t.matches("pgx", "python"), "ecosystem scope was ignored"
        assert t.matches("pgx", ""), "an unknown ecosystem must not exclude — it is 'not stated', not 'wrong'"

    def test_empty_name_never_matches(self):
        assert not self._t("redis").matches("")

    def test_THE_false_positives_that_motivated_this_design_do_not_match(self):
        """The token-overlap probe (2026-09-12) produced these. The curated
        mapping must not."""
        techs = ds.load_mapping()
        for dep in ("apache_atlas", "apache-flink", "apache-tvm-ffi",
                    "@babel/plugin-proposal-function-bind", "antlr4-python3-runtime",
                    "@babel/plugin-transform-runtime"):
            hits = [t.name for t in techs if t.matches(dep)]
            assert not hits, f"{dep!r} matched {hits} — that is the greedy match this design exists to prevent"

    def test_the_seed_matches_the_known_real_cases(self):
        techs = {t.name: t for t in ds.load_mapping()}
        assert techs["PostgreSQL"].matches("psycopg2-binary")
        assert techs["Apache Kafka"].matches("confluent-kafka")
        assert techs["Apache Airflow"].matches("apache-airflow-providers-postgres")
        assert techs["Egeria (pyegeria)"].matches("pyegeria")


# ── the three states ────────────────────────────────────────────────────────

class TestAZeroSaysWhichZeroItIs:
    def _deps(self, *names):
        return [{"dep_name": n, "ecosystem": "python"} for n in names]

    def test_unmatched_is_a_list_of_names_not_a_verdict(self):
        a = ds.assess(self._deps("psycopg2", "left-pad"), egeria_types={"PostgreSQL Server": True},
                      egeria_check="checked")
        assert [m.technology.name for m in a.matches] == ["PostgreSQL"]
        assert a.unmatched == ["left-pad"]
        assert a.total_dependencies == 2

    def test_egeria_not_consulted_reads_unchecked_not_absent(self):
        a = ds.assess(self._deps("psycopg2"), egeria_types=None, egeria_check="unreachable",
                      egeria_check_detail="ConnectionError")
        assert a.matches[0].egeria_state == "unchecked", (
            "Egeria was not reached and the technology reads as absent — 'could "
            "not check' and 'not there' are different facts")
        assert a.egeria_check == "unreachable"

    def test_egeria_reached_and_type_missing_reads_absent(self):
        a = ds.assess(self._deps("psycopg2"), egeria_types={"PostgreSQL Server": False},
                      egeria_check="checked")
        assert a.matches[0].egeria_state == "absent"

    def test_a_technology_with_no_egeria_type_is_a_promotion_candidate(self):
        techs = (ds.Technology("Redis", None, ("redis",)),)
        a = ds.assess(self._deps("redis"), technologies=techs, egeria_types={}, egeria_check="checked")
        assert a.matches[0].egeria_state == "no-type"

    def test_duplicate_dependency_names_count_once(self):
        a = ds.assess(self._deps("redis", "Redis", "REDIS"), egeria_types=None)
        assert a.total_dependencies == 1

    def test_the_coverage_row_carries_the_egeria_check_at_the_top(self):
        a = ds.assess(self._deps("psycopg2"), egeria_types=None, egeria_check="unreachable",
                      egeria_check_detail="timeout")
        cov = [r for r in a.as_findings() if r["check_name"] == "coverage"][0]
        assert cov["detail"]["egeria_check"] == "unreachable"
        assert cov["confidence"] == 0, "an unchecked coverage row must not carry full confidence"

    def test_unmatched_names_are_not_persisted_one_per_row(self):
        """800 dependencies would mean 800 rows saying 'unclassified'. They are
        re-derived at read time instead."""
        a = ds.assess(self._deps(*[f"lib{i}" for i in range(200)], "psycopg2"), egeria_types=None)
        rows = a.as_findings()
        assert len(rows) == 2          # one technology + one coverage
        assert rows[-1]["detail"]["unmatched_dependencies"] == 200


class TestEgeriaLookupNeverRaises:
    def test_no_linked_types_short_circuits(self):
        present, state, _ = ds.egeria_technology_types_present([])
        assert present == {} and state == "checked"

    def test_an_unreachable_catalog_reports_unreachable(self, monkeypatch):
        import resource_explorer.surveyors.egeria_tech_type_catalog as cat

        class Boom:
            def __init__(self, *a, **k): pass
            def connect(self): raise ConnectionError("no route to host")

        monkeypatch.setattr(cat, "EgeriaTechTypeCatalog", Boom)
        present, state, detail = ds.egeria_technology_types_present(["PostgreSQL Server"])
        assert present is None and state == "unreachable"
        assert "ConnectionError" in detail


# ── the surveyor, against the real registry ─────────────────────────────────

class TestTheSurveyor:
    @pytest.fixture
    def slug(self, request):
        import re
        return "ds_" + re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:48]

    @pytest.fixture
    def reg(self, pg_registry, slug):
        from resource_explorer.registry import Project
        pg_registry.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/x/{slug}"))
        return pg_registry

    def _run(self, reg, slug, monkeypatch, egeria=("unreachable", None)):
        from resource_explorer.surveyors.sub_surveyors.dependency_support import DependencySupportSurveyor
        state, present = egeria
        monkeypatch.setattr(ds, "egeria_technology_types_present",
                            lambda names: (present, state, "stubbed"))
        return DependencySupportSurveyor(project=reg.get(slug), registry=reg).run()

    def test_no_dependency_rows_is_unverified_not_zero_technologies(self, reg, slug, monkeypatch):
        anns = self._run(reg, slug, monkeypatch)
        # The ANNOTATION is `nothing_to_assess` — a different statement from the
        # normal path's `coverage`, and two annotations sharing a check_name
        # with no item key would publish one qualifiedName. The persisted
        # FINDING row keeps check_name `coverage` / label `no-dependencies`,
        # which is what the results reader keys on.
        assert len(anns) == 1 and anns[0].check_name == "nothing_to_assess"
        assert anns[0].json_properties.get("outcome") == "unverified"
        rows = reg.query_findings(slug, "dependency_support")
        assert rows and rows[0]["label"] == "no-dependencies"

    def test_matches_persist_and_read_back_with_detail(self, reg, slug, monkeypatch):
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            _dependency_support_headline, _dependency_support_results)
        reg.upsert_dependencies(slug, [
            {"dep_name": "psycopg2-binary", "dep_version": "2.9", "dep_type": "runtime",
             "ecosystem": "python", "source_file": "requirements.txt"},
            {"dep_name": "left-pad", "dep_version": "1.0", "dep_type": "runtime",
             "ecosystem": "javascript", "source_file": "package.json"},
        ])
        anns = self._run(reg, slug, monkeypatch, egeria=("checked", {"PostgreSQL Server": True}))
        assert {a.check_name for a in anns} == {"technology", "coverage"}

        res = _dependency_support_results(reg, slug)
        # The first version of the reader read `detail` where the registry
        # hands back `detail_json`, and every technology came back `unchecked`
        # with no dependencies. This is the check that would have caught it.
        t = res["technologies"][0]
        assert t["technology"] == "PostgreSQL"
        assert t["dependencies"] == ["psycopg2-binary"], "detail_json was not parsed"
        assert t["egeria_state"] == "present"
        assert res["coverage"]["egeria_check"] == "checked"
        assert res["unmatched"] == ["left-pad"]
        h = _dependency_support_headline(reg, slug)
        assert "1 technology indicated" in h["label"] and "1 dependencies unclassified" in h["label"]

    def test_egeria_down_shows_in_the_headline(self, reg, slug, monkeypatch):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _dependency_support_headline
        reg.upsert_dependencies(slug, [{"dep_name": "psycopg2", "dep_version": "", "dep_type": "runtime",
                                        "ecosystem": "python", "source_file": "r.txt"}])
        self._run(reg, slug, monkeypatch, egeria=("unreachable", None))
        h = _dependency_support_headline(reg, slug)
        assert "could not be checked" in h["label"] and h["status"] == "warn"

    def test_never_run_is_a_status_envelope_not_content(self, reg, slug):
        """live_read safety, same as every other reader here."""
        from resource_explorer.facts import _has_content
        from resource_explorer.surveyors.repo_survey_definition_adapter import _dependency_support_results
        res = _dependency_support_results(reg, "no_such_repo_at_all")
        assert "_status" in res and not _has_content(res)


# ── wiring ──────────────────────────────────────────────────────────────────

class TestItIsWiredEverywhere:
    def test_step_kind_and_ownership(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            ANALYSIS_KINDS, REPO_ANALYSIS_STEP_MAP, STEP_REGISTRY)
        assert "repo_dependency_support" in STEP_REGISTRY
        assert REPO_ANALYSIS_STEP_MAP["dependency_support"] == ["repo_dependency_support"]
        from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_RESULTS_MAP
        # render mode is the third positional of AnalysisKindResults; read it
        # off the derived results map the frontend test also uses
        assert "dependency_support" in REPO_ANALYSIS_RESULTS_MAP

    def test_the_step_fetches_nothing_so_discovery_is_honest(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
        assert not (getattr(STEP_REGISTRY["repo_dependency_support"], "requires_resources", {}) or {})

    def test_the_catalog_entry_is_discovery(self):
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        e = {a["id"]: a for a in get_analyses("repo", include_egeria_live=False)}["dependency_support"]
        assert e["intent"] == "discovery"

    def test_the_question_now_cites_it_as_mixed(self):
        from resource_explorer.surveyors.question_catalog_reader import get_questions
        q = [q for q in get_questions() if q["question"].startswith("Do we already support")][0]
        assert q["answering"]["kind"] == "mixed", "a machine starting point that a human confirms is `mixed`, not `human` and not `analysis`"
        assert q["answering"]["analysis_ids"] == ["dependency_support"]

    def test_it_is_in_the_discovery_survey(self):
        import csv
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "repo_survey_types.csv"
        rows = [r for r in csv.DictReader(p.open()) if r["step_key"] == "repo_dependency_support"]
        assert {r["survey_group"] for r in rows} == {"RepoDiscoverySurvey"}

    def test_the_frontend_has_a_renderer(self):
        from pathlib import Path
        html = (Path(__file__).resolve().parent.parent / "resource_explorer" / "web" / "static" / "index.html").read_text()
        assert "dependency_support: 'custom'" in html
        assert "function _renderDependencySupportResults" in html
        assert "dependency_support: _renderDependencySupportResults" in html
        assert "not yet classified" in html, "the unmatched list must read as unclassified, never unsupported"


@pytest.mark.live_egeria
@pytest.mark.requires_egeria
class TestAgainstLiveEgeria:
    """Every `egeria_technology_type` in the mapping must name a type Egeria
    actually holds — verified live, skipped (not passed) when Egeria is down.

    Unlike the Investigation/Curate tests this session also fixed (see
    conftest.py::mock_egeria_client_connections), the point of THIS test
    genuinely is a live Egeria round trip — mocking the client boundary
    would make it always skip, never actually verifying anything, which is
    a real loss of coverage, not just a speedup. Its own manual `pytest.skip`
    below still exists as a defense in depth (`egeria_technology_types_
    present()` can also fail for a reason unrelated to raw reachability,
    e.g. a bad credential), but `requires_egeria` gives it the same fast
    (~2s) reachability probe every other live-Egeria test in this suite
    already uses (`pytest_collection_modifyitems` in conftest.py), instead
    of reaching this test's own real client construction — which spent
    ~30s per run when Egeria was genuinely unreachable rather than the
    ~2s `_egeria_reachable()` check takes."""

    def test_every_linked_type_exists(self):
        linked = sorted({t.egeria_technology_type for t in ds.load_mapping() if t.egeria_technology_type})
        present, state, detail = ds.egeria_technology_types_present(linked)
        if state != "checked":
            pytest.skip(f"Egeria not reachable: {detail}")
        missing = sorted(n for n, ok in present.items() if not ok)
        assert not missing, (
            f"these mapping entries link to Egeria technology types that do not exist: {missing}. "
            f"Either the displayName is wrong or the type was removed.")


class TestItsAnnotationsPublish:
    """The regression #46 shipped: one ClassificationAnnotation per matched
    technology, all check_name="technology", no item_key — so any repo with two
    or more matched technologies produced two identical qualifiedNames and the
    publisher refused the WHOLE publish (Curate, ☁ Publish, resync), before
    writing anything. Found on the first live press of Curate → Catalogue on
    egeria_workspaces_git, 2026-09-12 17:13, not by any test.

    test_annotation_check_names did not catch it because it inspects static
    emission sites, and this is a loop. So this test does what the publisher
    does: runs the surveyor for real on a repo that matches ≥2 technologies and
    hands its annotations to the publisher's own uniqueness check.
    """

    @pytest.fixture
    def slug(self, request):
        import re
        return "dsp_" + re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:44]

    @pytest.fixture
    def reg(self, pg_registry, slug):
        from resource_explorer.registry import Project
        pg_registry.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/x/{slug}"))
        # Three technologies — the collision needs at least two.
        pg_registry.upsert_dependencies(slug, [
            {"dep_name": "psycopg2-binary", "dep_version": "2.9", "dep_type": "runtime",
             "ecosystem": "python", "source_file": "requirements.txt"},
            {"dep_name": "confluent-kafka", "dep_version": "2.3", "dep_type": "runtime",
             "ecosystem": "python", "source_file": "requirements.txt"},
            {"dep_name": "redis", "dep_version": "5.0", "dep_type": "runtime",
             "ecosystem": "python", "source_file": "requirements.txt"},
        ])
        return pg_registry

    def _annotations(self, reg, slug, monkeypatch):
        from resource_explorer.surveyors.sub_surveyors.dependency_support import DependencySupportSurveyor
        monkeypatch.setattr(ds, "egeria_technology_types_present",
                            lambda names: ({n: True for n in names}, "checked", "stubbed"))
        return DependencySupportSurveyor(project=reg.get(slug), registry=reg).run()

    def test_multiple_matched_technologies_publish_under_distinct_qualified_names(self, reg, slug, monkeypatch):
        from resource_explorer.surveyors.survey_report import assert_unique_qualified_names
        anns = self._annotations(reg, slug, monkeypatch)
        techs = [a for a in anns if a.check_name == "technology"]
        assert len(techs) >= 2, "the fixture must produce the collision case"
        # This is the exact call the publisher makes before writing; it raised
        # ValueError on the shipped code.
        assert_unique_qualified_names(f"Annotation::{slug}::2026-09-12T00:00:00", anns)

    def test_every_per_technology_annotation_carries_its_own_item_key(self, reg, slug, monkeypatch):
        anns = self._annotations(reg, slug, monkeypatch)
        techs = [a for a in anns if a.check_name == "technology"]
        keys = [a.item_key for a in techs]
        assert all(keys), f"a per-technology annotation has no item_key: {keys}"
        assert len(set(keys)) == len(keys), f"item_keys are not distinct: {keys}"
        assert set(keys) == {"PostgreSQL", "Apache Kafka", "Redis"}
