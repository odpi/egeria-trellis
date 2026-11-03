"""Files build step 8: data_class_match over the file profiles (F7), proposals only.

The database matcher (`column_matching.data_class_match`) runs over the newest profile run's columns, with values read
from the head of each file Resource Explorer profiled. Egeria's data classes arrive through a fake loader (no Egeria
call); the registry is a temp SQLite file. Nothing is published or created in Egeria.

Pinned: the precondition (no profiles: "not matched yet", the press refuses), a matched column stated against its
sample and named by file and column, a file Egeria profiled never opened, an unread catalogue never "no match", a
pattern no class has as a proposal, the filename rule apart from the value match, staleness after newer profiles,
the claim, an unreachable mount, nothing written into the path, and the routes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from resource_explorer.registry import FileSystemEntity, ProjectRegistry
from resource_explorer.surveyors.database.column_matching import KnownDataClass
from resource_explorer.surveyors.database.egeria_reference_catalog import ReferenceCatalog
from resource_explorer.surveyors.filesystem import file_class_match as fcm

EMAIL = KnownDataClass(guid="dc-email", qualified_name="DataClass::Email", display_name="Email Address",
                       specification=r"[^@\s]+@[^@\s]+\.[a-z]{2,}", match_property_names=("email",), data_type="string")
RUN = "2026-10-28T10:00:00"


def catalog(*classes):
    return lambda: ReferenceCatalog(data_classes=list(classes or [EMAIL]))


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "files8.db"))


def _register(registry, root, slug="tree"):
    registry.register_filesystem(FileSystemEntity(slug=slug, display_name=slug, local_mount_point=str(root)))
    return registry.get_filesystem(slug)


def _col(file_path, name, i, *, source="resource-explorer", state="measured", dtype="object", records=24):
    return {"file_path": file_path, "column_name": name, "ordinal": i, "declared_type": "", "inferred_general_type": dtype,
            "record_ct": records, "value_ct": records, "null_ct": 0, "distinct_ct": records, "sample_ratio": 1.0,
            "stats_source": source, "state": state}


#: Rows enough for a verdict: the matcher needs 20 values and 8 distinct (column_matching's own minimums).
ROWS = 24


def _tree(tmp_path) -> Path:
    import uuid

    root = tmp_path / "tree"
    (root / "hr").mkdir(parents=True)
    lines = [f"person{i}@example.com,{uuid.UUID(int=i * 7919 + 1, version=4)}" for i in range(ROWS)]
    (root / "hr" / "payroll.csv").write_text("email,id\n" + "\n".join(lines) + "\n")
    (root / "notes.csv").write_text("colour\n" + "\n".join(["red", "blue", "green"] * 8) + "\n")
    return root


def _profiles(registry, run=RUN, extra=()):
    registry.add_filesystem_file_columns("tree", run, [
        _col("hr/payroll.csv", "email", 0), _col("hr/payroll.csv", "id", 1), _col("notes.csv", "colour", 0), *extra])


def _by(out, column):
    return next(m for m in out["matches"] if m["column"] == column)


# ── before any profile ──

def test_with_no_profiles_it_is_not_priced_not_matched_and_the_press_refuses(registry, tmp_path):
    fs = _register(registry, tmp_path)
    v = fcm.view(registry, "tree")
    assert v["price"]["priced"] is False and "profile the data files first" in v["price"]["words"]
    assert v["run"] is None and v["words"] == "not matched yet"
    with pytest.raises(fcm.NothingToMatch):
        fcm.run_file_class_match(fs, registry, catalog_loader=catalog())


# ── the run ──

def test_a_column_matches_a_class_stated_against_its_sample_and_named_by_file_and_column(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    _profiles(registry)
    assert "matches 3 columns of 2 profiled files" in fcm.view(registry, "tree")["price"]["words"]
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    email = _by(out, "email")
    assert email["verdict"] == "matched" and email["established"] and email["matched_display_name"] == "Email Address"
    assert email["column_path"] == "hr/payroll.csv › email"
    assert "hr/payroll.csv › email" in email["statement"] and "head sample of 24 of 24 rows" in email["statement"]
    # every established claim carries its sample, never an unqualified "conforms"
    assert all("sample" in m["statement"] for m in out["matches"] if m["established"])
    assert out["established"] >= 1 and out["catalog"]["data_class_count"] == 1


def test_a_pattern_no_class_has_is_a_proposal_and_nothing_is_created_in_egeria(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    _profiles(registry)
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    ident = _by(out, "id")
    assert ident["verdict"] == "unmatched_patterned" and "uuid" in ident["detected_patterns"]
    assert out["proposals"] >= 1
    assert "a proposal for a new class" in fcm.run_words(out)


def test_a_file_egeria_profiled_is_never_opened_and_says_why(registry, tmp_path, monkeypatch):
    fs = _register(registry, _tree(tmp_path))
    _profiles(registry, extra=[_col("lake/people.csv", "email", 0, source="egeria:abcd1234")])
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    opened = []
    real = fp._open_plain
    monkeypatch.setattr(fp, "_open_plain", lambda p: opened.append(str(p)) or real(p))
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    egeria = next(m for m in out["matches"] if m["file_path"] == "lake/people.csv")
    assert egeria["verdict"] == "not_sampled" and "Egeria's CSV Survey Service profiled this file" in egeria["statement"]
    assert not any("people.csv" in p for p in opened)
    assert out["file_outcomes"]["egeria_profiled"] == 1


def test_an_unread_catalogue_is_not_compared_never_no_match(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    _profiles(registry)
    down = lambda: ReferenceCatalog(available=False, unavailable_reason="Egeria's data classes could not be read: refused")
    out = fcm.run_file_class_match(fs, registry, catalog_loader=down)
    assert {m["verdict"] for m in out["matches"]} == {"no_candidates"}
    assert out["established"] == 0 and "no_match" not in out["verdicts"]
    words = fcm.run_words(out)
    assert "nothing established · Egeria's data classes could not be read: refused" in words
    assert "match a data class" not in words


def test_the_filename_rule_is_its_own_proposal_on_the_name_only():
    assert fcm.name_signals("hr/payroll_2024.csv") == ["hr", "payroll"]
    assert fcm.name_signals("data/patients/visits.parquet") == ["patients"]
    assert fcm.name_signals("notes.csv") == []


def test_the_names_are_recorded_beside_the_value_match(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    _profiles(registry)
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    assert out["name_signals"] == [{"file_path": "hr/payroll.csv", "words": ["hr", "payroll"]}]
    assert out["name_signal_files"] == 1


def test_a_file_whose_profile_held_no_column_is_not_matched(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    _profiles(registry, extra=[{"file_path": "big.xlsx", "column_name": "", "ordinal": 0, "state": "over_limit",
                                "stats_source": "resource-explorer"}])
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    assert {"file_path": "big.xlsx", "outcome": "no_columns", "state": "over_limit"} in out["files_detail"]
    assert all(m["file_path"] != "big.xlsx" for m in out["matches"])


def test_the_run_reads_back_and_goes_stale_after_newer_profiles(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    _profiles(registry)
    fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    v = fcm.view(registry, "tree")
    assert v["run"]["words"].startswith("3 columns of 2 profiled files examined")
    assert v["run"]["stale"] is False and "nothing is published to Egeria" in v["run"]["rule"]
    _profiles(registry, run="2099-01-01T00:00:00")
    assert fcm.view(registry, "tree")["run"]["stale"] is True


def test_nothing_is_written_into_the_path(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    _profiles(registry)
    root = Path(fs.local_mount_point)
    before = sorted((str(p), p.stat().st_mtime_ns) for p in root.rglob("*"))
    fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    assert sorted((str(p), p.stat().st_mtime_ns) for p in root.rglob("*")) == before


def test_an_unreachable_mount_and_a_held_claim_each_refuse_and_record_nothing(registry, tmp_path):
    fs = _register(registry, tmp_path / "gone")
    _profiles(registry)
    with pytest.raises(fcm.NotReachable):
        fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    (tmp_path / "gone").mkdir()
    assert registry.take_claim(f"{fcm.STEP_KEY}:tree", "someone-else")
    with pytest.raises(fcm.AlreadyRunning):
        fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    assert not registry.query_step_runs(slug="tree", step_key=fcm.STEP_KEY)


# ── the routes and the card's read ──

@pytest.fixture
def api(registry, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app
    from tests.zone_fakes import open_access

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", registry.__dict__) or None)
    open_access(monkeypatch, caller_if_none="files8-tester")
    headers = {"Authorization": "Bearer " + create_access_token(user_id="files8-tester", egeria_token="tok")}
    return TestClient(app), headers


@pytest.mark.usefixtures("signed_in_caller")
def test_the_routes_price_refuse_and_run(api, registry, tmp_path, monkeypatch):
    client, headers = api
    assert client.get("/api/filesystems/ghost/class-match", headers=headers).status_code == 404
    _register(registry, _tree(tmp_path))
    r = client.post("/api/filesystems/tree/class-match", headers=headers)
    assert r.status_code == 409 and "Profile the data files first" in r.json()["detail"]
    _profiles(registry)
    monkeypatch.setattr(fcm, "default_catalog", catalog())
    r = client.post("/api/filesystems/tree/class-match", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == 3 and r.json()["view"]["run"]["established"] >= 1
    from resource_explorer.folder_survey_view import folder_survey_view

    assert folder_survey_view(registry, "tree")["class_match"]["run"]["profile_run"] == RUN
