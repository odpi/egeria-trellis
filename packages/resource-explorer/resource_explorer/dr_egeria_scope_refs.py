"""TR-117: a Funnel Stages scope reference is sent to Dr.Egeria as a guid, so a name shared with another element still
resolves. A narrow workaround: retire when PY-73 lands (pyegeria resolving a reference inside a named glossary).

RE's questions batch links each question to its funnel stage with `Link Element To Scope`, naming the stage by display
name. On laz three ACTIVE elements are called "Assessment" (our Funnel Stages term, a DataFieldNaming glossary term and
Egeria's ValidMetadataValue typeName-(Assessment)), so Dr.Egeria's resolver says "Multiple elements found ... use a
unique Qualified Name" and 17 of the batch's 647 commands fail on every heal. A qualified name cannot go in the
document: the term's carries the install's local qualifier.

Before the child processes a document, `rewrite_scope_refs` reads the ONE glossary named "Funnel Stages" and its
members, and rewrites each `Scope Reference` / `Scope Category` value that names one of its terms to
`<name> (guid:<guid>)`; Dr.Egeria's resolver takes the guid from that form. Nothing else in the document changes, the
original file is not touched (a rewritten copy is processed), and what was rewritten, or why nothing was, is reported.
A document with no `Link Element To Scope` is passed through without any Egeria read.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

GLOSSARY = "Funnel Stages"
COMMAND = "Link Element To Scope"
FIELDS = ("Scope Reference", "Scope Category")
_GUID = re.compile(r"\(guid:[^)]+\)")


@dataclass
class Rewrite:
    path: Path                       # what to process: the original, or the rewritten copy
    words: str = ""                  # reported by the child, always when a scope reference was seen
    rewritten: dict[str, str] = field(default_factory=dict)   # name -> guid used
    count: int = 0
    left: list[str] = field(default_factory=list)              # distinct values left as names: not in the glossary,
    left_count: int = 0                                        # or duplicated in it (W-117-1/2); left_count = distinct
    already: int = 0                                           # values that already carried a (guid:...) (W-117-2)


def _blocks(text: str) -> list[tuple[int, int]]:
    """(start, end) line spans of each `## Link Element To Scope` command."""
    lines = text.splitlines()
    spans, start = [], None
    for n, line in enumerate(lines):
        if line.startswith("## ") or line.startswith("# "):
            if start is not None:
                spans.append((start, n))
                start = None
            if line.strip() == f"## {COMMAND}":
                start = n
    if start is not None:
        spans.append((start, len(lines)))
    return spans


def scope_values(text: str) -> list[tuple[int, str]]:
    """(line index, value) of every Scope Reference / Scope Category value inside a Link Element To Scope command."""
    lines = text.splitlines()
    out = []
    for start, end in _blocks(text):
        n = start
        while n < end:
            if lines[n].strip() in {f"### {f}" for f in FIELDS}:
                m = n + 1
                while m < end and not lines[m].strip():
                    m += 1
                if m < end and not lines[m].startswith("#"):
                    out.append((m, lines[m].strip()))
                n = m
            n += 1
    return out


def _items(answer: Any) -> list[dict]:
    return [a for a in answer if isinstance(a, dict)] if isinstance(answer, list) else []


def _name(el: dict) -> str:
    props = el.get("properties") or {}
    return str(props.get("displayName") or props.get("name") or "")


def _guid(el: dict) -> str:
    return str((el.get("elementHeader") or {}).get("guid") or "")


def funnel_terms(client: Any) -> dict[str, str]:
    """{display name: guid} of the Funnel Stages glossary's members. Raises LookupError, in words, unless exactly one
    glossary has that display name."""
    found = [g for g in _items(client.get_glossaries_by_name(GLOSSARY, output_format="JSON")) if _name(g) == GLOSSARY]
    if len(found) != 1:
        raise LookupError(f"{len(found)} glossaries are named {GLOSSARY!r} (exactly one is needed)")
    gguid = _guid(found[0])
    terms: dict[str, list[str]] = {}
    start, page = 0, 100
    while True:
        members = _items(client.get_collection_members(collection_guid=gguid, start_from=start, page_size=page,
                                                       output_format="JSON"))
        for el in members:
            terms.setdefault(_name(el), []).append(_guid(el))
        if not members:
            break
        start += len(members)
    return {name: guids[0] for name, guids in terms.items() if name and len(guids) == 1}


def rewrite_scope_refs(path: str | Path, client: Any, *, terms: Callable[[Any], dict[str, str]] = funnel_terms,
                       tmpdir: str | None = None) -> Rewrite:
    path = Path(path)
    text = path.read_text()
    values = scope_values(text)
    if not values:
        return Rewrite(path)
    try:
        known = terms(client)
    except Exception as exc:  # noqa: BLE001 -- the document still runs as written; the reason is reported
        return Rewrite(path, words=f"TR-117: scope references sent by name, not rewritten: {exc}")
    lines = text.splitlines(keepends=True)
    out = Rewrite(path)
    left: list[str] = []
    for n, value in values:
        if _GUID.search(value):
            out.already += 1                # already a guid: fine as written, never "left as a name"
            continue
        if value not in known:
            left.append(value)
            continue
        ending = "\n" if lines[n].endswith("\n") else ""
        lines[n] = f"{value} (guid:{known[value]}){ending}"
        out.rewritten[value] = known[value]
        out.count += 1
    out.left = sorted(set(left))
    out.left_count = len(out.left)          # distinct, so the count matches the list it introduces
    left_words = (f"{out.already} already guids; {out.left_count} left as names"
                  + (f": {', '.join(out.left)}" if out.left else ""))
    if not out.count:
        out.words = (f"TR-117: no scope reference names a term of the {GLOSSARY} glossary; nothing rewritten; "
                     f"{left_words}")
        return out
    d = Path(tmpdir or tempfile.mkdtemp(prefix="re-scope-refs-"))
    d.mkdir(parents=True, exist_ok=True)
    copy = d / path.name
    copy.write_text("".join(lines))
    out.path = copy
    out.words = (f"TR-117: {out.count} scope reference(s) sent as guids from the {GLOSSARY} glossary "
                 f"({', '.join(sorted(out.rewritten))}); {left_words}; retire when PY-73 lands")
    return out
