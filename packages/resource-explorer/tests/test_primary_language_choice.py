"""The LANGUAGE tile and the Egeria publish make ONE choice of primary language (architect ruling 2026-10-23).

A measured value (RE's line census, by source files) beats GitHub's fetched by-bytes value when both exist, and
whatever is shown names its source. Live case: data_prep_kit read "HTML" from GitHub while the census measured
Python in 839 of 983 source files.
"""
from __future__ import annotations

from unittest.mock import create_autospec

import pytest
from fastapi.testclient import TestClient

from resource_explorer.primary_language import (
    GITHUB_SOURCE_ID, MEASURED_SOURCE_ID, choose_primary_language_from, publish_properties,
)
from resource_explorer.registry import Project, ProjectRegistry

CENSUS = {
    "surveyed_at": "2026-10-20T08:00:00",
    "detail": {"by_language": {
        "Python": {"files": 839, "code": 120000, "text_only": False},
        "HTML": {"files": 100, "code": 9000, "text_only": False},
        "Shell": {"files": 44, "code": 800, "text_only": False},
        # Text-only languages are not source files: never counted, never the answer.
        "Markdown": {"files": 5000, "code": 0, "text_only": True},
    }},
}
GITHUB = {"primary_language": "HTML", "fetched_at": "2026-10-19T07:00:00"}


class TestChooser:
    def test_classification_present_is_measured_with_its_basis(self):
        c = choose_primary_language_from(CENSUS, GITHUB)
        assert (c["value"], c["source"], c["chip"]) == ("Python", "measured", "measured")
        assert c["basis"] == "source files 839/983"
        assert c["source_id"] == MEASURED_SOURCE_ID
        assert c["hover"].startswith("measured · 839 of 983 source files · 2026-10-20 08:00")
        assert c["as_of"] == "2026-10-20T08:00:00"          # the census row's walk time is in the payload

    def test_a_census_with_no_walk_time_is_not_measured(self):
        """'measured' carries its as-of: a census row with no time falls back exactly as if it were absent."""
        timeless = {"detail": CENSUS["detail"]}
        c = choose_primary_language_from(timeless, GITHUB)
        assert c == choose_primary_language_from({}, GITHUB)
        assert c["source"] == "github" and "measured" not in c["hover"]
        assert choose_primary_language_from({"surveyed_at": "  ", "detail": CENSUS["detail"]}, {})["source"] == ""

    def test_absent_classification_falls_back_to_github(self):
        c = choose_primary_language_from({}, GITHUB)
        assert (c["value"], c["source"], c["chip"], c["basis"]) == ("HTML", "github", "GitHub", "bytes")
        assert c["source_id"] == GITHUB_SOURCE_ID
        assert c["hover"] == "GitHub · primary by bytes · fetched 2026-10-19"

    def test_a_census_of_only_text_files_is_not_a_measurement(self):
        only_text = {"surveyed_at": "x", "detail": {"by_language": {"Markdown": {"files": 9, "text_only": True}}}}
        assert choose_primary_language_from(only_text, GITHUB)["source"] == "github"

    def test_neither_is_not_measured_never_an_empty_measured(self):
        c = choose_primary_language_from({}, {})
        assert c["source"] == "" and c["value"] == "" and c["chip"] == ""
        assert publish_properties(c) == {"primary_language": "", "primary_language_source": "",
                                         "primary_language_basis": "", "primary_language_as_of": ""}

    def test_disagreement_line_only_when_both_exist_and_differ(self):
        both = choose_primary_language_from(CENSUS, GITHUB)
        assert both["disagreement"] == "GitHub says HTML (by bytes)"
        assert both["hover"].endswith("\nGitHub says HTML (by bytes)")
        agree = choose_primary_language_from(CENSUS, {"primary_language": "Python", "fetched_at": "t"})
        assert agree["disagreement"] == "" and "GitHub says" not in agree["hover"]
        assert choose_primary_language_from(CENSUS, {})["disagreement"] == ""
        assert choose_primary_language_from({}, GITHUB)["disagreement"] == ""

    def test_a_tie_does_not_depend_on_dict_order(self):
        a = {"surveyed_at": "t", "detail": {"by_language": {"Go": {"files": 3, "code": 10}, "Rust": {"files": 3, "code": 50}}}}
        b = {"surveyed_at": "t", "detail": {"by_language": {"Rust": {"files": 3, "code": 50}, "Go": {"files": 3, "code": 10}}}}
        assert choose_primary_language_from(a, {})["value"] == choose_primary_language_from(b, {})["value"] == "Rust"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "lang.db"))
    r.add(Project(slug="data_prep_kit", display_name="data-prep-kit",
                  github_url="https://github.com/data-prep-kit/data-prep-kit", description=""))
    with r._conn() as conn:
        conn.execute("INSERT INTO project_stats (project_slug, fetched_at, primary_language) VALUES (?, ?, ?)",
                     ("data_prep_kit", GITHUB["fetched_at"], GITHUB["primary_language"]))
    r.upsert_metric("data_prep_kit", "code_volume", {"code_lines": 129800}, detail=CENSUS["detail"],
                    surveyed_at=CENSUS["surveyed_at"])
    return r


class _Result:
    resource_slug = "data_prep_kit"
    project_display_name = "data-prep-kit"
    github_url = "https://github.com/data-prep-kit/data-prep-kit"


def test_publish_carries_the_tiles_choice_and_its_source(registry, monkeypatch):
    """Drive the real create path with an autospec'd AssetMaker: the asset's four additionalProperties are the
    chooser's, and its primary_language equals the value the scouting overview (the tile's payload) shows."""
    from pyegeria import AssetMaker

    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    maker = create_autospec(AssetMaker, instance=True, spec_set=True)
    maker.find_assets.return_value = []
    maker.find_software_capabilities.return_value = []
    maker.create_software_capability.return_value = "github-scl-guid"
    maker.create_asset.return_value = "new-asset-guid"
    pub = EgeriaPublisher.__new__(EgeriaPublisher)
    pub._asset_maker = maker
    pub._registry = registry
    pub.zone_names = None
    pub._zone_names = None

    assert pub._find_or_create_asset(_Result()) == "new-asset-guid"
    # The new-asset path is unchanged: the four properties ride on the create, nothing is read or updated after.
    maker.get_asset_by_guid.assert_not_called()
    maker.update_asset.assert_not_called()
    body = maker.create_asset.call_args.kwargs["body"]
    extra = body["properties"]["additionalProperties"]
    assert extra["primary_language"] == "Python"
    assert extra["primary_language_source"] == "resource-explorer:language_file_classification"
    assert extra["primary_language_basis"] == "source files 839/983"
    assert extra["primary_language_as_of"] == CENSUS["surveyed_at"]
    assert "as_of" not in extra                         # one prefix for all four keys (architect, 2026-10-23)

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app
    ov = TestClient(app).get("/api/projects/data_prep_kit/scouting-overview").json()
    assert ov["primary_language"] == extra["primary_language"]
    assert ov["language"]["source"] == "measured" and ov["language"]["chip"] == "measured"
    assert ov["language"]["disagreement"] == "GitHub says HTML (by bytes)"


# ── an EXISTING repository asset gets the choice on its next publish (fix round 1, 2026-10-23) ─────────────

QN = "GitHubRepository::https://github.com/data-prep-kit/data-prep-kit"
WANTED = {"primary_language": "Python", "primary_language_source": MEASURED_SOURCE_ID,
          "primary_language_basis": "source files 839/983",
          "primary_language_as_of": CENSUS["surveyed_at"]}


def _existing_publisher(registry, stored: dict):
    from pyegeria import AssetMaker

    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    registry.set_egeria_asset_guid("data_prep_kit", "asset-guid")
    maker = create_autospec(AssetMaker, instance=True, spec_set=True)
    maker.find_assets.return_value = [{"elementHeader": {"guid": "asset-guid"}, "properties": {"qualifiedName": QN}}]
    maker.get_asset_by_guid.return_value = {"elementHeader": {"guid": "asset-guid"},
                                            "properties": {"qualifiedName": QN, "additionalProperties": stored}}
    pub = EgeriaPublisher.__new__(EgeriaPublisher)
    pub._asset_maker = maker
    pub._registry = registry
    pub.zone_names = None
    pub._zone_names = None
    return pub, maker


def _language_rows(registry):
    return [e for e in registry.list_activity(entity_slug="data_prep_kit", operation="catalog", limit=50)
            if e.get("summary") == "primary_language updated"]


def test_existing_asset_with_stale_values_gets_one_update_and_a_proof_row(registry):
    stale = {"github_url": "https://github.com/data-prep-kit/data-prep-kit", "license": "Apache-2.0",
             "primary_language": "HTML"}
    pub, maker = _existing_publisher(registry, stale)
    assert pub._find_or_create_asset(_Result()) == "asset-guid"
    maker.create_asset.assert_not_called()
    maker.get_asset_by_guid.assert_called_once()
    maker.update_asset.assert_called_once()
    guid, = maker.update_asset.call_args.args
    body = maker.update_asset.call_args.kwargs["body"]
    assert guid == "asset-guid" and body["mergeUpdate"] is True
    extra = body["properties"]["additionalProperties"]
    assert {k: extra[k] for k in WANTED} == WANTED
    assert extra["license"] == "Apache-2.0" and extra["github_url"] == stale["github_url"]   # the rest is kept
    rows = _language_rows(registry)
    assert len(rows) == 1 and rows[0]["status"] == "success"
    assert '"primary_language": "HTML"' in rows[0]["detail"] and '"primary_language": "Python"' in rows[0]["detail"]


def test_existing_asset_with_identical_values_is_not_written(registry):
    pub, maker = _existing_publisher(registry, {"github_url": "x", **WANTED})
    assert pub._find_or_create_asset(_Result()) == "asset-guid"
    maker.get_asset_by_guid.assert_called_once()
    maker.update_asset.assert_not_called()
    assert _language_rows(registry) == []


def test_existing_asset_is_never_blanked_when_neither_source_exists(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "none.db"))
    r.add(Project(slug="data_prep_kit", display_name="data-prep-kit",
                  github_url="https://github.com/data-prep-kit/data-prep-kit", description=""))
    pub, maker = _existing_publisher(r, {"primary_language": "Go"})
    assert pub._find_or_create_asset(_Result()) == "asset-guid"
    maker.update_asset.assert_not_called()
    assert _language_rows(r) == []


# ── fix round 2 (2026-10-23): reads that cannot be trusted never write; "" never overwrites ──────────────────


def test_a_read_that_raises_writes_nothing(registry):
    pub, maker = _existing_publisher(registry, {"primary_language": "HTML"})
    maker.get_asset_by_guid.side_effect = RuntimeError("Egeria timed out")
    assert pub._find_or_create_asset(_Result()) == "asset-guid"
    maker.update_asset.assert_not_called()
    assert _language_rows(registry) == []


@pytest.mark.parametrize("answer", [
    {"elementHeader": {"guid": "asset-guid"}, "properties": None},                       # properties not read
    {"elementHeader": {"guid": "asset-guid"}},                                           # no properties at all
    {"elementHeader": {"guid": "asset-guid"}, "properties": {"qualifiedName": "SomethingElse::x"}},
    {"elementHeader": {"guid": "asset-guid"}, "properties": {"qualifiedName": QN, "additionalProperties": "a=b"}},
    "not a dict",
])
def test_an_unreadable_shape_writes_nothing(registry, answer):
    pub, maker = _existing_publisher(registry, {})
    maker.get_asset_by_guid.return_value = answer
    assert pub._find_or_create_asset(_Result()) == "asset-guid"
    maker.update_asset.assert_not_called()
    assert _language_rows(registry) == []


def test_this_asset_read_with_no_additional_properties_gets_the_four_keys_only(registry):
    pub, maker = _existing_publisher(registry, {})
    maker.get_asset_by_guid.return_value = {"class": "AssetElement", "elementHeader": {"guid": "asset-guid"},
                                            "properties": {"qualifiedName": QN}}
    assert pub._find_or_create_asset(_Result()) == "asset-guid"
    maker.update_asset.assert_called_once()
    assert maker.update_asset.call_args.kwargs["body"]["properties"]["additionalProperties"] == WANTED


def test_a_stored_bare_as_of_reads_as_absent(registry):
    """A bare `as_of` from earlier branch commits is not one of the four keys: the new key is written, the old
    one is left as stored."""
    stored = {k: v for k, v in WANTED.items() if k != "primary_language_as_of"}
    pub, maker = _existing_publisher(registry, {**stored, "as_of": CENSUS["surveyed_at"]})
    assert pub._find_or_create_asset(_Result()) == "asset-guid"
    maker.update_asset.assert_called_once()
    extra = maker.update_asset.call_args.kwargs["body"]["properties"]["additionalProperties"]
    assert extra["primary_language_as_of"] == CENSUS["surveyed_at"]


def test_an_empty_wanted_value_keeps_the_stored_one(tmp_path):
    """GitHub's choice with no fetched_at: primary_language_as_of is "", so the stored one stays."""
    r = ProjectRegistry(db_path=str(tmp_path / "gh.db"))
    r.add(Project(slug="data_prep_kit", display_name="data-prep-kit",
                  github_url="https://github.com/data-prep-kit/data-prep-kit", description=""))
    with r._conn() as conn:
        conn.execute("INSERT INTO project_stats (project_slug, fetched_at, primary_language) VALUES (?, ?, ?)",
                     ("data_prep_kit", "", "HTML"))
    pub, maker = _existing_publisher(r, {"primary_language": "Go", "primary_language_as_of": "2026-01-01T00:00:00",
                                         "license": "MIT"})
    assert pub._find_or_create_asset(_Result()) == "asset-guid"
    maker.update_asset.assert_called_once()
    extra = maker.update_asset.call_args.kwargs["body"]["properties"]["additionalProperties"]
    assert extra["primary_language"] == "HTML" and extra["primary_language_source"] == GITHUB_SOURCE_ID
    assert extra["primary_language_as_of"] == "2026-01-01T00:00:00" and extra["license"] == "MIT"


def test_a_failing_census_read_never_fails_the_create_and_says_github(registry, monkeypatch):
    from pyegeria import AssetMaker

    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    def boom(slug, kind, scope_locator=""):
        raise RuntimeError("metrics database is down")
    monkeypatch.setattr(registry, "query_metrics", boom)
    maker = create_autospec(AssetMaker, instance=True, spec_set=True)
    maker.find_assets.return_value = []
    maker.find_software_capabilities.return_value = []
    maker.create_software_capability.return_value = "github-scl-guid"
    maker.create_asset.return_value = "new-asset-guid"
    pub = EgeriaPublisher.__new__(EgeriaPublisher)
    pub._asset_maker = maker
    pub._registry = registry
    pub.zone_names = None
    pub._zone_names = None
    assert pub._find_or_create_asset(_Result()) == "new-asset-guid"
    extra = maker.create_asset.call_args.kwargs["body"]["properties"]["additionalProperties"]
    assert extra["primary_language"] == "HTML" and extra["primary_language_source"] == GITHUB_SOURCE_ID
    assert extra["primary_language_basis"] == "bytes"
