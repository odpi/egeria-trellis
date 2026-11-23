"""TR-61: `survey-definition` runs as the person who logged in.

`resource-explorer login` caches a session, but `survey-definition` never asked for it (every other Egeria command
calls `cli_session.require_and_activate(console)`), so right after a successful login it said "no one is signed in".
The three survey-definition commands (projects, `database`, `filesystem`) read the definition from Egeria, so each
needs the session: without one it exits 2 with the one-line sign-in message and runs nothing; with one it runs as that
person (the caller context is set when the run starts).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resource_explorer.cli.main import app
from resource_explorer.egeria_identity import EgeriaIdentity

# the three commands: argv, and the registry answers each one needs
COMMANDS = {
    "project": ["survey-definition", "myproj"],
    "database": ["database", "survey-definition", "mydb"],
    "filesystem": ["filesystem", "survey-definition", "myfs"],
}
RESULTS = {"errors": [], "steps": [], "process_qualified_name": "GovernanceActionProcess::Demo"}


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def registry(monkeypatch):
    r = MagicMock()
    r.get.return_value = MagicMock()
    r.database_exists.return_value = True
    r.get_filesystem.return_value = MagicMock()
    r.get_database.return_value = None
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: r)
    return r


@pytest.fixture(autouse=True)
def _no_activity_log(monkeypatch):
    monkeypatch.setattr("resource_explorer.activity_logger.log_survey", lambda *a, **k: None)


def _signed_out():
    return patch("resource_explorer.cli.session.load_session", return_value=None)


def _signed_in(user="erinoverview"):
    record = MagicMock(is_expired=False, token="app-jwt", user_id=user)
    return (patch("resource_explorer.cli.session.load_session", return_value=record),
            patch("resource_explorer.cli.session._identity_from",
                  return_value=EgeriaIdentity(user_id=user, token="egeria-token")))


@pytest.mark.parametrize("which", sorted(COMMANDS))
def test_without_a_session_it_exits_2_with_the_login_line_and_runs_nothing(runner, registry, which):
    with _signed_out(), patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
                              return_value=RESULTS) as run:
        result = runner.invoke(app, COMMANDS[which])
    assert result.exit_code == 2, result.output
    assert "resource-explorer login" in result.output
    run.assert_not_called()


@pytest.mark.parametrize("which", sorted(COMMANDS))
def test_after_login_it_runs_as_that_person(runner, registry, which):
    from resource_explorer.a2a_auth import current_caller

    seen = {}

    def run(**kwargs):
        caller = current_caller.get()
        seen["user"] = caller.user_id if caller else None
        seen["token"] = caller.egeria_token if caller else None
        return RESULTS

    live, ident = _signed_in()
    with live, ident, patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
                            side_effect=run):
        result = runner.invoke(app, COMMANDS[which])
    assert result.exit_code == 0, result.output
    assert seen == {"user": "erinoverview", "token": "egeria-token"}
    assert "no one is signed in" not in result.output.lower()
