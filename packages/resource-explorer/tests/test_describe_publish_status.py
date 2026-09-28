"""`describe_publish_status` — `is_published` must honour linkage staleness,
not just GUID presence.

Found live 2026-09-26: `coco_pharma`'s Egeria linkage had been recorded
`stale` (a platform reset — Egeria's repository 404s on the cached asset
GUID) since 2026-09-22, yet `databases.py`'s `is_published =
bool(egeria_asset_guid)` kept reporting it published throughout, because
nothing checked `registry.get_egeria_linkage(...)`, which already knew
better (repos already detect and record exactly this — `egeria_linkage.py`'s
`note_divergence`/`mark_egeria_linkage_stale` — but only repo's detail page
consulted it, via a separate best-effort `scouting-overview` fetch; the
list/summary rows for all three resource types never did).

Pulled forward from slice 18 (identity/read-back) per the coordinator's
ruling on this Slice-12 finding.
"""
from __future__ import annotations

import pytest

from resource_explorer.egeria_linkage import describe_publish_status
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="myproj", display_name="My Proj",
                    github_url="https://github.com/o/myproj", description=""))
    return reg


class TestNoGuidAtAll:
    def test_never_published_and_no_note(self, registry):
        result = describe_publish_status(registry, "repo", "myproj", "")
        assert result == {"is_published": False, "note": ""}


class TestGuidPresentAndHealthy:
    def test_published_with_no_linkage_row_at_all(self, registry):
        """No `egeria_linkage_status` row means healthy — absence is the
        default, matching `clear_egeria_linkage_status`'s own convention."""
        result = describe_publish_status(registry, "repo", "myproj", "guid-123")
        assert result == {"is_published": True, "note": ""}


class TestGuidPresentButLinkageStale:
    def test_not_published_and_note_names_the_date(self, registry):
        registry.mark_egeria_linkage_stale(
            "repo", "myproj", "guid-123",
            "OMRS-REPOSITORY-404-002 entity not known", )
        # Force a known detected_at/last_checked_at (equal — never rechecked
        # since first detection) so the date assertion is exact.
        with registry._conn() as conn:
            conn.execute(
                "UPDATE egeria_linkage_status SET detected_at = ?, last_checked_at = ? "
                "WHERE entity_type='repo' AND entity_slug='myproj'",
                ("2026-09-22T00:30:50.963974+00:00", "2026-09-22T00:30:50.963974+00:00"),
            )

        result = describe_publish_status(registry, "repo", "myproj", "guid-123")

        assert result["is_published"] is False
        assert result["note"] == (
            "published to Egeria · link stale since 2026-09-22 — element not found"
        )

    def test_note_names_both_dates_when_rechecked_after_first_detection(self, registry):
        registry.mark_egeria_linkage_stale("repo", "myproj", "guid-123", "gone")
        with registry._conn() as conn:
            conn.execute(
                "UPDATE egeria_linkage_status SET detected_at = ?, last_checked_at = ? "
                "WHERE entity_type='repo' AND entity_slug='myproj'",
                ("2026-09-22T00:30:50+00:00", "2026-09-26T12:00:00+00:00"),
            )

        result = describe_publish_status(registry, "repo", "myproj", "guid-123")

        assert result["note"] == (
            "published to Egeria · link stale since 2026-09-22 "
            "· last checked 2026-09-26 — element not found"
        )

    def test_note_is_empty_when_healthy_again(self, registry):
        """A stale linkage that was subsequently cleared (republish/resolve)
        must not keep showing the note — `clear_egeria_linkage_status`
        deletes the row, which is exactly the "no row" case."""
        registry.mark_egeria_linkage_stale("repo", "myproj", "guid-123", "gone")
        registry.clear_egeria_linkage_status("repo", "myproj")

        result = describe_publish_status(registry, "repo", "myproj", "guid-123")

        assert result == {"is_published": True, "note": ""}

    def test_a_different_entity_types_stale_linkage_does_not_leak(self, registry):
        """The linkage table is keyed on (entity_type, entity_slug) — a
        database's staleness must not affect a same-slugged repo's status."""
        registry.mark_egeria_linkage_stale("database", "myproj", "guid-999", "gone")

        result = describe_publish_status(registry, "repo", "myproj", "guid-123")

        assert result == {"is_published": True, "note": ""}


class TestRepeatedDetectionPreservesFirstDetectedAt:
    """`mark_egeria_linkage_stale`'s own regression pin — the bug that
    prompted (c)/(d) above: `recheck_all_linkages` re-confirming an
    already-stale link must not move `detected_at` forward, or "stale
    since" silently becomes "stale since the last time anyone checked."""

    def test_two_calls_keep_detected_at_and_advance_last_checked_at(self, registry):
        registry.mark_egeria_linkage_stale("repo", "myproj", "guid-123", "first detection")
        first = registry.get_egeria_linkage("repo", "myproj")

        registry.mark_egeria_linkage_stale("repo", "myproj", "guid-123", "recheck confirms still gone")
        second = registry.get_egeria_linkage("repo", "myproj")

        assert second["detected_at"] == first["detected_at"]
        assert second["last_checked_at"] >= first["last_checked_at"]
        assert second["detail"] == "recheck confirms still gone"

    def test_a_fresh_detection_after_recovery_gets_a_new_detected_at(self, registry):
        """Recovery (`clear_egeria_linkage_status`) deletes the row, so a
        LATER divergence is a genuinely new first detection, not a
        continuation of the old one."""
        registry.mark_egeria_linkage_stale("repo", "myproj", "guid-123", "first outage")
        first = registry.get_egeria_linkage("repo", "myproj")
        registry.clear_egeria_linkage_status("repo", "myproj")

        registry.mark_egeria_linkage_stale("repo", "myproj", "guid-456", "second outage")
        second = registry.get_egeria_linkage("repo", "myproj")

        assert second["detected_at"] != first["detected_at"]
        assert second["detected_at"] == second["last_checked_at"]
