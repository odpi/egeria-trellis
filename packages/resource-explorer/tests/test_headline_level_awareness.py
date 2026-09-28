"""Slice 21a: `FactLayer._headline_for`/`_primary_level` become level-aware.

Before this, the SAME resource-level headline answered every question an
analysis backs, regardless of the question's own declared `levels` — found
live, owner's question 2026-09-26: "How big is this database"
(`levels: [resource, container]`) and "Which schemas carry the data, and
which are system, empty or staging?" (`levels: [container]` alone) both
rendered `schema_inventory`'s identical resource-level sentence, so the
second question ticked ✓ on a line that names schemas but classifies none.
"""
from __future__ import annotations

from resource_explorer.facts import FactLayer
from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter,
    register_adapter,
)


def _fake_kind(headline_reader):
    class _Results:
        pass
    r = _Results()
    r.headline_reader = headline_reader
    class _Kind:
        pass
    k = _Kind()
    k.results = r
    return k


class TestPrimaryLevelPicksTheRightOne:
    def _fl(self):
        return FactLayer(registry=object(), resource_type="fake-levels")

    def test_resource_wins_when_declared_alongside_a_sub_level(self):
        """"How big is this database" declares [resource, container] — the
        resource reading is unchanged by this slice, per the coordinator's
        own gate."""
        fl = self._fl()
        assert fl._primary_level({"levels": ["resource", "container"]}) == "resource"

    def test_a_container_only_question_gets_container(self):
        """"Which schemas carry the data...?" declares [container] alone —
        no resource fallback to prefer."""
        fl = self._fl()
        assert fl._primary_level({"levels": ["container"]}) == "container"

    def test_no_levels_declared_defaults_to_resource(self):
        """Every question authored before the Level column existed."""
        fl = self._fl()
        assert fl._primary_level({}) == "resource"
        assert fl._primary_level({"levels": []}) == "resource"


class TestHeadlineForIsLevelAware:
    def test_same_analysis_two_levels_two_different_headlines(self):
        resource_reader = lambda registry, slug: {"label": "8 schema(s), resource reading"}
        container_reader = lambda registry, slug: {"label": "coco_ods 22 tables — data"}
        adapter = ResourceTypeAdapter(
            entity_type="fake-levels", technology_type="Fake",
            re_analysis_steps={},
            get_entity=lambda registry, slug: object(),
            publish=lambda *a, **k: "",
            analysis_kinds=lambda: {"schema_inventory": _fake_kind(resource_reader)},
            analysis_container_headline_map=lambda: {"schema_inventory": container_reader},
        )
        register_adapter(adapter)
        fl = FactLayer(registry=object(), resource_type="fake-levels")

        resource_headline = fl._headline_for("schema_inventory", "mydb", "resource")
        container_headline = fl._headline_for("schema_inventory", "mydb", "container")

        assert resource_headline == "8 schema(s), resource reading"
        assert container_headline == "coco_ods 22 tables — data"
        assert resource_headline != container_headline

    def test_a_reader_with_no_container_map_entry_falls_back_to_resource(self):
        """No regression for every analysis that doesn't register a
        container-level reader — the coordinator's own rule."""
        resource_reader = lambda registry, slug: {"label": "resource-only sentence"}
        adapter = ResourceTypeAdapter(
            entity_type="fake-levels-2", technology_type="Fake",
            re_analysis_steps={},
            get_entity=lambda registry, slug: object(),
            publish=lambda *a, **k: "",
            analysis_kinds=lambda: {"some_analysis": _fake_kind(resource_reader)},
            analysis_container_headline_map=lambda: {},
        )
        register_adapter(adapter)
        fl = FactLayer(registry=object(), resource_type="fake-levels-2")

        assert fl._headline_for("some_analysis", "mydb", "container") == "resource-only sentence"

    def test_a_resource_type_with_no_container_map_declared_at_all_is_unaffected(self):
        """None (undeclared), not {} — the case every resource type other
        than database is in today."""
        resource_reader = lambda registry, slug: {"label": "resource-only sentence"}
        adapter = ResourceTypeAdapter(
            entity_type="fake-levels-3", technology_type="Fake",
            re_analysis_steps={},
            get_entity=lambda registry, slug: object(),
            publish=lambda *a, **k: "",
            analysis_kinds=lambda: {"some_analysis": _fake_kind(resource_reader)},
        )
        register_adapter(adapter)
        fl = FactLayer(registry=object(), resource_type="fake-levels-3")

        assert fl._headline_for("some_analysis", "mydb", "container") == "resource-only sentence"
        assert fl._headline_for("some_analysis", "mydb", "resource") == "resource-only sentence"


class TestCheckLevelRequiresAGenuineLevelReaderNotTheFallback:
    """The coordinator's own regression case: "a question at container level
    whose analysis has only a resource reader still renders the resource
    line but does NOT tick." `_headline_for`'s fallback exists so a reader
    with no level support keeps rendering SOMETHING — it must not also be
    read by `_check_level` as evidence the question was answered AT its
    own declared level."""

    def test_resource_only_reader_renders_text_but_the_checkmark_is_withheld(self):
        from resource_explorer.facts import Envelope, Fact
        from resource_explorer.surveyors.result_status import MEASURED

        resource_reader = lambda registry, slug: {"label": "resource-only sentence"}
        adapter = ResourceTypeAdapter(
            entity_type="fake-levels-4", technology_type="Fake",
            re_analysis_steps={},
            get_entity=lambda registry, slug: object(),
            publish=lambda *a, **k: "",
            analysis_kinds=lambda: {"some_analysis": _fake_kind(resource_reader)},
            analysis_container_headline_map=lambda: {},
        )
        register_adapter(adapter)
        fl = FactLayer(registry=object(), resource_type="fake-levels-4")

        # Simulates what fact() would build: a container-only question, so
        # _headline_for falls back to the resource reader's text.
        headline = fl._headline_for("some_analysis", "mydb", "container")
        assert headline == "resource-only sentence"  # the text DOES render

        env = Envelope(subject="mydb")
        env.facts = [Fact(analysis_id="some_analysis", state=MEASURED,
                           value={"measured": True}, headline=headline)]
        fl._check_level(env, {"levels": ["container"]})

        assert env.level_mismatch is True  # but the checkmark is withheld

