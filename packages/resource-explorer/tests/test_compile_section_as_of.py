"""An as-of date on every packed evidence section (docs/design-notes/BRIEF-CONTEXT-SECTION-AS-OF.md, PR A).

Fixtures are real-shape (§3f): an `instructions` entry with role "instructions", evidence entries with and without
`as_of`, and a legacy entry whose only date is a pointer's, without a zone. Fixtures without `instructions` let
#1021 pass review while counting it as undated.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.compile_as_of import AS_OF_SEEN_CAP, refreshed, section_as_ofs
from resource_explorer.context_compile import _evidence_as_of, compile_context
from resource_explorer.feedback_diagnosis import not_judged, suggestions
from resource_explorer.journal import _as_ofs_of
from resource_explorer.registry import Project, ProjectRegistry

OLD = "2026-06-01T00:00:00+00:00"
NEW = "2026-11-01T00:00:00+00:00"
ANSWERED = "2026-11-10T00:00:00+00:00"


def manifest(*, deps=None, ci=None, legacy_pointer=None, declared=None, history=None) -> dict:
    """A manifest shaped like a real compile's: instructions first, then evidence."""
    packed = [{"key": "instructions", "role": "instructions", "rung": "FULL", "size": 300}]
    for key, as_of in (("deps", deps), ("ci", ci)):
        entry = {"key": key, "role": "evidence", "rung": "FULL", "size": 500}
        if as_of:
            entry.update(as_of=as_of, as_of_source="result")
        packed.append(entry)
    legacy = {"key": "docs", "role": "evidence", "rung": "SUMMARY", "size": 200}
    if legacy_pointer:
        legacy["pointer"] = {"resource_slug": "r", "view": "v", "perspective": "", "scope": "", "as_of": legacy_pointer}
    packed.append(legacy)
    m = {"coverage": {"kind": "analysis", "question": "q", "note": ""}, "packed": packed, "gaps": [], "dropped": [],
         "deferred": [], "declared": declared or {}}
    if history is not None:
        m["as_of_seen"] = history
    return m


class TestEvidenceAsOf:
    def test_the_oldest_result_time_is_the_as_of_and_the_newest_rides_beside_it(self):
        prov = ({"surveyed_at": NEW}, {"surveyed_at": OLD}, {"surveyed_at": None})
        assert _evidence_as_of(prov, lambda: "never read") == {"as_of": OLD, "as_of_source": "result",
                                                               "as_of_newest": NEW}

    def test_the_fact_is_read_only_when_the_result_has_no_time(self):
        calls = []

        def fact():
            calls.append(1)
            return NEW
        assert _evidence_as_of(({"surveyed_at": OLD},), fact)["as_of_source"] == "result" and not calls
        assert _evidence_as_of(({"surveyed_at": None},), fact) == {"as_of": NEW, "as_of_source": "fact"}

    def test_an_unknown_age_stays_unknown(self):
        assert _evidence_as_of((), lambda: "") == {}
        assert _evidence_as_of((), None) == {}

    def test_a_fact_that_raises_costs_the_date_not_the_compile(self):
        def boom():
            raise RuntimeError("fact layer down")
        assert _evidence_as_of((), boom) == {}


class TestSectionAsOfs:
    def test_packed_wins_over_declared_and_instructions_is_never_a_section(self):
        m = manifest(deps=NEW, declared={"deps": {"as_of": OLD}, "lic": {"as_of": OLD}})
        assert section_as_ofs(m) == {"deps": NEW, "lic": OLD}

    def test_a_pointer_date_is_not_an_evidence_date(self):
        assert section_as_ofs(manifest(legacy_pointer="2026-06-01T00:00:00")) == {}

    def test_an_answer_is_judged_by_the_dates_current_when_it_was_given(self):
        m = manifest(deps=NEW, history=[{"seen_at": "", "as_of": {"deps": OLD}},
                                        {"seen_at": "2026-11-05T00:00:00+00:00", "as_of": {"deps": NEW}}])
        assert section_as_ofs(m, at="2026-11-03T00:00:00+00:00") == {"deps": OLD}
        assert section_as_ofs(m, at=ANSWERED) == {"deps": NEW}
        assert section_as_ofs(m) == {"deps": NEW}


class TestRefreshed:
    def test_unchanged_dates_write_nothing(self):
        assert refreshed(manifest(deps=OLD), manifest(deps=OLD), ANSWERED) is None

    def test_a_repeat_with_newer_dates_refreshes_and_keeps_the_first(self):
        out = refreshed(manifest(deps=OLD), manifest(deps=NEW), ANSWERED)
        assert next(p for p in out["packed"] if p["key"] == "deps")["as_of"] == NEW
        assert out["as_of_seen"] == [{"seen_at": "", "as_of": {"deps": OLD}},
                                     {"seen_at": ANSWERED, "as_of": {"deps": NEW}}]
        assert next(p for p in out["packed"] if p["key"] == "instructions") == manifest()["packed"][0]

    def test_the_history_is_capped_but_keeps_the_first_compiles_dates(self):
        m = manifest(deps=OLD)
        for day in range(1, AS_OF_SEEN_CAP + 6):
            m = refreshed(m, manifest(deps=f"2026-10-{day:02d}T00:00:00+00:00"), f"2026-10-{day:02d}T01:00:00+00:00")
        assert len(m["as_of_seen"]) == AS_OF_SEEN_CAP
        assert m["as_of_seen"][0] == {"seen_at": "", "as_of": {"deps": OLD}}


class TestDiagnosisReadsThePackedEntry:
    def test_stale_fires_from_a_packed_as_of(self):
        out = [s for s in suggestions(manifest(deps=OLD, ci=NEW), answered_at=ANSWERED) if s["cause"] == "stale"]
        assert out and out[0]["sections"] == ["deps"]

    def test_stale_is_judged_at_the_answers_time_not_the_latest_refresh(self):
        m = manifest(deps=NEW, history=[{"seen_at": "", "as_of": {"deps": OLD}},
                                        {"seen_at": "2026-11-12T00:00:00+00:00", "as_of": {"deps": NEW}}])
        assert [s for s in suggestions(m, answered_at=ANSWERED) if s["cause"] == "stale"]

    def test_n_of_m_counts_evidence_only_and_the_legacy_pointer_is_undated(self):
        [n] = not_judged(manifest(deps=NEW, legacy_pointer="2026-06-01T00:00:00"), answered_at=ANSWERED)
        assert n["why"] == "2 of 3 sections have no as-of date (ci, docs), so their ages were not judged"
        assert n["partial"] is True

    def test_a_stored_time_without_a_zone_is_said_to_be_unreadable(self):
        [n] = not_judged(manifest(deps="2026-11-01T00:00:00", ci=NEW), answered_at=ANSWERED)
        assert "the as-of date of deps cannot be read" in n["why"]

    def test_all_evidence_dated_is_judged(self):
        m = manifest(deps=NEW, ci=NEW)
        m["packed"] = [p for p in m["packed"] if p["key"] != "docs"]
        assert not_judged(m, answered_at=ANSWERED) == []


def test_the_journal_reads_the_packed_entry_and_falls_back_to_a_legacy_pointer():
    assert _as_ofs_of({"manifest": manifest(deps=NEW, legacy_pointer="2026-10-18T08:00:00+00:00")}) == {
        "deps": NEW, "docs": "2026-10-18T08:00:00+00:00"}


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
    reg.add(Project(slug="marquez", display_name="M", github_url="u", description=""))
    return reg


def _finding(reg, surveyed_at):
    reg.upsert_finding("marquez", "cve_scan", [{
        "check_name": "python:idna", "label": "moderate", "summary": "4 advisories for idna ==2.10",
        "confidence": 100, "detail": {}}], surveyed_at=surveyed_at)


class TestCompileEndToEnd:
    def test_a_packed_finding_carries_its_survey_time_never_the_compile_time(self, registry):
        _finding(registry, OLD)
        c = compile_context(registry, "marquez", "Are there outstanding CVEs?")
        cve = next(p for p in c.manifest["packed"] if p["key"] == "cve_scan")
        assert cve["as_of"] == OLD and cve["as_of_source"] == "result"
        assert "as_of" not in next(p for p in c.manifest["packed"] if p["key"] == "instructions")

    def test_a_repeat_compile_over_a_resurvey_refreshes_the_stored_dates(self, registry):
        """Same finding text, surveyed again: the same compile id, newer dates, and the first dates kept."""
        _finding(registry, OLD)
        first = compile_context(registry, "marquez", "Are there outstanding CVEs?")
        _finding(registry, NEW)
        second = compile_context(registry, "marquez", "Are there outstanding CVEs?")
        assert second.compile_id == first.compile_id
        stored = registry.get_compile(first.compile_id)
        assert stored["hits"] == 2
        assert next(p for p in stored["manifest"]["packed"] if p["key"] == "cve_scan")["as_of"] == NEW
        seen = stored["manifest"]["as_of_seen"]
        assert seen[0] == {"seen_at": "", "as_of": {"cve_scan": OLD}} and seen[-1]["as_of"] == {"cve_scan": NEW}
        # The stored text the model saw is unchanged.
        assert json.dumps(stored["manifest"]["text"]) == json.dumps(first.text)
