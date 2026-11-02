"""pyegeria 6.2.19 refuses a dotted analytic_function path: every shipped report spec, ours included, names its
functions by registered name (scripts/specs_check.py, the 2026-10-31 registry walk: 424 specs, bad [])."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("pyegeria.view.format_set_executor")

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "specs_check.py"


def test_every_shipped_report_spec_names_registered_functions_only():
    spec = importlib.util.spec_from_file_location("specs_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.check()
    assert result["specs"] > 0 and result["refs"] > 0, result      # a walk that found nothing proves nothing
    assert result["bad"] == [], result["bad"]
