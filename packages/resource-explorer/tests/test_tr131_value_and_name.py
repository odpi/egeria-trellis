"""TR-131: a GENERIC value shape (a date, a run of digits) no longer establishes a Data Class on its values alone.

The Architect's real-data check (egeria_v6, 2026-11-22) found a value conformance of 0.95 at weight 0.70 matched
whatever the column was called: Date of Birth matched 35 date columns (purchase_date, system_install_date, ...),
Phone 17 (NAICS codes, ip_address, salary, value_gbp) while missing real phone columns with "x718" extensions, SSN
any 9-digit id and Credit Card any 13-19 digit number. Now:
- a class whose value shape is generic is established only when the values conform AND the column name carries the
  class's names (`requiresNameMatch`, explicit in the seed and inferred for any regex that matches a bare digit
  string or a plain date); on its values alone it is held back, named among the other candidates;
- the card's values must pass the Luhn checksum; Phone takes real formats with extensions and rejects decimals,
  dotted quads and money; SSN and Date of Birth have real regexes;
- name_only (a class with no value rule, Password) stays not established (TR-127 ruling (b)).
No Egeria and no database: the shipped seed, the classes it would create, and the pure matcher.
"""
from __future__ import annotations

import re

import pytest

from resource_explorer.surveyors.database import bootstrap_data_classes as bdc
from resource_explorer.surveyors.database.column_matching import (
    ESTABLISHED_VERDICTS, GENERIC_SHAPE_PROBES, MATCH_MATCHED, MATCH_NAME_ONLY, NOT_ESTABLISHED_VERDICTS,
    KnownDataClass, data_class_match, luhn_valid, name_hint_passes,
)
from resource_explorer.surveyors.database.egeria_reference_catalog import data_class_from_element
from resource_explorer.surveyors.database.sampling import RANDOM, SampleProvenance

SEED = {d["name"]: d for d in bdc.STANDARD_DATA_CLASSES}


def _as_created(name: str) -> KnownDataClass:
    """The class the bootstrap would create for seed entry `name`, read back the way the catalog reads Egeria."""
    props = bdc._data_class_body(SEED[name])["properties"]
    element = {"elementHeader": {"guid": f"g-{name}", "type": {"typeName": "DataClass"}}, "properties": props}
    cls = data_class_from_element(element)
    assert cls is not None
    return cls


def _sample(values):
    return SampleProvenance(strategy=RANDOM, sample_rows=len(values), total_rows=4_200_000, seed=7,
                            sampled_at="2026-11-22")


def _match(values, classes, column, column_type="text"):
    return data_class_match(schema_name="public", table_name="t", column_name=column, column_type=column_type,
                            known_classes=list(classes), sampled_values=values, provenance=_sample(values))


def _luhn_complete(body: str) -> str:
    for d in "0123456789":
        if luhn_valid(body + d):
            return body + d
    raise AssertionError("unreachable")


ALL = [_as_created(n) for n in SEED]
PHONE, SSN, CARD, DOB = (_as_created(n) for n in ("PhoneNumber", "SocialSecurityNumber", "CreditCardNumber",
                                                   "DateOfBirth"))


def _matched_as(m, cls) -> bool:
    return m.verdict == MATCH_MATCHED and m.matched_qualified_name == cls.qualified_name


# ── the regexes and the Luhn check: good and bad values per class ─────────────────────────────────────────────────

GOOD_BAD = {
    "PhoneNumber": (
        ["+1 (555) 123-4567", "555-123-4567", "(555) 123-4567", "555.123.4567", "5551234567", "+44 20 7946 0958",
         "555-123-4567 x718", "555-123-4567x718", "(555) 123-4567 ext. 12", "555 123 4567 Ext 9", "+442079460958",
         "+33 1 23 45 67 89", "555-123-4567 #22", "1-800-555-1234"],
        ["111110.0", "1234567890.0", "192.168.100.200", "10.0.0.1", "$1,234.56", "1234.56", "85000.00", "541511",
         "1,234,567", "2021-03-04", "+44 1 2", "12345"]),
    "SocialSecurityNumber": (
        ["123-45-6789", "123456789", "078-05-1120"],
        ["000-12-3456", "666-12-3456", "912-34-5678", "123-00-4567", "123-45-0000", "12345678", "1234567890"]),
    "DateOfBirth": (
        ["1987-04-12", "1987/4/2", "04/12/1987", "12.04.1987", "1987-04-12 00:00:00", "2001-12-31T00:00"],
        ["1987-13-01", "1987-04-32", "2021-03-04 14:22:01", "1887-01-01", "13/31/1990", "purchase", "20210304"]),
}


@pytest.mark.parametrize("name", sorted(GOOD_BAD))
def test_each_class_regex_takes_its_real_formats_and_refuses_the_lookalikes(name):
    rx = re.compile(SEED[name]["specification"])
    good, bad = GOOD_BAD[name]
    assert [v for v in good if not rx.fullmatch(v)] == []
    assert [v for v in bad if rx.fullmatch(v)] == []


def test_the_card_needs_the_luhn_checksum_not_just_its_shape():
    rx = re.compile(SEED["CreditCardNumber"]["specification"])
    for v in ("4111111111111111", "4111 1111 1111 1111", "5500-0000-0000-0004", "378282246310005"):
        assert rx.fullmatch(v) and luhn_valid(v), v
    for v in ("4111111111111112", "1700000000123", "1234567890123456"):
        assert rx.fullmatch(v) and not luhn_valid(v), v      # card-shaped, checksum fails
    assert not luhn_valid("123456789012") and not luhn_valid("12345678901234567890") and not luhn_valid(None)


def test_the_card_class_asks_for_the_luhn_check_through_egeria():
    props = bdc._data_class_body(SEED["CreditCardNumber"])["properties"]
    assert props["specificationDetails"] == {"requiresNameMatch": "true", "valueCheck": "luhn"}
    assert CARD.value_check == "luhn"


# ── "generic": marked in the seed, and inferred ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["PhoneNumber", "SocialSecurityNumber", "CreditCardNumber", "DateOfBirth"])
def test_the_generic_classes_need_the_name_both_as_marked_and_as_inferred(name):
    assert SEED[name]["requiresNameMatch"] is True
    assert _as_created(name).requires_name_match
    bare = KnownDataClass(guid="g", qualified_name=f"DataClass::{name}", display_name=name,
                          specification=SEED[name]["specification"])          # no marking at all: inferred
    assert bare.requires_name_match


def test_email_is_specific_so_its_values_alone_still_establish_it():
    email = _as_created("EmailAddress")
    assert not email.requires_name_match
    m = _match([f"user{i}@example.com" for i in range(40)], [email], column="contact")   # not named email
    assert _matched_as(m, email)


def test_a_hand_authored_generic_class_in_egeria_fails_closed_too():
    """Authored in Egeria with no specificationDetails (or seeded before TR-131): a digits regex is generic anyway."""
    nine = KnownDataClass(guid="g", qualified_name="DataClass::TaxId", display_name="Tax Id",
                          specification=r"\d{9}", match_property_names=("tax_id",))
    assert nine.requires_name_match
    ids = [str(100000000 + i * 7919) for i in range(40)]
    assert not _matched_as(_match(ids, [nine], column="employee_id"), nine)
    assert _matched_as(_match(ids, [nine], column="tax_id"), nine)


def test_an_explicit_false_cannot_loosen_an_inferred_generic_shape():
    cls = KnownDataClass(guid="g", qualified_name="DataClass::D", display_name="D", specification=r"\d{4}-\d{2}-\d{2}",
                         specification_details=(("requiresNameMatch", "false"),))
    assert cls.requires_name_match


def test_the_probes_are_only_bare_digits_and_plain_dates():
    for probe in GENERIC_SHAPE_PROBES:
        assert re.fullmatch(r"[\d\-/.: ]+", probe), probe


# ── the name half ─────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("column,hints,expected", [
    ("date_of_birth", ["dob", "birth_date", "Date of Birth"], True),
    ("birth_date", ["birth_date"], True),
    ("customer_dob", ["dob"], True),
    ("DateOfBirth", ["Date of Birth"], True),
    ("purchase_date", ["dob", "birth_date", "Date of Birth"], False),
    ("date", ["dob", "birth_date", "Date of Birth"], False),
    ("system_install_date", ["birth_date"], False),
    ("work_phone", ["phone", "phone_number"], True),
    ("ip_address", ["phone", "phone_number", "telephone"], False),
    ("employee_id", ["ssn", "ssn_num", "Social Security Number"], False),
    ("card_number", ["card_number", "Credit Card Number"], True),
    ("order_number", ["card_number", "Credit Card Number"], False),
])
def test_name_hint_passes_needs_a_distinctive_word_not_a_generic_one(column, hints, expected):
    assert name_hint_passes(column, hints) is expected


# ── the Architect's false positives, as fixtures: NOT matched ─────────────────────────────────────────────────────

def _dates(n, year=2023):
    return [f"{year}-{1 + i % 12:02d}-{1 + i % 28:02d}" for i in range(n)]


FALSE_POSITIVES = [
    # (column, column type, values, the class it used to be matched as)
    ("purchase_date", "date", _dates(40), DOB),
    ("system_install_date", "date", _dates(40, 2019), DOB),
    ("date", "date", [f"19{50 + i % 49:02d}-{1 + i % 12:02d}-{1 + i % 28:02d}" for i in range(40)], DOB),
    ("naics_code", "text", [f"5415{i:02d}" for i in range(40)], PHONE),
    ("ip_address", "text", [f"10.0.{i}.{(i * 7) % 250}" for i in range(40)], PHONE),
    ("salary", "numeric", [f"85{i:03d}.00" for i in range(40)], PHONE),
    ("value_gbp", "numeric", [f"{i + 1}234.56" for i in range(40)], PHONE),
    ("reading", "numeric", [f"1111{i:02d}.0" for i in range(40)], PHONE),
    ("epoch_seconds", "bigint", [str(1700000000 + i * 3607) for i in range(40)], PHONE),
    ("employee_id", "integer", [str(100000000 + i * 7919) for i in range(40)], SSN),
    ("created_epoch_ms", "bigint", [str(1700000000000 + i * 123457) for i in range(40)], CARD),
    ("order_ref", "text", [_luhn_complete(f"4111{i:011d}") for i in range(40)], CARD),   # even Luhn-valid: no name
]


@pytest.mark.parametrize("column,ctype,values,cls", FALSE_POSITIVES, ids=[f[0] for f in FALSE_POSITIVES])
def test_a_generic_shape_does_not_match_a_column_not_named_for_the_class(column, ctype, values, cls):
    m = _match(values, ALL, column=column, column_type=ctype)
    assert not _matched_as(m, cls), m.describe()
    assert m.matched_qualified_name != cls.qualified_name


@pytest.mark.parametrize("column,ctype,values,cls", FALSE_POSITIVES, ids=[f[0] for f in FALSE_POSITIVES])
def test_and_on_its_own_against_that_class_alone_it_is_not_established_as_it(column, ctype, values, cls):
    m = _match(values, [cls], column=column, column_type=ctype)
    assert not _matched_as(m, cls), m.describe()


def test_a_held_back_class_is_named_with_its_reason_never_as_the_match():
    m = _match(_dates(40), [DOB], column="purchase_date", column_type="date")
    assert m.verdict != MATCH_MATCHED and not m.matched_qualified_name
    held = [c for c in m.other_candidates if c.get("held_back_reason")]
    assert [c["display_name"] for c in held] == ["Date of Birth"] and held[0]["sampled_conformance"] >= 0.95


def test_epoch_milliseconds_named_card_number_still_fail_the_checksum():
    m = _match([str(1700000000000 + i * 123457) for i in range(40)], [CARD], column="card_number")
    assert not _matched_as(m, CARD)


# ── real-named columns: matched ───────────────────────────────────────────────────────────────────────────────────

REAL = [
    ("date_of_birth", "date", [f"19{50 + i % 49:02d}-{1 + i % 12:02d}-{1 + i % 28:02d}" for i in range(40)], DOB),
    ("birth_date", "text", [f"{1 + i % 12:02d}/{1 + i % 28:02d}/19{60 + i % 39:02d}" for i in range(40)], DOB),
    ("customer_dob", "text", [f"{1 + i % 28:02d}.{1 + i % 12:02d}.19{60 + i % 39:02d}" for i in range(40)], DOB),
    ("phone", "text", [f"(212) 555-{1000 + i} x718" for i in range(40)], PHONE),
    ("work_phone", "text", [f"+1-212-555-{1000 + i}" for i in range(20)]
                           + [f"212.555.{2000 + i} ext. {i + 1}" for i in range(20)], PHONE),
    ("telephone", "text", [f"+44 20 7946 {1000 + i}" for i in range(40)], PHONE),
    ("ssn", "text", [f"{100 + i:03d}-{10 + i % 80:02d}-{1000 + i:04d}" for i in range(40)], SSN),
    ("ssn_num", "text", [f"{200 + i:03d}{10 + i % 80:02d}{2000 + i:04d}" for i in range(40)], SSN),
    ("card_number", "text", [_luhn_complete(f"4111{i:011d}") for i in range(40)], CARD),
    ("credit_card", "text", [" ".join(re.findall("....", _luhn_complete(f"5500{i:011d}"))) for i in range(40)], CARD),
]


@pytest.mark.parametrize("column,ctype,values,cls", REAL, ids=[r[0] for r in REAL])
def test_a_real_named_column_with_conforming_values_is_matched(column, ctype, values, cls):
    m = _match(values, ALL, column=column, column_type=ctype)
    assert _matched_as(m, cls), m.describe()
    assert m.verdict in ESTABLISHED_VERDICTS and m.evidence == "value_pattern" and m.sampled_conformance >= 0.95


def test_a_named_column_whose_values_do_not_conform_is_not_matched():
    m = _match([f"note {i}" for i in range(40)], [PHONE], column="phone")
    assert not _matched_as(m, PHONE)


# ── TR-127 ruling (b) still holds; unknown checks fail closed ────────────────────────────────────────────────────

def test_password_has_no_value_rule_and_stays_name_only_not_established():
    m = _match([f"hash{i}x" for i in range(40)], ALL, column="password")
    assert m.verdict == MATCH_NAME_ONLY and m.verdict in NOT_ESTABLISHED_VERDICTS and not m.established


def test_an_unknown_value_check_tests_nothing_and_names_the_class():
    cls = KnownDataClass(guid="g", qualified_name="DataClass::Odd", display_name="Odd Number",
                         specification=r"\d{13,19}", match_property_names=("odd_number",),
                         specification_details=(("valueCheck", "no-such-check"),))
    m = _match([_luhn_complete(f"4111{i:011d}") for i in range(40)], [cls], column="odd_number")
    assert m.verdict == MATCH_NAME_ONLY and "DataClass::Odd" in m.unusable_specifications


# ── the seed refuses a bad marking ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("extra,words", [
    ("    requiresNameMatch: maybe\n", "requiresNameMatch"),
    ("    valueCheck: crc32\n", "valueCheck"),
])
def test_the_seed_refuses_a_bad_marking_naming_the_entry(tmp_path, extra, words):
    p = tmp_path / "seed.yaml"
    p.write_text("data_classes:\n  - name: Bad\n    displayName: Bad\n    description: d\n    dataPatterns: [bad]\n"
                 "    specification: '\\d{9}'\n" + extra)
    with pytest.raises(bdc.SeedError, match=words):
        bdc.load_data_class_seed(p)


def test_a_check_or_a_name_rule_with_no_specification_is_refused(tmp_path):
    for extra in ("    valueCheck: luhn\n", "    requiresNameMatch: true\n"):
        p = tmp_path / "seed.yaml"
        p.write_text("data_classes:\n  - name: Bad\n    displayName: Bad\n    description: d\n    dataPatterns: [bad]\n"
                     + extra)
        with pytest.raises(bdc.SeedError, match="specification"):
            bdc.load_data_class_seed(p)


# ── read-back W1: the "001-" exit code, and a name-fit column that mostly conforms ───────────────────────────────

#: Shapes from customers-100.csv / people-100.csv (Phone 1, Phone 2, Phone): about a fifth carry the "001-" prefix.
_REAL_PHONES = ["001-808-617-6467x12895", "001-949-844-8787", "229.077.5154", "(392)345-1234x0013", "686-517-1290",
                "+1-542-845-0089x56789", "(704)987-6543", "312.555.0188x441"]


def test_an_exit_code_prefix_is_a_phone_number():
    rx = re.compile(SEED["PhoneNumber"]["specification"])
    for v in ("001-808-617-6467x12895", "001-949-844-8787", "011 44 20 7946 0958", "0044 20 7946 0958",
              "00 33 1 23 45 67 89"):
        assert rx.fullmatch(v), v
    for v in ("00 1", "0011", "011-44-12"):
        assert not rx.fullmatch(v), v


@pytest.mark.parametrize("column", ["Phone 1", "Phone 2", "Phone"])
def test_the_real_phone_columns_now_match(column):
    values = [f"001-{200 + i:03d}-{300 + i:03d}-{1000 + i:04d}x{100 + i}" for i in range(8)] + \
             [_REAL_PHONES[i % len(_REAL_PHONES)][:-1] + str(i % 10) for i in range(32)]
    m = _match(values, ALL, column=column)
    assert _matched_as(m, PHONE), m.describe()


def test_a_name_fit_column_that_mostly_conforms_is_inconclusive_never_an_established_no_match():
    phones = [f"555-{200 + i:03d}-{1000 + i:04d}" for i in range(32)]
    others = [f"see note {i}" for i in range(8)]                     # 0.80 conform
    m = _match(phones + others, ALL, column="phone")
    assert m.verdict not in ESTABLISHED_VERDICTS and m.verdict in NOT_ESTABLISHED_VERDICTS
    assert m.verdict == "inconclusive" and abs(m.sampled_conformance - 0.80) < 1e-9
    assert "Phone Number" in m.not_established_reason and not m.matched_qualified_name
    assert [c["display_name"] for c in m.other_candidates if c.get("near_miss_reason")] == ["Phone Number"]


def test_mostly_conforming_values_under_an_unrelated_name_are_not_a_near_miss():
    phones = [f"555-{200 + i:03d}-{1000 + i:04d}" for i in range(32)] + [f"see note {i}" for i in range(8)]
    m = _match(phones, ALL, column="comments")
    assert m.verdict != "inconclusive" and not any(c.get("near_miss_reason") for c in m.other_candidates)


def test_a_name_fit_column_below_the_near_floor_is_still_a_finding():
    values = [f"555-{200 + i:03d}-{1000 + i:04d}" for i in range(8)] + [f"see note {i}" for i in range(32)]   # 0.20
    m = _match(values, [PHONE], column="phone")
    assert m.verdict in ESTABLISHED_VERDICTS and not _matched_as(m, PHONE)


# ── read-back W2: an empty cell in a file is missing data, not a value ───────────────────────────────────────────

def _birthdates(n):
    return [f"{1 + i % 12:02d}/{1 + i % 28:02d}/19{60 + i % 39:02d}" for i in range(n)]


@pytest.mark.parametrize("ext", ["csv", "parquet"])
def test_blank_cells_are_dropped_by_the_file_sampler(tmp_path, ext):
    import pandas as pd

    from resource_explorer.surveyors.filesystem.file_class_match import sample_values
    dates = _birthdates(77)
    col = dates + [""] * 20 + ["   "] * 3                             # 77% dates, 23% blank
    p = tmp_path / f"userdata.{ext}"
    df = pd.DataFrame({"birthdate": col, "id": list(range(100))})
    if ext == "csv":
        df.to_csv(p, index=False)
    else:
        df.to_parquet(p, index=False)
    values, rows, why = sample_values(p, ext)
    assert why == "" and rows == 100
    assert values["birthdate"] and all(str(v).strip() for v in values["birthdate"])
    assert len(values["birthdate"]) == 77
    m = _match(values["birthdate"], ALL, column="birthdate")
    assert _matched_as(m, DOB), m.describe()


def test_with_the_blanks_counted_the_same_column_would_not_have_matched():
    """The bite, without the sampler: 23 blanks among 100 values is 0.77 conformance."""
    m = _match(_birthdates(77) + [""] * 23, [DOB], column="birthdate")
    assert not _matched_as(m, DOB) and m.verdict == "inconclusive"      # and now inconclusive, not "no match"
