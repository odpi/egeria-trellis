"""pyegeria 6.2.17 (#516, ISSUE-169): a report spec's analytic_function resolves only as a registry name, a dotted
path some registered entry declares, or a dotted path under an allowed prefix (pyegeria, trellis_analytics).
Everything this library names must still resolve; a name outside those is refused before any import."""
import pytest

fse = pytest.importorskip("pyegeria.view.format_set_executor")

from trellis_analytics.manifests import CONTEXT_PACK, CURATION_COVERAGE, CURATION_COVERAGE_TILE  # noqa: E402
from trellis_analytics.portal import TILE_FUNCTION  # noqa: E402


@pytest.mark.parametrize("name", [
    TILE_FUNCTION,                                   # the Portal tile's FormatSet (portal.py, examples/curation_coverage_tile.md)
    "trellis_analytics.routines.curation_coverage",  # the walking-skeleton brief's spelling
    CURATION_COVERAGE.function, CONTEXT_PACK.function, CURATION_COVERAGE_TILE.function,
    "curation_coverage", "context_pack", "curation_coverage_tile",   # registry names, from the pyegeria.analytics entry points
])
def test_every_name_the_library_uses_resolves(name, monkeypatch):
    monkeypatch.delenv("PYEGERIA_ANALYTIC_MODULE_PREFIXES", raising=False)
    assert callable(fse._resolve_analytic_function(name))


def test_an_unregistered_path_outside_the_prefixes_is_refused(monkeypatch):
    monkeypatch.delenv("PYEGERIA_ANALYTIC_MODULE_PREFIXES", raising=False)
    with pytest.raises(fse.AnalyticFunctionRefused):
        fse._resolve_analytic_function("os.system")


def test_a_registered_name_resolves_to_the_tile_function(monkeypatch):
    """Q2(b): the tile's report spec names `curation_coverage_tile`, which resolves to the function its manifest
    declares, with no allowlist involved."""
    monkeypatch.setenv("PYEGERIA_ANALYTIC_MODULE_PREFIXES", "nothing_allowed")
    from trellis_analytics.portal import curation_coverage_tile
    assert fse._resolve_analytic_function(TILE_FUNCTION) is curation_coverage_tile


def test_every_report_spec_the_library_ships_names_a_registered_function():
    """Q2(b), project owner 2026-10-31: a report spec names its function by registered name, never a Python path --
    portal.report_specs() and the Dr.Egeria document of the same spec (examples/curation_coverage_tile.md)."""
    from pathlib import Path

    from pyegeria.view.analytic_registry import get_analytic_registry
    from trellis_analytics.portal import report_specs
    registered = set(get_analytic_registry())
    named = [fs.action.analytic_function for fs in report_specs().values()]
    doc = (Path(__file__).resolve().parents[1] / "examples" / "curation_coverage_tile.md").read_text()
    named.append(doc.split("### Analytic Function\n", 1)[1].split("\n", 1)[0].strip())
    assert named == [TILE_FUNCTION, TILE_FUNCTION]
    assert all("." not in n and n in registered for n in named)


def test_a_report_spec_naming_a_dotted_path_is_refused_a_registered_name_is_not():
    """pyegeria 6.2.19 (ISSUE-173): a report spec's dotted path is refused, naming the registered name to use; a
    registered name resolves. pyegeria decides this; the test holds trellis's names to it."""
    resolve = fse.resolve_report_spec_function
    assert callable(resolve(TILE_FUNCTION))
    with pytest.raises(fse.AnalyticFunctionRefused, match="curation_coverage_tile"):
        resolve(CURATION_COVERAGE_TILE.function)
