"""Tests for ProjectRegistry — SQLite CRUD, schema migration, stats."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from resource_explorer.registry import DatabaseEntity, Project, ProjectRegistry, ProjectStatus


@pytest.fixture
def db(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def sample_project():
    return Project(
        slug="test-project",
        display_name="Test Project",
        github_url="https://github.com/test/test-project",
        description="A test project",
    )


class TestCRUD:
    def test_add_and_get(self, db, sample_project):
        db.add(sample_project)
        result = db.get("test-project")
        assert result is not None
        assert result.slug == "test_project"
        assert result.display_name == "Test Project"

    def test_get_missing_returns_none(self, db):
        assert db.get("nonexistent") is None

    def test_exists(self, db, sample_project):
        assert not db.exists("test-project")
        db.add(sample_project)
        assert db.exists("test-project")

    def test_list_all_empty(self, db):
        assert db.list_all() == []

    def test_list_all_ordered_by_display_name(self, db):
        db.add(Project(slug="z", display_name="Zebra", github_url="https://github.com/a/z"))
        db.add(Project(slug="a", display_name="Apple", github_url="https://github.com/a/a"))
        names = [p.display_name for p in db.list_all()]
        assert names == ["Apple", "Zebra"]

    def test_remove(self, db, sample_project):
        db.add(sample_project)
        db.remove("test-project")
        assert db.get("test-project") is None

    def test_remove_also_removes_stats(self, db, sample_project):
        db.add(sample_project)
        conn = sqlite3.connect(db.db_path)
        conn.execute(
            "INSERT INTO project_stats (project_slug, fetched_at, stars) VALUES (?, ?, ?)",
            ("test_project", "2024-01-01T00:00:00", 100),
        )
        conn.commit()
        conn.close()
        db.remove("test-project")
        conn = sqlite3.connect(db.db_path)
        row = conn.execute(
            "SELECT * FROM project_stats WHERE project_slug = ?", ("test_project",)
        ).fetchone()
        conn.close()
        assert row is None

    def test_remove_cleans_up_every_fk_child_table(self, db, sample_project):
        """remove() previously only cleaned up 5 of the 10 tables with a real
        FK to projects.slug — project_dependencies, project_file_type_counts,
        project_file_inventory, project_egeria_surveys, and
        project_data_profiles were silently left orphaned. Invisible on
        SQLite (no FK enforcement), a hard FK-violation crash on Postgres —
        found live during Phase 4 cutover testing. Also verifies delete
        ordering: children must go before the parent `projects` row, which
        Postgres enforces and SQLite (foreign_keys pragma off) does not."""
        db.add(sample_project)
        conn = sqlite3.connect(db.db_path)
        conn.execute(
            "INSERT INTO project_dependencies (project_slug, dep_name, dep_version, dep_type, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("test_project", "requests", "2.31.0", "pip", "2024-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO project_file_type_counts (project_slug, surveyed_at, type_label, file_count) "
            "VALUES (?, ?, ?, ?)",
            ("test_project", "2024-01-01T00:00:00", ".py", 10),
        )
        conn.execute(
            "INSERT INTO project_file_inventory (project_slug, file_path, indexed_at) "
            "VALUES (?, ?, ?)",
            ("test_project", "README.md", "2024-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        db.remove("test-project")

        conn = sqlite3.connect(db.db_path)
        for table in ("project_dependencies", "project_file_type_counts", "project_file_inventory"):
            row = conn.execute(f"SELECT * FROM {table} WHERE project_slug = ?", ("test_project",)).fetchone()
            assert row is None, f"{table} row survived remove()"
        conn.close()


class TestStatusUpdates:
    def test_update_status(self, db, sample_project):
        db.add(sample_project)
        db.update_status("test-project", ProjectStatus.INDEXING)
        assert db.get("test-project").status == ProjectStatus.INDEXING

    def test_update_status_with_error(self, db, sample_project):
        db.add(sample_project)
        db.update_status("test-project", ProjectStatus.ERROR, "connection failed")
        p = db.get("test-project")
        assert p.status == ProjectStatus.ERROR
        assert p.error_message == "connection failed"

    def test_update_indexed_at(self, db, sample_project):
        db.add(sample_project)
        db.update_indexed_at("test-project", ["test-project_python_code"])
        p = db.get("test-project")
        assert "test-project_python_code" in p.collections
        assert p.last_indexed_at != ""

    def test_update_commit_sha(self, db, sample_project):
        db.add(sample_project)
        db.update_commit_sha("test-project", "abc123def456")
        assert db.get("test-project").last_commit_sha == "abc123def456"

    def test_update_project_surveyed_at(self, db, sample_project):
        db.add(sample_project)
        assert db.get("test-project").last_surveyed_at == ""
        db.update_project_surveyed_at("test-project")
        assert db.get("test-project").last_surveyed_at != ""

    def test_update_project_profiled_at(self, db, sample_project):
        db.add(sample_project)
        assert db.get("test-project").last_profiled_at == ""
        db.update_project_profiled_at("test-project")
        assert db.get("test-project").last_profiled_at != ""


class TestSchemaMigration:
    def test_migration_adds_last_commit_sha_to_old_db(self, tmp_path):
        db_path = str(tmp_path / "old.db")
        # Create a database without last_commit_sha (simulating pre-migration schema)
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE projects (
                slug TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                github_url TEXT NOT NULL, description TEXT DEFAULT '',
                homepage_url TEXT DEFAULT '', docs_url TEXT DEFAULT '',
                github_token_encrypted TEXT DEFAULT '', collections TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active', last_indexed_at TEXT DEFAULT '',
                last_stats_fetched_at TEXT DEFAULT '', created_at TEXT NOT NULL,
                error_message TEXT DEFAULT ''
            )
        """)
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old", "Old", "https://github.com/a/b", "", "", "", "[]", "[]",
             "active", "", "", "2024-01-01T00:00:00", ""),
        )
        conn.commit()
        conn.close()

        # Opening with ProjectRegistry should apply the migration
        db = ProjectRegistry(db_path=db_path)
        p = db.get("old")
        assert p is not None
        assert p.last_commit_sha == ""  # default applied by migration


class TestCurateTags:
    def test_add_and_list_tags(self, db):
        db.add_resource_tag("repo", "myproj", "gold-tier")
        db.add_resource_tag("repo", "myproj", "customer-facing")
        assert db.list_resource_tags("repo", "myproj") == ["customer-facing", "gold-tier"]

    def test_duplicate_tag_is_idempotent(self, db):
        db.add_resource_tag("repo", "myproj", "gold-tier")
        db.add_resource_tag("repo", "myproj", "gold-tier")
        assert db.list_resource_tags("repo", "myproj") == ["gold-tier"]

    def test_remove_tag(self, db):
        db.add_resource_tag("repo", "myproj", "gold-tier")
        db.remove_resource_tag("repo", "myproj", "gold-tier")
        assert db.list_resource_tags("repo", "myproj") == []

    def test_remove_nonexistent_tag_is_a_noop(self, db):
        db.remove_resource_tag("repo", "myproj", "nonexistent")  # should not raise

    def test_list_all_tags_with_counts(self, db):
        db.add_resource_tag("repo", "proj-a", "gold-tier")
        db.add_resource_tag("repo", "proj-b", "gold-tier")
        db.add_resource_tag("database", "db-a", "pii")
        tags = {t["tag"]: t["count"] for t in db.list_all_tags()}
        assert tags == {"gold-tier": 2, "pii": 1}

    def test_list_resources_by_tag(self, db):
        db.add_resource_tag("repo", "proj-a", "gold-tier")
        db.add_resource_tag("database", "db-a", "gold-tier")
        resources = db.list_resources_by_tag("gold-tier")
        assert {"entity_type": "repo", "entity_slug": "proj-a"} in resources
        assert {"entity_type": "database", "entity_slug": "db-a"} in resources

    def test_tags_scoped_per_resource(self, db):
        db.add_resource_tag("repo", "proj-a", "gold-tier")
        assert db.list_resource_tags("repo", "proj-b") == []


class TestCurateFeedback:
    def test_add_and_list_feedback(self, db):
        entry = db.add_resource_feedback("repo", "myproj", 4, "quality", "Schema looks stale")
        assert entry["rating"] == 4
        assert entry["category"] == "quality"
        listed = db.list_resource_feedback("repo", "myproj")
        assert len(listed) == 1
        assert listed[0]["message"] == "Schema looks stale"

    def test_feedback_without_rating(self, db):
        entry = db.add_resource_feedback("repo", "myproj", None, "", "Just a note")
        assert entry["rating"] is None

    def test_feedback_ordered_newest_first(self, db):
        db.add_resource_feedback("repo", "myproj", None, "", "first")
        db.add_resource_feedback("repo", "myproj", None, "", "second")
        listed = db.list_resource_feedback("repo", "myproj")
        assert listed[0]["message"] == "second"

    def test_feedback_scoped_per_resource(self, db):
        db.add_resource_feedback("repo", "proj-a", None, "", "a's feedback")
        assert db.list_resource_feedback("repo", "proj-b") == []


class TestCuratorNotes:
    def test_add_and_list_notes(self, db):
        entry = db.add_curator_note("repo", "myproj", "Needs a better README before promoting")
        assert entry["note"] == "Needs a better README before promoting"
        listed = db.list_curator_notes("repo", "myproj")
        assert len(listed) == 1

    def test_notes_ordered_newest_first(self, db):
        db.add_curator_note("repo", "myproj", "first")
        db.add_curator_note("repo", "myproj", "second")
        listed = db.list_curator_notes("repo", "myproj")
        assert listed[0]["note"] == "second"

    def test_delete_note(self, db):
        entry = db.add_curator_note("repo", "myproj", "temp note")
        assert db.delete_curator_note(entry["id"]) is True
        assert db.list_curator_notes("repo", "myproj") == []

    def test_delete_nonexistent_note_returns_false(self, db):
        assert db.delete_curator_note("nonexistent-id") is False

    def test_notes_scoped_per_resource(self, db):
        db.add_curator_note("repo", "proj-a", "a's note")
        assert db.list_curator_notes("repo", "proj-b") == []


class TestArchitectureComponentVerdicts:
    def test_record_and_get_latest_verdict(self, db):
        entry = db.record_component_verdict("repo", "myproj", "src/foo", "accepted")
        assert entry["verdict"] == "accepted"
        latest = db.get_component_verdicts("repo", "myproj")
        assert latest["src/foo"]["verdict"] == "accepted"

    def test_retyped_carries_retyped_to(self, db):
        db.record_component_verdict("repo", "myproj", "src/foo", "retyped", retyped_to="library")
        latest = db.get_component_verdicts("repo", "myproj")
        assert latest["src/foo"]["retyped_to"] == "library"

    def test_invalid_verdict_raises(self, db):
        with pytest.raises(ValueError):
            db.record_component_verdict("repo", "myproj", "src/foo", "maybe")

    def test_a_new_verdict_is_appended_not_overwritten(self, db):
        """Append-only, per record_component_verdict's own docstring — a
        curator's later verdict is history, not a correction, same as every
        other findings-shaped table here."""
        db.record_component_verdict("repo", "myproj", "src/foo", "accepted")
        db.record_component_verdict("repo", "myproj", "src/foo", "rejected", note="changed my mind")
        latest = db.get_component_verdicts("repo", "myproj")
        assert latest["src/foo"]["verdict"] == "rejected"
        history = db.list_component_verdict_history("repo", "myproj", "src/foo")
        assert [h["verdict"] for h in history] == ["rejected", "accepted"]  # newest first

    def test_verdicts_scoped_per_component(self, db):
        db.record_component_verdict("repo", "myproj", "src/foo", "accepted")
        latest = db.get_component_verdicts("repo", "myproj")
        assert "src/bar" not in latest

    def test_verdicts_scoped_per_resource(self, db):
        db.record_component_verdict("repo", "proj-a", "src/foo", "accepted")
        assert db.get_component_verdicts("repo", "proj-b") == {}

    def test_history_empty_for_a_never_ruled_on_component(self, db):
        assert db.list_component_verdict_history("repo", "myproj", "src/never-ruled") == []


class TestArchitectureMaterializedComponents:
    """Unlike verdicts, this table has one current row per scope, not an
    append-only history — get_materialized_component's docstring explains
    why: "does this exist in Egeria" is a fact with one current answer."""

    def test_nothing_materialized_reports_none(self, db):
        assert db.get_materialized_component("repo", "myproj", "src/foo") is None

    def test_record_and_read_back(self, db):
        entry = db.record_materialized_component(
            "repo", "myproj", "src/foo",
            "SolutionComponent::repo::myproj::src/foo", "guid-1",
        )
        assert entry["guid"] == "guid-1"
        row = db.get_materialized_component("repo", "myproj", "src/foo")
        assert row["guid"] == "guid-1"
        assert row["qualified_name"] == "SolutionComponent::repo::myproj::src/foo"

    def test_a_second_call_replaces_rather_than_appends(self, db):
        """Re-materializing (a stale GUID, or a corrected qualifiedName)
        must not leave two rows for the same scope — there is only ever one
        current answer to "does this exist"."""
        db.record_materialized_component("repo", "myproj", "src/foo", "qn-1", "guid-1")
        db.record_materialized_component("repo", "myproj", "src/foo", "qn-1", "guid-2")
        row = db.get_materialized_component("repo", "myproj", "src/foo")
        assert row["guid"] == "guid-2"

    def test_scoped_per_component(self, db):
        db.record_materialized_component("repo", "myproj", "src/foo", "qn-1", "guid-1")
        assert db.get_materialized_component("repo", "myproj", "src/bar") is None

    def test_scoped_per_resource(self, db):
        db.record_materialized_component("repo", "proj-a", "src/foo", "qn-1", "guid-1")
        assert db.get_materialized_component("repo", "proj-b", "src/foo") is None

    def test_bulk_read_returns_everything_for_the_resource(self, db):
        db.record_materialized_component("repo", "myproj", "src/foo", "qn-foo", "guid-foo")
        db.record_materialized_component("repo", "myproj", "src/bar", "qn-bar", "guid-bar")
        db.record_materialized_component("repo", "other", "src/foo", "qn-x", "guid-x")
        result = db.get_materialized_components("repo", "myproj")
        assert set(result) == {"src/foo", "src/bar"}
        assert result["src/foo"]["guid"] == "guid-foo"


class TestSchedules:
    def test_save_and_get_schedule(self, db):
        db.save_schedule("repo", "myproj", "security_scan", "weekly", True)
        rows = db.get_schedules("repo", "myproj")
        assert len(rows) == 1
        assert rows[0]["schedule"] == "weekly"
        assert rows[0]["enabled"] == 1
        assert rows[0]["next_run"]  # computed

    def test_save_schedule_upserts(self, db):
        db.save_schedule("repo", "myproj", "security_scan", "weekly", True)
        db.save_schedule("repo", "myproj", "security_scan", "daily", True)
        rows = db.get_schedules("repo", "myproj")
        assert len(rows) == 1
        assert rows[0]["schedule"] == "daily"

    def test_new_schedule_has_no_run_status_yet(self, db):
        db.save_schedule("repo", "myproj", "security_scan", "daily", True)
        rows = db.get_schedules("repo", "myproj")
        assert rows[0]["last_run_status"] == ""
        assert rows[0]["last_run_activity_id"] == ""

    def test_update_schedule_after_run_records_status_and_activity_id(self, db):
        db.save_schedule("repo", "myproj", "security_scan", "daily", True)
        db.update_schedule_after_run("repo", "myproj", "security_scan", status="ok", activity_id="act-123")
        rows = db.get_schedules("repo", "myproj")
        assert rows[0]["last_run_status"] == "ok"
        assert rows[0]["last_run_activity_id"] == "act-123"
        assert rows[0]["last_run"]  # timestamp recorded

    def test_update_schedule_after_run_records_error_status(self, db):
        db.save_schedule("repo", "myproj", "security_scan", "daily", True)
        db.update_schedule_after_run("repo", "myproj", "security_scan", status="error", activity_id="act-456")
        rows = db.get_schedules("repo", "myproj")
        assert rows[0]["last_run_status"] == "error"

    def test_update_schedule_after_run_advances_next_run(self, db):
        db.save_schedule("repo", "myproj", "security_scan", "daily", True)
        before = db.get_schedules("repo", "myproj")[0]["next_run"]
        db.update_schedule_after_run("repo", "myproj", "security_scan")
        after = db.get_schedules("repo", "myproj")[0]["next_run"]
        assert after != before  # advanced to a new next_run

    def test_update_schedule_after_run_missing_schedule_is_a_noop(self, db):
        db.update_schedule_after_run("repo", "nonexistent", "nonexistent-analysis")  # should not raise

    def test_list_all_schedules_across_resources(self, db):
        db.save_schedule("repo", "proj-a", "security_scan", "daily", True)
        db.save_schedule("database", "db-a", "schema_inventory", "weekly", True)
        rows = db.list_all_schedules()
        assert len(rows) == 2
        slugs = {r["entity_slug"] for r in rows}
        assert slugs == {"proj-a", "db-a"}

    def test_list_all_schedules_surfaces_errors_first(self, db):
        db.save_schedule("repo", "proj-ok", "security_scan", "daily", True)
        db.save_schedule("repo", "proj-err", "security_scan", "daily", True)
        db.update_schedule_after_run("repo", "proj-ok", "security_scan", status="ok")
        db.update_schedule_after_run("repo", "proj-err", "security_scan", status="error")
        rows = db.list_all_schedules()
        assert rows[0]["entity_slug"] == "proj-err"
        assert rows[0]["last_run_status"] == "error"

    def test_get_due_schedules_only_returns_enabled_and_past_due(self, db):
        db.save_schedule("repo", "proj-a", "security_scan", "manual", True)  # manual -> no next_run, never due
        db.save_schedule("repo", "proj-b", "security_scan", "daily", False)  # disabled
        assert db.get_due_schedules() == []

    def test_delete_schedule(self, db):
        db.save_schedule("repo", "myproj", "security_scan", "daily", True)
        assert db.delete_schedule("repo", "myproj", "security_scan") is True
        assert db.get_schedules("repo", "myproj") == []

    def test_delete_nonexistent_schedule_returns_false(self, db):
        assert db.delete_schedule("repo", "myproj", "nonexistent") is False


class TestAliases:
    """add_alias's project_aliases upsert was one of the two SQLite-only
    INSERT OR REPLACE statements converted to ON CONFLICT DO UPDATE for
    Postgres compatibility (migration plan Phase 3) — these specifically
    exercise both the insert and the update-on-conflict path."""

    def test_add_alias_then_resolve(self, db, sample_project):
        db.add(sample_project)
        db.add_alias("tp", "test-project")
        assert db.resolve_alias("tp") == "test_project"

    def test_resolve_missing_alias_returns_none(self, db):
        assert db.resolve_alias("ghost") is None

    def test_add_alias_normalizes_spaces_and_hyphens(self, db, sample_project):
        db.add(sample_project)
        db.add_alias("Test Project", "test-project")
        assert db.resolve_alias("test-project") == "test_project"
        assert db.resolve_alias("test project") == "test_project"

    def test_add_alias_conflict_updates_existing_row(self, db, sample_project):
        """The ON CONFLICT path: re-adding the same alias for a different
        project must update the mapping in place, not raise a PK violation."""
        db.add(sample_project)
        db.add(Project(slug="other-project", display_name="Other", github_url="https://github.com/a/other"))
        db.add_alias("tp", "test-project", confirmed_by="user")
        db.add_alias("tp", "other-project", confirmed_by="admin")
        assert db.resolve_alias("tp") == "other_project"
        aliases = db.list_aliases()
        assert len(aliases) == 1  # updated in place, not duplicated
        assert aliases[0]["confirmed_by"] == "admin"

    def test_remove_alias(self, db, sample_project):
        db.add(sample_project)
        db.add_alias("tp", "test-project")
        assert db.remove_alias("tp") is True
        assert db.resolve_alias("tp") is None

    def test_remove_nonexistent_alias_returns_false(self, db):
        assert db.remove_alias("ghost") is False

    def test_list_aliases_filtered_by_slug(self, db, sample_project):
        db.add(sample_project)
        db.add(Project(slug="other-project", display_name="Other", github_url="https://github.com/a/other"))
        db.add_alias("tp", "test-project")
        db.add_alias("op", "other-project")
        result = db.list_aliases(slug="test-project")
        assert [a["alias"] for a in result] == ["tp"]


class TestProjectGroups:
    """create_group's project_groups upsert is the other SQLite-only
    INSERT OR REPLACE converted to ON CONFLICT DO UPDATE for Postgres
    compatibility (migration plan Phase 3)."""

    def test_create_and_get_group(self, db):
        db.create_group("platform", "Platform Team", "Core platform repos")
        group = db.get_group("platform")
        assert group is not None
        assert group.display_name == "Platform Team"
        assert group.description == "Core platform repos"

    def test_get_missing_group_returns_none(self, db):
        assert db.get_group("ghost") is None

    def test_create_group_conflict_renames_in_place(self, db):
        """The ON CONFLICT path: re-creating the same slug must update the
        existing row (rename), not raise a PK violation or duplicate it."""
        db.create_group("platform", "Platform Team", "v1 description")
        db.create_group("platform", "Platform Team (renamed)", "v2 description")
        group = db.get_group("platform")
        assert group.display_name == "Platform Team (renamed)"
        assert group.description == "v2 description"
        assert len(db.list_groups()) == 1

    def test_list_groups_ordered_by_display_name(self, db):
        db.create_group("z", "Zebra Group")
        db.create_group("a", "Apple Group")
        names = [g.display_name for g in db.list_groups()]
        assert names == ["Apple Group", "Zebra Group"]

    def test_delete_group_ungroups_members(self, db, sample_project):
        db.create_group("platform", "Platform Team")
        db.add(sample_project)
        with db._conn() as conn:
            conn.execute(
                "UPDATE projects SET group_slug = ? WHERE slug = ?", ("platform", "test_project")
            )
        assert db.delete_group("platform") == 1
        assert db.get_group("platform") is None
        with db._conn() as conn:
            row = conn.execute(
                "SELECT group_slug FROM projects WHERE slug = ?", ("test_project",)
            ).fetchone()
        assert row["group_slug"] == ""


class TestCodeSymbolsAndRelationships:
    """AST-ownership-transfer plan Phase 3 — project_code_symbols' new fields
    (parent_class/return_type/is_private/is_async/complexity) and the new
    project_code_relationships table, both written by upsert_code_symbols()
    in a single call (mirroring Egeria Advisor's CodeSymbolStore.upsert_symbols())."""

    def _symbol(self, **overrides):
        from resource_explorer.ingestion.code_symbol_extractor import CodeSymbol
        defaults = dict(
            resource_slug="test-project", file_path="mod.py", language="python",
            kind="function", name="f", qualified_name="f", signature="()",
            docstring="", start_line=1, end_line=2,
        )
        defaults.update(overrides)
        return CodeSymbol(**defaults)

    def test_upsert_persists_new_fields(self, db, sample_project):
        db.add(sample_project)
        sym = self._symbol(
            kind="method", name="method", qualified_name="Widget.method",
            parent_class="Widget", return_type="int", is_private=True,
            is_async=True, complexity=5,
        )
        db.upsert_code_symbols("test-project", [sym])
        with db._conn() as conn:
            row = conn.execute(
                "SELECT parent_class, return_type, is_private, is_async, complexity "
                "FROM project_code_symbols WHERE qualified_name = ?",
                ("Widget.method",),
            ).fetchone()
        assert row["parent_class"] == "Widget"
        assert row["return_type"] == "int"
        assert bool(row["is_private"]) is True
        assert bool(row["is_async"]) is True
        assert row["complexity"] == 5

    def test_upsert_derives_inherits_from_relationships(self, db, sample_project):
        db.add(sample_project)
        cls = self._symbol(
            kind="class", name="Child", qualified_name="Child",
            bases=["Base", "Mixin"],
        )
        db.upsert_code_symbols("test-project", [cls])
        rels = db.get_code_relationships("test-project")
        assert {(r["source_name"], r["target_name"]) for r in rels} == {
            ("Child", "Base"), ("Child", "Mixin"),
        }

    def test_non_class_symbols_produce_no_relationships(self, db, sample_project):
        db.add(sample_project)
        fn = self._symbol(kind="function", name="f", qualified_name="f")
        db.upsert_code_symbols("test-project", [fn])
        assert db.get_code_relationships("test-project") == []

    def test_upsert_conflict_updates_new_fields(self, db, sample_project):
        """ON CONFLICT DO UPDATE must cover the new columns too — re-upserting
        the same symbol with a different complexity/parent_class must update
        in place, not silently keep the stale value."""
        db.add(sample_project)
        sym1 = self._symbol(qualified_name="f", complexity=1)
        sym2 = self._symbol(qualified_name="f", complexity=9, return_type="str")
        db.upsert_code_symbols("test-project", [sym1])
        db.upsert_code_symbols("test-project", [sym2])
        with db._conn() as conn:
            row = conn.execute(
                "SELECT complexity, return_type FROM project_code_symbols WHERE qualified_name = ?",
                ("f",),
            ).fetchone()
        assert row["complexity"] == 9
        assert row["return_type"] == "str"

    def test_relationship_conflict_does_nothing_no_duplicate(self, db, sample_project):
        db.add(sample_project)
        cls = self._symbol(kind="class", name="Child", qualified_name="Child", bases=["Base"])
        db.upsert_code_symbols("test-project", [cls])
        db.upsert_code_symbols("test-project", [cls])  # re-upsert same relationship
        rels = db.get_code_relationships("test-project")
        assert len(rels) == 1

    def test_clear_code_symbols_full_also_clears_relationships(self, db, sample_project):
        db.add(sample_project)
        cls = self._symbol(kind="class", name="Child", qualified_name="Child", bases=["Base"])
        db.upsert_code_symbols("test-project", [cls])
        assert db.get_code_relationships("test-project") != []
        db.clear_code_symbols("test-project")
        assert db.get_code_relationships("test-project") == []

    def test_clear_code_symbols_language_filtered_keeps_relationships(self, db, sample_project):
        db.add(sample_project)
        cls = self._symbol(kind="class", name="Child", qualified_name="Child", bases=["Base"])
        db.upsert_code_symbols("test-project", [cls])
        db.clear_code_symbols("test-project", language="python")
        # language-filtered clear intentionally leaves relationships alone (see docstring)
        assert db.get_code_relationships("test-project") != []

    def test_remove_project_cleans_up_relationships(self, db, sample_project):
        db.add(sample_project)
        cls = self._symbol(kind="class", name="Child", qualified_name="Child", bases=["Base"])
        db.upsert_code_symbols("test-project", [cls])
        db.remove("test-project")
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM project_code_relationships WHERE project_slug = ?",
                ("test_project",),
            ).fetchall()
        assert rows == []


class TestAppSettings:
    """'Discover repos to scout' plan, D1 — generic runtime key-value store."""

    def test_round_trip(self, db):
        db.set_setting("github_base_url", "https://ghe.example.com/api/v3")
        assert db.get_setting("github_base_url") == "https://ghe.example.com/api/v3"

    def test_missing_key_returns_default(self, db):
        assert db.get_setting("nope") is None
        assert db.get_setting("nope", "fallback") == "fallback"

    def test_set_overwrites_prior_value(self, db):
        db.set_setting("k", "v1")
        db.set_setting("k", "v2")
        assert db.get_setting("k") == "v2"


class TestRepoDispositions:
    """'Discover repos to scout' plan, D10 — undecided/tracking/investigating/
    ignored, keyed by github_url so it covers both never-imported search
    candidates and already-registered repos."""

    def test_never_decided_returns_none(self, db):
        assert db.get_disposition("https://github.com/never/decided") is None

    def test_round_trip_for_a_never_imported_candidate(self, db):
        db.set_disposition(
            "https://github.com/foo/bar", "ignored", reason="too small", decided_by="dan",
        )
        disp = db.get_disposition("https://github.com/foo/bar")
        assert disp["disposition"] == "ignored"
        assert disp["reason"] == "too small"
        assert disp["decided_by"] == "dan"
        assert disp["project_slug"] == ""

    def test_set_overwrites_not_appends(self, db):
        db.set_disposition("https://github.com/foo/bar", "ignored", reason="too small")
        db.set_disposition("https://github.com/foo/bar", "tracking")
        disp = db.get_disposition("https://github.com/foo/bar")
        assert disp["disposition"] == "tracking"
        assert disp["reason"] == ""  # overwritten, not merged with the prior reason

    def test_url_normalization_matches_get_by_github_url(self, db):
        db.set_disposition("https://github.com/foo/bar.git", "ignored")
        assert db.get_disposition("https://github.com/foo/bar") is not None
        assert db.get_disposition("https://github.com/foo/bar/") is not None
        assert db.get_disposition("HTTPS://GITHUB.COM/foo/bar") is not None

    def test_records_project_slug_when_given(self, db, sample_project):
        db.add(sample_project)
        db.set_disposition(sample_project.github_url, "investigating", resource_slug=sample_project.slug)
        disp = db.get_disposition(sample_project.github_url)
        assert disp["project_slug"] == "test-project"


class TestResolveRepoEntitySlug:
    """The PK generalized 2026-09-22 (Backlog.md, "Disposition is NOT fixed
    here") from `github_url` alone to `(entity_type, entity_slug)` --
    `resolve_repo_entity_slug` is what computes `entity_slug` for a repo
    (database/filesystem entities pass their own slug directly, no
    resolution needed)."""

    def test_never_imported_candidate_falls_back_to_url_derived_slug(self, db):
        # Same derivation org_importer._url_to_slug already uses elsewhere
        # for a pre-import candidate's entity_slug.
        assert db.resolve_repo_entity_slug("https://github.com/foo/bar") == "bar"

    def test_imported_repo_uses_its_real_slug(self, db, sample_project):
        db.add(sample_project)
        # sample_project.slug is 'test-project' (dash); add() normalizes to
        # 'test_project' -- resolve_repo_entity_slug must return the REAL,
        # stored slug, not a fresh guess from the URL.
        assert db.resolve_repo_entity_slug(sample_project.github_url) == "test_project"

    def test_real_slug_can_differ_from_a_url_derived_guess(self, db):
        # The exact shape confirmed live in the shared dev registry:
        # odpi/egeria's project_slug is 'egeria_git', not the url-derived
        # 'egeria' -- a manual slug choice or collision-avoidance rename.
        db.add(Project(slug="egeria_git", display_name="egeria",
                       github_url="https://github.com/odpi/egeria"))
        assert db.resolve_repo_entity_slug("https://github.com/odpi/egeria") == "egeria_git"


class TestEntityGenericDisposition:
    """`set_disposition_for_entity`/`get_disposition_for_entity`/
    `get_disposition_history_for_entity` -- the generalized primitive
    database/filesystem callers use directly (no pre-import ambiguity to
    resolve: a database/filesystem's slug IS its stable identity)."""

    def test_round_trip_for_a_database(self, db):
        db.set_disposition_for_entity("database", "mydb", "tracking", reason="worth a look")
        disp = db.get_disposition_for_entity("database", "mydb")
        assert disp["disposition"] == "tracking"
        assert disp["reason"] == "worth a look"
        assert disp["entity_type"] == "database"
        assert disp["entity_slug"] == "mydb"

    def test_round_trip_for_a_filesystem(self, db):
        db.set_disposition_for_entity("filesystem", "myfs", "using")
        disp = db.get_disposition_for_entity("filesystem", "myfs")
        assert disp["disposition"] == "using"

    def test_never_decided_returns_none(self, db):
        assert db.get_disposition_for_entity("database", "nope") is None

    def test_history_accumulates(self, db):
        db.set_disposition_for_entity("database", "mydb", "tracking")
        db.set_disposition_for_entity("database", "mydb", "using")
        history = db.get_disposition_history_for_entity("database", "mydb")
        assert [h["disposition"] for h in history] == ["tracking", "using"]

    def test_different_entity_types_with_the_same_slug_do_not_collide(self, db):
        # The composite key is (entity_type, entity_slug) -- a database and
        # a filesystem could legitimately share a slug string.
        db.set_disposition_for_entity("database", "shared", "tracking")
        db.set_disposition_for_entity("filesystem", "shared", "ignored")
        assert db.get_disposition_for_entity("database", "shared")["disposition"] == "tracking"
        assert db.get_disposition_for_entity("filesystem", "shared")["disposition"] == "ignored"

    def test_repo_convenience_methods_delegate_to_the_generic_ones(self, db):
        # set_disposition/get_disposition (github_url-keyed) and
        # set_disposition_for_entity/get_disposition_for_entity
        # (entity_type='repo', entity_slug=<resolved>) read/write the same
        # underlying row.
        db.set_disposition("https://github.com/foo/bar", "tracking")
        assert db.get_disposition_for_entity("repo", "bar")["disposition"] == "tracking"
        db.set_disposition_for_entity("repo", "bar", "using")
        assert db.get_disposition("https://github.com/foo/bar")["disposition"] == "using"


class TestDispositionReconciliationOnImport:
    """`add()`'s `_reconcile_disposition_on_import` -- the mitigation for
    TestResolveRepoEntitySlug's divergent-slug case: a disposition set
    before import (keyed provisionally by a url-derived slug guess) must
    not become orphaned when the repo is later imported under a genuinely
    different slug."""

    def test_provisional_row_is_rekeyed_onto_the_real_slug_at_import(self, db):
        db.set_disposition("https://github.com/odpi/egeria", "using", reason="core platform")
        # Provisional: no project exists yet, so entity_slug is the
        # url-derived guess 'egeria'.
        assert db.get_disposition_for_entity("repo", "egeria") is not None
        db.add(Project(slug="egeria_git", display_name="egeria",
                       github_url="https://github.com/odpi/egeria"))
        # Reconciled onto the real slug -- the guess is gone, the real one
        # carries the original decision forward.
        assert db.get_disposition_for_entity("repo", "egeria") is None
        real = db.get_disposition_for_entity("repo", "egeria_git")
        assert real["disposition"] == "using"
        assert real["reason"] == "core platform"
        assert real["project_slug"] == "egeria_git"
        # The repo-convenience API sees the same, now-reconciled row.
        assert db.get_disposition("https://github.com/odpi/egeria")["disposition"] == "using"

    def test_history_is_rekeyed_too(self, db):
        db.set_disposition("https://github.com/odpi/egeria", "tracking")
        db.set_disposition("https://github.com/odpi/egeria", "using")
        db.add(Project(slug="egeria_git", display_name="egeria",
                       github_url="https://github.com/odpi/egeria"))
        history = db.get_disposition_history_for_entity("repo", "egeria_git")
        assert [h["disposition"] for h in history] == ["tracking", "using"]
        assert db.get_disposition_history_for_entity("repo", "egeria") == []

    def test_no_op_when_the_guessed_slug_already_matches(self, db):
        # The overwhelmingly common case (org_importer's own path always
        # lands here, since it refuses on a slug collision rather than
        # picking an alternate) -- must not touch an unrelated row.
        db.set_disposition("https://github.com/foo/bar", "tracking")
        db.add(Project(slug="bar", display_name="bar", github_url="https://github.com/foo/bar"))
        assert db.get_disposition_for_entity("repo", "bar")["disposition"] == "tracking"

    def test_no_op_when_no_provisional_row_exists(self, db, sample_project):
        # The common case of an import with no pre-existing disposition at
        # all must not raise or fabricate a row.
        db.add(sample_project)
        assert db.get_disposition(sample_project.github_url) is None

    def test_real_slug_already_taken_favors_the_real_row_over_the_guess(self, db):
        # Pathological (shouldn't happen -- real_slug wasn't registered a
        # moment before add() ran) but must not crash the import: the
        # provisional guess is dropped rather than clobbering a row already
        # set directly against the real slug.
        db.set_disposition_for_entity("repo", "egeria", "tracking")
        db.set_disposition_for_entity("repo", "egeria_git", "using")
        db.add(Project(slug="egeria_git", display_name="egeria",
                       github_url="https://github.com/odpi/egeria"))
        assert db.get_disposition_for_entity("repo", "egeria") is None
        assert db.get_disposition_for_entity("repo", "egeria_git")["disposition"] == "using"


class TestDispositionEntityKeyMigration:
    """The one-time migration from `github_url TEXT PRIMARY KEY` to
    `(entity_type, entity_slug)` (`_migrate_repo_dispositions_to_entity_key`/
    `_migrate_repo_disposition_history_to_entity_key`) -- exercised against
    a hand-built pre-migration SQLite file, since the `db` fixture's fresh
    database never takes this path (CREATE TABLE already declares the new
    shape)."""

    def _seed_old_schema_db(self, path: str) -> None:
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE repo_dispositions (
            github_url   TEXT PRIMARY KEY,
            disposition  TEXT NOT NULL DEFAULT 'undecided',
            reason       TEXT DEFAULT '',
            decided_by   TEXT DEFAULT '',
            decided_at   TEXT NOT NULL,
            project_slug TEXT DEFAULT ''
        )""")
        conn.execute("""CREATE TABLE repo_disposition_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            github_url TEXT NOT NULL,
            disposition TEXT NOT NULL,
            reason TEXT DEFAULT '',
            decided_by TEXT DEFAULT '',
            decided_at TEXT NOT NULL
        )""")
        # A normal already-imported repo (project_slug resolved) and the
        # real divergent-slug case seen live in the shared dev registry.
        conn.execute(
            "INSERT INTO repo_dispositions VALUES (?, ?, ?, ?, ?, ?)",
            ("https://github.com/odpi/egeria", "using", "", "", "2026-01-01T00:00:00", "egeria_git"),
        )
        # Never-imported candidate: project_slug is empty.
        conn.execute(
            "INSERT INTO repo_dispositions VALUES (?, ?, ?, ?, ?, ?)",
            ("https://github.com/intake/intake", "tracking", "worth watching", "dan", "2026-01-01T00:00:00", ""),
        )
        conn.execute(
            "INSERT INTO repo_disposition_history (github_url, disposition, decided_at) VALUES (?, ?, ?)",
            ("https://github.com/odpi/egeria", "using", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO repo_disposition_history (github_url, disposition, reason, decided_by, decided_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("https://github.com/intake/intake", "tracking", "worth watching", "dan", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

    def test_backfill_uses_project_slug_when_present(self, tmp_path):
        db_path = str(tmp_path / "pre_migration.db")
        self._seed_old_schema_db(db_path)
        reg = ProjectRegistry(db_path=db_path)
        # No Project row exists in this fixture, so the repo-convenience
        # get_disposition (which resolves via resolve_repo_entity_slug,
        # i.e. get_by_github_url) can't find it post-migration -- this is
        # the one case the Backlog entry names as not fully solved by the
        # migration alone (no Project to resolve through). The direct
        # entity-keyed read proves the backfill itself landed correctly.
        disp = reg.get_disposition_for_entity("repo", "egeria_git")
        assert disp is not None
        assert disp["disposition"] == "using"
        assert disp["github_url"] == "https://github.com/odpi/egeria"

    def test_backfill_falls_back_to_url_derived_slug_when_project_slug_is_empty(self, tmp_path):
        db_path = str(tmp_path / "pre_migration.db")
        self._seed_old_schema_db(db_path)
        reg = ProjectRegistry(db_path=db_path)
        disp = reg.get_disposition_for_entity("repo", "intake")
        assert disp is not None
        assert disp["disposition"] == "tracking"
        assert disp["reason"] == "worth watching"

    def test_history_is_backfilled_consistently_with_the_current_row(self, tmp_path):
        db_path = str(tmp_path / "pre_migration.db")
        self._seed_old_schema_db(db_path)
        reg = ProjectRegistry(db_path=db_path)
        history = reg.get_disposition_history_for_entity("repo", "egeria_git")
        assert [h["disposition"] for h in history] == ["using"]
        history2 = reg.get_disposition_history_for_entity("repo", "intake")
        assert [h["disposition"] for h in history2] == ["tracking"]

    def test_full_repo_convenience_api_works_once_the_matching_project_exists(self, tmp_path):
        # Mirrors the real shared dev registry, where every project_slug
        # !='' row in repo_dispositions has a matching `projects` row --
        # confirming the migration is transparent to the existing repo API
        # once that's true, which is the realistic, verified-live case.
        db_path = str(tmp_path / "pre_migration.db")
        self._seed_old_schema_db(db_path)
        reg = ProjectRegistry(db_path=db_path)
        reg.add(Project(slug="egeria_git", display_name="egeria",
                        github_url="https://github.com/odpi/egeria"))
        disp = reg.get_disposition("https://github.com/odpi/egeria")
        assert disp is not None
        assert disp["disposition"] == "using"
        history = reg.get_disposition_history("https://github.com/odpi/egeria")
        assert [h["disposition"] for h in history] == ["using"]

    def test_migration_is_a_no_op_on_a_second_open(self, tmp_path):
        db_path = str(tmp_path / "pre_migration.db")
        self._seed_old_schema_db(db_path)
        ProjectRegistry(db_path=db_path)
        # Re-opening must not re-run the backfill or error on already-
        # migrated columns.
        reg2 = ProjectRegistry(db_path=db_path)
        assert reg2.get_disposition_for_entity("repo", "egeria_git")["disposition"] == "using"


class TestFileInventoryModes:
    """Assessment sub-resource cataloging plan, D9 Tier 1 — file_mode is
    optional/additive on top of the existing path+size inventory."""

    def test_modes_by_path_threaded_through(self, db, sample_project):
        db.add(sample_project)
        db.upsert_file_inventory(
            sample_project.slug,
            [("README.md", 100), ("run.sh", 200), ("no_mode.txt", 50)],
            modes_by_path={"README.md": "100644", "run.sh": "100755"},
        )
        rows = {r["file_path"]: r for r in db.get_file_inventory_with_sizes(sample_project.slug)}
        assert rows["README.md"]["file_mode"] == "100644"
        assert rows["run.sh"]["file_mode"] == "100755"
        assert rows["no_mode.txt"]["file_mode"] == ""  # not in modes_by_path — empty, not an error

    def test_modes_by_path_omitted_defaults_to_empty_string(self, db, sample_project):
        db.add(sample_project)
        db.upsert_file_inventory(sample_project.slug, [("a.py", 10)])
        rows = db.get_file_inventory_with_sizes(sample_project.slug)
        assert rows[0]["file_mode"] == ""

    def test_get_file_inventory_with_sizes_includes_size_and_mode(self, db, sample_project):
        db.add(sample_project)
        db.upsert_file_inventory(
            sample_project.slug, [("a.py", 123)], modes_by_path={"a.py": "100644"},
        )
        rows = db.get_file_inventory_with_sizes(sample_project.slug)
        assert rows == [{"file_path": "a.py", "file_size_bytes": 123, "file_mode": "100644", "vendored": False}]

    def test_repeated_upsert_replaces_not_appends(self, db, sample_project):
        db.add(sample_project)
        db.upsert_file_inventory(sample_project.slug, [("a.py", 1)], modes_by_path={"a.py": "100644"})
        db.upsert_file_inventory(sample_project.slug, [("b.py", 2)], modes_by_path={"b.py": "100755"})
        rows = db.get_file_inventory_with_sizes(sample_project.slug)
        assert [r["file_path"] for r in rows] == ["b.py"]


class TestFileExists:
    """Assessment expansion plan B3 — exact-filename lookup against
    project_file_inventory, an indexed point-lookup rather than a full-list
    client-side scan."""

    def test_returns_first_matching_candidate(self, db, sample_project):
        db.add(sample_project)
        db.upsert_file_inventory(sample_project.slug, [(".github/CODEOWNERS", 10)])
        assert db.file_exists(
            sample_project.slug, "CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
        ) == ".github/CODEOWNERS"

    def test_prefers_earlier_candidate_when_multiple_present(self, db, sample_project):
        db.add(sample_project)
        db.upsert_file_inventory(
            sample_project.slug, [("CODEOWNERS", 10), (".github/CODEOWNERS", 20)],
        )
        assert db.file_exists(
            sample_project.slug, "CODEOWNERS", ".github/CODEOWNERS",
        ) == "CODEOWNERS"

    def test_returns_none_when_no_candidate_present(self, db, sample_project):
        db.add(sample_project)
        db.upsert_file_inventory(sample_project.slug, [("README.md", 10)])
        assert db.file_exists(sample_project.slug, "CODEOWNERS", ".github/CODEOWNERS") is None

    def test_returns_none_for_no_candidates_given(self, db, sample_project):
        db.add(sample_project)
        assert db.file_exists(sample_project.slug) is None

    def test_nested_path_not_matched_by_basename(self, db, sample_project):
        # file_exists is an exact-path lookup, not a basename-anywhere match
        # (that's _HYGIENE_FILES' job in DocumentationSurveyor) — a
        # non-canonical location must not match.
        db.add(sample_project)
        db.upsert_file_inventory(sample_project.slug, [("src/CODEOWNERS", 10)])
        assert db.file_exists(
            sample_project.slug, "CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
        ) is None


class TestSubResources:
    """Repo scope-narrowing funnel plan, D2/D4 — the local "Catalog" stage,
    generic across resource types (only 'repo' is exercised in these tests,
    matching Phase 1's scope) and deliberately repeatable/idempotent."""

    def test_catalog_and_list_round_trip(self, db):
        db.catalog_sub_resource("repo", "myproj", "docs", "folder")
        rows = db.list_sub_resources("repo", "myproj")
        assert len(rows) == 1
        assert rows[0]["locator"] == "docs"
        assert rows[0]["kind"] == "folder"
        assert rows[0]["egeria_guid"] == ""

    def test_root_locator_is_representable(self, db):
        db.catalog_sub_resource("repo", "myproj", "", "folder")
        rows = db.list_sub_resources("repo", "myproj")
        assert rows[0]["locator"] == ""

    def test_recataloging_is_a_no_op_not_a_duplicate(self, db):
        db.catalog_sub_resource("repo", "myproj", "docs", "folder", source_finding="run-1")
        db.catalog_sub_resource("repo", "myproj", "docs", "folder", source_finding="run-2")
        rows = db.list_sub_resources("repo", "myproj")
        assert len(rows) == 1
        assert rows[0]["source_finding"] == "run-1"  # first write wins, not overwritten

    def test_recataloging_never_clobbers_an_existing_egeria_guid(self, db):
        db.catalog_sub_resource("repo", "myproj", "docs", "folder")
        db.set_sub_resource_egeria_guid("repo", "myproj", "docs", "real-guid-123")
        db.catalog_sub_resource("repo", "myproj", "docs", "folder")  # re-select, e.g. from the UI again
        rows = db.list_sub_resources("repo", "myproj")
        assert rows[0]["egeria_guid"] == "real-guid-123"

    def test_detail_is_stored_as_denormalized_json(self, db):
        db.catalog_sub_resource(
            "repo", "myproj", "docs/SECURITY.md", "file",
            detail={"owners": ["@team"], "last_updated_at": "2026-01-01"},
        )
        rows = db.list_sub_resources("repo", "myproj")
        detail = json.loads(rows[0]["detail_json"])
        assert detail == {"owners": ["@team"], "last_updated_at": "2026-01-01"}

    def test_uncatalog_removes_the_row(self, db):
        db.catalog_sub_resource("repo", "myproj", "docs", "folder")
        db.uncatalog_sub_resource("repo", "myproj", "docs")
        assert db.list_sub_resources("repo", "myproj") == []

    def test_uncatalog_of_untracked_locator_does_not_raise(self, db):
        db.uncatalog_sub_resource("repo", "myproj", "never-cataloged")  # just shouldn't blow up

    def test_list_is_scoped_to_resource_type_and_slug(self, db):
        db.catalog_sub_resource("repo", "myproj", "docs", "folder")
        db.catalog_sub_resource("repo", "other-proj", "docs", "folder")
        db.catalog_sub_resource("database", "myproj", "public", "schema")
        rows = db.list_sub_resources("repo", "myproj")
        assert len(rows) == 1
        assert rows[0]["resource_type"] == "repo"
        assert rows[0]["resource_slug"] == "myproj"

    def test_set_egeria_guid_updates_the_row(self, db):
        db.catalog_sub_resource("repo", "myproj", "docs", "folder")
        db.set_sub_resource_egeria_guid("repo", "myproj", "docs", "guid-abc")
        rows = db.list_sub_resources("repo", "myproj")
        assert rows[0]["egeria_guid"] == "guid-abc"


class TestScopeLocatorOnFindingsAndMetrics:
    """Repo scope-narrowing funnel plan, D5/D6 — scope_locator keeps a
    scoped analysis run's findings/metrics distinct from whole-resource
    runs under the same `kind`, without disturbing any pre-scope-aware
    caller (default '' everywhere)."""

    @pytest.fixture(autouse=True)
    def _register_myproj(self, db):
        # upsert_finding() now rejects an unregistered slug (2026-08-23,
        # project_analysis_findings_project_slug_fkey incident) — register
        # "myproj" the same way every other project-bearing test in this
        # file does via `sample_project`, so this class's findings calls
        # still exercise the scope_locator behavior under test rather than
        # the new guard.
        db.add(Project(slug="myproj", display_name="My Project",
                        github_url="https://github.com/test/myproj"))

    def test_default_scope_locator_is_whole_resource(self, db):
        db.upsert_finding("myproj", "api_structure", [
            {"check_name": "a.py", "label": "ok", "summary": ""},
        ])
        rows = db.query_findings("myproj", "api_structure")
        assert len(rows) == 1
        assert rows[0]["scope_locator"] == ""

    def test_scoped_and_whole_resource_findings_stay_distinct(self, db):
        db.upsert_finding("myproj", "api_structure", [
            {"check_name": "whole", "label": "ok", "summary": ""},
        ])
        db.upsert_finding("myproj", "api_structure", [
            {"check_name": "scoped", "label": "ok", "summary": ""},
        ], scope_locator="src")
        whole = db.query_findings("myproj", "api_structure")
        scoped = db.query_findings("myproj", "api_structure", scope_locator="src")
        assert [r["check_name"] for r in whole] == ["whole"]
        assert [r["check_name"] for r in scoped] == ["scoped"]

    def test_scoped_and_whole_resource_metrics_stay_distinct(self, db):
        db.upsert_metric("myproj", "api_structure", {"symbol_count": 100})
        db.upsert_metric("myproj", "api_structure", {"symbol_count": 7}, scope_locator="src")
        assert db.query_metrics("myproj", "api_structure")["symbol_count"] == 100
        assert db.query_metrics("myproj", "api_structure", scope_locator="src")["symbol_count"] == 7

    def test_metrics_history_is_scope_aware(self, db):
        db.upsert_metric("myproj", "api_structure", {"symbol_count": 1}, surveyed_at="2026-01-01")
        db.upsert_metric("myproj", "api_structure", {"symbol_count": 2}, surveyed_at="2026-01-02")
        db.upsert_metric("myproj", "api_structure", {"symbol_count": 99}, scope_locator="src", surveyed_at="2026-01-01")
        whole_history = db.query_metrics_history("myproj", "api_structure", "symbol_count")
        scoped_history = db.query_metrics_history("myproj", "api_structure", "symbol_count", scope_locator="src")
        assert [r["metric_value"] for r in whole_history] == [1, 2]
        assert [r["metric_value"] for r in scoped_history] == [99]

    def test_different_scopes_are_also_kept_distinct_from_each_other(self, db):
        db.upsert_finding("myproj", "api_structure", [
            {"check_name": "a", "label": "ok", "summary": ""},
        ], scope_locator="src")
        db.upsert_finding("myproj", "api_structure", [
            {"check_name": "b", "label": "ok", "summary": ""},
        ], scope_locator="tests")
        assert [r["check_name"] for r in db.query_findings("myproj", "api_structure", "src")] == ["a"]
        assert [r["check_name"] for r in db.query_findings("myproj", "api_structure", "tests")] == ["b"]

    # A TestReconcileOrphanedRunningActivity class lived here briefly
    # (2026-08-26), covering ProjectRegistry.reconcile_orphaned_running_activity()
    # — removed along with that method in favor of
    # resource_explorer/run_reconciler.py's ownership-based reconciliation
    # (see its own tests). See registry.py's note at the old method's former
    # location for why the blanket version was unsafe.


class TestSupersedesPrevious:
    """upsert_finding(..., supersedes_previous=True) — "a run that finds
    nothing retires the previous run's findings" (docs/Backlog.md). The
    cve_scan reproduction: a positive finding at T1, then a clean/empty
    COMPLETE run at T2 must retire the T1 row from query_findings(), while
    query_findings_history_raw() keeps seeing it."""

    @pytest.fixture(autouse=True)
    def _register_myproj(self, db):
        db.add(Project(slug="myproj", display_name="My Project",
                        github_url="https://github.com/test/myproj"))

    def test_empty_supersedes_previous_run_retires_stale_positive(self, db):
        db.upsert_finding("myproj", "cve_scan", [
            {"check_name": "click", "label": "advisory", "summary": "1 advisory"},
        ], surveyed_at="2026-09-01T11:57:00")
        assert len(db.query_findings("myproj", "cve_scan")) == 1

        db.upsert_finding("myproj", "cve_scan", [], surveyed_at="2026-09-12T12:16:00",
                          supersedes_previous=True)

        assert db.query_findings("myproj", "cve_scan") == []
        # History is untouched — the T1 row is still there, just no longer
        # "current".
        history = db.query_findings_history_raw("myproj", "cve_scan")
        assert [r["check_name"] for r in history] == ["click"]

    def test_supersedes_previous_does_not_cross_scopes(self, db):
        db.upsert_finding("myproj", "cve_scan", [
            {"check_name": "click", "label": "advisory", "summary": "1 advisory"},
        ], surveyed_at="2026-09-01T00:00:00", scope_locator="a")

        # A complete-but-empty run for a DIFFERENT scope must not retire
        # scope "a"'s rows.
        db.upsert_finding("myproj", "cve_scan", [], surveyed_at="2026-09-12T00:00:00",
                          supersedes_previous=True, scope_locator="b")

        assert [r["check_name"] for r in db.query_findings("myproj", "cve_scan", "a")] == ["click"]
        assert db.query_findings("myproj", "cve_scan", "b") == []

    def test_non_empty_supersedes_previous_run_replaces_stale_positive(self, db):
        db.upsert_finding("myproj", "cve_scan", [
            {"check_name": "click", "label": "advisory", "summary": "1 advisory"},
        ], surveyed_at="2026-09-01T00:00:00")

        db.upsert_finding("myproj", "cve_scan", [
            {"check_name": "requests", "label": "advisory", "summary": "a different one"},
        ], surveyed_at="2026-09-12T00:00:00", supersedes_previous=True)

        current = db.query_findings("myproj", "cve_scan")
        assert [r["check_name"] for r in current] == ["requests"]
        history = db.query_findings_history_raw("myproj", "cve_scan")
        assert [r["check_name"] for r in history] == ["click", "requests"]

    def test_default_is_byte_for_byte_old_behaviour(self, db):
        """supersedes_previous defaults to False: an empty findings call
        still no-ops entirely (no row, no exception, no side effect), and a
        stale positive from an earlier run is NOT retired by a later empty
        call that doesn't opt in."""
        db.upsert_finding("myproj", "cve_scan", [
            {"check_name": "click", "label": "advisory", "summary": "1 advisory"},
        ], surveyed_at="2026-09-01T00:00:00")

        db.upsert_finding("myproj", "cve_scan", [], surveyed_at="2026-09-12T00:00:00")

        # Old, buggy-but-unchanged behaviour: the stale positive is still
        # served as current, because the T2 call wrote nothing and made no
        # assertion of completeness.
        assert [r["check_name"] for r in db.query_findings("myproj", "cve_scan")] == ["click"]


class TestHasAssignedEgeriaProject:
    """The single gate both auto-publish paths use (survey_definition_executor.py
    and projects.py's Assessment/Analysis run route) — deliberately tighter than
    egeria.py's publish route's own inline check, which lets personal/deferred/
    declined pass too. Only a real `linked` status with a GUID counts here."""

    def test_no_context_at_all_is_false(self, db):
        assert db.has_assigned_egeria_project("repo", "nobody-decided") is False

    def test_linked_with_a_guid_is_true(self, db):
        db.set_project_context("repo", "s", status="linked", egeria_project_guid="g-1")
        assert db.has_assigned_egeria_project("repo", "s") is True

    def test_unset_is_false(self, db):
        db.set_project_context("repo", "s", status="unset")
        assert db.has_assigned_egeria_project("repo", "s") is False

    def test_personal_deferred_declined_are_all_false(self, db):
        """Egeria's own manual-publish route treats these as "decided, proceed" —
        deliberately not the case here: they're all real answers to "should this
        be in Egeria" and the answer each gives is no."""
        for status in ("personal", "deferred", "declined"):
            db.set_project_context("repo", f"s-{status}", status=status)
            assert db.has_assigned_egeria_project("repo", f"s-{status}") is False, status

    def test_linked_but_no_guid_is_false(self, db):
        """A 'linked' row with no GUID shouldn't be possible via set_project_context
        in practice, but the check must not trust status alone."""
        db.set_project_context("repo", "s", status="linked", egeria_project_guid="")
        assert db.has_assigned_egeria_project("repo", "s") is False

    def test_falls_back_to_and_writes_the_inherited_context(self, db, monkeypatch):
        """Same write-through behavior as egeria.py's publish route: an
        inheritance that stayed read-only would make a resource's context
        depend on investigation membership at read time."""
        monkeypatch.setattr(db, "inherited_egeria_project_context", lambda et, es: {
            "egeria_project_guid": "inherited-guid",
            "egeria_project_qualified_name": "qn",
            "_inherited_from_name": "Q3 Health Review",
        })
        assert db.has_assigned_egeria_project("repo", "s") is True
        context = db.get_project_context("repo", "s")
        assert context["status"] == "linked"
        assert context["egeria_project_guid"] == "inherited-guid"

    def test_no_inheritance_available_is_false(self, db, monkeypatch):
        monkeypatch.setattr(db, "inherited_egeria_project_context", lambda et, es: None)
        assert db.has_assigned_egeria_project("repo", "s") is False


class TestSurveyDefinitionLastActivityRepoWideFallback:
    """Confirmed live 2026-08-27: a candidate with NO last_run_at at all still
    received last_published_scope: 'repo' from the repo-wide-publish fallback,
    because `"" <= repo_wide_publish_at` is always True in Python string
    comparison — reproducing "Never Run" + "Published today" on the same card."""

    def _log_survey(self, db, ref, ts_suffix=""):
        from resource_explorer.activity_logger import log_survey
        log_survey(
            db, entity_type="repo", entity_slug="s", entity_name="s",
            entity_location="", intent="assessment", status="ok",
            summary="ran", detail=json.dumps({"survey_definition_ref": ref}),
        )

    def _log_catalog_no_ref(self, db):
        from resource_explorer.activity_logger import log_catalog
        log_catalog(
            db, entity_type="repo", entity_slug="s", entity_name="s",
            entity_location="", status="ok",
            summary="published", detail=json.dumps({}),
        )

    def test_a_ref_with_no_survey_row_never_gets_the_repo_wide_fallback(self, db):
        """The regression guard. This ref has an entry (via a ref-tagged
        catalog row further below) but no matching 'survey' row — i.e. it
        never ran — so it must not receive last_published_scope: 'repo'."""
        from resource_explorer.activity_logger import log_catalog

        # A ref-tagged catalog row with no matching survey row — the entry
        # this ref gets in `result` has no last_run_at.
        log_catalog(
            db, entity_type="repo", entity_slug="s", entity_name="s",
            entity_location="", status="ok",
            summary="published", detail=json.dumps({"survey_definition_ref": "RefX"}),
        )
        # An untagged catalog row after it, to populate repo_wide_publish_at.
        self._log_catalog_no_ref(db)

        activity = db.get_survey_definition_last_activity("repo", "s")
        assert "last_run_at" not in activity.get("RefX", {})
        assert activity["RefX"].get("last_published_scope") != "repo"

    def test_a_ref_that_actually_ran_before_the_repo_wide_publish_still_gets_it(self, db):
        """The fallback must still work for the case it's actually for."""
        self._log_survey(db, "RefY")
        self._log_catalog_no_ref(db)

        activity = db.get_survey_definition_last_activity("repo", "s")
        assert activity["RefY"]["last_published_scope"] == "repo"

    def test_executor_publish_flag_is_trusted_even_when_its_own_run_row_is_newer(self, db):
        """Confirmed live 2026-08-28: SurveyDefinitionExecutor.run() (added
        2026-08-27, see get_survey_definition_last_activity's own 'survey' row
        comment) calls adapter.publish() BEFORE it logs the 'survey' row
        recording the run — so the run's own timestamp is always a few ms
        *after* its untagged 'catalog' row's timestamp, permanently failing
        the repo-wide fallback's `last_run_at <= repo_wide_publish_at` check.
        Running RepoCoarseScout against egeria_docs published successfully
        (a real 'catalog' row existed) yet the candidate still showed
        unpublished. The 'survey' row's own `detail.published` must be
        trusted directly rather than relying on the timestamp heuristic."""
        from resource_explorer.activity_logger import log_catalog, log_survey

        # Untagged catalog row lands first (adapter.publish() runs first).
        log_catalog(
            db, entity_type="repo", entity_slug="s", entity_name="s",
            entity_location="", status="ok",
            summary="published", detail=json.dumps({}),
        )
        # The survey row recording the run lands after, with its own outcome.
        log_survey(
            db, entity_type="repo", entity_slug="s", entity_name="s",
            entity_location="", intent="discovery", status="ok",
            summary="ran", detail=json.dumps({
                "survey_definition_ref": "RefZ", "published": True,
            }),
        )

        activity = db.get_survey_definition_last_activity("repo", "s")
        assert activity["RefZ"]["last_published_scope"] == "candidate"
        assert "last_published_at" in activity["RefZ"]


class TestSurveyDefinitionLastActivityCarriesRunErrors:
    """The ⚠ beside "ran Xm ago" on a Survey Definition card used to do
    nothing on click (found live 2026-09-26) because the step report a run
    actually recorded was parsed here and then discarded — only
    last_run_at/last_run_status survived into the per-ref entry."""

    def test_errors_from_the_run_detail_are_carried_into_the_entry(self, db):
        from resource_explorer.activity_logger import log_survey
        log_survey(
            db, entity_type="database", entity_slug="s", entity_name="s",
            entity_location="", intent="scouting", status="error",
            summary="ran, 1 error",
            detail=json.dumps({
                "survey_definition_ref": "RefE",
                "errors": ["RE step 'postgres_schema_and_stats' failed: boom"],
            }),
        )

        activity = db.get_survey_definition_last_activity("database", "s")

        assert activity["RefE"]["last_run_errors"] == [
            "RE step 'postgres_schema_and_stats' failed: boom"
        ]

    def test_a_clean_run_carries_an_empty_list_not_a_missing_key(self, db):
        from resource_explorer.activity_logger import log_survey
        log_survey(
            db, entity_type="database", entity_slug="s", entity_name="s",
            entity_location="", intent="scouting", status="ok",
            summary="ran clean",
            detail=json.dumps({"survey_definition_ref": "RefC"}),
        )

        activity = db.get_survey_definition_last_activity("database", "s")

        assert activity["RefC"]["last_run_errors"] == []


class TestAnalysisLastRunPublishFailedFlag:
    """last_publish_failed backs the ☁ Publish button's visibility (2026-08-27):
    shown as a recovery action when the last auto-publish attempt failed, hidden
    once cleanly published. Must not conflate "never attempted" with "failed" —
    an unassigned resource's card would otherwise look broken forever."""

    def _log(self, db, published):
        from resource_explorer.activity_logger import log_analysis_run
        log_analysis_run(
            db, "repo", "s", "s", "ok", "ran", "fake_analysis", published=published,
        )

    def test_a_failed_publish_attempt_sets_the_flag(self, db):
        self._log(db, published=False)
        activity = db.get_analysis_last_run("repo", "s")
        assert activity["fake_analysis"]["last_publish_failed"] is True

    def test_a_successful_publish_does_not_set_the_flag(self, db):
        self._log(db, published=True)
        activity = db.get_analysis_last_run("repo", "s")
        assert activity["fake_analysis"]["last_publish_failed"] is False

    def test_never_attempted_is_not_read_as_failed(self, db):
        """The regression this whole flag exists to prevent: None must not
        collapse into "failed" the way it easily could with a plain bool."""
        self._log(db, published=None)
        activity = db.get_analysis_last_run("repo", "s")
        assert activity["fake_analysis"]["last_publish_failed"] is False


class TestMaterializedPorts:
    """architecture_materialized_ports (PortMaterializer's registry cache,
    Backlog.md item 8) — same round-trip shape as architecture_materialized_
    blueprints/_components, exercised directly since PortMaterializer's own
    tests mock the registry entirely and never run this SQL."""

    def test_missing_returns_none(self, db):
        assert db.get_materialized_port("repo", "myproj", "src/web", "http") is None

    def test_round_trips(self, db):
        entry = db.record_materialized_port(
            "repo", "myproj", "src/web", "http",
            "SolutionPort::repo::myproj::src/web::http", "guid-1",
        )
        assert entry["guid"] == "guid-1"
        got = db.get_materialized_port("repo", "myproj", "src/web", "http")
        assert got["guid"] == "guid-1"
        assert got["qualified_name"] == "SolutionPort::repo::myproj::src/web::http"

    def test_two_ports_on_the_same_component_are_distinct(self, db):
        """The reason for keying on (scope_locator, port_name) rather than
        scope_locator alone — one component can have more than one port."""
        db.record_materialized_port("repo", "myproj", "src/web", "http", "qn-http", "guid-http")
        db.record_materialized_port("repo", "myproj", "src/web", "metrics", "qn-metrics", "guid-metrics")
        assert db.get_materialized_port("repo", "myproj", "src/web", "http")["guid"] == "guid-http"
        assert db.get_materialized_port("repo", "myproj", "src/web", "metrics")["guid"] == "guid-metrics"

    def test_re_recording_overwrites_not_appends(self, db):
        """Nothing append-only about "does this exist in Egeria" — same
        reasoning record_materialized_component/_blueprint already give."""
        db.record_materialized_port("repo", "myproj", "src/web", "http", "qn", "guid-old")
        db.record_materialized_port("repo", "myproj", "src/web", "http", "qn", "guid-new")
        got = db.get_materialized_port("repo", "myproj", "src/web", "http")
        assert got["guid"] == "guid-new"
        with db._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM architecture_materialized_ports "
                "WHERE entity_type='repo' AND entity_slug='myproj' AND scope_locator='src/web' AND port_name='http'"
            ).fetchone()[0]
        assert count == 1

    def test_get_materialized_ports_keys_by_scope_and_name(self, db):
        db.record_materialized_port("repo", "myproj", "src/web", "http", "qn-http", "guid-http")
        db.record_materialized_port("repo", "myproj", "src/web", "metrics", "qn-metrics", "guid-metrics")
        all_ports = db.get_materialized_ports("repo", "myproj")
        assert set(all_ports.keys()) == {"src/web::http", "src/web::metrics"}
        assert all_ports["src/web::http"]["guid"] == "guid-http"


class TestUpdateDatabaseCredentials:
    """update_database_credentials -- the only supported way to repoint an
    already-registered database's db_user/db_password (e.g. from a narrow
    role to a broader one) without losing its registration history."""

    def test_updates_only_credentials_leaves_everything_else_untouched(self, db):
        db.register_database(DatabaseEntity(
            slug="mydb", display_name="My DB", db_type="postgresql",
            host="localhost", port=5432, database_name="mydb",
            db_user="egeria_user", db_password="old-secret",
            egeria_asset_guid="guid-123", description="a database",
        ))
        db.update_database_surveyed_at("mydb")
        before = db.get_database("mydb")
        assert before.db_user == "egeria_user"

        db.update_database_credentials("mydb", "surveyor", "new-secret")

        after = db.get_database("mydb")
        assert after.db_user == "surveyor"
        assert after.db_password == "new-secret"
        # Everything else survives untouched.
        assert after.slug == before.slug
        assert after.display_name == before.display_name
        assert after.egeria_asset_guid == before.egeria_asset_guid
        assert after.description == before.description
        assert after.last_surveyed_at == before.last_surveyed_at

    def test_normalizes_slug_like_other_database_updates(self, db):
        db.register_database(DatabaseEntity(
            slug="my-db", display_name="My DB", db_type="postgresql",
            host="localhost", port=5432, database_name="mydb",
        ))
        db.update_database_credentials("my-db", "newuser", "newpass")
        assert db.get_database("my_db").db_user == "newuser"

    def test_unknown_slug_is_a_no_op_like_other_update_by_slug_methods(self, db):
        # Matches update_database_status's precedent: an UPDATE that matches
        # no row is silently a no-op, not an error.
        db.update_database_credentials("nope", "user", "pass")
        assert db.get_database("nope") is None
