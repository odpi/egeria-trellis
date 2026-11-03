"""Every Tailwind class literal used in a `class="..."` attribute inside
resource_explorer/web/static/next/**/*.js must exist somewhere in the
compiled next/tailwind-next.css (or, for the handful of things a utility
class is the wrong tool for, in index.html's own inline <style> block -- see
its opening comment). An uncompiled/typo'd class name otherwise renders as
literally nothing, silently, with no error and no visual cue -- exactly the
bug this file exists to catch. See
TAILWIND-NEXT-FRESHNESS-CHECK-IMPLEMENTED.md for the full
design writeup, including why this check is independent of (not a
replacement for) test_tailwind_next_freshness.py's rebuild-and-diff check.

## How extraction mirrors Tailwind's own content scanning

tailwind-next.config.js's `content` glob has no custom `extract` function, so
Tailwind's own default scanner is a broad, syntax-unaware regex over the raw
file text: it does not parse JS, it just finds quote/backtick/whitespace/
`${`/`}`-delimited candidate tokens. That is *why* a conditional class built
with a template-literal ternary like
`` `${isOpen ? 'border-accent bg-chrome-line/20' : 'border-chrome-line'}` ``
compiles correctly -- Tailwind sees the two complete literal strings inside
the ternary branches, not the ternary itself.

This check mirrors that: it does not parse the ternary as JS, it walks the
`${...}` interpolation looking for fragments, between the unquoted `?`/`:`
separators, that are themselves *wholly* a single quoted string literal --
i.e. the same "collect complete literal strings, ignore everything else"
rule Tailwind's own scanner effectively applies. A fragment that mixes code
and a literal (`f.label === 'worthy' ? ...`, where `'worthy'` is a
comparison operand, not a class) is deliberately NOT treated as a candidate,
because the literal there is not in value position -- collecting it would be
exactly the false-positive Tailwind's own scanner also avoids by not knowing
JS syntax at all (it has no notion of "comparison" vs "result" either; it
simply never sees `'worthy'` as a value because it isn't followed by
whitespace/quote boundaries that make it a stand-alone class-list string in
the surrounding markup -- our ternary-aware split gets the same answer by a
different, JS-aware route).

## Known, documented gaps (false negatives, by design)

**Fully dynamic interpolations with no literal branch at all** -- e.g.
`${cls}`, `${mutedCls}`, `${hoverCls[tone]}`, `${chip(state.scope === id)}`
-- are invisible to this check, exactly as they are invisible to Tailwind's
own build (neither can resolve a bare identifier or function call to a
string at scan time; Tailwind CLI's own documented guidance is "never
construct class names dynamically" for precisely this reason). These are
silently skipped rather than guessed at -- see `EXCLUDED_DYNAMIC_COUNT`
below, which pins the current count so a large unexplained jump (someone
introducing a lot of new dynamic-only class construction) is visible in a
diff even though no individual name can be checked.
"""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
NEXT_DIR = PACKAGE_ROOT / "resource_explorer" / "web" / "static" / "next"
CSS_PATH = NEXT_DIR / "tailwind-next.css"
INDEX_HTML_PATH = NEXT_DIR / "index.html"

CLASS_ATTR_RE = re.compile(r'''class=(["'])(.*?)\1''', re.S)
TOKEN_SHAPE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-./:%\[\]]*$")
STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
PURE_LITERAL_RE = re.compile(r"""^\s*(?:'([^']*)'|"([^"]*)")\s*$""")

# ---------------------------------------------------------------------------
# Allowlists. Both are reviewed here, deliberately, rather than as inline
# source comments -- this task's brief is scoped to tests/CI config only, and
# next/*.js belongs to other agents' branches tonight. Anyone editing
# next/*.js is free to replace an entry here with a real fix (a CSS rule, or
# correcting a typo) and delete the entry.
# ---------------------------------------------------------------------------

# Classes used purely as DOM query-selector / event-delegation hooks --
# `.closest('.fb-star')` --
# never rendered as a CSS class anywhere (confirmed: absent from both
# tailwind-next.css and index.html's inline <style> block). Not a staleness
# bug: these were never meant to carry style, and adding one here is a
# judgement call about intent, not a parsing gap, so review it before
# extending the list.
KNOWN_HOOK_ONLY_CLASSES = {
    "fb-star": "feedback.js -- .closest('.fb-star') delegated click handler",
    "wl-band": "worklist.js -- divider row marker, no visual rule anywhere",
    "wl-bar": "worklist.js -- bar-chart cell wrapper, styled via sibling utilities only",
    "wl-countrow": "app.js -- row marker for the investigation count table",
}

# Genuinely uncompiled Tailwind class attempts this check found on its first
# run (2026-09-28) -- undefined color tokens (paper-raised, paper-alt,
# rule-soft weren't in tailwind-next.config.js's palette) and an out-of-range
# spacing scale step (s5 -- the scale only defined s1/s2/s3/s4/s6/s8). All
# seven were resolved the same day (see
# TAILWIND-NEXT-FRESHNESS-CHECK-IMPLEMENTED.md §4): `s5` was added to the
# spacing scale (fits the existing sN = N*4.6px progression); `paper-raised`
# and `paper-alt` usages were replaced with the existing `paper-surface`
# token (site list in the doc); `rule-soft` usages were replaced with the
# existing `rule` token. Kept empty, not deleted, as the place a future
# genuinely-pre-existing (not newly-introduced) gap would go -- deliberately:
# a new entry here should be rare and reviewed, not a quiet way to make a
# red check green.
KNOWN_PREEXISTING_GAPS: set[str] = set()

# Pinned so a big jump is visible in review even though individual names in
# this bucket can't be checked (see module docstring). Re-measure and update
# deliberately if legitimate new dynamic-only class construction is added.
# 2026-10-14 cleanup: constant-helper interpolations (btnCls() x19, LINK_BTN x3, indent x22) made literal;
# re-measured at 46 (was 90 at the 75+15 ceiling). Lowered deliberately so the +15 headroom is real again.
EXPECTED_EXCLUDED_DYNAMIC_COUNT = 46
EXCLUDED_DYNAMIC_SLACK = 15


def _find_interpolations(value: str) -> list[tuple[int, int]]:
    """Balanced `${...}` spans in `value` (no nested braces observed in this
    codebase's class attributes as of 2026-09-28 -- verified empirically --
    but the scan is brace-balanced anyway rather than assuming that stays
    true)."""
    spans = []
    i = 0
    while True:
        idx = value.find("${", i)
        if idx == -1:
            break
        depth = 1
        j = idx + 2
        while j < len(value) and depth > 0:
            if value[j] == "{":
                depth += 1
            elif value[j] == "}":
                depth -= 1
            j += 1
        spans.append((idx, j))
        i = j
    return spans


def _quote_aware_split(expr: str) -> list[str]:
    """Split `expr` on top-level (unquoted) '?' and ':' -- the ternary
    separators -- without splitting on one that appears inside a JS string
    literal (e.g. a `hover:text-x` class name sitting inside a quoted
    branch)."""
    parts: list[str] = []
    buf: list[str] = []
    quote = None
    i = 0
    while i < len(expr):
        ch = expr[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(expr):
                buf.append(expr[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in ("?", ":"):
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _literal_fragments(inner: str) -> list[str]:
    """Class-list strings for each ternary-branch fragment of `inner` that is
    wholly a single quoted literal -- i.e. a value position, not a
    condition/comparison. A fragment mixing other code with a quote (an
    `x === 'foo'` comparison) is not a value position and is skipped."""
    out = []
    for frag in _quote_aware_split(inner):
        m = PURE_LITERAL_RE.match(frag)
        if m:
            out.append(m.group(1) if m.group(1) is not None else m.group(2))
    return out


def _extract_from_file(path: Path) -> tuple[set[str], list[tuple[str, str]]]:
    text = path.read_text(encoding="utf-8")
    tokens: set[str] = set()
    excluded: list[tuple[str, str]] = []
    for m in CLASS_ATTR_RE.finditer(text):
        val = m.group(2)
        spans = _find_interpolations(val)
        remainder_parts = []
        last = 0
        for (s, e) in spans:
            remainder_parts.append(val[last:s])
            inner = val[s + 2 : e - 1]
            lits = _literal_fragments(inner)
            if lits:
                for lit in lits:
                    tokens.update(lit.split())
            else:
                excluded.append((path.name, inner.strip()[:80]))
            last = e
        remainder_parts.append(val[last:])
        remainder = "".join(remainder_parts)
        tokens.update(remainder.split())
    valid = {t for t in tokens if TOKEN_SHAPE_RE.match(t)}
    return valid, excluded


def _escape_for_css_selector(token: str) -> str:
    """Mirror Tailwind's own selector escaping: every character that isn't
    alnum/underscore/hyphen gets a leading backslash in the emitted CSS
    class selector (`.px-\\[8px\\]`, `.hover\\:text-chrome-ink:hover`)."""
    return "".join(ch if (ch.isalnum() or ch in ("_", "-")) else f"\\{ch}" for ch in token)


def _style_block_text(html_path: Path) -> str:
    return "".join(STYLE_BLOCK_RE.findall(html_path.read_text(encoding="utf-8")))


def _all_js_files() -> list[Path]:
    return sorted(NEXT_DIR.rglob("*.js"))


def test_every_class_literal_used_in_next_js_exists_in_committed_css_or_style_block():
    haystack = CSS_PATH.read_text(encoding="utf-8") + "\n" + _style_block_text(INDEX_HTML_PATH)

    all_tokens: set[str] = set()
    excluded_total = 0
    token_files: dict[str, list[str]] = {}
    for f in _all_js_files():
        toks, excluded = _extract_from_file(f)
        excluded_total += len(excluded)
        for t in toks:
            token_files.setdefault(t, []).append(f.relative_to(NEXT_DIR).as_posix())

    missing = []
    for tok in sorted(token_files):
        if tok in KNOWN_HOOK_ONLY_CLASSES or tok in KNOWN_PREEXISTING_GAPS:
            continue
        selector = "." + _escape_for_css_selector(tok)
        if selector not in haystack:
            missing.append(tok)

    if missing:
        lines = []
        for tok in missing:
            files = ", ".join(sorted(set(token_files[tok]))[:3])
            lines.append(f"  {tok!r} (used in: {files})")
        raise AssertionError(
            "Class literal(s) used in next/**/*.js are not compiled into "
            "tailwind-next.css (and aren't in index.html's inline <style> "
            "block either). Either it's a typo, or tailwind-next.css is "
            "stale -- rebuild with:\n"
            "  cd packages/resource-explorer/frontend-build && npx tailwindcss "
            "-c tailwind-next.config.js -i ./input.css "
            "-o ../resource_explorer/web/static/next/tailwind-next.css --minify\n\n"
            + "\n".join(lines)
        )

    # A sanity check on the allowlists themselves: an entry that has quietly
    # started compiling (e.g. a color token was added to the palette) should
    # be noticed and removed, not linger forever pretending to be a gap.
    still_present_in_haystack = sorted(
        tok
        for tok in KNOWN_PREEXISTING_GAPS
        if ("." + _escape_for_css_selector(tok)) in haystack
    )
    assert not still_present_in_haystack, (
        "These are listed in KNOWN_PREEXISTING_GAPS but now compile fine -- "
        f"remove them from the allowlist: {still_present_in_haystack}"
    )


def test_dynamic_exclusion_count_has_not_jumped_unexplained():
    """Not a correctness check on any specific class -- a tripwire. This
    check cannot see inside a fully dynamic interpolation (`${cls}`,
    `${chip(...)}` -- see module docstring), so a large, silent increase in
    how much of the class-attribute surface has become unreadable to this
    check is itself worth a human looking at, even though no individual
    missing class can be named."""
    excluded_total = 0
    for f in _all_js_files():
        _, excluded = _extract_from_file(f)
        excluded_total += len(excluded)

    assert excluded_total <= EXPECTED_EXCLUDED_DYNAMIC_COUNT + EXCLUDED_DYNAMIC_SLACK, (
        f"Dynamic (unresolvable) class interpolations jumped to {excluded_total}, "
        f"more than {EXCLUDED_DYNAMIC_SLACK} above the {EXPECTED_EXCLUDED_DYNAMIC_COUNT} "
        "measured on 2026-09-28. If this is legitimate new dynamic class "
        "construction, re-measure and raise EXPECTED_EXCLUDED_DYNAMIC_COUNT "
        "deliberately; if not, something now newly resolvable is being missed "
        "and its literal branches should be checked instead."
    )
