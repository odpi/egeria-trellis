"""Every element id a /next stage module looks up must exist somewhere.

Regression: E1 (18b3bb41) deleted ``<div id="enrichment-form">`` and
``renderCurate`` kept doing ``$('enrichment-form')`` then
``if (!host) return;`` -- Curate drew nothing for every resource kind. The
render-level proof is frontend-build/test-harness/curate-pane-renders.test.mjs;
this pins the whole class: an id looked up by ``$()`` / ``getElementById()``
in ``stages/*.js`` must appear as ``id="..."`` in index.html or in some
``next/**/*.js`` markup (or be assigned with ``.id = '...'``).
"""
from __future__ import annotations

import re
from pathlib import Path

NEXT = Path(__file__).resolve().parent.parent / "resource_explorer" / "web" / "static" / "next"
LOOKUP = re.compile(r"""(?:\$|getElementById)\(\s*['"]([A-Za-z][\w-]*)['"]\s*\)""")


def looked_up_ids(src: str) -> set[str]:
    return set(LOOKUP.findall(src))


def defined_somewhere(id_: str, haystacks: list[str]) -> bool:
    pat = re.compile(r"""id=\\?["']%s\\?["']|\.id\s*=\s*['"]%s['"]""" % (re.escape(id_), re.escape(id_)))
    return any(pat.search(h) for h in haystacks)


# enrichment.js's renderEnrichment/renderEnrichmentForm still look up
# #enrichment-form, which no markup renders since E1. They are unreachable:
# nothing calls them (checked below), so they are listed, not hidden. If a
# caller appears, test_allowlisted_enrichment_form_is_still_dead fails.
KNOWN_DEAD = {("enrichment.js", "enrichment-form")}


def missing_ids() -> list[tuple[str, str]]:
    haystacks = [p.read_text() for p in NEXT.rglob("*.js")] + [(NEXT / "index.html").read_text()]
    out = []
    for f in sorted((NEXT / "stages").glob("*.js")):
        for id_ in sorted(looked_up_ids(f.read_text())):
            if not defined_somewhere(id_, haystacks) and (f.name, id_) not in KNOWN_DEAD:
                out.append((f.name, id_))
    return out


def test_every_stage_lookup_id_exists_in_markup_or_is_created():
    assert missing_ids() == [], (
        "stage module looks up an id nothing renders; its `if (!host) return` "
        "will silently draw nothing"
    )


def test_known_negative_the_checker_flags_the_deleted_id():
    # The pin must be able to fail: the pre-fix renderCurate lookup, against a
    # markup set that no longer carries the id, is reported.
    src = "const host = $('enrichment-form'); if (!host) return;"
    assert looked_up_ids(src) == {"enrichment-form"}
    assert not defined_somewhere("enrichment-form", ['<div id="question-rows"></div>'])
    assert defined_somewhere("question-rows", ['<div id="question-rows"></div>'])


def test_curate_mounts_its_own_host():
    src = (NEXT / "stages" / "curate.js").read_text()
    body = src[src.index("export async function renderCurate("):][:300]
    assert "mountCurateHost()" in body
    assert "$('enrichment-form')" not in src


def test_allowlisted_enrichment_form_is_still_dead():
    callers = []
    for p in NEXT.rglob("*.js"):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            code = line.split("//")[0]
            if code.lstrip().startswith("*"):
                continue
            if re.search(r"\brenderEnrichment(?:Form)?\(", code) and "function " not in code:
                callers.append((p.name, n))
    # only the self-recursion inside enrichment.js; nothing else reaches it
    assert {c[0] for c in callers} <= {"enrichment.js"}, callers
