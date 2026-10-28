"""Host 3: the Portal tile, a FormatSet run by pyegeria's real exec_report_spec (brief 2b, 2d).

Nothing is faked. Since pyegeria 6.2.2 (ISSUE-137) exec_report_spec builds an EgeriaTech only for a routine whose
leading parameters name a client; the tile's routine names none, so the fixture makes any EgeriaTech construction
fail the test instead of faking one.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from trellis_analytics import __version__
from test_hosts import EX, SLUG, counts_of, measured_url, notebook_result, run_notebook  # noqa: F401

fse = pytest.importorskip("pyegeria.view.format_set_executor")
LABEL = "Trellis - Curation Coverage"


class NoEgeria:
    """The tile reads only RE's store: building an Egeria client at all is the regression (ISSUE-137)."""
    def __init__(self, *a, **k):
        raise AssertionError("exec_report_spec built an EgeriaTech for a routine that takes no client")


@pytest.fixture
def tile(monkeypatch):
    from pyegeria.view import base_report_formats as brf
    from trellis_analytics.portal import report_specs
    monkeypatch.setattr(fse, "EgeriaTech", NoEgeria)
    brf.register_report_specs(report_specs(), source="test")
    yield lambda url, fmt="DICT", **p: fse.exec_report_spec(
        LABEL, output_format=fmt, params={"slug": SLUG, "ods_url": url, **p})
    brf.clear_runtime_report_specs()


def kpi_counts(data: dict) -> dict:
    return {k["key"]: (k["value"], k["rows"]) for k in data["kpis"]}


def test_tile_counts_and_subtitle_come_from_the_routine(tile, measured_url, monkeypatch):
    import trellis_analytics.portal as portal
    rendered = []                       # the result the tile itself rendered, so the clock cannot tick between two runs
    real = portal.curation_coverage
    monkeypatch.setattr(portal, "curation_coverage", lambda *a, **k: rendered.append(real(*a, **k)) or rendered[-1])
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.routines import curation_coverage
    routine = curation_coverage(OdsReader(measured_url), "database", SLUG)
    out = tile(measured_url)
    assert out["kind"] == "json" and len(rendered) == 1
    data = out["data"]
    assert data["manifest"]["as_of"] == rendered[0].envelope.measured_at      # from the envelope, not the clock
    assert kpi_counts(data) == counts_of(routine.to_dict())      # same counts and the same ids
    assert data["subtitle"].startswith("measured")
    assert data["provenance"].startswith(f"from trellis-analytics {__version__} · curation coverage · as of ")
    assert data["manifest"]["version"] == __version__ == routine.envelope.producer_version
    # (as_of is checked above against the run the tile rendered; a second, separate run can tick a second)
    assert data["manifest"]["read_back_at"] == routine.as_of == "2026-10-05T10:02:00Z"
    # the bar is published against not yet published
    assert data["Published"] == 2 and data["Not yet published"] == 4
    # the bar's second value is the routine's own not-yet lines added up, not a separate count
    assert data["Not yet published"] == sum(l.count for l in routine.group("not_yet").lines)


def test_tile_bar_chart_carries_the_two_counts(tile, measured_url):
    out = tile(measured_url, fmt="BAR")
    assert out["kind"] == "json" and "$schema" in out["data"]
    text = json.dumps(out["data"])
    assert '"Published"' in text and '"Not yet published"' in text
    vals = {v["category"]: v["count"] for v in out["data"]["data"]["values"]}
    assert vals == {"Published": 2, "Not yet published": 4}
    # pyegeria 6.2.3 (ISSUE-139): the envelope's state rides on the chart itself, as its subtitle.
    assert out["data"]["title"]["subtitle"] == out["envelope"]["subtitle"] == "measured"
    assert out["envelope"]["state"] == "measured"
    assert "measured" in json.dumps(out["data"]["title"])


def test_dict_output_carries_the_envelope_beside_the_tile(tile, measured_url):
    """ISSUE-139: the executor reads the state off the tile's own state/state_reason/subtitle keys."""
    out = tile(measured_url)
    assert out["envelope"] == {"state": "measured", "reason": out["data"]["state_reason"],
                               "subtitle": out["data"]["subtitle"]}


@pytest.mark.parametrize("fmt", ["DICT", "BAR"])
def test_not_established_shows_its_word_never_a_zero_chart(tile, tmp_path, fmt):
    out = tile(f"sqlite:///{tmp_path}/missing.sqlite", fmt=fmt)
    assert out["kind"] == "json"
    data = out["data"]
    assert "$schema" not in data                               # not a chart
    assert data["subtitle"].startswith("not established") and "could not open" in data["subtitle"]
    assert data["state"] == "not_established"
    assert "Published" not in data and data["kpis"] == []


@pytest.mark.parametrize("fmt", ["DICT", "BAR"])
def test_not_applicable_shows_its_word_never_a_zero_chart(tile, measured_url, fmt):
    out = tile(measured_url, fmt=fmt, resource_type="file_system")
    data = out["data"]
    assert "$schema" not in data and data["state"] == "not_applicable"
    assert data["subtitle"].startswith("not applicable") and data["kpis"] == []


def test_nothing_found_is_its_own_word(tile, make_ods):
    url = make_ods(slug=SLUG, schemas=["a", "b"])
    data = tile(url)["data"]
    assert data["state"] == "nothing_found" and data["subtitle"].startswith("nothing found")
    assert data["Published"] == 0 and data["Not yet published"] == 2


def test_the_spec_loads_through_pyegeria_report_spec_modules(monkeypatch):
    from pyegeria.view import base_report_formats as brf
    monkeypatch.setenv("PYEGERIA_REPORT_SPEC_MODULES", "trellis_analytics.portal:report_specs")
    try:
        skipped = brf.refresh_report_specs()
        assert [s for s in skipped if "trellis_analytics" in s[0]] == []
        assert LABEL in brf.get_report_registry()
    finally:
        monkeypatch.delenv("PYEGERIA_REPORT_SPEC_MODULES")
        brf.refresh_report_specs()


def test_three_hosts_same_numbers(tile, tmp_path, measured_url):
    script = subprocess.run([sys.executable, str(EX / "curation_coverage.py"), measured_url, SLUG],
                            capture_output=True, text=True, check=True).stdout
    from_script = counts_of(json.loads(script.split("\n", 1)[1]))
    _nb, text = run_notebook(tmp_path, measured_url)
    from_notebook = counts_of(notebook_result(text))
    from_tile = kpi_counts(tile(measured_url)["data"])
    assert from_script == from_notebook == from_tile
    assert from_tile["published.schemas"][0] == 2 and from_tile["published.tables"][0] == 5
    assert all(rows for (n, rows) in from_tile.values() if n)         # every count keeps its ids
