"""Outcome data reaching a surface — the readers, and the cards that read them.

Across the corpus 67 persisted outcomes are not `recovered`. Wiring them into
readers is only half the job: a reader that computes a field no renderer reads
is the same failure as not computing it, which is exactly what
_architecture_recovery_results' own docstring says about the layer below it —
and then its renderer read none of run_outcomes/unverified/partial/
completeness_note. The same defect, one layer further along.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_ADAPTER = (pathlib.Path(__file__).parent.parent / "resource_explorer" / "surveyors"
            / "repo_survey_definition_adapter.py")


class TestManifestParseShowsWhyNotJustHowMany:

    def test_each_sub_parse_reports_separately(self):
        """egeria_git parses its CI and conventions fine and cannot read its
        build.gradle at all. One card-level status would have to pick a winner
        and would be wrong two ways out of three."""
        src = _ADAPTER.read_text()
        reader = re.search(r"def _manifest_parse_results\(.*?\n\n\ndef ", src, re.S).group(0)
        for key in ("dependencies", "ci_quality", "conventions"):
            assert f'"{key}"' in reader, key


class TestLiveReadRequiresAnAbsenceGate:
    """`live_read=True` bypasses the run gate: facts.py reads the results and
    reports MEASURED whenever `_has_content()` says there is something there.
    That is only correct if the reader can say "nothing here" — and these
    readers cannot say it by returning an empty-ish dict, because every one of
    them stamps `slug` into the payload unconditionally, and a non-empty string
    IS content.

    Measured 2026-09-09 before the gate went in: `_architecture_recovery_results`
    on a slug that does not exist returned `_has_content() == True` with a
    headline of None — so live_read would have reported "measured" with no
    label, for every unknown repo. The fix is the `_status` envelope, which is
    the one shape `_has_content` exempts.
    """

    def _live_read_kinds(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import ANALYSIS_KINDS
        return {
            aid: kind for aid, kind in ANALYSIS_KINDS.items()
            if getattr(getattr(kind, "results", None), "live_read", False)
        }

    def test_there_is_at_least_one_live_read_kind_to_check(self):
        assert self._live_read_kinds(), (
            "no AnalysisKind sets live_read — this whole class is passing "
            "vacuously; the derivation broke, not the code"
        )

    def test_every_live_read_reader_reports_absence_as_absence(self):
        """The property that matters, checked by calling the readers rather than
        by reading their source: on a slug nothing has ever touched, a live_read
        reader must produce something `_has_content()` calls empty."""
        from resource_explorer.facts import _has_content
        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry()
        absent = "definitely_not_a_repo_" + "x" * 12
        offenders = []
        for aid, kind in self._live_read_kinds().items():
            reader = kind.results.results_reader
            try:
                value = reader(registry, absent)
            except Exception:
                continue  # a reader that refuses outright cannot claim measured
            if _has_content(value):
                keys = sorted(
                    k for k, v in (value or {}).items()
                    if k not in {"_status", "surveyed_at", "detail", "scoped_to",
                                 "run_outcomes"} and _has_content(v)
                )
                offenders.append((aid, keys))
        assert not offenders, (
            f"these live_read analyses report content for a repo that does not "
            f"exist, so facts.py will call them MEASURED with no headline: "
            f"{offenders}. Return a bare {{'_status': ...}} when there is "
            f"genuinely nothing, the way _architecture_diagram_results does."
        )

    @pytest.mark.corpus
    def test_the_gate_keys_on_the_timestamp_not_on_having_components(self):
        """The gate must not swallow the real answers it sits in front of.

        Marked `corpus`: it asserts over whatever the live registry holds,
        and on CI's empty Postgres it failed on its own vacuity guard --
        the one failure left after #43 let the suite run to the end.

        The tempting wrong fix is `if not all_components` — it looks equivalent
        and is not. Measured 2026-09-09: 8 of 61 repos have ZERO
        architecture_recovery findings and still carry a surveyed_at, because
        their steps ran and recorded a run_outcome saying the repo could not be
        read. "Unverified — coupling, detect could not read this repo" is a real
        answer (README finding 57: an unverified run and a genuine zero are
        indistinguishable as a bare count), and a component-count gate reports
        it as never-run. Only a repo nothing has ever touched has no timestamp.
        """
        from resource_explorer.facts import _has_content
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            _architecture_recovery_results)

        registry = ProjectRegistry()
        surveyed_but_empty = []
        for project in registry.list_all():
            value = _architecture_recovery_results(registry, project.slug)
            if value.get("surveyed_at") and not value.get("component_count"):
                surveyed_but_empty.append((project.slug, _has_content(value)))

        assert surveyed_but_empty, (
            "no repo in the registry is surveyed-but-componentless, so this test "
            "cannot distinguish a timestamp gate from a component gate — it is "
            "passing vacuously and the corpus, not the code, changed"
        )
        silenced = [slug for slug, answered in surveyed_but_empty if not answered]
        assert not silenced, (
            f"{len(silenced)} of {len(surveyed_but_empty)} repos that were "
            f"surveyed but recovered no components now report nothing: "
            f"{silenced[:5]}. The absence gate is keying on component count "
            f"rather than surveyed_at, which discards the 'ran but could not "
            f"read the repo' outcome."
        )


class TestSynthesisingReadersUseTheEnvelope:
    """`_architecture_summary_results` and `_architecture_doc_lens_results`
    said "nothing yet" as top-level `{"state": ..., "message": ...}` — the
    pre-result_status shape. Neither facts._has_content nor
    context_compile._has_content exempts those keys, so for a repo nobody had
    surveyed the compiler packed a section headed "state: never_run" and the
    fact layer called it measured. Found by the compiler session on 2026-09-12
    while checking what #50's section cap was counting.

    Checked by calling the readers on a stub registry, so the assertion is
    about the shape they return and not about what Postgres holds today.
    """

    class _Empty:
        def query_findings(self, *a, **k):
            return []

        def query_finding_scopes(self, *a, **k):
            return []

    class _LensRanAndNamedNothing(_Empty):
        def query_findings(self, slug, kind, scope=None):
            if kind == "architecture_doc_lens" and scope is None:
                return [{"check_name": "lens_run", "surveyed_at": "2026-09-12T00:00:00+00:00"}]
            return []

    @pytest.mark.parametrize("reader_name", [
        "_architecture_summary_results", "_architecture_doc_lens_results",
    ])
    def test_never_run_is_the_envelope_and_both_content_checks_call_it_empty(self, reader_name):
        from resource_explorer import context_compile, facts
        from resource_explorer.surveyors import repo_survey_definition_adapter as adapter
        from resource_explorer.surveyors.result_status import NEVER_RUN

        value = getattr(adapter, reader_name)(self._Empty(), "no_such_repo")
        assert "state" not in value and "message" not in value, (
            f"{reader_name} still uses top-level state/message: {value}")
        assert value["_status"]["state"] == NEVER_RUN
        assert value["_status"].get("hint"), "a never-run without a hint renders as a blank card"
        assert not facts._has_content(value), "facts.py would call this MEASURED"
        assert not context_compile._has_content(value), "the compiler would pack a section for it"

    def test_doc_lens_nothing_found_survives_as_a_real_answer(self):
        """The lens ran and named nothing: still the envelope (so the compiler
        does not pack it), but `nothing_found`, so the card renders a ✓ and
        the fact layer reports a measured absence rather than a missing run."""
        from resource_explorer import context_compile, facts
        from resource_explorer.surveyors import repo_survey_definition_adapter as adapter
        from resource_explorer.surveyors.result_status import NOTHING_FOUND

        value = adapter._architecture_doc_lens_results(self._LensRanAndNamedNothing(), "r")
        assert value["_status"]["state"] == NOTHING_FOUND
        assert not facts._has_content(value)
        assert not context_compile._has_content(value)
        # facts._state_for trusts the envelope over content: nothing_found, not never_run.
        assert facts.FactLayer._state_for(value, {}) == NOTHING_FOUND

    @pytest.mark.parametrize("headline_name", [
        "_architecture_summary_headline", "_architecture_doc_lens_headline",
    ])
    def test_headlines_return_none_on_the_envelope(self, headline_name):
        from resource_explorer.surveyors import repo_survey_definition_adapter as adapter
        assert getattr(adapter, headline_name)(self._Empty(), "no_such_repo") is None
