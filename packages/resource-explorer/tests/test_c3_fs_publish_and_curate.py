"""Item C3 (2026-11-19): a file system's Publish says what it does and re-publishes without duplicates, the declared
measures that were not writable become writable (ResourceProfileAnnotation), and Curate reviews the file system's
proposals (folder descriptions, data-class matches) with signed verdicts.

Every Egeria call is a fake (`tests/fs_egeria_fake.py`: the REAL publisher over an in-memory report and annotation
store). Nothing contacts Egeria and nothing is written to the shared registry (a temp sqlite registry throughout)."""
from __future__ import annotations

import pytest

from resource_explorer import fs_review
from resource_explorer import publish_table as pt
from tests.fs_egeria_fake import FakeEgeria, install
from tests.test_files_walk_extensions import _probe_row, _register, _tree, api, registry  # noqa: F401 - fixtures reused

ROW = {"analysis_id": "fs_permissions", "analysis": "File permissions", "measure": "fs_permissions",
       "locator": "fs_permissions/fs_permissions", "value": 134, "value_words": "134 files", "unit": "files",
       "as_of": "2026-10-29T00:14:46", "value_field": "files", "scope": "resource",
       "confidence": {"form": "measured", "method": "walk"},
       "writes_as": {"route": "generic", "type": "ResourceMeasureAnnotation", "words": pt.GENERIC_WORDS, "writable": True}}
ROW2 = {**ROW, "analysis_id": "fs_change_rates", "analysis": "Change rates", "measure": "fs_change_rates",
        "locator": "fs_change_rates/fs_change_rates", "value": 7}


def _declared(rows):
    return {"state": "sent" if rows else "none_chosen", "rows": rows, "annotations": [pt.annotation_for(r) for r in rows]}


def _chosen(monkeypatch, holder: list):
    """The chosen rows as the test holds them now: the press reads them every time."""
    monkeypatch.setattr(pt, "annotations_for", lambda reg, slug, entity_type="database": _declared(list(holder)))


def _egeria_led(client, headers, registry, tmp_path):
    _register(registry, _tree(tmp_path))
    _probe_row(registry, "egeria_verified")
    r = client.post("/api/filesystems/walkme/walk-extensions", json={"publish": False}, headers=headers)
    assert r.status_code == 200, r.text


# ── the resumable publish ───────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.usefixtures("signed_in_caller")
def test_a_second_press_with_nothing_new_is_200_and_creates_nothing(api, registry, tmp_path, monkeypatch):
    client, headers = api
    egeria = install(monkeypatch, FakeEgeria())
    _chosen(monkeypatch, [ROW])
    _egeria_led(client, headers, registry, tmp_path)

    first = client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers)
    assert first.status_code == 200 and first.json()["published"] is True and first.json()["nothing_new"] is False
    made = len(egeria.annotation_creates)
    assert len(egeria.report_creates) == 1 and made > 1
    assert first.json()["declared_measures"]["sent"] == 1
    assert first.json()["added"] == made and first.json()["report_reused"] is False

    again = client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers)
    body = again.json()
    assert again.status_code == 200
    assert body["nothing_new"] is True and body["words"] == "already published · nothing new to send"
    assert body["report_guid"] == first.json()["report_guid"] and body["published"] is True
    assert len(egeria.report_creates) == 1 and len(egeria.annotation_creates) == made, "nothing was created"


@pytest.mark.usefixtures("signed_in_caller")
def test_a_row_switched_to_publish_adds_exactly_one_annotation_to_the_same_report(api, registry, tmp_path, monkeypatch):
    client, headers = api
    egeria = install(monkeypatch, FakeEgeria())
    chosen = [ROW]
    _chosen(monkeypatch, chosen)
    _egeria_led(client, headers, registry, tmp_path)
    first = client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers).json()
    made = len(egeria.annotation_creates)

    chosen.append(ROW2)                                    # Keep -> Publish on Curate, no new Measure run
    second = client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers)
    body = second.json()
    assert second.status_code == 200 and body["nothing_new"] is False and body["added"] == 1
    assert body["report_guid"] == first["report_guid"] and body["report_reused"] is True
    assert len(egeria.annotation_creates) == made + 1 and len(egeria.report_creates) == 1
    assert egeria.annotation_creates[-1].endswith("declared_measure.fs_change_rates.fs_change_rates")
    assert body["declared_measures"]["sent"] == 2


@pytest.mark.usefixtures("signed_in_caller")
def test_an_interrupted_publish_resumes_with_the_remaining_annotations_and_no_second_report(api, registry, tmp_path, monkeypatch):
    client, headers = api
    egeria = install(monkeypatch, FakeEgeria(fail_after=2))
    _chosen(monkeypatch, [ROW])
    _egeria_led(client, headers, registry, tmp_path)

    cut = client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers).json()
    assert len(egeria.annotation_creates) == 2 and len(egeria.report_creates) == 1
    assert cut["annotations_in_egeria"] == 2 and cut["annotation_count"] > 2        # said, not hidden

    egeria.fail_after = None
    resumed = client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers).json()
    total = cut["annotation_count"]
    assert len(egeria.annotation_creates) == total and len(set(egeria.annotation_creates)) == total
    assert len(egeria.report_creates) == 1 and resumed["report_guid"] == cut["report_guid"]
    assert resumed["added"] == total - 2 and resumed["annotations_in_egeria"] == total
    assert resumed["nothing_new"] is False


@pytest.mark.usefixtures("signed_in_caller")
def test_the_report_that_cannot_be_read_creates_no_duplicate(api, registry, tmp_path, monkeypatch):
    """Unreadable is not empty: each annotation is looked up by name, so a second press still creates nothing twice."""
    client, headers = api
    egeria = install(monkeypatch, FakeEgeria())
    _chosen(monkeypatch, [ROW])
    _egeria_led(client, headers, registry, tmp_path)
    client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers)
    made = len(egeria.annotation_creates)
    egeria.report_readable = False
    again = client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers)
    assert again.status_code == 200 and again.json()["nothing_new"] is False
    assert len(egeria.annotation_creates) == made and len(egeria.report_creates) == 1


@pytest.mark.usefixtures("signed_in_caller")
def test_the_re_led_walk_publish_is_resumable_the_same_way(api, registry, tmp_path, monkeypatch):
    from tests.test_files2_fix_round_1 import _re_led_walk

    client, headers = api
    egeria = install(monkeypatch, FakeEgeria())
    chosen = [ROW]
    _chosen(monkeypatch, chosen)
    _re_led_walk(client, headers, registry, tmp_path)
    first = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    made = len(egeria.annotation_creates)
    nothing = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert nothing["nothing_new"] is True and len(egeria.annotation_creates) == made
    chosen.append(ROW2)
    more = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert more["added"] == 1 and more["report_guid"] == first["report_guid"] and len(egeria.report_creates) == 1


def test_the_card_asks_for_the_publish_again_while_a_chosen_measure_is_not_on_the_report(registry, tmp_path, monkeypatch):
    """`measures_pending` comes from the chosen measures and the read-back proofs of THAT report, never from a click."""
    _register(registry, _tree(tmp_path))
    monkeypatch.setattr(pt, "chosen_rows", lambda *a, **k: [ROW, ROW2])
    for loc in (ROW["locator"], ROW2["locator"]):
        registry.append_resource_scope_event("filesystem", "walkme", locator=loc, kind="measure", choice="publish", author="dan")
    assert pt.pending_for_report(registry, "walkme", "") == [], "a walk never published has no report to be behind"
    assert sorted(pt.pending_for_report(registry, "walkme", "report-1")) == sorted([ROW["locator"], ROW2["locator"]])
    pt.record_proofs(registry, "walkme", [{"analysis_id": "fs_permissions", "measure": "fs_permissions", "guid": "a1"}],
                     report_guid="report-1", entity_type="filesystem")
    assert pt.pending_for_report(registry, "walkme", "report-1") == [ROW2["locator"]]
    assert sorted(pt.pending_for_report(registry, "walkme", "report-2")) == sorted([ROW["locator"], ROW2["locator"]]), \
        "a proof against another walk's report does not count"
    pt.record_proofs(registry, "walkme", [{"analysis_id": "fs_change_rates", "measure": "fs_change_rates", "guid": "a2"}],
                     report_guid="report-1", entity_type="filesystem")
    assert pt.pending_for_report(registry, "walkme", "report-1") == []


@pytest.mark.usefixtures("signed_in_caller")
def test_the_view_carries_measures_pending_for_the_published_walk(api, registry, tmp_path, monkeypatch):
    client, headers = api
    install(monkeypatch, FakeEgeria())
    _chosen(monkeypatch, [ROW])
    monkeypatch.setattr(pt, "pending_for_report", lambda reg, slug, guid: [ROW2["locator"]] if guid else [])
    _egeria_led(client, headers, registry, tmp_path)
    assert client.get("/api/filesystems/walkme/folder-survey", headers=headers).json()["extensions"]["measures_pending"] == 0
    client.post("/api/filesystems/walkme/walk-extensions/publish", headers=headers)
    ext = client.get("/api/filesystems/walkme/folder-survey", headers=headers).json()["extensions"]
    assert ext["published"] is True and ext["measures_pending"] == 1


# ── standing words per kind; one name per action ────────────────────────────────────────────────────────────────

def test_standing_words_say_where_a_chosen_row_goes_per_kind():
    assert pt.standing_words("database") == "sent by every commit until Keep in RE"
    assert pt.standing_words("filesystem") == "sent by Publish chosen measures until Keep in RE"
    assert "commit" not in pt.standing_words("filesystem")


# ── ResourceProfileAnnotation from a declaration ────────────────────────────────────────────────────────────────

def _row_from(manifest_module: str, measure_name: str, value):
    import importlib

    manifest = importlib.import_module(f"{manifest_module}.manifest").MANIFEST
    m = next(x for x in manifest.declaration.measures if x.name == measure_name)
    w = pt.writes_as(m, "measured")
    return {"analysis_id": manifest.name, "analysis": manifest.display_name, "measure": m.name, "locator": f"{manifest.name}/{m.name}",
            "unit": m.unit, "value_field": m.value_field, "value": value, "value_words": str(value),
            "as_of": "2026-11-19T01:00:00", "scope": "resource", "confidence": {"form": "measured"}, "writes_as": w}


@pytest.mark.parametrize("module,measure", [("fs_activity", "fs_activity"), ("fs_activity", "fs_access_activity"),
                                            ("fs_permissions", "fs_permissions"), ("fs_layout_signals", None)])
def test_a_resource_profile_row_is_writable_and_built_with_its_declared_properties(module, measure):
    import importlib

    from resource_explorer.surveyors.annotation_props import build_annotation_props
    from resource_explorer.surveyors.survey_report import ResourceProfileAnnotation

    name = measure or importlib.import_module(f"{module}.manifest").MANIFEST.declaration.measures[0].name
    row = _row_from(module, name, 21)
    assert row["writes_as"]["type"] == "ResourceProfileAnnotation" and row["writes_as"]["writable"] is True
    ann = pt.annotation_for(row)
    assert isinstance(ann, ResourceProfileAnnotation)
    assert ann.value_count == {name: 21}                                   # the step's own number, as Egeria's valueCount
    extra = ann.additional_properties
    assert extra["declaredRoute"] == "native" and extra["analysis"] == module and extra["measure"] == name
    assert extra["asOf"] == "2026-11-19T01:00:00" and extra["valueField"] == row["value_field"]
    props = build_annotation_props(ann, "qn")
    assert props["class"] == "ResourceProfileAnnotationProperties" and props["valueCount"] == {name: 21}
    assert props["additionalProperties"]["measure"] == name
    assert "free text" not in " ".join(str(v) for v in props.values())


def test_a_resource_profile_value_that_is_not_a_whole_number_travels_as_the_step_stated_it():
    row = _row_from("fs_activity", "fs_activity", "about 40%")
    ann = pt.annotation_for(row)
    assert ann.value_count == {} and ann.additional_properties["fs_activity"] == "about 40%"


def test_a_request_for_action_row_is_still_not_written_and_says_why():
    row = _row_from("fs_permissions", "world_readable_sensitive", 3)
    assert row["writes_as"]["writable"] is False and row["writes_as"]["type"] == "RequestForActionAnnotation"
    assert row["writes_as"]["why"] == "Resource Explorer does not write RequestForActionAnnotation from a declaration yet"
    with pytest.raises(ValueError, match="cannot write"):
        pt.annotation_for(row)
    assert "RequestForActionAnnotation" not in pt.NATIVE_WRITABLE


# ── Curate's review: signed verdicts on proposals ───────────────────────────────────────────────────────────────

def _walk_with_narratives(registry, tmp_path, entries):
    """A stored walk whose `folder_narratives` are `entries` (what the newest walk proposed)."""
    fs = _register(registry, _tree(tmp_path))
    registry.add_filesystem_survey(fs.slug, "2026-11-19T01:00:00", {"surveyed_at": "2026-11-19T01:00:00", "total_files": 3,
                                                                    "folder_narratives": entries,
                                                                    "walk_extensions": {"run_id": "2026-11-19T01:00:00"}})
    return fs


def _narr(file, title="Payroll", description="Payroll extracts for 2026."):
    return {"folder": file.rpartition("/")[0], "file": file, "title": title, "description": description, "status": "proposal"}


def _class_run(registry, matches, *, ran_at="2026-11-19T02:00:00", not_kept=0):
    registry.record_step_run("walkme", "file_data_class_match", ran_at, entity_type="filesystem", source="resource-explorer",
                             executor="local", metrics={"ran_at": ran_at, "matches": matches, "matches_not_kept": not_kept})


def _match(file, column, cls="Email Address", guid="dc-1", *, established=True):
    return {"file_path": file, "column": column, "established": established, "matched_qualified_name": f"DataClass::{cls}",
            "matched_display_name": cls, "matched_guid": guid, "confidence": 90, "privacy_relevant": True}


def test_confirm_then_reject_then_confirm_records_three_signed_rows_and_the_newest_stands(registry, tmp_path):
    _walk_with_narratives(registry, tmp_path, [_narr("data/README.md")])
    key = "data/README.md"
    for who, verdict in (("dan", "confirm"), ("mandy", "reject"), ("dan", "confirm")):
        fs_review.save_verdict(registry, "walkme", kind=fs_review.KIND_FOLDER, key=key, verdict=verdict, author=who)
    rows = [e for e in registry.list_resource_scope_events("filesystem", "walkme") if e["kind"] == fs_review.KIND_FOLDER]
    assert [(e["author"], e["choice"], e["locator"]) for e in rows] == [("dan", "confirm", key), ("mandy", "reject", key),
                                                                         ("dan", "confirm", key)]
    assert all(e["changed_at"] and e["proposal_rule"] == "2026-11-19T01:00:00" and e["source"] == "proposal" for e in rows)
    item = fs_review.build_review(registry, "walkme")["folders"]["items"][0]
    assert item["verdict"]["verdict"] == "confirm" and item["verdict"]["by"] == "dan"
    # the verdict kinds never count as scope
    assert registry.current_resource_scope("filesystem", "walkme") == {}


def test_a_verdict_needs_a_proposal_that_stands_a_verdict_word_and_a_known_kind(registry, tmp_path):
    _walk_with_narratives(registry, tmp_path, [_narr("data/README.md")])
    for kw, why in (({"kind": fs_review.KIND_FOLDER, "key": "nope.md", "verdict": "confirm"}, "not proposed now"),
                    ({"kind": fs_review.KIND_FOLDER, "key": "data/README.md", "verdict": "maybe"}, "confirm, reject"),
                    ({"kind": "fs_other", "key": "data/README.md", "verdict": "confirm"}, "not something")):
        with pytest.raises(fs_review.ReviewRefused, match=why):
            fs_review.save_verdict(registry, "walkme", author="dan", **kw)
    assert registry.list_resource_scope_events("filesystem", "walkme") == []


def test_verdicts_survive_a_rerun_when_the_key_persists_and_say_no_longer_proposed_when_it_does_not(registry, tmp_path):
    fs = _walk_with_narratives(registry, tmp_path, [_narr("data/README.md"), _narr("docs/NOTES.md", "Notes", "Design notes.")])
    _class_run(registry, [_match("data/p.csv", "email"), _match("data/p.csv", "phone", "Phone Number", "dc-2")])
    c_key = "data/p.csv::email::DataClass::Email Address"
    fs_review.save_verdict(registry, "walkme", kind=fs_review.KIND_FOLDER, key="data/README.md", verdict="confirm", author="dan")
    fs_review.save_verdict(registry, "walkme", kind=fs_review.KIND_FOLDER, key="docs/NOTES.md", verdict="reject", author="dan")
    fs_review.save_verdict(registry, "walkme", kind=fs_review.KIND_CLASS, key=c_key, verdict="confirm", author="dan")

    # a later walk no longer finds docs/NOTES.md; a later match run matches the email column to another class
    registry.add_filesystem_survey("walkme", "2026-11-20T01:00:00", {"surveyed_at": "2026-11-20T01:00:00", "total_files": 3,
                                                                      "folder_narratives": [_narr("data/README.md")],
                                                                      "walk_extensions": {"run_id": "2026-11-20T01:00:00"}})
    _class_run(registry, [_match("data/p.csv", "email", "Contact Email", "dc-9"), _match("data/p.csv", "phone", "Phone Number", "dc-2")],
               ran_at="2026-11-20T02:00:00")
    review = fs_review.build_review(registry, "walkme")
    folders, classes = review["folders"], review["classes"]
    kept = next(i for i in folders["items"] if i["key"] == "data/README.md")
    assert kept["verdict"]["verdict"] == "confirm", "the key persisted: the verdict is kept"
    assert [g["key"] for g in folders["gone"]] == ["docs/NOTES.md"]
    assert folders["gone"][0]["words"] == "no longer proposed" and folders["gone"][0]["verdict"]["verdict"] == "reject"
    assert folders["gone"][0]["verdict"]["run"] == "2026-11-19T01:00:00" and folders["gone"][0]["listed"] is False
    assert [g["key"] for g in classes["gone"]] == [c_key] and classes["gone"][0]["verdict"]["by"] == "dan"
    new = next(i for i in classes["items"] if i["data_class"] == "Contact Email")
    assert new["verdict"] is None, "a different class for the column is a new proposal, not the old verdict"
    # the counts the collapsed line reads: still to review, and confirmed (the gone one is in neither)
    assert review["to_review"] == 2 and review["confirmed"] == 1
    # a verdict that stands never disappears: the rows are all still in the record
    assert len(registry.list_resource_scope_events("filesystem", "walkme")) == 3


def test_only_established_matches_are_proposed_and_the_run_says_what_it_did_not_keep(registry, tmp_path):
    _walk_with_narratives(registry, tmp_path, [])
    _class_run(registry, [_match("a.csv", "x"), _match("a.csv", "y", established=False)], not_kept=7)
    classes = fs_review.build_review(registry, "walkme")["classes"]
    assert classes["proposed"] == 1 and classes["not_kept"] == 7


def test_the_review_routes_sign_with_the_actor_and_read_without_egeria(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _walk_with_narratives(registry, tmp_path, [_narr("data/README.md")])
    got = client.get("/api/filesystems/walkme/review", headers=headers).json()
    assert got["to_review"] == 1 and got["folders"]["items"][0]["verdict"] is None
    r = client.put("/api/filesystems/walkme/review/verdict", headers=headers,
                   json={"kind": fs_review.KIND_FOLDER, "key": "data/README.md", "verdict": "confirm"})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["verdict"]["by"] == "walkext-tester" and r.json()["confirmed"] == 1
    refused = client.put("/api/filesystems/walkme/review/verdict", headers=headers,
                         json={"kind": fs_review.KIND_FOLDER, "key": "elsewhere.md", "verdict": "confirm"})
    assert refused.status_code == 422 and "not proposed now" in refused.json()["detail"]


# ── the data-class write after a confirm (PROPOSED shape; fakes only) ───────────────────────────────────────────

def _confirmed_setup(registry, tmp_path):
    _walk_with_narratives(registry, tmp_path, [])
    _class_run(registry, [_match("a.csv", "email"), _match("a.csv", "phone", "Phone Number", "dc-2"),
                          _match("b.csv", "ssn", "Social Security Number", "dc-3")])
    k = lambda f, c, n: f"{f}::{c}::DataClass::{n}"  # noqa: E731
    fs_review.save_verdict(registry, "walkme", kind=fs_review.KIND_CLASS, key=k("a.csv", "email", "Email Address"),
                           verdict="confirm", author="dan")
    fs_review.save_verdict(registry, "walkme", kind=fs_review.KIND_CLASS, key=k("a.csv", "phone", "Phone Number"),
                           verdict="reject", author="dan")
    # b.csv::ssn is never reviewed


def test_the_data_class_publish_writes_once_per_confirmed_match_and_never_for_a_rejected_or_unreviewed_one(registry, tmp_path, monkeypatch):
    _confirmed_setup(registry, tmp_path)
    egeria = install(monkeypatch, FakeEgeria())
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    run, confirmed = fs_review.confirmed_classes(registry, "walkme")
    assert [c["column"] for c in confirmed] == ["email"]
    fs = registry.get_filesystem("walkme")
    proof = EgeriaFileSystemSurveyor().publish_confirmed_data_classes(fs, run, confirmed, registry)
    assert len(egeria.annotation_creates) == 1 and len(egeria.report_creates) == 1
    ann = next(iter(egeria.annotations.values()))["body"]["properties"]
    assert ann["class"] == "DataClassAnnotationProperties" and ann["candidateDataClassGUIDs"] == ["dc-1"]
    assert "a.csv › email" in ann["summary"] and "confirmedBy" in ann["jsonProperties"] and "dan" in ann["jsonProperties"]
    assert proof["sent"] == 1 and proof["added"] == 1 and proof["nothing_new"] is False
    assert "class_match" in egeria.report_creates[0] and egeria.report_creates[0].endswith(run)

    # pressed again: the report and the annotation are read by name; nothing is created
    again = EgeriaFileSystemSurveyor().publish_confirmed_data_classes(fs, run, confirmed, registry)
    assert again["nothing_new"] is True and len(egeria.annotation_creates) == 1 and len(egeria.report_creates) == 1


def test_a_confirmed_match_with_no_class_guid_is_not_sent_and_says_so(registry, tmp_path, monkeypatch):
    _walk_with_narratives(registry, tmp_path, [])
    _class_run(registry, [_match("a.csv", "email", guid="")])
    key = "a.csv::email::DataClass::Email Address"
    fs_review.save_verdict(registry, "walkme", kind=fs_review.KIND_CLASS, key=key, verdict="confirm", author="dan")
    egeria = install(monkeypatch, FakeEgeria())
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    run, confirmed = fs_review.confirmed_classes(registry, "walkme")
    proof = EgeriaFileSystemSurveyor().publish_confirmed_data_classes(registry.get_filesystem("walkme"), run, confirmed, registry)
    assert proof["sent"] == 0 and proof["not_sent"][0]["key"] == key and egeria.annotation_creates == [] and egeria.report_creates == []


@pytest.mark.usefixtures("signed_in_caller")
def test_the_data_class_route_is_off_until_the_shape_is_ruled(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _confirmed_setup(registry, tmp_path)
    egeria = install(monkeypatch, FakeEgeria())
    monkeypatch.delenv("RE_FS_DATA_CLASS_PUBLISH", raising=False)
    refused = client.post("/api/filesystems/walkme/review/publish-data-classes", headers=headers)
    assert refused.status_code == 409 and "Architect's ruling" in refused.json()["detail"]
    assert egeria.annotation_creates == [] and egeria.report_creates == []
    monkeypatch.setenv("RE_FS_DATA_CLASS_PUBLISH", "1")
    ok = client.post("/api/filesystems/walkme/review/publish-data-classes", headers=headers)
    assert ok.status_code == 200 and ok.json()["sent"] == 1 and len(egeria.annotation_creates) == 1
