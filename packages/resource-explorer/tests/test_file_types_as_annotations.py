"""File types are a measurement, published as Egeria's own folder-survey annotations (ruling
2026-10-07, DESIGN-FILE-TYPES-AS-ANNOTATIONS.md). Recording fakes only: no Egeria, no network."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from resource_explorer import repo_publish as rp
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.annotation_props import build_annotation_props
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher, sub_resource_display_name
from resource_explorer.surveyors.file_type_profile import (
    CAPTURE_FILE_COUNTS, DEFAULT_NAMES, PROFILE_ASSET_TYPES, PROFILE_FILE_EXTENSIONS, PROFILE_FILE_NAMES,
    PROFILE_FILE_TYPES, build_file_type_annotations, file_names_log_annotation,
)
from resource_explorer.surveyors.sub_surveyors.file_inventory import FileInventorySurveyor
from resource_explorer.surveyors.survey_snapshot import annotation_from_dict, annotation_to_dict

SENTENCE = ("file types are published as profile annotations in the survey report; "
            "files are published by selection on Curate")
FILES = [("LICENSE", 10), ("README.md", 20), ("src/a.py", 5), ("src/b.py", 5), ("src/c.java", 7),
         ("docs/guide.md", 3), ("Dockerfile", 2)]


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="egeria_git", display_name="egeria_git", github_url="https://github.com/o/egeria_git"))
    r.upsert_file_inventory("egeria_git", FILES)
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr("resource_explorer.web.routes.egeria.get_current_user", lambda request: {"user_id": "dan"})
    from resource_explorer.web.app import app
    return TestClient(app)


def _by_name(anns):
    return {a.annotation_type_name: a for a in anns}


# ── (1) the publish emits exactly Egeria's annotation types and names ────────────────────────

class TestEgeriaNamesVerbatim:
    def test_names_and_types_for_a_fixture_repository(self, registry):
        anns = build_file_type_annotations(registry, "egeria_git", surveyed_at="2026-10-07T00:00:00")
        got = {(a.annotation_type_name, a.annotation_type.value) for a in anns}
        assert got >= {(CAPTURE_FILE_COUNTS, "ResourceMeasureAnnotation"),
                       (PROFILE_FILE_EXTENSIONS, "ResourceProfileAnnotation"),
                       (PROFILE_FILE_TYPES, "ResourceProfileAnnotation")}
        assert {a.annotation_type_name for a in anns} <= set(DEFAULT_NAMES)
        assert (CAPTURE_FILE_COUNTS, PROFILE_FILE_EXTENSIONS, PROFILE_FILE_TYPES, PROFILE_ASSET_TYPES) == DEFAULT_NAMES
        assert (CAPTURE_FILE_COUNTS, PROFILE_FILE_EXTENSIONS, PROFILE_FILE_TYPES, PROFILE_ASSET_TYPES) == (
            "Capture File Counts", "Profile File Extensions", "Profile File Types", "Profile Asset Types")

    def test_counts_equal_the_inventory(self, registry):
        by = _by_name(build_file_type_annotations(registry, "egeria_git", surveyed_at="x"))
        ext = by[PROFILE_FILE_EXTENSIONS].value_count
        assert ext == {"py": 2, "java": 1, "md": 2, "(none)": 2}
        rp = by[CAPTURE_FILE_COUNTS].resource_properties
        assert sum(ext.values()) == len(FILES) == rp["Number of files"]
        types = by[PROFILE_FILE_TYPES].value_count
        assert sum(types.values()) == len(FILES)
        assert rp["Number of subdirectories (folders)"] == 2                      # src, docs
        assert rp["Number of unique file extensions"] == len(ext)
        assert rp["Number of file types"] == len(types)
        assert rp["Number of unique filenames"] == len({f.rsplit("/", 1)[-1] for f, _ in FILES})
        assert rp["Total file size"] == "%s" % float(sum(n for _, n in FILES))

    def test_capture_keys_are_egerias_display_names_and_unmeasured_ones_are_omitted(self, registry):
        rp = _by_name(build_file_type_annotations(registry, "egeria_git", surveyed_at="x"))[CAPTURE_FILE_COUNTS].resource_properties
        assert set(rp) == {"Number of files", "Total file size", "Number of subdirectories (folders)",
                           "Number of unique filenames", "Number of unique file extensions", "Number of file types"}
        for unmeasured in ("Hidden File Count", "Readable files/directories", "Symbolic Link File Count",
                           "Number of unclassified files", "Number of inaccessible files",
                           "Number of deployed implementation types", "Number of asset types"):
            assert unmeasured not in rp, "never a zero for what was not measured"
        body = build_annotation_props(_by_name(build_file_type_annotations(registry, "egeria_git", surveyed_at="x"))[CAPTURE_FILE_COUNTS], "q")
        assert body["resourceProperties"]["Number of files"] == str(len(FILES))
        assert body["resourceProperties"]["Total file size"] == "%s" % float(sum(n for _, n in FILES))

    def test_summary_and_explanation_are_egerias_text(self, registry):
        by = _by_name(build_file_type_annotations(registry, "egeria_git", surveyed_at="x"))
        assert by[PROFILE_FILE_TYPES].summary == "Iterate through files under a directory (folder) and count the occurrences of each file type."
        assert by[PROFILE_FILE_EXTENSIONS].explanation == "The file extension often provides a hint as to the type of file."
        assert by[CAPTURE_FILE_COUNTS].summary.startswith("Count up the number of files and directories")

    def test_wire_body_is_egerias_class_with_value_count_and_the_envelope(self, registry):
        by = _by_name(build_file_type_annotations(registry, "egeria_git", surveyed_at="2026-10-07T00:00:00"))
        body = build_annotation_props(by[PROFILE_FILE_TYPES], "Annotation::egeria_git::x::3")
        assert body["class"] == "ResourceProfileAnnotationProperties"
        assert body["annotationType"] == "Profile File Types"
        assert body["valueCount"] == by[PROFILE_FILE_TYPES].value_count
        assert all(isinstance(v, int) for v in body["valueCount"].values())
        assert body["additionalProperties"] == {
            "producingStep": "FileInventory", "resultState": "MEASURED", "measuredAt": "2026-10-07T00:00:00",
            "producingRun": "egeria_git::2026-10-07T00:00:00", "scope": "WHOLE"}
        capture = build_annotation_props(by[CAPTURE_FILE_COUNTS], "q")
        assert capture["class"] == "ResourceMeasureAnnotationProperties" and capture["annotationType"] == "Capture File Counts"
        assert capture["additionalProperties"]["resultState"] == "MEASURED"

    def test_partial_scope_names_its_reason(self, registry):
        anns = build_file_type_annotations(registry, "egeria_git", surveyed_at="x", partial_reason="depth limited to 3")
        env = anns[1].additional_properties
        assert env["scope"] == "PARTIAL" and env["scopeReason"] == "depth limited to 3"

    def test_file_names_is_not_in_the_default_publish(self, registry):
        names = {a.annotation_type_name for a in build_file_type_annotations(registry, "egeria_git", surveyed_at="x")}
        assert PROFILE_FILE_NAMES not in names and PROFILE_FILE_NAMES not in DEFAULT_NAMES

    def test_file_names_on_request_is_a_log_annotation_pointing_at_a_csv(self, registry, tmp_path):
        csv_path = str(tmp_path / "names.csv")
        ann = file_names_log_annotation(registry, "egeria_git", surveyed_at="x", csv_path=csv_path)
        body = build_annotation_props(ann, "q")
        assert body["class"] == "ResourceProfileLogAnnotationProperties" and body["annotationType"] == "Profile File Names to External Log"
        assert body["additionalProperties"]["logFile"] == csv_path
        lines = open(csv_path).read().splitlines()
        assert lines[0] == "fileName,count" and "LICENSE,1" in lines

    def test_asset_types_profile_equals_the_selection_and_is_absent_without_one(self, registry):
        names = {a.annotation_type_name for a in build_file_type_annotations(registry, "egeria_git", surveyed_at="x")}
        assert PROFILE_ASSET_TYPES not in names, "nothing chosen is not a measured zero"
        for loc, kind in (("", "folder"), ("src", "folder"), ("LICENSE", "file"), ("src/a.py", "file")):
            with registry._conn() as conn:
                conn.execute("INSERT INTO sub_resources (resource_type, resource_slug, locator, kind, cataloged_at,"
                             " source_finding, detail_json, egeria_guid) VALUES ('repo','egeria_git',?,?,'t','','{}','')",
                             (loc, kind))
        by = _by_name(build_file_type_annotations(registry, "egeria_git", surveyed_at="x"))
        assert by[PROFILE_ASSET_TYPES].value_count == {"FileFolder": 2, "DataFile": 2}
        assert by[CAPTURE_FILE_COUNTS].resource_properties["Number of asset types"] == 2
        assert by[PROFILE_ASSET_TYPES].additional_properties["basis"] == "the selection on Curate"

    def test_the_inventory_step_emits_them_and_they_survive_the_snapshot(self, registry, tmp_path):
        from unittest.mock import patch
        proj = registry.get("egeria_git")
        with patch("resource_explorer.ingestion.pipeline.IngestionPipeline._store_file_inventory", return_value=len(FILES)), \
             patch("resource_explorer.ingestion.pipeline.IngestionPipeline._record_line_census"):
            anns = FileInventorySurveyor(proj, registry, local_path=str(tmp_path), surveyed_at="2026-10-07T00:00:00").run()
        names = {a.annotation_type_name for a in anns}
        assert set(DEFAULT_NAMES) - {PROFILE_ASSET_TYPES} <= names
        kept = [annotation_from_dict(annotation_to_dict(a)) for a in anns]
        assert [type(a) for a in kept] == [type(a) for a in anns]
        assert kept[2].value_count == anns[2].value_count and kept[2].annotation_type_name == anns[2].annotation_type_name

    def test_a_failure_to_profile_is_reported_not_swallowed(self, registry, tmp_path, monkeypatch):
        from unittest.mock import patch
        monkeypatch.setattr("resource_explorer.surveyors.file_type_profile.build_file_type_annotations",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        with patch("resource_explorer.ingestion.pipeline.IngestionPipeline._store_file_inventory", return_value=3), \
             patch("resource_explorer.ingestion.pipeline.IngestionPipeline._record_line_census"):
            anns = FileInventorySurveyor(registry.get("egeria_git"), registry, local_path=str(tmp_path)).run()
        bad = [a for a in anns if a.check_name == "file_type_profile"]
        assert bad and bad[0].confidence == 0 and "boom" in bad[0].explanation


# ── (2) the press is retired ─────────────────────────────────────────────────────────────────

class TestPressRetired:
    @pytest.mark.parametrize("method,path,body", [
        ("post", "/api/egeria/egeria_git/catalog-elements", {"elements": [{"label": "Python", "file_count": 1}]}),
        ("post", "/api/egeria/egeria_git/file-types/commit", {"elements": [{"label": "Python", "file_count": 1}]}),
        ("get", "/api/egeria/egeria_git/file-types", None),
    ])
    def test_old_routes_answer_410_with_the_sentence(self, client, method, path, body):
        r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 410 and r.json()["detail"] == SENTENCE

    def test_no_dataset_creation_call_is_possible_from_any_route(self, client, registry, monkeypatch):
        """Every AssetMaker the process could build is a recording fake that fails the test on a create."""
        calls = []
        fake = MagicMock()
        fake.create_asset.side_effect = lambda *a, **k: calls.append(("create_asset", a, k))
        fake.add_capability_asset_use.side_effect = lambda *a, **k: calls.append(("link", a, k))
        monkeypatch.setattr("pyegeria.AssetMaker", lambda *a, **k: fake)
        registry.set_egeria_asset_guid("egeria_git", "asset-1")
        client.post("/api/egeria/egeria_git/catalog-elements", json={"elements": [{"label": "Python", "file_count": 1}]})
        client.post("/api/egeria/egeria_git/file-types/commit", json={"elements": [{"label": "Python", "file_count": 1}]})
        assert calls == []
        assert not hasattr(rp, "FileTypeGateway") and not hasattr(rp, "file_types_commit") and not hasattr(rp, "file_types_preview")
        import inspect
        from resource_explorer.web.routes import egeria as routes
        assert "create_asset" not in inspect.getsource(routes.catalog_elements)
        assert "DataSetProperties" not in inspect.getsource(routes)

    def test_measurements_read_back_old_datasets_as_history_with_no_write(self, client, registry):
        registry.append_catalogue_commit_proof(
            "egeria_git", proof=rp.P_FILE_TYPE, node_kind=rp.NODE_FILE_TYPE, table_name="Python",
            element_guid="ds-old", qualified_name="DataSet::egeria_git::Python", recorded_by="dan", detail={})
        b = client.get("/api/egeria/egeria_git/file-type-measurements").json()
        assert b["inventoried"] is True
        assert [r["label"] for r in b["retired"]] == ["Python"]
        assert b["retired"][0]["word"] == "retired mechanism · kept in Egeria" and b["retired"][0]["dataset_guid"] == "ds-old"
        assert {p["name"] for p in b["profiles"]} >= {"Profile File Types", "Profile File Extensions", "Capture File Counts"}
        assert len(registry.list_catalogue_commit_proofs("egeria_git")) == 1, "reading wrote nothing"


# ── (3) the naming rule ──────────────────────────────────────────────────────────────────────

GITHUB_URL = "https://github.com/o/egeria_git"


def _row(locator, kind):
    return {"locator": locator, "kind": kind, "cataloged_at": "t", "source_finding": "", "detail_json": "{}", "egeria_guid": ""}


def _publisher(registry_rows, existing=None):
    registry = MagicMock()
    registry.list_sub_resources.return_value = registry_rows
    registry.get.return_value = Project(slug="egeria_git", display_name="egeria_git", github_url=GITHUB_URL)
    pub = EgeriaPublisher(platform_url="https://fake", registry=registry)
    pub._automated_curation = MagicMock()
    pub._asset_maker = MagicMock()
    pub._discovery = MagicMock()
    pub._connect = MagicMock()
    existing = existing or {}
    pub._automated_curation.get_guid_for_name.side_effect = lambda qn: existing.get(qn, "No elements found")

    async def _tmpl(name):
        return f"template-{name}"

    pub._automated_curation._async_get_template_guid_for_technology_type = _tmpl
    n = {"n": 0}

    def _create(body):
        n["n"] += 1
        return f"guid-{n['n']}"

    pub._automated_curation.create_elem_from_template.side_effect = _create
    return pub


class TestDisplayName:
    def test_rule(self):
        assert sub_resource_display_name("LICENSE", "file", "egeria_git") == "LICENSE · egeria_git"
        assert sub_resource_display_name("docs/README.md", "file", "egeria-workspaces") == "docs/README.md · egeria-workspaces"
        assert sub_resource_display_name("docs", "folder", "r") == "docs/ · r"
        assert sub_resource_display_name("", "folder", "r") == "/ · r"

    def test_a_new_license_carries_path_and_repository_and_its_filename_is_the_basename(self):
        pub = _publisher([_row("", "folder"), _row("docs", "folder"), _row("docs/LICENSE", "file"), _row("LICENSE", "file")])
        pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["", "docs", "docs/LICENSE", "LICENSE"])
        bodies = [c.args[0] for c in pub._automated_curation.create_elem_from_template.call_args_list]
        by_name = {b["placeholderPropertyValues"].get("fileName") or b["placeholderPropertyValues"]["directoryName"]: b
                   for b in bodies}
        lic = [b for b in bodies if b["placeholderPropertyValues"].get("fileName") == "LICENSE"]
        names = sorted(b["replacementProperties"]["displayName"] for b in lic)
        assert names == ["LICENSE · egeria_git", "docs/LICENSE · egeria_git"]
        assert all(b["placeholderPropertyValues"]["fileName"] == "LICENSE" for b in lic)
        assert by_name["docs"]["replacementProperties"]["displayName"] == "docs/ · egeria_git"
        assert all(b["replacementProperties"]["qualifiedName"].startswith(f"GitHubRepository::{GITHUB_URL}::") for b in bodies)

    def test_an_existing_element_is_renamed_forward_by_a_merge_update_only(self):
        qn = f"GitHubRepository::{GITHUB_URL}::LICENSE"
        guid = "11111111-2222-3333-4444-555555555555"
        pub = _publisher([_row("LICENSE", "file")], existing={qn: guid})
        out = pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["LICENSE"])
        assert out == {"LICENSE": guid}
        pub._automated_curation.create_elem_from_template.assert_not_called()
        (g,), kw = pub._asset_maker.update_asset.call_args
        assert g == guid and kw["body"]["mergeUpdate"] is True
        assert kw["body"]["properties"] == {"class": "AssetProperties", "displayName": "LICENSE · egeria_git"}
        for name in ("delete_asset", "archive_asset", "delete_element"):
            assert not getattr(pub._asset_maker, name).called

    def test_a_failed_rename_does_not_stop_the_publish(self):
        qn = f"GitHubRepository::{GITHUB_URL}::LICENSE"
        guid = "11111111-2222-3333-4444-555555555555"
        pub = _publisher([_row("LICENSE", "file")], existing={qn: guid})
        pub._asset_maker.update_asset.side_effect = RuntimeError("refused")
        assert pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["LICENSE"]) == {"LICENSE": guid}


# ── review fixes ─────────────────────────────────────────────────────────────────────────────

class TestEmptyInventoryIsNotMeasuredZero:
    def test_builder_returns_nothing_for_an_empty_inventory(self, tmp_path):
        r = ProjectRegistry(db_path=str(tmp_path / "e.db"))
        r.add(Project(slug="empty", display_name="empty", github_url="https://github.com/o/empty"))
        assert build_file_type_annotations(r, "empty", surveyed_at="x") == []

    def test_the_step_publishes_no_file_type_annotation_when_zero_files_were_inventoried(self, tmp_path):
        from unittest.mock import patch
        r = ProjectRegistry(db_path=str(tmp_path / "e.db"))
        r.add(Project(slug="empty", display_name="empty", github_url="https://github.com/o/empty"))
        with patch("resource_explorer.ingestion.pipeline.IngestionPipeline._store_file_inventory", return_value=0), \
             patch("resource_explorer.ingestion.pipeline.IngestionPipeline._record_line_census"):
            anns = FileInventorySurveyor(r.get("empty"), r, local_path=str(tmp_path)).run()
        assert not ({a.annotation_type_name for a in anns} & set(DEFAULT_NAMES))
        assert [a.check_name for a in anns] == ["file_inventory"]


class TestRenameOnlyWhenDifferent:
    GUID = "11111111-2222-3333-4444-555555555555"
    QN = f"GitHubRepository::{GITHUB_URL}::LICENSE"

    def _pub(self, current):
        pub = _publisher([_row("LICENSE", "file")], existing={self.QN: self.GUID})
        pub._asset_maker.get_asset_by_guid.return_value = current
        return pub

    def test_unchanged_republish_does_no_write(self):
        pub = self._pub({"properties": {"displayName": "LICENSE · egeria_git"}})
        pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["LICENSE"])
        pub._asset_maker.update_asset.assert_not_called()
        assert pub.rename_counts == {"updated": 0, "unchanged": 1, "failed": 0}

    def test_flat_displayName_shape_is_also_read(self):
        pub = self._pub({"displayName": "LICENSE · egeria_git"})
        pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["LICENSE"])
        pub._asset_maker.update_asset.assert_not_called()

    @pytest.mark.parametrize("current", [{"properties": {"displayName": "LICENSE"}}, None, {"x": 1}])
    def test_different_or_unreadable_name_is_updated_once(self, current):
        pub = self._pub(current)
        pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["LICENSE"])
        assert pub._asset_maker.update_asset.call_count == 1
        assert pub.rename_counts == {"updated": 1, "unchanged": 0, "failed": 0}

    def test_a_failed_rename_is_counted_and_said(self):
        pub = self._pub({"displayName": "old"})
        pub._asset_maker.update_asset.side_effect = RuntimeError("refused")
        pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["LICENSE"])
        assert pub.rename_counts == {"updated": 0, "unchanged": 0, "failed": 1}
        from resource_explorer.surveyors.egeria_publisher import rename_sentence
        assert rename_sentence(pub.rename_counts) == " · 0 names updated · 1 could not be updated"
        assert rename_sentence({"updated": 2, "unchanged": 3, "failed": 0}) == " · 2 names updated"
        assert rename_sentence({"updated": 0, "unchanged": 3, "failed": 0}) == ""


class TestRootFolderDirectoryName:
    def test_root_folder_directory_name_is_slash_never_the_repository_url(self):
        pub = _publisher([_row("", "folder"), _row("docs", "folder")])
        pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["", "docs"])
        bodies = [c.args[0] for c in pub._automated_curation.create_elem_from_template.call_args_list]
        names = [b["placeholderPropertyValues"]["directoryName"] for b in bodies]
        assert names == ["/", "docs"]
        assert GITHUB_URL not in names


class TestRenameReadIsTheCheapestRead:
    def test_the_pre_rename_read_does_not_fetch_relationships(self):
        qn = f"GitHubRepository::{GITHUB_URL}::LICENSE"
        guid = "11111111-2222-3333-4444-555555555555"
        pub = _publisher([_row("LICENSE", "file")], existing={qn: guid})
        pub._asset_maker.get_asset_by_guid.return_value = {"displayName": "LICENSE \u00b7 egeria_git"}
        pub.publish_sub_resources("egeria_git", GITHUB_URL, "asset", ["LICENSE"])
        (g,), kw = pub._asset_maker.get_asset_by_guid.call_args
        assert g == guid and kw["graph_query_depth"] == 0 and kw["output_format"] == "JSON"


class TestRetiredRequestModelGone:
    def test_catalog_request_model_is_removed(self):
        from resource_explorer.web.routes import egeria as routes
        assert not hasattr(routes, "CatalogRequest") and not hasattr(routes, "CatalogElement")


class TestRetiredRouteAnswers410BeforeValidating:
    def test_malformed_body_still_gets_410(self, client):
        for path in ("/api/egeria/egeria_git/catalog-elements", "/api/egeria/egeria_git/file-types/commit"):
            r = client.post(path, json={"elements": "not a list"})
            assert r.status_code == 410 and r.json()["detail"] == SENTENCE
            r = client.post(path, content=b"{not json", headers={"content-type": "application/json"})
            assert r.status_code == 410


class TestTotalFileSizeIsJavaDoubleToString:
    @pytest.mark.parametrize("n,text", [(0, "0.0"), (49, "49.0"), (9999999, "9999999.0"), (10000000, "1.0E7"),
                                        (12345678, "1.2345678E7"), (5_000_000_000, "5.0E9")])
    def test_matches_double_tostring(self, n, text):
        from resource_explorer.surveyors.file_type_profile import java_double_string
        assert java_double_string(float(n)) == text


# ── analysisStep is Egeria's; RE's attribution key is producingStep ──────────────────────────

EGERIA_STEP = "Profiling Associated Resources"


class TestProducingStep:
    def test_new_annotations_carry_producing_step_and_egerias_analysis_step(self, registry, tmp_path):
        anns = build_file_type_annotations(registry, "egeria_git", surveyed_at="x")
        anns.append(file_names_log_annotation(registry, "egeria_git", surveyed_at="x", csv_path=str(tmp_path / "n.csv")))
        assert len(anns) == 4
        for a in anns:
            assert a.analysis_step == EGERIA_STEP, a.annotation_type_name
            assert a.additional_properties["producingStep"] == "FileInventory"
            body = build_annotation_props(a, "q")
            assert body["analysisStep"] == EGERIA_STEP
            assert body["additionalProperties"]["producingStep"] == "FileInventory"

    def test_other_annotations_of_the_same_step_and_other_steps_are_unchanged(self, registry, tmp_path):
        from unittest.mock import patch
        with patch("resource_explorer.ingestion.pipeline.IngestionPipeline._store_file_inventory", return_value=3), \
             patch("resource_explorer.ingestion.pipeline.IngestionPipeline._record_line_census"):
            anns = FileInventorySurveyor(registry.get("egeria_git"), registry, local_path=str(tmp_path)).run()
        old = [a for a in anns if a.check_name == "file_inventory"][0]
        assert old.analysis_step == "FileInventory" and "producingStep" not in old.additional_properties

    def test_snapshot_round_trip_keeps_producing_step(self, registry):
        a = build_file_type_annotations(registry, "egeria_git", surveyed_at="x")[1]
        back = annotation_from_dict(annotation_to_dict(a))
        assert back.additional_properties["producingStep"] == "FileInventory" and back.analysis_step == EGERIA_STEP

    def test_step_of_reads_producing_step_first_then_analysis_step(self):
        from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation, step_of
        new = ResourceMeasureAnnotation(summary="s", analysis_step=EGERIA_STEP, additional_properties={"producingStep": "FileInventory"})
        old = ResourceMeasureAnnotation(summary="s", analysis_step="FileInventory")
        assert step_of(new) == step_of(old) == "FileInventory"
        assert step_of({"analysis_step": "A", "additional_properties": {"producingStep": "B"}}) == "B"
        assert step_of({"analysis_step": "A"}) == "A"


class TestMaterialiserAttribution:
    def _kind(self, ann):
        from resource_explorer.surveyors.egeria_annotation_materializer import EgeriaAnnotationMaterializer
        from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_STEP_MAP
        return EgeriaAnnotationMaterializer(registry=MagicMock(), reader=MagicMock())._kind_for(ann), REPO_ANALYSIS_STEP_MAP

    def test_attributes_via_producing_step_when_analysis_step_is_egerias(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_STEP_MAP
        analysis, keys = next((a, k) for a, k in REPO_ANALYSIS_STEP_MAP.items() if k)
        ann = {"annotation_type": "ResourceMeasureAnnotation", "analysis_step": EGERIA_STEP,
               "additional_properties": {"producingStep": keys[0]}}
        assert self._kind(ann)[0] == analysis

    def test_old_shape_without_producing_step_is_still_attributed_by_analysis_step(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_STEP_MAP
        analysis, keys = next((a, k) for a, k in REPO_ANALYSIS_STEP_MAP.items() if k)
        assert self._kind({"annotation_type": "ResourceMeasureAnnotation", "analysis_step": keys[0]})[0] == analysis

    def test_egeria_step_alone_attributes_to_nothing(self):
        from resource_explorer.surveyors.egeria_annotation_materializer import UNATTRIBUTED_KIND
        assert self._kind({"annotation_type": "ResourceMeasureAnnotation", "analysis_step": EGERIA_STEP})[0] == UNATTRIBUTED_KIND

    def test_the_readers_carry_additional_properties_through(self):
        from resource_explorer.surveyors.egeria_reader import _parse_annotation
        d = _parse_annotation({"guid": "g", "type": {"typeName": "ResourceProfileAnnotation"}},
                              {"annotationType": "Profile File Types", "analysisStep": EGERIA_STEP,
                               "additionalProperties": {"producingStep": "FileInventory"}})
        assert d["additional_properties"] == {"producingStep": "FileInventory"}
