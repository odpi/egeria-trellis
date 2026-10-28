"""The CLI prints which registry it will use, once, to stderr."""
from __future__ import annotations

from typer.testing import CliRunner

from resource_explorer.cli.main import app

SHARED = "postgresql://egeria_advisor:s3cretpw@localhost:5442/egeria_advisor?options=-csearch_path%3Dresource_explorer"


def _run(monkeypatch, url):
    """Invoke a no-op probe command: only the root callback can print the line,
    and nothing touches a registry, a session file or Egeria."""
    import resource_explorer.config as config
    monkeypatch.setenv("REGISTRY_DATABASE_URL", url)
    config._config = None

    @app.command(name="zz-registry-line-probe")
    def _probe():
        print("probe-ran")

    try:
        return CliRunner().invoke(app, ["zz-registry-line-probe"])
    finally:
        app.registered_commands[:] = [
            c for c in app.registered_commands if c.name != "zz-registry-line-probe"]


def test_non_shared_registry_is_announced_once(monkeypatch, tmp_path):
    r = _run(monkeypatch, f"sqlite:///{tmp_path}/cli.db")
    assert r.output.count("registry: sqlite:///cli.db") == 1


def test_shared_default_is_announced_as_shared_without_credentials(monkeypatch):
    r = _run(monkeypatch, SHARED)
    assert r.output.count("registry: localhost:5442/egeria_advisor (shared)") == 1
    assert "s3cretpw" not in r.output
