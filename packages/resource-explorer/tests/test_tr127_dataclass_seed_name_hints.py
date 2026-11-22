"""TR-127: the data-class seed put column-name KEYWORDS in the DataClass's dataPatterns, but the value matcher
fullmatches `specification` + `dataPatterns` as VALUE regexes, so every seeded class failed its own value test and
disqualified itself on real values. Now: the seed writes the keywords as matchPropertyNames (name hints) and a real
value regex as `specification` where one exists, and the matcher treats metachar-free dataPatterns as name hints so
classes already seeded in Egeria stop disqualifying themselves. No Egeria; a duck-typed seed and the pure matcher.
"""
from __future__ import annotations

import re

import pytest

from resource_explorer.surveyors.database import bootstrap_data_classes as bdc
from resource_explorer.surveyors.database.column_matching import (
    MATCH_MATCHED, MATCH_NAME_ONLY, KnownDataClass, data_class_match,
)
from resource_explorer.surveyors.database.sampling import RANDOM, SampleProvenance  # noqa: F401  (see _sample)

_EMAILS = [f"user{i}@example.com" for i in range(40)]


def _sample(values):
    return SampleProvenance(strategy=RANDOM, sample_rows=len(values), total_rows=4_200_000, seed=7,
                            sampled_at="2026-11-22")


def _match(values, cls, column="email"):
    return data_class_match(schema_name="public", table_name="customer", column_name=column, column_type="text",
                            known_classes=[cls], sampled_values=values, provenance=_sample(values))


# ── the matcher ─────────────────────────────────────────────────────────────

SEEDED_AS_BEFORE = KnownDataClass(      # what the OLD seed wrote into Egeria: keywords as dataPatterns, no spec
    guid="g-email", qualified_name="DataClass::EmailAddress", display_name="Email Address", data_type="string",
    data_patterns=("email", "email_address", "mail_addr", "emailaddr"))


def test_a_class_seeded_the_old_way_no_longer_disqualifies_itself_but_is_name_only_never_matched():
    m = _match(_EMAILS, SEEDED_AS_BEFORE)
    assert m.verdict == MATCH_NAME_ONLY and m.verdict != MATCH_MATCHED and m.matched_guid == "g-email"
    assert m.evidence == "name_and_type"          # honest: nothing tested the values, the name and type fit


def test_metachar_free_data_patterns_are_name_hints_and_not_value_regexes():
    assert SEEDED_AS_BEFORE.value_regexes == ()
    assert set(SEEDED_AS_BEFORE.name_hints) >= {"email", "email_address"}


def test_a_dataPattern_with_regex_metacharacters_is_still_a_value_regex():
    cls = KnownDataClass(guid="g", qualified_name="DataClass::Digits", display_name="Digits", data_type="string",
                         data_patterns=(r"\d{3}-\d{4}", "digits"))
    assert cls.value_regexes == (r"\d{3}-\d{4}",)
    assert "digits" in cls.name_hints
    assert _match(_EMAILS, cls, column="digits").verdict != MATCH_MATCHED        # a real regex still disqualifies


def test_a_real_specification_still_tests_values_and_still_disqualifies():
    cls = KnownDataClass(guid="g", qualified_name="DataClass::Email", display_name="Email Address", data_type="string",
                         specification=r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", match_property_names=("email",))
    assert _match(_EMAILS, cls).evidence == "value_pattern" and _match(_EMAILS, cls).verdict == MATCH_MATCHED
    assert _match([f"n{i}" for i in range(40)], cls).verdict != MATCH_MATCHED


def test_blank_entries_are_ignored_not_a_value_regex_that_matches_nothing():
    cls = KnownDataClass(guid="g", qualified_name="DataClass::X", display_name="X", data_type="string",
                         data_patterns=("", "  ", "email"), match_property_names=("email",))
    assert cls.value_regexes == () and "" not in cls.name_hints and "  " not in cls.name_hints


def test_name_hints_come_from_matchPropertyNames_and_the_name_hint_dataPatterns():
    cls = KnownDataClass(guid="g", qualified_name="DataClass::X", display_name="X", data_type="string",
                         data_patterns=("phone",), match_property_names=("tel",))
    assert set(cls.name_hints) == {"tel", "phone"}


# ── the seed ────────────────────────────────────────────────────────────────

SEED = {d["name"]: d for d in bdc.STANDARD_DATA_CLASSES}
WITH_SPEC = {"EmailAddress", "PhoneNumber", "SocialSecurityNumber", "CreditCardNumber", "DateOfBirth"}
EXAMPLES = {
    "EmailAddress": (["jane.doe@example.com", "a_b+c@sub.example.org"], ["jane.doe", "not an email", "12345"]),
    "PhoneNumber": (["+44 20 7946 0958", "(555) 123-4567", "555-123-4567"], ["hello", "12", "jane@example.com"]),
    "SocialSecurityNumber": (["123-45-6789", "123456789"], ["12-345-6789", "hello", "1234"]),
    "CreditCardNumber": (["4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111"], ["1234", "hello"]),
    "DateOfBirth": (["1990-04-17", "04/17/1990"], ["hello", "17 April", "1990"]),
}


#: 40 distinct, real-looking values per class (the matcher's sample floor is 20 values and 8 distinct).
MANY = {
    "EmailAddress": lambda n: [f"user{i}@example.com" for i in range(n)],
    "PhoneNumber": lambda n: [f"+44 20 7946 {i:04d}" for i in range(n)],
    "SocialSecurityNumber": lambda n: [f"{100 + i:03d}-45-{6000 + i:04d}" for i in range(n)],
    "CreditCardNumber": lambda n: [f"4111 1111 1111 {1000 + i:04d}" for i in range(n)],
    "DateOfBirth": lambda n: [f"1990-04-{(i % 28) + 1:02d}" for i in range(n)],
}


def test_the_seed_still_lists_the_same_six_classes_and_keywords():
    assert set(SEED) == WITH_SPEC | {"Password"}
    assert "email" in SEED["EmailAddress"]["dataPatterns"] and "pwd" in SEED["Password"]["dataPatterns"]


@pytest.mark.parametrize("name", sorted(WITH_SPEC))
def test_a_class_with_a_value_shape_carries_a_real_regex_as_its_specification(name):
    spec = SEED[name].get("specification")
    assert spec, f"{name} has a recognisable value shape"
    rx = re.compile(spec)
    good, bad = EXAMPLES[name]
    assert all(rx.fullmatch(v) for v in good), [v for v in good if not rx.fullmatch(v)]
    assert not any(rx.fullmatch(v) for v in bad), [v for v in bad if rx.fullmatch(v)]


def test_password_has_no_specification_because_a_password_has_no_value_shape():
    assert "specification" not in SEED["Password"]


@pytest.mark.parametrize("name", sorted(SEED))
def test_the_created_data_class_sends_keywords_as_name_hints_never_as_value_patterns(name):
    props = bdc._data_class_body(SEED[name])["properties"]
    assert props["matchPropertyNames"] == SEED[name]["dataPatterns"]
    assert "dataPatterns" not in props                       # not keywords, and not a blank list item
    assert (props.get("specification") or None) == (SEED[name].get("specification") or None)


@pytest.mark.parametrize("name", sorted(SEED))
def test_what_the_seed_would_create_does_not_disqualify_itself_on_real_values(name):
    props = bdc._data_class_body(SEED[name])["properties"]
    cls = KnownDataClass(guid="g", qualified_name=props["qualifiedName"], display_name=props["displayName"],
                         data_type="string", specification=props.get("specification", ""),
                         match_property_names=tuple(props["matchPropertyNames"]))
    values = MANY[name](40) if name in MANY else [f"value{i}" for i in range(40)]
    m = _match(values, cls, column=SEED[name]["dataPatterns"][0])
    # a class with a value shape is tested on values; one with none (Password) is name_only, never "matched"
    want = MATCH_MATCHED if SEED[name].get("specification") else MATCH_NAME_ONLY
    assert m.verdict == want, (name, m.verdict, m.not_established_reason)


def test_a_specification_that_is_not_a_regex_is_refused_naming_the_entry(tmp_path):
    p = tmp_path / "seed.yaml"
    p.write_text("data_classes:\n  - name: Bad\n    displayName: Bad\n    description: d\n"
                 "    dataPatterns: [bad]\n    specification: '[unclosed'\n")
    with pytest.raises(bdc.SeedError, match="Bad.*specification"):
        bdc.load_data_class_seed(path=p, extra_path="")


def test_the_specification_field_is_optional_and_must_be_a_string(tmp_path):
    p = tmp_path / "seed.yaml"
    p.write_text("data_classes:\n  - name: NoSpec\n    displayName: N\n    description: d\n    dataPatterns: [nospec]\n")
    assert bdc.load_data_class_seed(path=p, extra_path="")[0].get("specification") is None
    p.write_text("data_classes:\n  - name: Odd\n    displayName: O\n    description: d\n"
                 "    dataPatterns: [odd]\n    specification: 5\n")
    with pytest.raises(bdc.SeedError, match="specification"):
        bdc.load_data_class_seed(path=p, extra_path="")
