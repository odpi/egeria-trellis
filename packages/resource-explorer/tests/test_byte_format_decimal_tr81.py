"""TR-81: one byte count, one unit. The inventory headline and the survey card's price line both said the same
60,209,097 bytes, one as "57.4 MB" (1024-based) and one as "60.2 MB" (decimal). Every size labelled KB/MB/GB is decimal,
through `resource_explorer.byte_format.format_bytes`."""
from __future__ import annotations

import pytest

from resource_explorer import byte_format, catalogue_scope, quality_batch
from resource_explorer.open_data import profile as open_profile
from resource_explorer.surveyors.database import survey_definition_adapter as db_adapter
from resource_explorer.surveyors.filesystem import survey_definition_adapter as fs_adapter
from resource_explorer.surveyors.filesystem import walk_derived, walk_extensions
from resource_explorer.surveyors.sub_surveyors import data_profiler, file_size

LOADING_BAY_BYTES = 60_209_097


def test_the_headline_and_the_card_price_line_say_the_same_size(monkeypatch):
    monkeypatch.setattr(fs_adapter, "_egeria_led_inventory", lambda registry, slug: {
        "file_count": 151, "directory_count": 24, "total_size_bytes": LOADING_BAY_BYTES, "report_sentence": "surveyed"})
    headline = fs_adapter._filesystem_inventory_headline(None, "loading_bay")["label"]
    assert headline.startswith("151 files in 24 folders · 60.2 MB · ")
    assert walk_derived._fmt_bytes(LOADING_BAY_BYTES) == "60.2 MB"
    assert walk_extensions._fmt_bytes(LOADING_BAY_BYTES) == "60.2 MB"


def test_the_original_report_8_4_gb_is_not_7_8_gb():
    assert fs_adapter._fmt_size(8_400_000_000) == walk_derived._fmt_bytes(8_400_000_000) == "8.4 GB"


@pytest.mark.parametrize("fmt", [
    byte_format.format_bytes, fs_adapter._fmt_size, walk_derived._fmt_bytes, catalogue_scope._fmt_bytes,
    data_profiler._fmt_size, file_size._fmt_size, db_adapter._format_bytes, quality_batch._bytes_words,
    open_profile._mb])
def test_every_size_formatter_is_decimal(fmt):
    assert fmt(LOADING_BAY_BYTES).replace("bytes", "B") == "60.2 MB"
    assert fmt(1_500) == "1.5 KB"
    assert fmt(1_048_576) == "1.0 MB"      # not "1.0 MiB"-in-disguise: 1,048,576 bytes is 1.0 MB decimal
    assert "1.1" not in fmt(1_048_576)


def test_the_boundaries_move_up_a_unit_rather_than_show_1000():
    f = byte_format.format_bytes
    assert [f(0), f(999), f(1000), f(999_949), f(999_950)] == ["0 B", "999 B", "1.0 KB", "999.9 KB", "1.0 MB"]
    assert f(2_500_000_000_000) == "2.5 TB"
    assert f(5, bytes_word="bytes") == "5 bytes"


# ── fix-up 1: the sites that divided by 1024 under an MB label ───────────────────────────────────────────────────────

def test_reindex_says_the_space_it_needs_in_decimal_words():
    from resource_explorer import reindex
    assert "needs about 60.2 MB more" in reindex.needs_words(LOADING_BAY_BYTES)


def _registry_with_repo_size(tmp_path, kb):
    from resource_explorer.registry import Project, ProjectRegistry
    registry = ProjectRegistry(db_path=str(tmp_path / "r.db"))
    registry.add(Project(slug="demo", display_name="Demo", github_url="https://github.com/demo/demo"))
    with registry._conn() as conn:
        conn.execute("INSERT INTO project_stats (project_slug, fetched_at, stars, forks, repo_size_kb, deployments_count) "
                     "VALUES (?, ?, 5, 1, ?, 0)", ("demo", "2026-11-01T00:00:00", kb))
    return registry


# GitHub reports a repository's size in KiB: 58,800 KiB is 60,211,200 bytes, "60.2 MB" in the tile and in chat alike.
def test_chat_and_the_repo_header_give_a_repository_the_same_size(tmp_path, monkeypatch):
    from resource_explorer.agents import stats_agent, tools
    registry = _registry_with_repo_size(tmp_path, 58_800)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: registry)
    assert "Size: 60.2 MB" in tools.query_project_stats_raw("demo")
    monkeypatch.setattr(stats_agent, "ProjectRegistry", lambda *a, **k: registry)
    assert "Size:               60.2 MB" in stats_agent.StatsAgent._fetch_stats(object.__new__(stats_agent.StatsAgent), "demo")
