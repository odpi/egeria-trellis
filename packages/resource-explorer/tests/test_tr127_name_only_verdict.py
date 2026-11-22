"""TR-127 / Architect ruling (b), 2026-11-22: a column whose NAME and TYPE fit a class that has no value rule is
`name_only`: a distinct verdict, never "matched". Every consumer keys on the verdict, so a distinct one fails CLOSED:
it counts as a match nowhere, is not sent to Egeria by the database publish, is not on Curate's review band or in
Send, and masks in the table preview. One red test per consumer. No Egeria.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from resource_explorer import fs_review, table_preview as tp  # noqa: E402
from resource_explorer.registry import ProjectRegistry  # noqa: E402
from resource_explorer.surveyors.database import column_matching as cm  # noqa: E402
from resource_explorer.surveyors.database.column_profile_step import _data_class_annotation, column_match_rows  # noqa: E402
from resource_explorer.surveyors.database.egeria_reference_catalog import ReferenceCatalog  # noqa: E402
from resource_explorer.surveyors.database.sampling import RANDOM, SampleProvenance  # noqa: E402
from resource_explorer.surveyors.filesystem import file_class_match as fcm  # noqa: E402

PASSWORD = cm.KnownDataClass(            # a class with a name, a type and NO value shape: the seed's Password
    guid="g-pwd", qualified_name="DataClass::Password", display_name="Password", data_type="string",
    match_property_names=("password", "passwd", "pwd"))
EMAIL = cm.KnownDataClass(
    guid="g-email", qualified_name="DataClass::EmailAddress", display_name="Email Address", data_type="string",
    specification=r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", match_property_names=("email",))
SECRETS = [f"s3cret-{i}-x" for i in range(40)]
EMAILS = [f"user{i}@example.com" for i in range(40)]
PROV = SampleProvenance(strategy=RANDOM, sample_rows=40, total_rows=40, seed=1, sampled_at="2026-11-22T10:00:00")


def _match(values, cls, column):
    return cm.data_class_match(schema_name="sales", table_name="orders", column_name=column, column_type="text",
                               known_classes=[cls], sampled_values=values, provenance=PROV)


# ── the verdict itself ──────────────────────────────────────────────────────

def test_a_name_and_type_fit_with_no_value_rule_is_name_only_never_matched():
    m = _match(SECRETS, PASSWORD, "password")
    assert m.verdict == cm.MATCH_NAME_ONLY == "name_only"
    assert m.verdict != cm.MATCH_MATCHED
    assert m.matched_guid == "g-pwd" and m.matched_display_name == "Password"      # it still says which class fits


def test_name_only_counts_as_established_nowhere_and_is_in_the_not_established_set():
    assert cm.MATCH_NAME_ONLY not in cm.ESTABLISHED_VERDICTS
    assert cm.MATCH_NAME_ONLY in cm.NOT_ESTABLISHED_VERDICTS
    m = _match(SECRETS, PASSWORD, "password")
    assert m.established is False and m.as_dict()["established"] is False


def test_the_honest_sentence_is_kept_and_does_not_claim_a_value_match():
    s = _match(SECRETS, PASSWORD, "password").describe()
    assert "column name and type only" in s and "no value conformance was tested" in s
    assert "conforms" not in s


def test_a_class_with_a_real_value_rule_still_matches_on_values():
    m = _match(EMAILS, EMAIL, "email")
    assert m.verdict == cm.MATCH_MATCHED and m.evidence == "value_pattern"


def test_a_real_rule_that_fails_still_disqualifies_the_class_name_only_does_not_rescue_it():
    assert _match(SECRETS, EMAIL, "email").verdict != cm.MATCH_NAME_ONLY


# ── consumer 1: the database auto-publish ───────────────────────────────────

def test_the_database_publish_sends_no_class_guid_for_a_name_only_column():
    ann = _data_class_annotation(_match(SECRETS, PASSWORD, "password"))
    assert ann.candidate_data_class_names == []        # today this was ["g-pwd"]: Password went out on name alone
    assert ann.label == "name_only" and ann.confidence == 0
    assert "NOT ESTABLISHED" in ann.explanation and "name" in ann.explanation.lower()


def test_a_real_match_is_still_published_with_its_class_guid():
    ann = _data_class_annotation(_match(EMAILS, EMAIL, "email"))
    assert ann.candidate_data_class_names == ["g-email"] and ann.label == "matched"


# ── consumer 2: the table preview ───────────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    from resource_explorer.registry import DatabaseEntity

    r = ProjectRegistry(db_path=str(tmp_path / "tp.db"))
    r.register_database(DatabaseEntity(slug="coco", display_name="Coco", db_type="postgresql", host="h", port=5432,
                                       database_name="coco", db_user="u", db_password="p"))
    return r


def test_a_name_only_column_stays_masked_as_undecided_not_as_a_matched_class(registry):
    m = _match(SECRETS, PASSWORD, "password")
    registry.write_detail_rows("database_column_matches", "coco", "2026-11-22T10:00:00", rows=column_match_rows([m.as_dict()]))
    d = {x["name"]: x for x in tp.masking_decisions(registry, "coco", "sales", "orders", ["password"])}["password"]
    assert d["masked"] is True and d["reason"] == tp.REASON_UNDECIDED
    assert cm.MATCH_NAME_ONLY not in tp._VERDICTS_MATCHED


# ── consumers 3-5: the file match run, its counts and rank, Curate's band and Send ─────

def _tree(tmp_path) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "users.csv").write_text("email,password\n" + "\n".join(f"u{i}@example.com,{s}" for i, s in enumerate(SECRETS)) + "\n")
    return root


def _col(name, i):
    return {"file_path": "users.csv", "column_name": name, "ordinal": i, "declared_type": "", "inferred_general_type": "object",
            "record_ct": 40, "value_ct": 40, "null_ct": 0, "distinct_ct": 40, "sample_ratio": 1.0,
            "stats_source": "resource-explorer", "state": "measured"}


@pytest.fixture
def run(tmp_path):
    from resource_explorer.registry import FileSystemEntity

    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    reg.register_filesystem(FileSystemEntity(slug="tree", display_name="tree", local_mount_point=str(_tree(tmp_path))))
    reg.add_filesystem_file_columns("tree", "2026-11-22T09:00:00", [_col("email", 0), _col("password", 1)])
    fs = reg.get_filesystem("tree")
    out = fcm.run_file_class_match(fs, reg, catalog_loader=lambda: ReferenceCatalog(data_classes=[EMAIL, PASSWORD]))
    return reg, out


def test_the_file_run_keeps_name_only_out_of_the_match_counts(run):
    _reg, out = run
    by = {m["column"]: m for m in out["matches"]}
    assert by["email"]["verdict"] == "matched" and by["password"]["verdict"] == "name_only"
    assert out["established"] == 1                                    # the email column, not the password
    assert out["verdicts"].get("name_only") == 1
    assert "1 column match a data class" in fcm.run_words(out)


def test_the_file_rank_puts_name_only_after_matched(run):
    _reg, out = run
    order = [m["column"] for m in out["matches"]]
    assert order.index("email") < order.index("password")


def test_curates_review_band_does_not_list_a_name_only_column(run):
    reg, _out = run
    _run, proposals, _kept = fs_review.class_proposals(reg, "tree")
    assert [p["column"] for p in proposals] == ["email"]


def test_send_can_never_be_given_a_name_only_column_even_if_someone_confirmed_its_key(run):
    reg, _out = run
    # a verdict for the name-only column's key, as if a person (or a stale page) confirmed it
    reg.append_resource_scope_event("filesystem", "tree", locator="users.csv::password::DataClass::Password",
                                    kind=fs_review.KIND_CLASS, choice=fs_review.CONFIRM, author="tester",
                                    proposal_rule="2026-11-22")
    _run, confirmed = fs_review.confirmed_classes(reg, "tree")
    assert all(c["column"] != "password" for c in confirmed)
