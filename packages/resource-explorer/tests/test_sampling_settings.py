"""Sampling settings at every scope (design §5.8): global → per resource type → per resource → per run.

Before 2026-10-20 only two slots of `resolve_sampling_config` were ever filled, and a run's own override
was passed into the GLOBAL slot, so a resolved config said `scope: global` for a setting nobody made
globally, and no deployment, resource type or resource could set anything at all.
"""
from __future__ import annotations

import json
import logging

import pytest
from resource_explorer.config import SamplingSettings
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.sampling import (
    HEAD,
    SamplingConfigError,
    check_sampling_overrides,
    resolve_sampling_config,
    scoped_settings,
)
from typer.testing import CliRunner

from tests.test_postgres_column_profile import _run


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    reg.register_database(DatabaseEntity(slug="coco_ods", display_name="Coco ODS", db_type="postgresql",
                                         host="localhost", port=5432, database_name="coco_ods"))
    return reg


@pytest.fixture
def settings(monkeypatch):
    """Replace the process's sampling settings for one test."""
    from resource_explorer import config

    def _set(**kw):
        monkeypatch.setattr(config.get_config(), "sampling", SamplingSettings(**kw))
    _set()
    return _set


# ── the deployment-wide settings ──────────────────────────────────────────────────────────────────────────────


def test_global_and_per_type_settings_are_read_as_json_objects():
    s = SamplingSettings(defaults_json='{"max_rows": 500}',
                         by_resource_type_json='{"database": {"strategy": "head"}, "filesystem": {"max_rows": 9}}')
    assert s.defaults() == {"max_rows": 500}
    assert s.for_resource_type("database") == {"strategy": "head"}
    assert s.for_resource_type("repo") == {}


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '"head"'])
def test_an_unreadable_setting_is_ignored_with_one_warning_never_a_crash(bad, caplog):
    with caplog.at_level(logging.WARNING):
        assert SamplingSettings(defaults_json=bad).defaults() == {}
    assert any("SAMPLING_DEFAULTS" in r.getMessage() for r in caplog.records)


# ── a person's setting is strict ────────────────────────────────────────────────────────────────────────────


def test_an_unknown_key_is_refused_by_name_not_stored_and_silently_ignored():
    with pytest.raises(SamplingConfigError, match="stratgy"):
        check_sampling_overrides({"stratgy": "head"})


def test_the_design_docs_time_budget_spelling_is_accepted():
    check_sampling_overrides({"time_budget": 30, "strategy": "head"})


def test_a_bad_value_is_refused():
    with pytest.raises(SamplingConfigError):
        check_sampling_overrides({"strategy": "everything"})


def test_a_resource_setting_is_stored_read_and_cleared(registry):
    assert registry.get_resource_sampling("database", "coco_ods") == {}
    registry.set_resource_sampling("database", "coco_ods", {"strategy": "head", "max_rows": 50}, updated_by="dan")
    assert registry.get_resource_sampling("database", "coco_ods") == {"strategy": "head", "max_rows": 50}
    registry.set_resource_sampling("database", "coco_ods", None)
    assert registry.get_resource_sampling("database", "coco_ods") == {}


def test_a_refused_setting_leaves_the_stored_one_as_it_was(registry):
    registry.set_resource_sampling("database", "coco_ods", {"max_rows": 50}, updated_by="dan")
    with pytest.raises(SamplingConfigError):
        registry.set_resource_sampling("database", "coco_ods", {"stratgy": "head"}, updated_by="dan")
    assert registry.get_resource_sampling("database", "coco_ods") == {"max_rows": 50}


# ── the scopes resolve narrowest-wins, and say which ────────────────────────────────────────────────────────


def test_each_stored_scope_is_supplied_and_the_narrowest_wins(registry, settings):
    settings(defaults_json='{"max_rows": 1000, "max_values": 10}',
             by_resource_type_json='{"database": {"max_rows": 500}}')
    registry.set_resource_sampling("database", "coco_ods", {"max_values": 20}, updated_by="dan")
    scopes = scoped_settings(registry, "database", "coco_ods")
    assert scopes == {"global_settings": {"max_rows": 1000, "max_values": 10},
                      "resource_type_settings": {"max_rows": 500},
                      "resource_settings": {"max_values": 20}}
    config = resolve_sampling_config(purpose="matching", resource_slug="coco_ods", **scopes)
    assert (config.max_rows, config.max_values, config.scope) == (500, 20, "resource")


def test_the_column_profile_step_resolves_the_stored_scopes(registry, settings):
    registry.set_resource_sampling("database", "coco_ods", {"strategy": HEAD, "max_values": 25}, updated_by="dan")
    _, result = _run(scope_settings=scoped_settings(registry, "database", "coco_ods"))
    config = result["sampling"]["config"]
    assert (config["strategy"], config["max_values"], config["scope"]) == (HEAD, 25, "resource")


def test_a_runs_own_override_is_the_run_scope_not_global(registry, settings):
    """The old wiring passed it into the global slot; the resolved config then said `global`."""
    settings(defaults_json='{"max_values": 10}')
    registry.set_resource_sampling("database", "coco_ods", {"max_values": 20}, updated_by="dan")
    _, result = _run(scope_settings=scoped_settings(registry, "database", "coco_ods"),
                     sampling_overrides={"max_values": 30})
    config = result["sampling"]["config"]
    assert (config["max_values"], config["scope"]) == (30, "run")


def test_a_request_parameter_with_an_unknown_sampling_key_is_refused():
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY,
    )
    from resource_explorer.surveyors.step_parameters import coerce_step_parameters

    got = coerce_step_parameters(DATABASE_STEP_REGISTRY["postgres_column_profile"].parameters,
                                 {"sampling": '{"stratgy": "head"}'})
    assert got.accepted == {} and "stratgy" in got.invalid[0]


# ── the CLI ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_cli_sets_shows_and_clears_a_databases_settings(registry, settings, monkeypatch):
    from resource_explorer import registry as registry_module
    from resource_explorer.cli.main import app

    # The command imports ProjectRegistry from the module when it runs: hand it this test's registry. (Never
    # patch the class's __new__: undoing that leaves object.__new__ set explicitly and breaks every later test.)
    monkeypatch.setattr(registry_module, "ProjectRegistry", lambda *a, **k: registry)
    runner = CliRunner()

    out = runner.invoke(app, ["database", "sampling", "coco_ods", "--set", json.dumps({"strategy": "head"})])
    assert out.exit_code == 0, out.output
    assert "stored" in out.output and '"strategy": "head"' in out.output
    assert registry.get_resource_sampling("database", "coco_ods") == {"strategy": "head"}

    out = runner.invoke(app, ["database", "sampling", "coco_ods", "--set", '{"stratgy": "head"}'])
    assert out.exit_code == 1 and "Not stored" in out.output and "stratgy" in out.output

    out = runner.invoke(app, ["database", "sampling", "coco_ods", "--clear"])
    assert out.exit_code == 0 and registry.get_resource_sampling("database", "coco_ods") == {}
    assert "this database (coco_ods): not set" in out.output
