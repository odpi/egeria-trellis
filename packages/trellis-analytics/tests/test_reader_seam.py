"""The reader seam (LS2 step 1, brief §2): the library owns the reads, an installed package answers them.

Pins four things: the Protocol names exactly the typed reads the implementation has; the installed `ods` is RE's
reader; a routine with no reader installed answers not_established in words instead of importing RE; and the modules
that leave trellis with the library (brief §1) import neither RE nor the reader implementations.
"""
import ast
import inspect
from pathlib import Path

import pytest

from trellis_analytics import readers
from trellis_analytics.envelope import ResultState
from trellis_analytics.readers import ODS, ODS_READS, OdsRead, ReaderMissing, open_reader

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "trellis_analytics"

#: What leaves trellis with the library (brief §1), as paths under the package or the examples.
MOVING = ["__init__.py", "declaration.py", "declare.py", "envelope.py", "answer_scoring.py", "manifest.py",
          "manifests.py", "discovery.py", "step.py", "portal.py", "readers/__init__.py", "readers/egeria.py",
          "routines/context_pack.py"]
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


def test_the_installed_ods_reader_is_res_and_meets_the_protocol(tmp_path):
    pytest.importorskip("resource_explorer")
    from trellis_analytics.readers.ods import OdsReader
    assert readers.installed_readers()[ODS] == "trellis_analytics.readers.ods:OdsReader"
    r = open_reader(ODS, f"sqlite:///{tmp_path / 'none.sqlite'}")
    assert type(r) is OdsReader and isinstance(r, OdsRead)


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


def test_the_tile_with_no_reader_is_not_established_and_draws_no_counts(no_readers):
    from trellis_analytics.portal import curation_coverage_tile
    tile = curation_coverage_tile("demo", "sqlite:///never-opened")
    assert tile["state"] == ResultState.NOT_ESTABLISHED.value
    assert tile["state_reason"] == "no reader installed for ods here"
    assert "Published" not in tile and "Not yet published" not in tile and tile["kpis"] == []


def _imports(path: Path):
    """(module, guarded) for every import in the file; guarded means inside a `try` that handles ImportError."""
    tree = ast.parse(path.read_text())
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
                h.type is not None and "ImportError" in ast.unparse(h.type) for h in node.handlers):
            for stmt in node.body:
                guarded.update(id(n) for n in ast.walk(stmt))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((a.name, id(node) in guarded) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, id(node) in guarded


def _moving_files():
    yield from (SRC / p for p in MOVING)
    yield from sorted((ROOT / "examples" / "steps").glob("*/src/*/*.py"))


@pytest.mark.parametrize("path", list(_moving_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_what_leaves_with_the_library_imports_no_implementation(path):
    """The seam's guard: a moving module reads through `trellis_analytics.readers`, never RE or a reader it implements.
    An import of RE inside `try ... except ImportError` (discovery's purposes and question checks, which degrade to a
    warning without RE) is the one form allowed."""
    bad = [m for m, guarded in _imports(path) if m.startswith(STAYS) and not guarded]
    assert not bad, f"{path.relative_to(ROOT)} imports {bad}: read through trellis_analytics.readers instead"
