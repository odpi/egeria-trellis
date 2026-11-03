"""Files step 2, fix round 1 (2026-10-20): the prefix map, authorisation, counts-only publish, what landed, and the walk's
honesty about what it skipped.

Fakes bind the real signatures (`create_autospec(..., spec_set=True)` of pyegeria's AssetMaker, the real
`docker_inspect_mounts` signature). Nothing here reaches Egeria, docker, a database or Ollama; the conftest guard makes an
accidental probe launch FAIL (a BaseException no `except Exception` swallows).
"""
from __future__ import annotations

import json
import os
import subprocess
import types
from unittest.mock import create_autospec

import pytest

from resource_explorer import egeria_mounts, file_reach
from resource_explorer.registry import FileSystemEntity, ProjectRegistry

ASSET = "bbbbbbbb-0000-0000-0000-000000000002"


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "fix1.db"))


def _tree(tmp_path):
    root = tmp_path / "walkme"
    (root / "data").mkdir(parents=True)
    (root / "data" / "payroll_2026.csv").write_text("a,b\n1,2\n")
    (root / "README.md").write_text("# hi\n")
    (root / ".env").write_text("SECRET=1")
    (root / "keys.omsecrets").write_text("x")
    (root / "weird.xyz123").write_text("mystery")
    return root


def _register(registry, root, *, slug="walkme", canonical="/x/walkme"):
    registry.register_filesystem(FileSystemEntity(slug=slug, display_name="W", local_mount_point=str(root),
                                                  canonical_mount_point=canonical, egeria_asset_guid=ASSET))
    return registry.get_filesystem(slug)


def _walk(registry, root, **kw):
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    fs = _register(registry, root)
    return fs, LocalFileSystemSurveyor(fs, registry, **kw).run(profile_contents=False)


# ── B. the prefix map: one test per source, the empty map, precedence ──────────────────────────────────────────────

MOUNTS = [{"Type": "bind", "Source": "/Users/x/exchange/landing-area", "Destination": "/deployments/landing-area"},
          {"Type": "bind", "Source": "/Users/x/exchange/coco-data-lake", "Destination": "/deployments/coco-data-lake"}]

COMPOSE = """
services:
  egeria-main:
    container_name: quickstart-egeria-main
    volumes:
      - ../../exchange/landing-area:/deployments/landing-area
      - ../../exchange/coco-data-lake:/deployments/coco-data-lake:ro
      - namedvol:/deployments/ignored
  other:
    volumes:
      - ../../nope:/deployments/nope
"""


def _inspect_fake(mounts):
    fake = create_autospec(egeria_mounts._REAL_DOCKER_INSPECT, spec_set=True)   # binds the real signature
    fake.return_value = mounts
    return fake


def _compose_file(tmp_path):
    d = tmp_path / "ws" / "compose-configs" / "egeria-quickstart"
    d.mkdir(parents=True)
    f = d / "egeria-quickstart.yaml"
    f.write_text(COMPOSE)
    return f


def test_source_configured_json_and_equals_forms():
    pairs = egeria_mounts.build_mount_map(configured='{"/Users/x/lake": "/deployments/lake"}', inspect=_inspect_fake([]),
                                          today="2026-10-20")
    assert [(p.host_prefix, p.container_prefix, p.source, p.read_on) for p in pairs] == [
        ("/Users/x/lake", "/deployments/lake", "configured", "2026-10-20")]
    pairs = egeria_mounts.build_mount_map(configured="/a=/deployments/a, /b=/deployments/b", inspect=_inspect_fake([]))
    assert {p.host_prefix: p.container_prefix for p in pairs} == {"/a": "/deployments/a", "/b": "/deployments/b"}
    assert egeria_mounts.parse_configured("{not json") == [] and egeria_mounts.parse_configured("nonsense") == []


def test_source_docker_inspect_mounts_with_the_real_signature():
    inspect_fake = _inspect_fake(MOUNTS)
    pairs = egeria_mounts.build_mount_map(inspect=inspect_fake, today="2026-10-20")
    inspect_fake.assert_called_once_with("quickstart-egeria-main")
    assert {p.container_prefix: (p.host_prefix, p.source) for p in pairs} == {
        "/deployments/landing-area": ("/Users/x/exchange/landing-area", "read from quickstart-egeria-main mounts"),
        "/deployments/coco-data-lake": ("/Users/x/exchange/coco-data-lake", "read from quickstart-egeria-main mounts")}
    assert all(p.as_dict()["label"] == "read from quickstart-egeria-main mounts · 2026-10-20" for p in pairs)


def test_the_real_docker_inspect_parses_its_output_and_names_every_failure(monkeypatch):
    real = egeria_mounts._REAL_DOCKER_INSPECT
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(MOUNTS), stderr="")

    monkeypatch.setattr(subprocess, "run", create_autospec(subprocess.run, side_effect=fake_run))
    assert real("quickstart-egeria-main") == MOUNTS
    assert ran[0][:2] == ["docker", "inspect"] and ran[0][-1] == "quickstart-egeria-main"
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No such object"))
    with pytest.raises(egeria_mounts.DockerUnavailable):
        real("quickstart-egeria-main")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError("docker")))
    with pytest.raises(egeria_mounts.DockerUnavailable):
        real("quickstart-egeria-main")


def test_source_quickstart_compose_file_resolves_relative_paths_with_no_docker(tmp_path):
    compose = _compose_file(tmp_path)
    inspect_fake = create_autospec(egeria_mounts._REAL_DOCKER_INSPECT, spec_set=True,
                                   side_effect=egeria_mounts.DockerUnavailable("no docker"))
    pairs = egeria_mounts.build_mount_map(compose_setting=str(compose), inspect=inspect_fake, today="2026-10-20")
    base = tmp_path / "ws"            # ../../ from compose-configs/egeria-quickstart
    assert {p.container_prefix: p.host_prefix for p in pairs} == {
        "/deployments/landing-area": os.path.normpath(str(base / "exchange" / "landing-area")),
        "/deployments/coco-data-lake": os.path.normpath(str(base / "exchange" / "coco-data-lake"))}
    assert {p.source for p in pairs} == {"read from the quickstart compose file"}
    assert pairs[0].as_dict()["label"] == "read from the quickstart compose file · 2026-10-20"
    assert "ignored" not in " ".join(p.container_prefix for p in pairs)     # a named volume is not a path


def test_the_compose_file_setting_is_found_by_its_relative_default_not_a_hard_coded_path(tmp_path):
    compose = _compose_file(tmp_path)
    here = tmp_path / "somewhere" / "pkg" / "mod.py"
    here.parent.mkdir(parents=True)
    here.write_text("")
    found = egeria_mounts.find_compose_file("ws/compose-configs/egeria-quickstart/egeria-quickstart.yaml", start=here)
    assert found == compose
    assert egeria_mounts.find_compose_file("nope/none.yaml", start=here) is None
    import inspect

    src = inspect.getsource(egeria_mounts)
    assert 'DEFAULT_COMPOSE_FILE = "egeria-workspaces-fs/compose-configs/egeria-quickstart/egeria-quickstart.yaml"' in src
    assert "/Users/" not in src, "a machine's absolute path is hard-coded"


def test_precedence_configured_then_docker_then_compose(tmp_path):
    compose = _compose_file(tmp_path)
    base = os.path.normpath(str(tmp_path / "ws" / "exchange"))
    docker = [{"Source": "/Users/x/exchange/landing-area", "Destination": "/deployments/landing-area-DOCKER"}]
    pairs = egeria_mounts.build_mount_map(
        configured="/Users/x/exchange/landing-area=/deployments/landing-area-CONFIGURED",
        compose_setting=str(compose), inspect=_inspect_fake(docker), today="2026-10-20")
    by_host = {p.host_prefix: p for p in pairs}
    assert by_host["/Users/x/exchange/landing-area"].source == "configured"
    assert by_host["/Users/x/exchange/landing-area"].container_prefix == "/deployments/landing-area-CONFIGURED"
    assert f"{base}/landing-area" in by_host and by_host[f"{base}/landing-area"].source.endswith("compose file")


def test_no_source_gives_the_empty_map_and_nothing_is_guessed(tmp_path):
    no_docker = create_autospec(egeria_mounts._REAL_DOCKER_INSPECT, spec_set=True,
                                side_effect=egeria_mounts.DockerUnavailable("down"))
    assert egeria_mounts.build_mount_map(inspect=no_docker, compose_setting=str(tmp_path / "missing.yaml")) == []


def test_the_empty_map_makes_every_attach_say_mounts_unknown(registry, tmp_path):
    root = _tree(tmp_path)
    _register(registry, root, canonical="/deployments/coco-data-lake")
    state = file_reach.probe_filesystem("walkme", registry)           # the map under test is the loaded (empty) one
    assert state.state == file_reach.MOUNTS_UNKNOWN
    assert "Egeria's mounts unknown on this deployment · RE-led until configured" in state.sentence


def test_a_loaded_map_feeds_the_probe_and_records_the_prefix_source(registry, tmp_path, monkeypatch):
    root = _tree(tmp_path)
    _register(registry, root, canonical="/deployments/coco-data-lake/2026")
    monkeypatch.setattr(egeria_mounts, "docker_inspect_mounts", _inspect_fake(MOUNTS))
    egeria_mounts.reset_for_tests()
    egeria_mounts.load_at_start()
    state = file_reach.probe_filesystem("walkme", registry)
    assert state.state == file_reach.EXPECTED and state.prefix == "/deployments/coco-data-lake"
    assert state.prefix_source.startswith("read from quickstart-egeria-main mounts · ")


# ── the Admin route shows the map read-only ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def api(registry, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", registry.__dict__) or None)
    from tests.zone_fakes import open_access

    open_access(monkeypatch, caller_if_none="fix1-tester")
    headers = {"Authorization": "Bearer " + create_access_token(user_id="fix1-tester", egeria_token="tok")}
    return TestClient(app), headers


@pytest.mark.usefixtures("signed_in_caller")
def test_admin_shows_the_map_with_each_pairs_source_and_date(api, monkeypatch):
    client, headers = api
    from resource_explorer.web import admin_auth

    monkeypatch.setattr(admin_auth, "require_admin", lambda request: None)
    monkeypatch.setattr(egeria_mounts, "docker_inspect_mounts", _inspect_fake(MOUNTS))
    egeria_mounts.reset_for_tests()
    body = client.get("/api/egeria/mounts", headers=headers).json()
    assert body["empty"] is False and len(body["pairs"]) == 2
    assert all(p["source"] == "read from quickstart-egeria-main mounts" and p["read_on"] and p["label"] for p in body["pairs"])
    egeria_mounts.reset_for_tests()
    monkeypatch.setattr(egeria_mounts, "docker_inspect_mounts", _inspect_fake([]))
    empty = client.get("/api/egeria/mounts", headers=headers).json()
    assert empty["empty"] is True and "mounts unknown" in empty["empty_words"]


# ── C. M1: the reach routes need the run route's authorisation ───────────────────────────────────────────────────────


@pytest.mark.usefixtures("signed_in_caller")
def test_m1_reach_and_run_are_403_without_start_action(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _register(registry, _tree(tmp_path))
    asked = []

    def denied(reg, entity_type, slug, write, **kw):
        asked.append((entity_type, slug, write))
        return types.SimpleNamespace(allowed=False, reason="you may not start surveys on this file system")

    monkeypatch.setattr("resource_explorer.workflows.curate.resource_access", denied)
    for method, url in (("post", "/api/filesystems/walkme/reach"),
                        ("post", "/api/filesystems/walkme/folder-survey")):
        r = getattr(client, method)(url, headers=headers, **({"json": {}} if url.endswith("survey") else {}))
        assert r.status_code == 403, (url, r.text)
    assert {a[2] for a in asked} == {"start_action"}
    assert registry.get_latest_reachability("walkme") is None, "a refused press wrote a probe row"


# ── M2: the user's press runs as the signed-in Caller, never as the reachability daemon ──────────────────────────────


def test_m2_no_code_path_runs_a_user_press_as_the_reachability_daemon():
    import inspect

    from resource_explorer.web.routes import filesystems, native_surveys

    for mod in (file_reach, filesystems, native_surveys, egeria_mounts):
        assert "DaemonReason.REACHABILITY" not in inspect.getsource(mod)
    assert "_get_clients" not in inspect.getsource(file_reach)


# ── M3: counts only; no path reaches an annotation property ──────────────────────────────────────────────────────────


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _strings(v)


def test_m3_no_path_or_file_name_reaches_any_annotation_property(registry, tmp_path):
    from resource_explorer.surveyors.filesystem.folder_survey_annotations import folder_specs, to_annotations

    root = _tree(tmp_path)
    (root / "locked").mkdir()
    fs, sd = _walk(registry, root)
    sd["inaccessible_files"] = [{"path": "locked/.omsecrets", "error": "denied"}]
    sd["inaccessible_file_count"] = 1
    assert sd["classification"]["unclassified_count"] >= 1, "the fixture must have unclassified files to leak"
    annotations = to_annotations(folder_specs(sd, directory_name="/x/walkme"), additional={"surveyor": "Resource Explorer"})
    seen = []
    for a in annotations:
        for attr in ("resource_properties", "json_properties", "value_count", "additional_properties", "action_requested",
                     "summary", "explanation"):
            seen.extend(_strings(getattr(a, attr, None)))
    blob = "\n".join(seen)
    for secret in (str(root), "/x/walkme", "payroll_2026", "README.md", ".env", "keys.omsecrets", ".omsecrets", "weird.xyz123",
                   "locked", "data/"):
        assert secret not in blob, f"{secret!r} reached an annotation property"
    # the counts themselves are still there
    counts = [json.loads(json.dumps(a.json_properties)) for a in annotations if getattr(a, "json_properties", None)]
    assert any(c.get("count") for c in counts)


def test_m3_the_paths_stay_in_the_registry_and_the_card_reads_them(registry, tmp_path):
    from resource_explorer.surveyors.filesystem.folder_survey_annotations import folder_specs, read_folder_survey

    root = _tree(tmp_path)
    fs, sd = _walk(registry, root)
    sd["inaccessible_files"] = [{"path": "locked/a", "error": "denied"}]
    sd["inaccessible_file_count"] = 1
    view = read_folder_survey(folder_specs(sd, include_paths=True))
    assert view["inaccessible"]["listed"] == ["locked/a"]
    assert read_folder_survey(folder_specs(sd))["inaccessible"]["listed"] is None      # the publish form has no list


# ── M4 and the HTTP-level publish test ───────────────────────────────────────────────────────────────────────────────


def _fake_surveyor(monkeypatch, *, landed, read_back_guid="report-guid-7"):
    """The real EgeriaFileSystemSurveyor over a fake AssetMaker that binds pyegeria's real signatures."""
    from pyegeria import AssetMaker

    from resource_explorer.surveyors.filesystem import egeria_filesystem_surveyor as mod

    maker = create_autospec(AssetMaker, instance=True, spec_set=True)
    maker.create_asset.return_value = "report-guid-7"
    maker.get_asset_by_guid.return_value = {"elementHeader": {"guid": read_back_guid, "type": {"typeName": "SurveyReport"}},
                                            "properties": {"qualifiedName": "q"}}
    real = mod.EgeriaFileSystemSurveyor

    class Fake(real):
        def __init__(self, **kw):
            super().__init__()
            self.connect = lambda: None
            self._asset_maker = maker

        def _create_annotations(self, anns, report_guid, slug, at):
            n = len(anns) if landed is None else min(landed, len(anns))
            return [f"g{i}" for i in range(n)] + [""] * (len(anns) - n)

    monkeypatch.setattr(mod, "EgeriaFileSystemSurveyor", Fake)
    return maker


def _re_led_walk(client, headers, registry, tmp_path):
    root = _tree(tmp_path)
    _register(registry, root)
    registry.record_reachability_check(
        "walkme", probed_at="2026-10-20T10:00:00+00:00", outcome="re_only",
        error_detail=json.dumps({"path": "/x/walkme"}))
    r = client.post("/api/filesystems/walkme/folder-survey", json={}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.usefixtures("signed_in_caller")
def test_http_publish_reports_n_of_m_from_what_landed_and_reads_the_report_back(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _re_led_walk(client, headers, registry, tmp_path)
    maker = _fake_surveyor(monkeypatch, landed=None)
    r = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    built = body["annotation_count"]
    assert body["published"] is True and body["annotations_in_egeria"] == built
    assert body["landed_words"] == f"{built} of {built} annotations in Egeria"
    maker.get_asset_by_guid.assert_called_once()                          # the SurveyReport was read back
    view = client.get("/api/filesystems/walkme/folder-survey", headers=headers).json()
    assert view["published"] is True and view["publish"]["landed_words"] == f"{built} of {built} annotations in Egeria"


@pytest.mark.usefixtures("signed_in_caller")
def test_http_publish_with_some_annotations_missing_says_how_many_landed(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _re_led_walk(client, headers, registry, tmp_path)
    _fake_surveyor(monkeypatch, landed=2)
    body = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert body["annotations_in_egeria"] == 2 and body["annotation_count"] > 2
    assert body["landed_words"] == f"2 of {body['annotation_count']} annotations in Egeria"
    assert body["published"] is True


@pytest.mark.usefixtures("signed_in_caller")
def test_http_publish_with_zero_landed_reads_not_published_and_says_why(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _re_led_walk(client, headers, registry, tmp_path)
    _fake_surveyor(monkeypatch, landed=0)
    body = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert body["published"] is False and body["not_published_why"] == "no annotation landed in Egeria"
    view = client.get("/api/filesystems/walkme/folder-survey", headers=headers).json()
    assert view["published"] is False
    assert view["publish"]["not_published_why"] == "no annotation landed in Egeria"


@pytest.mark.usefixtures("signed_in_caller")
def test_http_publish_whose_report_does_not_read_back_is_not_published(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _re_led_walk(client, headers, registry, tmp_path)
    _fake_surveyor(monkeypatch, landed=None, read_back_guid="some-other-element")
    body = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert body["published"] is False and "different element" in body["not_published_why"]


@pytest.mark.usefixtures("signed_in_caller")
def test_http_publish_refused_until_the_probe_proves_a_lead(api, registry, tmp_path, monkeypatch):
    client, headers = api
    root = _tree(tmp_path)
    _register(registry, root)
    registry.record_reachability_check("walkme", probed_at="2026-10-20T10:00:00+00:00", outcome="re_only",
                                       error_detail=json.dumps({"path": "/x/walkme"}))
    assert client.post("/api/filesystems/walkme/folder-survey", json={}, headers=headers).status_code == 200
    registry.record_reachability_check("walkme", probed_at="2026-10-20T11:00:00+00:00", outcome="expected_reachable",
                                       error_detail=json.dumps({"path": "/x/walkme", "prefix": "/x"}))
    _fake_surveyor(monkeypatch, landed=None)
    body = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert body["published"] is False and "Folder Survey leads" in body["publish_refused"]


# ── D. the Egeria-led report's detail lines; noise directories counted and named ───────────────────────────────────


def test_d_the_egeria_led_report_states_the_csv_and_the_analysis_level(registry, tmp_path):
    from resource_explorer import native_survey_run as nsr
    from resource_explorer.folder_survey_view import folder_survey_view
    from tests.test_files2_reach_fork_folder_survey import _Port, _ann, _fs, _row

    _fs(registry, tmp_path)
    _row(registry, "expected_reachable", prefix="/deployments/data-lake")
    port = _Port()
    nsr.submit_native_survey(registry, port, "filesystem", "lake", nsr.FOLDER_SURVEY_QN,
                             technology_type="File System Directory", submitted_by="dan")
    port.status = ("COMPLETED", "done")
    port.reports["ea-folder-9"] = "report-9"
    port.originators["report-9"] = "ea-folder-9"
    port.annotations["report-9"] = [_ann(1, "Capture File Counts", resource_properties={"Number of files": "3"})]
    run = registry.list_native_survey_runs("filesystem", "lake", nsr.FOLDER_SURVEY_QN)[0]
    nsr.refresh_run(registry, port, run, entity_type="filesystem", slug="lake")
    v = folder_survey_view(registry, "lake")
    assert v["surveyor"] == "Egeria's Folder Survey"
    assert "analysis level: top level only" in v["detail_lines"]
    assert ("Egeria writes a file-name CSV inside its container on every Egeria-led survey; "
            "Resource Explorer never reads, stores or shows it.") in v["detail_lines"]
    assert v["reach"]["state"] == "egeria" and "verified" in v["reach"]["sentence"]


def test_d_skipped_noise_directories_are_counted_and_named(registry, tmp_path):
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import not_walked_words

    root = _tree(tmp_path)
    for name in (".git", "node_modules", ".venv"):
        (root / name).mkdir()
        (root / name / "junk.txt").write_text("x")
    (root / "data" / "node_modules").mkdir()
    fs, sd = _walk(registry, root)
    lim = sd["walk_limits"]
    assert lim["not_walked_directories"] == 4 and lim["not_walked_names_found"] == [".git", ".venv", "node_modules"]
    assert not_walked_words(lim) == "4 directories not walked: .git, .venv, node_modules"
    assert not_walked_words({"not_walked_directories": 0}) == ""


def test_d_the_published_report_names_the_skipped_directories(registry, tmp_path, monkeypatch):
    root = _tree(tmp_path)
    (root / ".git").mkdir()
    fs, sd = _walk(registry, root)
    registry.record_reachability_check("walkme", probed_at="2026-10-20T10:00:00+00:00", outcome="re_only",
                                       error_detail=json.dumps({"path": "/x/walkme"}))
    sd["surveyed_by"] = {"surveyor": "Resource Explorer",
                         "reach": file_reach.fork_for(registry, fs).as_dict()}
    maker = _fake_surveyor(monkeypatch, landed=None)
    from resource_explorer.surveyors.filesystem import egeria_filesystem_surveyor as mod

    mod.EgeriaFileSystemSurveyor().publish_step_annotations(fs, sd, registry)
    props = maker.create_asset.call_args.kwargs["body"]["properties"]
    assert "1 directory not walked: .git" in props["description"]
    assert props["additionalProperties"]["notWalked"] == "1 directory not walked: .git"


# ── E. LOW items ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_e_broken_symlinks_are_counted_and_listed(registry, tmp_path):
    root = _tree(tmp_path)
    os.symlink(tmp_path / "gone", root / "dangling")
    fs, sd = _walk(registry, root)
    assert sd["broken_symlink_count"] == 1 and sd["broken_symlinks"] == ["dangling"]
    assert sd["walk_limits"]["broken_symlink_count"] == 1
    assert "dangling" not in [f["file_path"] for f in sd["inventory"]]


def test_e_a_request_for_action_keeps_its_additional_properties():
    from resource_explorer.surveyors.annotation_props import build_annotation_props
    from resource_explorer.surveyors.survey_report import RequestForActionAnnotation

    ann = RequestForActionAnnotation(check_name="c", analysis_step="s", annotation_type_name="Inaccessible files",
                                     summary="x", explanation="y", action_requested="look",
                                     additional_properties={"surveyor": "Resource Explorer", "walkCapped": "true"},
                                     json_properties={"count": 2})
    props = build_annotation_props(ann, "Annotation::x")
    assert props["additionalProperties"]["surveyor"] == "Resource Explorer"
    assert props["additionalProperties"]["walkCapped"] == "true"

