"""CLI parity for the workflows and the run queue — plan §3.

"The CLI has no `analysis` command at all" was the plan's finding, and it held
for discovery and curate materialization too, purely because that code lived
inside route modules. These tests assert the commands exist, that each one
reaches the *same* workflow function the route reaches, and that `--queue`
enqueues instead of running — which is the whole difference between the CLI
being a second implementation and the CLI being a second caller.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resource_explorer.cli.main import app
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    r = ProjectRegistry(db_path=str(tmp_path / "cli.db"))
    r.add(Project(
        slug="myproj", display_name="My Project",
        github_url="https://github.com/test/myproj", description="A test repo.",
    ))
    # Every command builds its own registry; point them all at this one so a
    # CLI test can never touch the shared live registry.
    monkeypatch.setattr("resource_explorer.cli.runs_commands._registry", lambda: r)
    return r


class TestAnalysisCommands:
    def test_run_calls_the_workflow_inline(self, runner, registry):
        from resource_explorer.workflows.analysis import AnalysisRunResult

        with patch("resource_explorer.workflows.analysis.resolve_analysis_plan",
                   return_value=(False, ["repo_security"])), \
             patch("resource_explorer.workflows.analysis.run_analysis",
                   return_value=AnalysisRunResult(status="ok", summary="3 annotation(s).")) as run:
            result = runner.invoke(app, ["analysis", "run", "myproj", "security_scan"])
        assert result.exit_code == 0, result.output
        assert "3 annotation(s)" in result.output
        run.assert_called_once()

    def test_an_unmapped_analysis_exits_nonzero(self, runner, registry):
        with patch("resource_explorer.workflows.analysis.resolve_analysis_plan",
                   return_value=(False, None)):
            result = runner.invoke(app, ["analysis", "run", "myproj", "nonsense"])
        assert result.exit_code == 1
        assert "no mapped survey step" in result.output

    def test_a_failed_publish_is_reported_distinctly_from_no_publish(self, runner, registry):
        """`published=False` needs a retry; `published=None` does not. Printing
        the same line for both is exactly the failure the three-state value
        exists to avoid."""
        from resource_explorer.workflows.analysis import AnalysisRunResult

        with patch("resource_explorer.workflows.analysis.resolve_analysis_plan",
                   return_value=(False, ["repo_security"])), \
             patch("resource_explorer.workflows.analysis.run_analysis",
                   return_value=AnalysisRunResult(status="ok", summary="ok", published=False)):
            result = runner.invoke(app, ["analysis", "run", "myproj", "security_scan"])
        assert "Auto-publish to Egeria failed" in result.output

    def test_queue_enqueues_and_runs_nothing(self, runner, registry):
        with patch("resource_explorer.workflows.analysis.run_analysis") as run:
            result = runner.invoke(
                app, ["analysis", "run", "myproj", "security_scan", "--queue"])
        assert result.exit_code == 0, result.output
        run.assert_not_called()
        rows = registry.list_runs(state="queued")
        assert len(rows) == 1
        assert rows[0]["kind"] == "analysis_run"

    def test_stage_batch_queues_the_derived_step_keys(self, runner, registry):
        with patch("resource_explorer.workflows.analysis.resolve_stage_step_keys",
                   return_value=(["repo_health", "repo_language"], 2)):
            result = runner.invoke(
                app, ["analysis", "stage-batch", "myproj", "scouting", "--queue"])
        assert result.exit_code == 0, result.output
        import json

        target = json.loads(registry.list_runs(state="queued")[0]["target"])
        assert target["step_keys"] == ["repo_health", "repo_language"]

    def test_a_stage_with_no_runnable_analyses_exits_nonzero(self, runner, registry):
        with patch("resource_explorer.workflows.analysis.resolve_stage_step_keys",
                   return_value=([], 0)):
            result = runner.invoke(app, ["analysis", "stage-batch", "myproj", "enrichment"])
        assert result.exit_code == 1
        assert "no batch-runnable" in result.output


class TestScoutCommand:
    def test_run_calls_the_workflow(self, runner, registry):
        from resource_explorer.workflows.scouting import ScoutingScanResult

        with patch("resource_explorer.workflows.scouting.run_scouting_scan",
                   return_value=ScoutingScanResult(status="ok", slug="myproj",
                                                   message="Coarse scan complete.")):
            result = runner.invoke(app, ["scout", "run", "myproj"])
        assert result.exit_code == 0, result.output
        assert "Coarse scan complete" in result.output

    def test_queue_enqueues_a_scouting_scan(self, runner, registry):
        result = runner.invoke(app, ["scout", "run", "myproj", "--queue"])
        assert result.exit_code == 0, result.output
        assert registry.list_runs(state="queued")[0]["kind"] == "scouting_scan"


class TestDiscoveryCommands:
    def test_search_renders_the_enriched_results(self, runner, registry):
        with patch("resource_explorer.workflows.discovery.run_search_query",
                   return_value=[{
                       "full_name": "odpi/egeria", "html_url": "https://github.com/odpi/egeria",
                       "description": "", "stars": 800, "language": "Java",
                       "license": "apache-2.0", "forks": 200,
                   }]):
            result = runner.invoke(app, ["discovery", "search", "egeria", "--limit", "5"])
        assert result.exit_code == 0, result.output
        assert "odpi/egeria" in result.output

    def test_a_bad_query_exits_nonzero_with_the_reason(self, runner, registry):
        from resource_explorer.workflows.discovery import DiscoveryError

        with patch("resource_explorer.workflows.discovery.run_search_query",
                   side_effect=DiscoveryError("At least one search filter is required.")):
            result = runner.invoke(app, ["discovery", "search"])
        assert result.exit_code == 1
        assert "At least one search filter" in result.output

    def test_expand_org_prints_urls_and_says_when_it_truncated(self, runner, registry):
        from resource_explorer.workflows.discovery import OrgExpansion

        with patch("resource_explorer.workflows.discovery.expand_org",
                   return_value=OrgExpansion(org="odpi",
                                             urls=["https://github.com/odpi/egeria"],
                                             truncated=True)):
            result = runner.invoke(app, ["discovery", "expand-org", "odpi"])
        assert result.exit_code == 0, result.output
        assert "odpi/egeria" in result.output
        assert "Truncated" in result.output

    def test_expand_org_queue_enqueues(self, runner, registry):
        result = runner.invoke(app, ["discovery", "expand-org", "odpi", "--queue"])
        assert result.exit_code == 0, result.output
        assert registry.list_runs(state="queued")[0]["kind"] == "discovery_expand"


@pytest.mark.usefixtures("mock_egeria_client_connections")
class TestCurateCommand:
    """`curate materialize` requires a signed-in session as of 2026-09-04.

    It creates real Egeria elements and stamps them with an owner, so it needs
    a person to be that owner (plan §4). These tests supply one rather than
    working around the gate — `_signed_in` gives the command exactly what a
    real `resource-explorer login` would leave behind, so what they exercise is
    still the routing, not the gate. `TestTheGate` below covers the other side.
    """

    @pytest.fixture(autouse=True)
    def _signed_in(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("RE_JWT_SECRET", "cli-curate-test-secret")
        import jwt as _jwt

        from resource_explorer import auth as re_auth
        from resource_explorer.cli import session as cli_session

        # jwt_secret() reads through a cached BaseSettings instance (see
        # auth.py's _AuthSecretsConfig) — without resetting it here, a test
        # running after this fixture set RE_JWT_SECRET would keep seeing this
        # value instead of its own.
        re_auth.reset_auth_secrets_cache()
        egeria_token = _jwt.encode({"sub": "dan", "exp": 9999999999}, "irrelevant",
                                   algorithm="HS256")
        cli_session.save_login("dan", egeria_token)
        yield
        cli_session.activate(None)
        re_auth.reset_auth_secrets_cache()

    def test_without_a_session_it_exits_2_and_names_the_fix(self, runner, registry,
                                                            tmp_path, monkeypatch):
        """Exit 2, not 1: "you are not signed in" is a different outcome from
        "the command ran and failed", and a wrapping script must be able to
        tell them apart without parsing the message."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
        result = runner.invoke(app, ["curate", "materialize", "myproj", "src/api"])
        assert result.exit_code == 2
        assert "resource-explorer login" in result.output

    def test_a_component_id_routes_to_the_component_materializer(self, runner, registry):
        with patch("resource_explorer.workflows.curate.materialize_component_if_accepted",
                   return_value={"status": "ok", "guid": "g1"}) as comp, \
             patch("resource_explorer.workflows.curate.materialize_blueprint_if_accepted") as bp:
            result = runner.invoke(app, ["curate", "materialize", "myproj", "src/api"])
        assert result.exit_code == 0, result.output
        comp.assert_called_once()
        bp.assert_not_called()

    def test_a_double_colon_id_routes_to_the_blueprint_materializer(self, runner, registry):
        """`perspective::cluster_name` is the key add_blueprint_verdict already
        builds, not a new convention invented for the CLI."""
        with patch("resource_explorer.workflows.curate.materialize_component_if_accepted") as comp, \
             patch("resource_explorer.workflows.curate.materialize_blueprint_if_accepted",
                   return_value={"status": "ok", "guid": "g2"}) as bp:
            result = runner.invoke(app, ["curate", "materialize", "myproj", "runtime::Ingest"])
        assert result.exit_code == 0, result.output
        bp.assert_called_once_with(registry, "repo", "myproj", "runtime", "Ingest", "accepted")
        comp.assert_not_called()

    def test_a_materialization_error_exits_nonzero(self, runner, registry):
        with patch("resource_explorer.workflows.curate.materialize_component_if_accepted",
                   return_value={"status": "error", "error": "no component finding"}):
            result = runner.invoke(app, ["curate", "materialize", "myproj", "src/api"])
        assert result.exit_code == 1


class TestRunsCommands:
    def test_list_says_when_the_queue_is_empty(self, runner, registry):
        """"No rows matched" printed as an empty table reads as a rendering
        bug; say which answer this is."""
        result = runner.invoke(app, ["runs", "list"])
        assert result.exit_code == 0, result.output
        assert "No runs" in result.output

    def test_list_shows_queued_rows(self, runner, registry):
        registry.enqueue_run("scouting_scan", {"slug": "myproj"})
        result = runner.invoke(app, ["runs", "list", "--state", "queued"])
        assert result.exit_code == 0, result.output
        assert "scouting_scan" in result.output

    def test_list_rejects_an_unknown_state(self, runner, registry):
        result = runner.invoke(app, ["runs", "list", "--state", "wandering"])
        assert result.exit_code == 1
        assert "Unknown state" in result.output

    def test_show_prints_the_row(self, runner, registry):
        run_id = registry.enqueue_run("scouting_scan", {"slug": "myproj"})
        result = runner.invoke(app, ["runs", "show", run_id])
        assert result.exit_code == 0, result.output
        assert run_id in result.output

    def test_show_of_an_unknown_id_exits_nonzero(self, runner, registry):
        result = runner.invoke(app, ["runs", "show", "nope"])
        assert result.exit_code == 1

    def test_cancel_cancels_a_queued_row(self, runner, registry):
        run_id = registry.enqueue_run("scouting_scan", {"slug": "myproj"})
        result = runner.invoke(app, ["runs", "cancel", run_id])
        assert result.exit_code == 0, result.output
        assert registry.get_run(run_id)["state"] == "cancelled"

    def test_cancel_refuses_a_claimed_row_and_explains_why(self, runner, registry):
        run_id = registry.enqueue_run("scouting_scan", {"slug": "myproj"})
        registry.claim_next_run("host:1", {"pid": 1})
        result = runner.invoke(app, ["runs", "cancel", run_id])
        assert result.exit_code == 1
        # Substrings short enough to survive rich's line wrapping at 80 columns.
        assert "is claimed, not queued" in result.output
        assert "nothing was" in result.output
        assert registry.get_run(run_id)["state"] == "claimed"

    def test_enqueue_queues_arbitrary_work(self, runner, registry):
        result = runner.invoke(app, [
            "runs", "enqueue", "analysis_run",
            '{"slug": "myproj", "analysis_id": "security_scan"}',
        ])
        assert result.exit_code == 0, result.output
        rows = registry.list_runs(state="queued")
        assert rows[0]["kind"] == "analysis_run"

    def test_enqueue_rejects_an_unknown_kind(self, runner, registry):
        result = runner.invoke(app, ["runs", "enqueue", "teleport", "{}"])
        assert result.exit_code == 1
        assert "Unknown kind" in result.output

    def test_enqueue_rejects_non_json_target(self, runner, registry):
        result = runner.invoke(app, ["runs", "enqueue", "scouting_scan", "not-json"])
        assert result.exit_code == 1
        assert "not valid JSON" in result.output

    def test_enqueue_can_attribute_to_a_user(self, runner, registry):
        """The step-5 hook is reachable today even though nothing sets a real
        user id yet — an operator can queue attributed work and watch fairness
        apply."""
        runner.invoke(app, ["runs", "enqueue", "scouting_scan",
                            '{"slug": "myproj"}', "--requested-by", "alice"])
        assert registry.list_runs(state="queued")[0]["requested_by"] == "alice"
