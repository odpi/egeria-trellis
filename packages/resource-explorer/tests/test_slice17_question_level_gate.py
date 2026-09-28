"""Slice 17, item 3 (docs/design-notes/COORDINATOR-BRIEF-MULTI-RESOURCE.md,
Phase 1b row 17; docs/multi-resource-questions-design.md §18.3): a checkmark
is withheld when the question is asked below `resource` level (`container`,
`member`, `field` — design §18.3's engine-neutral vocabulary) and nothing
that actually reaches the screen names an item at that level.

`QuestionCatalogEntry.levels` (from #290) is the new field this reads —
consumers here, not the CSV/YAML machinery that produces it (already merged
and stable, per this slice's own ground rules).

**Slice 17b revision (2026-09-26):** the first cut bound this to
`target_shape` alone — a static catalog declaration of what an analysis is
CAPABLE of producing. Live gate on `coco_pharma` found that wrong:
`schema_inventory` declares `target_shape: single_container` (real
per-table rows, schema_name and all) and so satisfied the old gate, but the
rendered answer for "Which schemas carry the data" was
"table count 56 · column count 427" — no schema named anywhere, because
`schema_inventory` has no `headline_reader` and the frontend's
`scalarMeasures()` fallback drops list/object fields by design. The gate
now requires a known fact to have produced a `headline` — the one rung that
can carry member-naming prose past that fallback — before counting it
toward "answered at level". `target_shape` still decides what the note
says: "no per-{level} rows exist at all" (`whole_resource_only`) reads
differently from "rows exist, no reader shows them yet" (anything else) —
the second is a live pointer at slice 22's per-schema/per-table view.

**Slice 17c revision (2026-09-26):** `_check_level` now runs a stricter,
prior check unconditionally (see `test_slice17c_renderable_answer_gate.py`
for its own tests) — does ANY known fact render text at all, at any level?
The fixtures below default each fact's `value` to a non-empty scalar
(`{"measured": True}`) so they clear that check the same way a real
analysis with SOME scalar field would, and continue to exercise the
sub-resource-specific rule below it exactly as before. A fixture that wants
to test the "nothing renders at all" case passes `value={}` explicitly (see
`test_slice17c_renderable_answer_gate.py`).
"""
from __future__ import annotations

import pytest

from resource_explorer.facts import Envelope, Fact, FactLayer, clear_target_shape_cache
from resource_explorer.surveyors.result_status import MEASURED


@pytest.fixture(autouse=True)
def _clear_shape_cache():
    clear_target_shape_cache()
    yield
    clear_target_shape_cache()


def _fact_layer(
    monkeypatch, catalog: list[dict], container_headline_analyses: set[str] | None = None,
) -> FactLayer:
    monkeypatch.setattr(
        "resource_explorer.surveyors.analysis_catalog_reader.get_analyses",
        lambda resource_type, **kwargs: catalog,
    )
    fl = FactLayer.__new__(FactLayer)  # skip __init__ -- no registry needed here
    fl.resource_type = "database"
    # Slice 21a: `_check_level` now calls `self._map(...)` (via
    # `_level_specific_headline_exists`) for a sub-resource-level question,
    # which needs the cache `__init__` would normally set up.
    fl._maps_cache = {}
    if container_headline_analyses:
        # These tests assert "a fact with a genuine per-level headline
        # satisfies the gate" as a property of `_check_level` itself, for
        # analysis_ids that don't necessarily have a REAL container reader
        # registered today (only `schema_inventory` does, as of Slice 21a)
        # — the scenario under test is "if one existed", not "this specific
        # id happens to have one in production right now". Simulate that
        # registration directly rather than reaching for the real
        # database adapter's map.
        real_map = fl._map
        def _map(name, _real=real_map, _ids=container_headline_analyses):
            if name == "analysis_container_headline_map":
                return {aid: (lambda registry, slug: None) for aid in _ids}
            return _real(name)
        fl._map = _map
    return fl


def _measured_envelope(analysis_ids: list[str], headlines: dict | None = None) -> Envelope:
    headlines = headlines or {}
    env = Envelope(subject="coco_ods")
    env.facts = [
        Fact(
            analysis_id=aid, state=MEASURED, value={"measured": True},
            headline=headlines.get(aid, ""),
        )
        for aid in analysis_ids
    ]
    return env


class TestWholeResourceRollupBelowResourceLevel:
    def test_container_level_question_with_only_a_rollup_withholds_the_tick(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [
            {"id": "subject_signals", "target_shape": "whole_resource_only"},
        ])
        env = _measured_envelope(["subject_signals"])
        fl._check_level(env, {"levels": ["container", "member"]})
        assert env.answerable is True  # something real WAS measured
        assert env.level_mismatch is True
        assert "subject_signals" in env.level_note
        assert "rollup" in env.level_note

    def test_member_level_question_with_only_a_rollup_withholds_the_tick(self, monkeypatch):
        # coverage_signals's own real levels (question_catalog.yaml): ["member"].
        fl = _fact_layer(monkeypatch, [
            {"id": "coverage_signals", "target_shape": "whole_resource_only"},
        ])
        env = _measured_envelope(["coverage_signals"])
        fl._check_level(env, {"levels": ["member"]})
        assert env.level_mismatch is True

    def test_the_real_preliminary_fit_row_names_all_four_contributing_ids(self, monkeypatch):
        # preliminary_fit's real question row names four analysis_ids, all
        # target_shape: whole_resource_only in the real catalog.
        fl = _fact_layer(monkeypatch, [
            {"id": "grain_determination", "target_shape": "whole_resource_only"},
            {"id": "subject_signals", "target_shape": "whole_resource_only"},
            {"id": "coverage_signals", "target_shape": "whole_resource_only"},
            {"id": "preliminary_fit", "target_shape": "whole_resource_only"},
        ])
        env = _measured_envelope(
            ["grain_determination", "subject_signals", "coverage_signals", "preliminary_fit"]
        )
        fl._check_level(env, {"levels": ["container"]})
        assert env.level_mismatch is True
        for aid in ("grain_determination", "subject_signals", "coverage_signals", "preliminary_fit"):
            assert aid in env.level_note


class TestNotGatedWhenItShouldNotBe:
    def test_resource_level_question_is_exempt(self, monkeypatch):
        """"resource" is the default level -- a whole-database answer at
        that level genuinely IS the answer, not a rollup standing in for one."""
        fl = _fact_layer(monkeypatch, [
            {"id": "subject_signals", "target_shape": "whole_resource_only"},
        ])
        env = _measured_envelope(["subject_signals"])
        fl._check_level(env, {"levels": ["resource"]})
        assert env.level_mismatch is False
        assert env.level_note == ""

    def test_missing_levels_defaults_to_resource_and_is_exempt(self, monkeypatch):
        """Entries generated before the Level column existed carry no
        `levels` key at all -- must default to `["resource"]`, not gate."""
        fl = _fact_layer(monkeypatch, [
            {"id": "subject_signals", "target_shape": "whole_resource_only"},
        ])
        env = _measured_envelope(["subject_signals"])
        fl._check_level(env, {})
        assert env.level_mismatch is False

    def test_a_per_member_analysis_with_a_headline_satisfies_a_sub_resource_level(self, monkeypatch):
        """A real per-member breakdown that actually wrote a headline is
        enough -- the question is answered at its own level, even if a
        rollup-only analysis also contributed a fact."""
        fl = _fact_layer(monkeypatch, [
            {"id": "schema_inventory", "target_shape": "corpus"},
            {"id": "subject_signals", "target_shape": "whole_resource_only"},
        ])
        env = _measured_envelope(
            ["schema_inventory", "subject_signals"],
            headlines={"schema_inventory": "3 schemas: public, staging, audit."},
        )
        fl._check_level(env, {"levels": ["member"]})
        assert env.level_mismatch is False

    def test_single_container_shape_with_a_headline_also_satisfies_the_level(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [
            {"id": "grant_change", "target_shape": "single_container"},
        ], container_headline_analyses={"grant_change"})
        env = _measured_envelope(
            ["grant_change"], headlines={"grant_change": "2 grants changed on public.orders."}
        )
        fl._check_level(env, {"levels": ["container"]})
        assert env.level_mismatch is False


class TestCapableButUnrenderedStillGates:
    """The exact live regression (coco_pharma, 2026-09-26): an analysis whose
    catalog entry declares a per-member `target_shape` still withholds the
    tick when nothing it produced actually reaches the screen -- capability
    to store per-member rows is not the same as a reader that shows them."""

    def test_schema_inventory_without_a_headline_still_gates_a_container_question(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [
            {"id": "schema_inventory", "target_shape": "single_container"},
        ])
        env = _measured_envelope(["schema_inventory"])  # no headline -- the live bug
        fl._check_level(env, {"levels": ["container"]})
        assert env.level_mismatch is True
        assert "schema_inventory" in env.level_note
        assert "no reader shows them yet" in env.level_note
        assert "rollup" not in env.level_note  # this is NOT the "nothing to show" case

    def test_the_note_distinguishes_stored_but_unrendered_from_no_data_at_all(self, monkeypatch):
        """Mixing a whole_resource_only id with a capable-but-unrendered one:
        the note should point at the capable id specifically, not claim
        broadly that nothing names a member."""
        fl = _fact_layer(monkeypatch, [
            {"id": "schema_inventory", "target_shape": "single_container"},
            {"id": "db_activity_signals", "target_shape": "whole_resource_only"},
        ])
        env = _measured_envelope(["schema_inventory", "db_activity_signals"])
        fl._check_level(env, {"levels": ["container"]})
        assert env.level_mismatch is True
        assert "schema_inventory" in env.level_note
        assert "no reader shows them yet" in env.level_note

    def test_a_headline_on_one_capable_analysis_is_enough_even_with_others_unrendered(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [
            {"id": "schema_inventory", "target_shape": "single_container"},
            {"id": "row_count_snapshot", "target_shape": "corpus"},
        ], container_headline_analyses={"row_count_snapshot"})
        env = _measured_envelope(
            ["schema_inventory", "row_count_snapshot"],
            headlines={"row_count_snapshot": "1,204 rows, 3 tables measured."},
        )
        fl._check_level(env, {"levels": ["container"]})
        assert env.level_mismatch is False

    def test_no_known_facts_does_not_gate(self, monkeypatch):
        """`_check_level` is only reached from `answer()` when `env.answerable`
        is already true, but pinned directly here too: an envelope with
        nothing known must not fabricate a level_mismatch on top of already
        having no answer."""
        fl = _fact_layer(monkeypatch, [
            {"id": "subject_signals", "target_shape": "whole_resource_only"},
        ])
        env = Envelope(subject="coco_ods")
        env.facts = []
        fl._check_level(env, {"levels": ["member"]})
        assert env.level_mismatch is False


class TestEnvelopeSerialization:
    def test_level_mismatch_and_note_are_in_as_dict(self):
        env = Envelope(subject="x", level_mismatch=True, level_note="a note")
        d = env.as_dict()
        assert d["level_mismatch"] is True
        assert d["level_note"] == "a note"

    def test_defaults_are_false_and_empty(self):
        env = Envelope(subject="x")
        d = env.as_dict()
        assert d["level_mismatch"] is False
        assert d["level_note"] == ""


class TestRealCatalogAgreesWithTheLiveBugReport(object):
    """Confirms, against the REAL analysis_catalog.yaml and question_catalog.
    yaml on disk (not a fake catalog), that the exact three ids the review
    named (REVIEW-SURVEY-PANE-285.md's small-findings list: "which schemas
    carry the data... no schema named") are genuinely `target_shape:
    whole_resource_only` while being asked at `container`/`member` level --
    i.e. that this gate has real work to do today, not only in a fixture."""

    def test_subject_signals_is_whole_resource_only_but_asked_below_resource(self):
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        catalog = {a["id"]: a for a in get_analyses("database", include_egeria_live=False)}
        assert catalog["subject_signals"]["target_shape"] == "whole_resource_only"

        questions = get_questions("database")
        row = next(
            q for q in questions
            if "subject_signals" in (q["answering"].get("analysis_ids") or [])
            and q["answering"]["kind"] == "analysis"
        )
        assert set(row["levels"]) & {"container", "member", "field"}


class TestRealCatalogAgreesWithTheSlice17bLiveBugReport:
    """Confirms, against the real catalog on disk, that the coco_pharma
    gate's original regression is now closed: `schema_inventory` was given
    a real headline_reader (docs/design-notes/
    ENUMERATION-FLOOR-AND-COLLECTOR-HONESTY-IMPLEMENTED.md's follow-up)
    that names actual schemas, not just a count -- see
    `test_schema_inventory_headline.py` for the headline's own content
    tests. The mechanism test below (no headline at all -> gate fires) is
    kept as a regression pin on `_check_level` itself, using a synthetic
    envelope rather than the real map, since that is what it is actually
    testing."""

    def test_schema_inventory_now_has_a_headline_reader_in_the_real_map(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )

        assert "schema_inventory" in DATABASE_ANALYSIS_HEADLINE_MAP

    def test_which_schemas_carry_the_data_is_answered_only_by_schema_inventory(self):
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        row = next(
            q for q in get_questions("database")
            if q["question"].startswith("Which schemas carry the data")
        )
        assert row["answering"]["analysis_ids"] == ["schema_inventory"]
        assert "container" in row["levels"]

    def test_the_real_question_is_answered_now_that_schema_inventory_has_a_headline(self, monkeypatch):
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        catalog = get_analyses("database", include_egeria_live=False)
        fl = _fact_layer(monkeypatch, catalog)
        env = _measured_envelope(
            ["schema_inventory"],
            headlines={"schema_inventory": "7 schema(s) (coco_ods, coco_sus, demo)."},
        )
        row = next(
            q for q in get_questions("database")
            if q["question"].startswith("Which schemas carry the data")
        )
        fl._check_level(env, row)
        assert env.level_mismatch is False

    def test_the_mechanism_still_gates_a_schema_inventory_fact_with_no_headline_at_all(self, monkeypatch):
        """Regression pin on `_check_level` itself, independent of whether
        the real `schema_inventory` currently has a headline -- if a future
        change ever removed it, this question must gate again, not silently
        pass."""
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        catalog = get_analyses("database", include_egeria_live=False)
        fl = _fact_layer(monkeypatch, catalog)
        env = _measured_envelope(["schema_inventory"])  # no headline
        row = next(
            q for q in get_questions("database")
            if q["question"].startswith("Which schemas carry the data")
        )
        fl._check_level(env, row)
        assert env.level_mismatch is True
        assert "no reader shows them yet" in env.level_note

    def test_how_big_is_this_database_still_passes_via_row_count_snapshots_headline(self, monkeypatch):
        """The gate's other half (item c): "How big is this database" must
        stay ticked, because row_count_snapshot -- one of its two
        contributing analyses -- has a real headline_reader."""
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        catalog = get_analyses("database", include_egeria_live=False)
        fl = _fact_layer(monkeypatch, catalog)
        env = _measured_envelope(
            ["schema_inventory", "row_count_snapshot"],
            headlines={"row_count_snapshot": "1,204 row(s), 3.2 MB (3 of 3 tables measured)."},
        )
        row = next(
            q for q in get_questions("database")
            if q["question"].startswith("How big is this database")
        )
        fl._check_level(env, row)
        assert env.level_mismatch is False
