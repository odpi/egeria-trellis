"""Every design note cited from code, tests, scripts, CI and config must exist.

Design notes live under `docs/design-notes/`, in the root and in the
`implemented/` and `evidence/` subdirectories. They get moved between those
directories as they age (the 2026-10-01 reorganisation moved ~130 of them),
and a pointer written as a full path breaks silently when that happens: a
comment, docstring or log message that names a note nobody can open. That is
a "confident wrong pointer", not an error, and nothing else in the suite
fails on it.

The rule this test enforces: a note named in a comment, docstring or message
string resolves to a file ANYWHERE under `docs/design-notes/`. Citations use
the BARE note name (`see SOME-NOTE-IMPLEMENTED.md`), never a directory path,
so a later move of a note between subdirectories cannot dangle them. A bare
name is satisfied by a file of that name in any subdirectory. A citation
written as a PATH (`docs/design-notes/<...>/NAME`) is held to the path it
names: that exact file must exist, so a move that strands a path-qualified
citation fails here even though the note still exists somewhere under the
directory. Paths are how the 2026-10-01 move would have dangled ~85 pointers,
and they are not the convention.

What counts as a citation:

  * an uppercase-and-dash stem ending in `.md` (`SOME-NOTE-IMPLEMENTED.md`);
  * a path under `design-notes/` ending in a file extension;
  * a `step_runs-<date>...csv` data file.

References that wrap across a line break (very common in comments and in
adjacent string literals, e.g. `PER-REQUEST-SERVER-` on one line and
`LATENCY-IMPLEMENTED.md` on the next, or `docs/design-notes/` ending a line)
are joined before matching: a one-line regex alone would pass while they
dangle. `test_the_joiner_*` pins that behaviour so the guard cannot quietly
degrade into a one-line scan.

Deliberately NOT checked: `docs/` prose and the design notes themselves (a
note citing another note by bare name is outside this guard), and this file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
DESIGN_NOTES = PACKAGE_ROOT / "docs" / "design-notes"

# Where citations are scanned. docs/ is intentionally absent (see docstring).
SCAN_ROOTS = [
    PACKAGE_ROOT / "resource_explorer",
    PACKAGE_ROOT / "tests",
    PACKAGE_ROOT / "scripts",
    PACKAGE_ROOT / "frontend-build",
    PACKAGE_ROOT / "egeria-outbox",
    REPO_ROOT / ".github",
]
SCAN_FILES = [
    PACKAGE_ROOT / ".env.example",
    PACKAGE_ROOT / "pyproject.toml",
    PACKAGE_ROOT / "CLAUDE.md",
    PACKAGE_ROOT / "README.md",
]
SCAN_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".yml", ".yaml", ".toml", ".cfg", ".ini",
    ".sh", ".html", ".css", ".json", ".example", ".md", ".txt",
}
SKIP_DIR_NAMES = {"node_modules", "__pycache__", ".git", ".venv", "dist", "build"}
SKIP_SUFFIXES = (".min.js", ".min.css", "-lock.json")

# Names that look like a design note but are not one. Keep empty unless a real
# non-note file is cited; say why next to each entry.
NOT_DESIGN_NOTES: frozenset[str] = frozenset()

# Citations that already pointed at nothing when this guard was written
# (2026-10-01): neither file has ever existed in git history on any branch.
# They are recorded here, not silently fixed, because the right repair (name
# the real note, or write the missing one) is not this guard's call. Remove an
# entry the moment its citations are repaired -- the test below fails if an
# entry is stale, so this list can only shrink.
#   STAGE-PAGE-ROUND.md           routes/projects.py, routes/survey_definitions.py,
#                                 workflows/stage_page.py, tests/test_stage_page.py
#                                 (probably the designer's stage-page round; the
#                                 committed note is SPEC-THE-STAGE-PAGE.md, unverified)
#   SORT-DIRECTION-FIX-IMPLEMENTED.md   web/static/next/stages/curate.js
KNOWN_DANGLING: frozenset[str] = frozenset(
    {"STAGE-PAGE-ROUND.md", "SORT-DIRECTION-FIX-IMPLEMENTED.md"}
)

_NOTE_NAME = re.compile(r"(?<![\w-])([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\.md)\b")
_NOTE_PATH = re.compile(
    r"(design-notes/(?:[\w.-]+/)*[\w.-]+\.(?:md|csv|json|html|webp|dot|txt))\b"
)
_CSV_NAME = re.compile(r"(?<![\w-])(step_runs-[0-9][\w-]*\.csv)\b")

# Joining continuation lines. A line is continued when, ignoring trailing
# string-literal quotes, it ends in `design-notes/` or in an uppercase token
# ending with a hyphen, and the next line (ignoring a comment leader or an
# opening quote) starts with the rest of the name.
_TRAILING_QUOTES = re.compile(r"""["'`]*\s*$""")
_LEADER = re.compile(r"""^\s*(?:(?:#:?|//|\*+|>)\s*)?(?:[rbfRBF]{0,2}["'`])?""")
_ENDS_OPEN_PATH = re.compile(r"design-notes/$")
_ENDS_OPEN_NAME = re.compile(r"(?<![A-Za-z0-9_])[A-Z0-9]+(?:-[A-Z0-9]+)*-$")
_STARTS_NAME_REST = re.compile(r"^[A-Za-z0-9_]")


def join_wrapped_lines(text: str) -> list[tuple[int, str]]:
    """Return `(first_physical_line_number, logical_line)` pairs with wrapped
    citations rejoined onto one logical line."""
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        if out:
            prev_no, prev = out[-1]
            prev_core = _TRAILING_QUOTES.sub("", prev)
            if _ENDS_OPEN_PATH.search(prev_core) or _ENDS_OPEN_NAME.search(prev_core):
                m = _LEADER.match(raw)
                rest = raw[m.end():] if m else raw
                if _STARTS_NAME_REST.match(rest):
                    out[-1] = (prev_no, prev_core + rest)
                    continue
        out.append((lineno, raw))
    return out


def cited_names(text: str) -> list[tuple[int, str]]:
    """Every `(line, ref)` cited in `text`, wrapped names included. `ref` is the
    bare file name for a bare citation, or `design-notes/<path>` for a
    path-qualified one (even when the path has no subdirectory)."""
    found: list[tuple[int, str]] = []
    for lineno, line in join_wrapped_lines(text):
        for rx in (_NOTE_NAME, _NOTE_PATH, _CSV_NAME):
            for m in rx.finditer(line):
                found.append((lineno, m.group(1)))
    return found


def _existing_note_names() -> set[str]:
    return {p.name for p in DESIGN_NOTES.rglob("*") if p.is_file()}


def _resolves(ref: str, existing: set[str]) -> bool:
    """A path-qualified ref must exist at that path; a bare name anywhere."""
    if ref.startswith("design-notes/"):
        return (DESIGN_NOTES / ref[len("design-notes/"):]).is_file()
    return ref in existing


def _scanned_files() -> list[Path]:
    this = Path(__file__).resolve()
    files: list[Path] = [f for f in SCAN_FILES if f.is_file()]
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.resolve() == this:
                continue
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            if p.suffix not in SCAN_SUFFIXES or p.name.endswith(SKIP_SUFFIXES):
                continue
            files.append(p)
    return sorted(files)


def test_every_cited_design_note_resolves_somewhere_under_design_notes():
    existing = _existing_note_names()
    assert existing, f"no design notes found under {DESIGN_NOTES}: wrong root?"
    dangling: list[str] = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, name in cited_names(text):
            base = name.rsplit("/", 1)[-1]  # `design-notes/x/NAME` -> NAME
            if base in NOT_DESIGN_NOTES or base in KNOWN_DANGLING:
                continue
            if _resolves(name, existing):
                continue
            dangling.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {name}")
    assert not dangling, (
        "design notes cited here do not resolve under "
        "packages/resource-explorer/docs/design-notes/ (cite the bare note name; "
        "a path-qualified citation must name the file's CURRENT path, and the "
        "note may have moved or been renamed):\n  " + "\n  ".join(dangling)
    )


def test_the_known_dangling_list_has_no_stale_entries():
    """An entry whose note now exists, or that nothing cites any more, must be
    deleted from KNOWN_DANGLING so the list only ever shrinks."""
    existing = _existing_note_names()
    still_cited: set[str] = set()
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        still_cited.update(name for _, name in cited_names(text))
    for name in sorted(KNOWN_DANGLING):
        assert name not in existing, f"{name} now exists: drop it from KNOWN_DANGLING"
        assert name in still_cited, f"{name} is no longer cited: drop it from KNOWN_DANGLING"


def test_the_scan_actually_covers_the_known_citation_sites():
    """A guard that scans nothing passes forever. Pin that the scan reaches
    code, tests, scripts, CI and config, and that citations exist in them."""
    files = {str(p.relative_to(REPO_ROOT)) for p in _scanned_files()}
    for needle in (
        "packages/resource-explorer/resource_explorer/bootstrap.py",
        "packages/resource-explorer/tests/test_web.py",
        ".github/workflows/resource-explorer.yml",
        "packages/resource-explorer/.env.example",
    ):
        assert needle in files, f"{needle} is not scanned"
    cited = [
        p for p in _scanned_files()
        if cited_names(p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert len(cited) >= 20, f"only {len(cited)} scanned files cite a design note"


def test_the_joiner_sees_a_name_wrapped_mid_name_in_a_comment():
    text = "# see docs/design-notes/PER-REQUEST-SERVER-\n# LATENCY-ROUND-2-IMPLEMENTED.md for why\n"
    assert (1, "PER-REQUEST-SERVER-LATENCY-ROUND-2-IMPLEMENTED.md") in cited_names(text)


def test_the_joiner_sees_a_name_wrapped_after_the_directory():
    text = 'msg = ("see docs/design-notes/"\n       "SOME-NOTE-IMPLEMENTED.md")\n'
    assert (1, "SOME-NOTE-IMPLEMENTED.md") in cited_names(text)
    text = "# see docs/design-notes/\n# SOME-NOTE-IMPLEMENTED.md\n"
    assert (1, "SOME-NOTE-IMPLEMENTED.md") in cited_names(text)


def test_the_joiner_sees_a_name_wrapped_in_a_docstring_and_a_js_block_comment():
    assert (2, "ANOTHER-NOTE-FIX.md") in cited_names('"""x\nsee ANOTHER-NOTE-\nFIX.md\n"""')
    assert (2, "ANOTHER-NOTE-FIX.md") in cited_names("/*\n * see ANOTHER-NOTE-\n * FIX.md\n */")


def test_a_path_citation_is_held_to_its_path_but_a_bare_name_is_not(tmp_path, monkeypatch):
    (tmp_path / "implemented").mkdir()
    (tmp_path / "implemented" / "MOVED-NOTE-IMPLEMENTED.md").write_text("x")
    monkeypatch.setattr(sys.modules[__name__], "DESIGN_NOTES", tmp_path)
    existing = _existing_note_names()
    # A bare name is satisfied by the file in any subdirectory.
    assert _resolves("MOVED-NOTE-IMPLEMENTED.md", existing)
    # A path must name where the file is now: the pre-move root path dangles
    # even though the note still exists elsewhere under design-notes/.
    assert _resolves("design-notes/implemented/MOVED-NOTE-IMPLEMENTED.md", existing)
    assert not _resolves("design-notes/MOVED-NOTE-IMPLEMENTED.md", existing)
    # And the parser reports a path citation as a path, so it is held to it.
    refs = [r for _, r in cited_names("# docs/design-notes/implemented/MOVED-NOTE-IMPLEMENTED.md")]
    assert "design-notes/implemented/MOVED-NOTE-IMPLEMENTED.md" in refs
    refs = [r for _, r in cited_names("# docs/design-notes/MOVED-NOTE-IMPLEMENTED.md")]
    assert "design-notes/MOVED-NOTE-IMPLEMENTED.md" in refs


def test_a_dangling_name_is_reported_and_an_existing_one_is_not():
    existing = {"REAL-NOTE-IMPLEMENTED.md"}
    cited = {n for _, n in cited_names("# REAL-NOTE-IMPLEMENTED.md and GONE-NOTE-IMPLEMENTED.md")}
    assert cited - existing == {"GONE-NOTE-IMPLEMENTED.md"}


def test_ordinary_prose_hyphens_do_not_join_unrelated_lines():
    text = "# a well-\n# known thing\n# SOME-NOTE.md is cited here\n"
    assert cited_names(text) == [(3, "SOME-NOTE.md")]
