"""The reader seam (LS2 step 1, brief §2, §1 amended 2026-11-02): the library owns the reads, an installed package
answers them.

Pins: the Protocol names exactly the typed reads the implementation has; the installed readers are RE's; with no reader
installed the routine and the tile answer not_established in words instead of importing RE; importing the library
resolves no reader; and the modules that leave trellis with the library import neither RE nor a reader implementation.
"""
import ast
import inspect
from pathlib import Path

import pytest

from trellis_analytics import readers
from trellis_analytics.envelope import ResultState
from trellis_analytics.readers import (CATALOGUE_SCOPE, ODS, ODS_READS, CatalogueScopeRead, OdsRead, ReaderMissing,
                                       open_reader)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "trellis_analytics"

#: What leaves trellis with the library (brief §1, amended 2026-11-02: curation_coverage, its manifests and portal.py move).
MOVING = ["__init__.py", "declaration.py", "declare.py", "envelope.py", "answer_scoring.py", "manifest.py",
          "manifests.py", "discovery.py", "step.py", "portal.py", "readers/__init__.py", "readers/egeria.py",
          "routines/__init__.py", "routines/context_pack.py", "routines/curation_coverage.py"]
CATALOGUE_WORDS = "no reader for Resource Explorer's catalogue is installed here"
#: The implementations that stay behind (RE's), which the moving modules must not import.
STAYS = ("resource_explorer", "trellis_analytics.readers.ods", "trellis_analytics.readers.registry_view", "psycopg2")


def _public_reads(cls) -> set[str]:
    return {n for n, f in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}


def test_the_protocol_names_every_typed_read_of_the_implementation():
    from trellis_analytics.readers.ods import OdsReader
    # `query` takes SQL text and `registry_view` hands over a registry: neither is a read a step may ask for.
    assert set(ODS_READS) == _public_reads(OdsReader) - {"query", "registry_view"}
    for name in ODS_READS:          # same parameters, so a caller written against the Protocol calls the reader right
        assert list(inspect.signature(getattr(OdsRead, name)).parameters) == \
            list(inspect.signature(getattr(OdsReader, name)).parameters), name


def test_the_installed_readers_are_res_and_meet_the_protocols(tmp_path):
    pytest.importorskip("resource_explorer")
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.readers.registry_view import CatalogueScopeReader
    assert set(readers.installed_readers()) >= {ODS, CATALOGUE_SCOPE, "investigation_purposes", "question_ids"}
    url = f"sqlite:///{tmp_path / 'none.sqlite'}"
    r = open_reader(ODS, url)
    assert type(r) is OdsReader and isinstance(r, OdsRead)
    c = open_reader(CATALOGUE_SCOPE, r)          # from the ODS reader of the same registry, as the routine builds it
    assert type(c) is CatalogueScopeReader and isinstance(c, CatalogueScopeRead)


def test_the_old_exception_names_are_the_seams():
    from trellis_analytics.readers.ods import OdsUnreadable
    from trellis_analytics.readers.registry_view import ResourceExplorerMissing
    assert OdsUnreadable is readers.OdsUnreadable and ResourceExplorerMissing is readers.ResourceExplorerMissing


@pytest.fixture
def no_readers(monkeypatch):
    """This environment as the Portal's: nothing registered in the readers group."""
    real = readers.entry_points
    monkeypatch.setattr(readers, "entry_points",
                        lambda group=None, **kw: [] if group == readers.GROUP else real(group=group, **kw))


def test_a_missing_reader_is_said_in_words(no_readers):
    assert readers.installed_readers() == {}
    with pytest.raises(ReaderMissing) as exc:
        open_reader(ODS, "sqlite:///x")
    assert str(exc.value) == "no reader installed for ods here"


def test_a_reader_that_will_not_load_is_missing_and_says_why(monkeypatch):
    class Broken:
        name, value = ODS, "nowhere.at_all:Reader"

        def load(self):
            raise ImportError("No module named 'nowhere'")
    monkeypatch.setattr(readers, "entry_points", lambda group=None, **kw: [Broken()] if group == readers.GROUP else [])
    with pytest.raises(ReaderMissing, match=r"no reader installed for ods here \(nowhere.at_all:Reader failed to load"):
        open_reader(ODS)


def test_the_routine_with_no_readers_is_not_established_in_the_catalogues_words(no_readers):
    """Condition 2 of the amendment: an absence, never an error, never a zero."""
    from trellis_analytics.routines import curation_coverage

    class Unread:                           # any read at all would be the regression: the catalogue is asked first
        def __getattr__(self, name):
            raise AssertionError(f"read {name} with no catalogue reader installed")
    r = curation_coverage(Unread(), "database", "demo")
    assert r.envelope.result_state is ResultState.NOT_ESTABLISHED
    assert r.envelope.result_state_reason == CATALOGUE_WORDS
    assert r.known == [] and r.groups == [] and r.known_schemas == 0


def test_the_tile_with_no_readers_is_not_established_and_draws_no_counts(no_readers):
    from trellis_analytics.portal import curation_coverage_tile
    tile = curation_coverage_tile("demo", "sqlite:///never-opened")
    assert tile["state"] == ResultState.NOT_ESTABLISHED.value
    assert tile["state_reason"] == CATALOGUE_WORDS and tile["subtitle"] == f"not established · {CATALOGUE_WORDS}"
    assert "Published" not in tile and "Not yet published" not in tile and tile["kpis"] == []


def test_discovery_with_no_readers_skips_its_checks(no_readers):
    from trellis_analytics import discovery
    assert discovery.known_purposes() is None and discovery.known_question_ids() is None


def test_importing_the_library_resolves_no_reader():
    """Condition 4: routines/__init__ stays cheap. Importing every moving module loads no reader implementation and no RE;
    a reader is resolved when a routine runs. (psycopg2 is left out here only because pyegeria's own import loads it; the
    source guard below still refuses it in the library.)"""
    import subprocess
    import sys
    mods = ["trellis_analytics." + p.removesuffix(".py").removesuffix("/__init__").replace("/", ".")
            for p in MOVING if p != "__init__.py"]
    code = ("import sys\n" + "".join(f"import {m}\n" for m in mods) +
            f"print(sorted(m for m in sys.modules if m.startswith({STAYS[:3]!r})))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


def _imports(path: Path):
    """Every module the file imports, at any depth (a lazy import inside a function counts)."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _moving_files():
    yield from (SRC / p for p in MOVING)
    yield from sorted((ROOT / "examples" / "steps").glob("*/src/*/*.py"))


@pytest.mark.parametrize("path", list(_moving_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_what_leaves_with_the_library_imports_no_implementation(path):
    """The seam's guard: a moving module reads through `trellis_analytics.readers`, never RE or a reader it implements,
    not even lazily or behind `except ImportError`."""
    bad = [m for m in _imports(path) if m.startswith(STAYS)]
    assert not bad, f"{path.relative_to(ROOT)} imports {bad}: read through trellis_analytics.readers instead"
