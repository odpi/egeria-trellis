"""Both kinds of survey card offer the same actions.

Resource Explorer renders surveys through two independent renderers -- local
analysis_catalog entries, and Egeria Survey Definitions -- and Discovery shows
both on one screen. They drifted into disjoint feature sets: Notify on one,
Publish on the other, Results on one, and a perspective filter that narrowed
only half the list while looking like it acted on the panel.

These assert the union, so a feature added to one renderer and not the other
fails here rather than being found by eye.
"""
from __future__ import annotations


# ── the backend halves the cards depend on ──────────────────────────────────


def test_catalog_entries_carry_their_step_keys():
    """Without these the local cards can offer no Publish button at all --
    which is exactly why they had none."""
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

    entries = {e["id"]: e for e in get_analyses("repo", include_egeria_live=False)}
    assert entries["repository_health"]["re_analysis_steps"] == ["repo_health"]
    # A multi-step analysis keeps all of them: publishing one third of what an
    # analysis produced, under its name, would be worse than publishing none.
    assert entries["language_file_classification"]["re_analysis_steps"] == [
        "repo_language", "repo_file_classification", "repo_file_structure",
    ]
    assert all("re_analysis_steps" in e for e in entries.values())


def test_survey_perspectives_are_the_union_over_its_steps():
    """A Survey Definition declares no perspectives in Egeria. Deriving them
    from its steps is what lets one filter act on both card kinds; inventing
    them would make the filter agree with nothing."""
    from resource_explorer.web.routes.survey_definitions import _derived_from_steps

    got = _derived_from_steps([
        {"re_analysis_step": "repo_health"},
        {"re_analysis_step": "repo_security"},
    ])
    assert got["analysis_ids"] == ["repository_health", "security_scan"]
    assert "Security" in got["perspectives"]
    assert got["perspectives_source"] == "derived"

    # A native/prefect-routed survey has no local steps, so it derives nothing
    # -- and must return empty rather than a plausible-looking default, since
    # the UI treats "no perspectives" as "do not filter this card out".
    empty = {"analysis_ids": [], "perspectives": [], "perspectives_source": ""}
    assert _derived_from_steps([{"re_analysis_step": None}]) == empty
    assert _derived_from_steps([]) == empty


def test_a_survey_reports_every_analysis_its_steps_run():
    """The Results button opens all of them, so a missing id is a silently
    missing results panel."""
    from resource_explorer.web.routes.survey_definitions import _derived_from_steps

    # repo_language belongs to language_file_classification, which owns three
    # step keys -- matching ANY of them must claim the analysis.
    got = _derived_from_steps([{"re_analysis_step": "repo_language"}])
    assert got["analysis_ids"] == ["language_file_classification"]


def test_a_scoped_perspective_replaces_the_derived_one():
    """A ScopedBy declaration must be able to NARROW.

    Merging declared with derived would quietly re-add whatever the author left
    out, so a survey tagged "Security" would still answer to Steward -- making
    the declaration incapable of changing anything.
    """
    from resource_explorer.web.routes.survey_definitions import _derived_from_steps

    steps = [{"re_analysis_step": "repo_health"}, {"re_analysis_step": "repo_security"}]
    derived = _derived_from_steps(steps)
    declared = _derived_from_steps(steps, declared=["Security"])

    assert "Steward" in derived["perspectives"]
    assert declared["perspectives"] == ["Security"]
    assert declared["perspectives_source"] == "scoped"
    # analysis_ids still come from the steps -- a declaration is about lenses,
    # not about which analyses ran.
    assert declared["analysis_ids"] == derived["analysis_ids"]


def test_a_survey_publishes_itself_only_when_every_step_is_native():
    """One resource-explorer step means local findings nobody will publish
    unless asked, so "already published" would be false for the whole survey."""
    from resource_explorer.web.routes.survey_definitions import _execution_split

    native = [{"executes_at": "egeria"}, {"executes_at": "egeria"}]
    mixed = [{"executes_at": "egeria"}, {"executes_at": "resource-explorer"}]
    local = [{"executes_at": "resource-explorer"}]

    assert _execution_split(native)["publishes_itself"] is True
    assert _execution_split(mixed)["publishes_itself"] is False
    assert _execution_split(local)["publishes_itself"] is False
    # A survey with no steps at all publishes nothing -- it must not claim to.
    assert _execution_split([])["publishes_itself"] is False

    assert _execution_split(mixed) == {
        "steps_native": 1, "steps_local": 1, "publishes_itself": False,
    }


def test_declared_perspectives_parse_from_additional_properties():
    """Egeria stores Additional Properties as strings, so a multi-valued one is
    separated by convention -- and an untrimmed " Steward" would match no
    filter while looking correct in the payload."""
    from resource_explorer.surveyors.survey_definition_reader import _split_perspectives

    assert _split_perspectives("Security, Steward") == ["Security", "Steward"]
    assert _split_perspectives("Security;Steward") == ["Security", "Steward"]
    assert _split_perspectives("A,,B, A") == ["A", "B"]
    assert _split_perspectives(None) == []
    assert _split_perspectives("") == []


def test_all_does_not_swallow_a_surveys_derived_perspectives():
    """One untagged step must not make a whole survey match every filter.

    "all" on a single analysis means "not specific to any lens". Unioned across
    a survey's steps it means the opposite -- that the survey serves every lens
    -- so a ten-step survey with nine Security steps and one generic one became
    unfilterable. Caught live: every Discovery candidate derived ["all", ...]
    and no perspective could narrow the tab.
    """
    from resource_explorer.web.routes.survey_definitions import _derived_from_steps

    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

    # Pick a step whose analysis really does carry "all", from the catalog
    # rather than by name -- an earlier version of this test hard-coded one and
    # broke the moment that entry was retagged.
    carrier = next(
        a for a in get_analyses("repo", include_egeria_live=False)
        if "all" in (a.get("perspectives") or []) and (a.get("re_analysis_steps") or [])
    )
    got = _derived_from_steps([
        {"re_analysis_step": "repo_security"},
        {"re_analysis_step": carrier["re_analysis_steps"][0]},
    ])
    assert "all" not in got["perspectives"], "'all' leaked into a derived union"
    assert "Security" in got["perspectives"]

    # A survey whose steps carry no real lens at all derives an empty list,
    # which callers treat as "cannot be judged, so keep it" -- the same visible
    # outcome as matching everything, reached honestly rather than by "all".
    assert _derived_from_steps([{"re_analysis_step": "no_such_step"}])["perspectives"] == []


def test_prefect_routed_steps_are_local_not_egeria_native():
    """Prefect is a dispatch mechanism, not a different owner of the findings.

    executes_at="prefect" runs Resource Explorer's OWN surveyor as a Prefect
    flow (resource_explorer/prefect/flows.py); the annotations come back here
    and still need publishing. Classifying anything that is not
    "resource-explorer" as Egeria-native was safe only while "egeria" was the
    sole alternative — a peer session began routing repo_arch_coupling through
    Prefect on 2026-08-26, and a survey whose steps were ALL Prefect-routed
    would then have reported "Publishes itself" and hidden the only button that
    publishes them.
    """
    from resource_explorer.web.routes.survey_definitions import _execution_split

    all_prefect = _execution_split([{"executes_at": "prefect"}, {"executes_at": "prefect"}])
    assert all_prefect["publishes_itself"] is False, \
        "a Prefect-routed survey must still offer Publish"
    assert all_prefect["steps_native"] == 0

    # A genuinely Egeria-native survey is unchanged: it writes its own
    # annotations, so there is nothing local left to publish.
    assert _execution_split([{"executes_at": "egeria"}])["publishes_itself"] is True

    mixed = _execution_split([{"executes_at": "resource-explorer"}, {"executes_at": "prefect"}])
    assert mixed["steps_local"] == 2 and mixed["steps_native"] == 0
