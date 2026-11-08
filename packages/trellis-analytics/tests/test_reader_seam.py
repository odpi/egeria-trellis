"""The reader seam from trellis's side (LS2 step 3): the library is the installed egeria-trellis-analytics wheel, and
RE's implementations (trellis_analytics_re) answer what it declares.

Pins: the library is imported from the installed distribution, not this tree, and its manifests are discovered from
it; the Protocol names exactly the typed reads the implementation has; the installed readers are RE's; the old
exception names are the seam's; and neither the installed library nor a reference step imports RE or a reader
implementation. The seam's own behaviour with no reader installed is tested in the library's repository.
"""
import ast
import inspect
from importlib.metadata import distribution
from pathlib import Path

import pytest

import trellis_analytics
from trellis_analytics import readers
from trellis_analytics.readers import (CATALOGUE_SCOPE, ODS, ODS_READS, CatalogueScopeRead, OdsRead, open_reader)

ROOT = Path(__file__).resolve().parents[1]
#: The implementations, which neither the library nor a step may import.
STAYS = ("resource_explorer", "trellis_analytics_re", "psycopg2")
LIBRARY = "egeria-trellis-analytics"


def _public_reads(cls) -> set[str]:
    return {n for n, f in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}


def test_the_library_is_the_installed_wheel_not_this_tree():
    """Brief section 5: what RE runs is the released library. Its modules come from the installed distribution, and
    every manifest it ships is discovered from there."""
    from trellis_analytics.discovery import discover
    dist = distribution(LIBRARY)
    pkg = Path(trellis_analytics.__file__).resolve().parent
    assert ROOT not in pkg.parents, f"trellis_analytics imported from this tree ({pkg}), not the {LIBRARY} wheel"
    assert dist.version.startswith("0.4.")
    shipped = {ep.name for ep in dist.entry_points if ep.group == "pyegeria.analytics"}
    assert shipped == {"curation_coverage", "context_pack", "curation_coverage_tile"}
    assert shipped <= set(discover(check=False))


def test_the_protocol_names_every_typed_read_of_the_implementation():
    from trellis_analytics_re.ods import OdsReader
    # `query` takes SQL text and `registry_view` hands over a registry: neither is a read a step may ask for.
    assert set(ODS_READS) == _public_reads(OdsReader) - {"query", "registry_view"}
    for name in ODS_READS:          # same parameters, so a caller written against the Protocol calls the reader right
        assert list(inspect.signature(getattr(OdsRead, name)).parameters) == \
            list(inspect.signature(getattr(OdsReader, name)).parameters), name


def test_the_installed_readers_are_res_and_meet_the_protocols(tmp_path):
    pytest.importorskip("resource_explorer")
    from trellis_analytics_re.ods import OdsReader
    from trellis_analytics_re.registry_view import CatalogueScopeReader
    assert set(readers.installed_readers()) >= {ODS, CATALOGUE_SCOPE, "investigation_purposes", "question_ids",
                                                "table_rows"}
    url = f"sqlite:///{tmp_path / 'none.sqlite'}"
    r = open_reader(ODS, url)
    assert type(r) is OdsReader and isinstance(r, OdsRead)
    c = open_reader(CATALOGUE_SCOPE, r)          # from the ODS reader of the same registry, as the routine builds it
    assert type(c) is CatalogueScopeReader and isinstance(c, CatalogueScopeRead)


def test_the_old_exception_names_are_the_seams():
    from trellis_analytics_re.ods import OdsUnreadable
    from trellis_analytics_re.registry_view import ResourceExplorerMissing
    assert OdsUnreadable is readers.OdsUnreadable and ResourceExplorerMissing is readers.ResourceExplorerMissing


def test_the_routine_and_the_tile_import_and_run_with_re_absent():
    """The Portal's case on this tree's environment: RE's readers are registered, but RE and trellis_analytics_re cannot
    be imported. The installed routine and tile still import (their module level names only the seam), and a run answers
    not_established instead of raising."""
    import subprocess
    import sys
    code = (
        "import importlib.abc, sys\n"
        "class _Absent(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('resource_explorer', 'trellis_analytics_re'):\n"
        "            raise ModuleNotFoundError(f'No module named {name!r}', name=name)\n"
        "sys.meta_path.insert(0, _Absent())\n"
        "from trellis_analytics.routines import curation_coverage\n"
        "from trellis_analytics.portal import curation_coverage_tile\n"
        "class Unread:\n"
        "    def __getattr__(self, name): raise AssertionError(name)\n"
        "r = curation_coverage(Unread(), 'database', 'demo')\n"
        "t = curation_coverage_tile('demo', 'sqlite:///never-opened')\n"
        "print(r.envelope.result_state.value, t['state'])\n")
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.split() == ["not_established", "not_established"]


def _imports(path: Path):
    """Every module the file imports, at any depth (a lazy import inside a function counts)."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _guarded_files():
    yield from sorted(Path(trellis_analytics.__file__).resolve().parent.rglob("*.py"))   # the installed library
    yield from sorted((ROOT / "examples" / "steps").glob("*/src/*/*.py"))                # the reference steps


@pytest.mark.parametrize("path", list(_guarded_files()), ids=lambda p: "/".join(p.parts[-3:]))
def test_the_library_and_the_steps_import_no_implementation(path):
    """The seam's guard: they read through `trellis_analytics.readers`, never RE or a reader it implements, not even
    lazily or behind `except ImportError`."""
    bad = [m for m in _imports(path) if m.startswith(STAYS)]
    assert not bad, f"{path} imports {bad}: read through trellis_analytics.readers instead"
