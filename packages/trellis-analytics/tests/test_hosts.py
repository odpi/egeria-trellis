"""The three hosts of curation_coverage (brief 2b) and the presentation spec (2d).

Script, papermill notebook and Portal tile each run the same routine over the same scratch registry;
nothing a host shows is computed beside the routine's result.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from trellis_analytics import __version__
from trellis_analytics.readers.ods import OdsReader
from trellis_analytics.routines import curation_coverage

EX = Path(__file__).resolve().parents[1] / "examples"
SLUG = "demo"


def els(*tables):
    return json.dumps({"tables": list(tables)})


@pytest.fixture
def measured_url(make_ods):
    return make_ods(
        slug=SLUG, schemas=["sales", "hr", "ops", "fin", "arch", "blk", "lone"],
        proofs=[("schema", "sales", "elements_read_back", "2026-10-05T10:00:00Z", els("a", "b", "c")),
                ("schema", "hr", "elements_read_back", "2026-10-05T10:01:00Z", els("p", "q")),
                ("database", "", "report_published", "2026-10-05T10:02:00Z", json.dumps({"annotation_count": 76})),
                ("schema", "blk", "leave_out_blocked", "2026-10-05T11:00:00Z", "{}")],
        events=[("sales", "catalogue", "person"), ("hr", "catalogue", "person"), ("ops", "catalogue", "person"),
                ("fin", "catalogue", "rule-1"), ("arch", "leave_out", "person"), ("blk", "leave_out", "person")])


def counts_of(result_dict: dict) -> dict:
    """{group.line: (count, rows)} from a CoverageResult dict: what every host must agree on."""
    return {f"{g['key']}.{l['key']}": (l["count"], l["rows"]) for g in result_dict["groups"] for l in g["lines"]}


# ── notebook host ──

def run_notebook(tmp_path, url, slug=SLUG, resource_type="database"):
    import papermill as pm
    out = tmp_path / "out.ipynb"
    pm.execute_notebook(str(EX / "curation_coverage.ipynb"), str(out),
                        parameters={"slug": slug, "ods_url": url, "resource_type": resource_type},
                        kernel_name="python3", progress_bar=False)
    import nbformat
    nb = nbformat.read(str(out), as_version=4)
    text = "".join(o.get("text", "") for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", [])
                   if o.get("output_type") == "stream" and o.get("name") == "stdout")
    return nb, text


def notebook_result(text):
    return json.loads(text.split("RESULT_JSON ", 1)[1].splitlines()[0])


def test_notebook_has_a_parameters_cell_with_slug_and_ods_url():
    import nbformat
    nb = nbformat.read(str(EX / "curation_coverage.ipynb"), as_version=4)
    tagged = [c for c in nb.cells if "parameters" in c.metadata.get("tags", [])]
    assert len(tagged) == 1
    assert "slug =" in tagged[0].source and "ods_url =" in tagged[0].source


def test_notebook_prints_the_script_hosts_numbers_and_the_state(tmp_path, measured_url):
    _nb, text = run_notebook(tmp_path, measured_url)
    script = subprocess.run([sys.executable, str(EX / "curation_coverage.py"), measured_url, SLUG],
                            capture_output=True, text=True, check=True).stdout
    script_result = json.loads(script.split("\n", 1)[1])
    nb_result = notebook_result(text)
    assert nb_result["envelope"]["result_state"] == "measured"
    assert "measured" in text                                  # the envelope's state, as words
    assert counts_of(nb_result) == counts_of(script_result)
    assert counts_of(nb_result)["published.schemas"][0] == 2


def test_notebook_shows_the_state_word_when_not_established(tmp_path):
    _nb, text = run_notebook(tmp_path, f"sqlite:///{tmp_path}/does-not-exist.sqlite")
    assert notebook_result(text)["envelope"]["result_state"] == "not_established"
    assert "not established" in text
