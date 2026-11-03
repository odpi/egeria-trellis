"""Source pins for the Curate scope table layout (Part 2 of the 2026-10-06 slice): the table scrolls
INSIDE its own box and its headers wrap. The behaviour is in the harness test
`curate-scope-layout.test.mjs`; these pins keep the structure from being edited away unseen."""
from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
SCOPE = (NEXT / "stages" / "curate-scope.js").read_text(encoding="utf-8")
BANDS = (NEXT / "stages" / "curate-bands.js").read_text(encoding="utf-8")


def test_the_tree_host_scrolls_inside_its_own_box_and_can_shrink():
    assert 'data-scope-tree class="min-w-0 max-w-full overflow-x-auto"' in SCOPE


def test_the_section_that_holds_the_scope_can_shrink_in_its_parent():
    assert 'data-curate-work="scope" class="mb-s3 min-w-0"' in BANDS


def test_the_classification_header_is_the_full_word_on_one_line():
    # Headers are drawn by one helper (label, title, handle) and held on one line with an
    # ellipsis and the full text in the title, so a header never breaks mid-word.
    assert "hc('cls', 'Classification'" in SCOPE
    assert "'Classes'" not in SCOPE
    assert "white-space:nowrap" in (NEXT / "colresize.js").read_text(encoding="utf-8")


def test_no_truncating_class_in_the_scope_table():
    for bad in ("truncate", "text-ellipsis"):
        assert bad not in SCOPE


# ── the designer's state vocabulary (2026-10-06 addendum): "deleted", no glyph ─────────────────────

GLYPHS = (NEXT / "glyphs.js").read_text(encoding="utf-8")


def test_removed_is_not_a_glyph_key_and_deleted_has_no_glyph():
    import re
    entries = re.findall(r"^\s{2}(\w+):\s+\{", GLYPHS, re.M)
    assert "removed" not in entries and "deleted" not in entries


def test_no_scope_string_calls_an_egeria_soft_delete_removed():
    import re
    for src in (SCOPE,):
        for line in src.splitlines():
            if line.strip().startswith(("//", "*", "/*")):
                continue
            assert not re.search(r"deleted from Egeria|'removed'|\"removed\"", line), line
