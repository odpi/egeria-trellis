"""Where a check comes from, said only in detailed descriptions (owner, 2026-10-30), and the publish state from the counts.

- "Implements TestGen test type <id> (catalog commit <sha>)" only for a vendored check whose statement pin matches;
- "Inspired by TestGen's test type <id>; ..." for a check RE proposes itself or whose pin no longer matches;
- nothing for an imported check, an unknown type or an unreadable pin (never a guess);
- the rule's `description` carries it, its name, summary and the row's headline words do not;
- a republish of an already published rule UPDATES the description (it is not "edited in Egeria");
- publish state: a press that wrote nothing is not "published".
"""
from __future__ import annotations

from resource_explorer import quality_review
from resource_explorer.quality_words import attribution_of
from resource_explorer.surveyors.database.quality import egeria_rules as er
from resource_explorer.surveyors.database.quality.catalogue import catalogue_ref, load_catalogue
from tests.test_quality_q5_data_quality_rule import (  # noqa: F401 - fixtures and helpers reused
    SLUG,
    UNIQUE,
    FakeGateway,
    _accept,
    _gateway,
    _publish,
    entity,
    registry,
)

CATALOGUE = load_catalogue()


def test_a_vendored_check_with_a_matching_pin_implements_a_testgen_type():
    unique = CATALOGUE["Unique"]
    said = attribution_of(unique, "vendored", catalogue_ref(unique))
    assert said == f"Implements TestGen test type {unique.id} (catalog commit 3606859c)"


def test_a_check_re_proposes_itself_is_inspired_by_and_says_what_it_does_differently():
    rows = CATALOGUE["Row_Ct_Pct"]
    said = attribution_of(rows, "re", catalogue_ref(rows))
    assert said.startswith(f"Inspired by TestGen's test type {rows.id}; ") and "no selection rule" in said


def test_a_vendored_check_whose_pin_no_longer_matches_is_not_called_an_implementation():
    unique = CATALOGUE["Unique"]
    stale = catalogue_ref(unique).replace("sha256:", "sha256:0")[:-1]
    said = attribution_of(unique, "vendored", stale)
    assert said.startswith("Inspired by") and "Implements" not in said and "3606859c" in said


def test_nothing_is_claimed_without_the_data_to_claim_it():
    unique = CATALOGUE["Unique"]
    assert attribution_of(None, "vendored", "x") == ""
    assert attribution_of(unique, "testgen", catalogue_ref(unique)) == "", "an imported check is not described here"
    assert attribution_of(unique, "vendored", "testgen@3606859c:Unique") == "", "an unreadable pin claims nothing"
    assert attribution_of(unique, "", "") == ""


def _proposal(test_type, origin, ref):
    return {"id": 1, "check_key": f"s.t.c::{test_type}", "schema_name": "s", "table_name": "t", "column_name": "c",
            "test_type": test_type, "origin": origin, "catalogue_ref": ref, "params_json": {"severity": "error"}}


def test_the_row_carries_the_attribution_apart_from_its_headline_words():
    unique = CATALOGUE["Unique"]
    row = quality_review._row(_proposal("Unique", "vendored", catalogue_ref(unique)), None, CATALOGUE)
    assert row["attribution"].startswith("Implements TestGen test type")
    for headline in ("test_name", "reason_words", "status", "dimension", "column_name"):
        assert "TestGen" not in str(row[headline]), f"{headline} must not say TestGen"


def test_the_rule_description_carries_it_and_the_name_and_summary_do_not():
    unique = CATALOGUE["Unique"]
    props = er.rule_properties(UNIQUE, unique, {"threshold_value": 0, "severity": "error"},
                               proposal={"origin": "vendored", "catalogue_ref": catalogue_ref(unique)})
    assert props["description"].endswith(f"Implements TestGen test type {unique.id} (catalog commit 3606859c)")
    assert (unique.usage_notes or unique.description) in props["description"]
    for field in ("displayName", "summary", "qualifiedName", "method"):
        assert "TestGen" not in props.get(field, ""), field


def test_a_rule_with_no_attribution_keeps_its_old_description():
    unique = CATALOGUE["Unique"]
    props = er.rule_properties(UNIQUE, unique, {"threshold_value": 0, "severity": "error"}, proposal=None)
    assert props["description"] == (unique.usage_notes or unique.description)


def test_a_republish_updates_the_description_of_a_published_rule_and_is_not_reported_as_edited_in_egeria(
        registry, entity, monkeypatch):
    _accept(registry, UNIQUE, "Unique")
    proposal = {"origin": "vendored", "catalogue_ref": catalogue_ref(CATALOGUE["Unique"]), "profile_surveyed_at": ""}
    monkeypatch.setattr(registry, "get_quality_proposal", lambda _id: proposal)   # a real accept links its proposal
    gw = _gateway(entity)
    keep = er._described
    monkeypatch.setattr(er, "_described", lambda tt, proposal: tt.usage_notes or tt.description or "")
    assert _publish(registry, entity, gw)["counts"] == {"created": 1}        # published before the attribution existed
    old = gw.rules["rule-1"].properties["description"]
    assert "TestGen test type" not in old
    monkeypatch.setattr(er, "_described", keep)
    again = _publish(registry, entity, gw)
    assert again["counts"] == {"updated": 1}, again["counts"]
    assert "edited_in_egeria" not in again["counts"]
    assert "Implements TestGen test type" in gw.rules["rule-1"].properties["description"]
    assert len(gw.made("update")) == 1 and len(gw.made("create")) == 1
    assert _publish(registry, entity, gw)["counts"] == {"unchanged": 1}, "and a second republish changes nothing"


# ── the publish state words ───────────────────────────────────────────────────────────────────────────────

def test_only_checks_waiting_for_contents_is_not_published():
    """The live evidence: coco_pharma's press returned "published" with 3 not_in_egeria and nothing written."""
    state = er.publish_state({"not_in_egeria": 3})
    assert state == {"state": "nothing_published", "state_words": "nothing published · 3 waiting for Contents"}


def test_a_rule_in_egeria_and_nothing_waiting_is_published():
    for counts in ({"created": 2}, {"unchanged": 1}, {"adopted": 1, "updated": 1}, {"edited_in_egeria": 1}):
        assert er.publish_state(counts) == {"state": "published", "state_words": "published"}


def test_a_mix_says_how_many_of_each():
    assert er.publish_state({"created": 2, "not_in_egeria": 3}) == {
        "state": "partly_published", "state_words": "2 published · 3 waiting"}
    assert er.publish_state({"unchanged": 1, "created": 1, "not_in_egeria": 1})["state_words"] == "2 published · 1 waiting"


def test_trouble_is_added_to_the_words_never_hidden():
    said = er.publish_state({"created": 1, "refused": 2, "unreadable": 1})
    assert said["state"] == "published" and said["state_words"] == "published · 3 could not be published"
    assert er.publish_state({"refused": 1})["state_words"] == "nothing published · 1 could not be published"


def test_no_checks_and_retire_only_are_said_plainly():
    assert er.publish_state({})["state_words"] == "nothing to publish"
    assert er.publish_state({"retired": 2}) == {"state": "retired", "state_words": "2 retired"}


def test_a_publish_with_no_element_in_egeria_writes_nothing_and_does_not_say_published(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = FakeGateway({})                                  # Egeria holds no column or table for the check
    out = _publish(registry, entity, gw)
    assert out["counts"] == {"not_in_egeria": 1} and gw.made("create") == []
    assert out["state"] == "nothing_published" and out["state_words"] == "nothing published · 1 waiting for Contents"


# ── the Publish control's server side: what a press would do, and what it did ─────────────────────────────

def test_the_preview_before_any_press_counts_what_is_accepted_and_does_not_guess_what_egeria_holds(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    states = er.rule_states(registry, SLUG)
    assert states["preview"]["accepted"] == 1 and states["preview"]["to_publish"] == 1
    assert states["preview"]["waiting"] == 0, "waiting is known only from a press"
    assert states["preview"]["words"] == "1 to publish"
    assert states["in_egeria"] is False, "the fixture database has no Egeria asset yet"


def test_after_a_press_that_found_no_element_the_preview_says_waiting_for_contents(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    out = _publish(registry, entity, FakeGateway({}))
    assert out["state_words"] == "nothing published · 1 waiting for Contents"
    assert out["outcomes"][UNIQUE] == {"state": "not_in_egeria", "words": "not in Egeria · commit its schema in Contents"}
    states = er.rule_states(registry, SLUG)
    assert states["preview"]["words"] == "1 waiting for Contents (schema not committed)"
    assert states["rules"][UNIQUE]["words"] == "not in Egeria · commit its schema in Contents"


def test_a_published_rule_is_unchanged_in_the_preview_and_a_changed_accept_is_to_publish(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    assert _publish(registry, entity, gw)["outcomes"][UNIQUE]["words"] == "published · created in Egeria"
    assert er.rule_states(registry, SLUG)["preview"]["words"] == "1 unchanged"
    assert _publish(registry, entity, gw)["outcomes"][UNIQUE]["words"] == "published · unchanged"


def test_a_refusal_carries_egeria_s_own_sentence_in_the_row_words():
    assert er.outcome_words(er.REFUSED, {"error": "the type is not known"}) == "refused by Egeria · the type is not known"
    assert er.outcome_words(er.REFUSED, {}) == "refused by Egeria"
    assert er.outcome_words(er.UNREADABLE, {"error": "Egeria could not be reached"}).endswith("Egeria could not be reached")


def test_the_preview_says_nothing_accepted_and_does_not_say_nothing_to_publish_for_it(registry, entity):
    assert er.preview_words({"accepted": 0, "to_publish": 0, "to_retire": 0, "waiting": 0, "unchanged": 0, "edited": 0,
                             "retry": 0}) == "nothing is accepted yet"
    assert er.rule_states(registry, SLUG)["preview"]["words"] == "nothing is accepted yet"
