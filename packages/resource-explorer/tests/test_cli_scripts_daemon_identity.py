"""CLI scripts that build a SurveyDefinitionReader run as the daemon (no signed-in person in a CLI)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"cli_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeReader:
    seen: list = []

    def __init__(self, *a, **k):
        from resource_explorer.egeria_clients import current_principal

        # what the real reader's principal() does: raises NoCallerIdentity with no daemon declared
        type(self).seen.append(current_principal())

    def find_process_guid_by_name(self, name):
        return None

    def __getattr__(self, name):
        return lambda *a, **k: ([] if name.startswith(("get_", "list_")) else None)


@pytest.mark.parametrize("script", [
    "reconcile_survey_definition_links",
    "reconcile_survey_definition_scopes",
    "reconcile_database_survey_definition_links",
    "reconcile_database_survey_definition_scopes",
])
def test_reconciler_main_reaches_reader_as_daemon(script, monkeypatch):
    from resource_explorer.egeria_clients import KIND_DAEMON
    from resource_explorer.surveyors import survey_definition_reader as sdr

    _FakeReader.seen = []
    monkeypatch.setattr(sdr, "SurveyDefinitionReader", _FakeReader)
    monkeypatch.setattr(sys, "argv", [script] + ([] if "scopes" in script else ["--dry-run"]))
    mod = _load(script)
    try:
        mod.main()
    except SystemExit:
        pass  # a scopes script exits non-zero when its fake finds nothing; only the identity matters
    # raises NoCallerIdentity if main() is not wrapped in acting_as(Daemon(...))
    assert _FakeReader.seen and all(p.kind == KIND_DAEMON for p in _FakeReader.seen)
