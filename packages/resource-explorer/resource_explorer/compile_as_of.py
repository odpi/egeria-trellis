"""The as-of date of each evidence section in a stored compile (docs/design-notes/BRIEF-CONTEXT-SECTION-AS-OF.md).

One place answers "how old was the evidence for section X", for the closed-loop diagnosis (`feedback_diagnosis`) and
the journal alike, so the three readers the brief names cannot drift apart again (§3d).

Where a date can be, in order of precedence:
- `manifest.as_of_seen`: the history a repeat compile keeps (§3h). Asked about a moment (`at`), the newest entry
  at or before it wins, so an answer is judged by the dates current when it was given. An entry with an empty
  `seen_at` holds the first compile's dates, from before any repeat.
- `packed[i].as_of` on an evidence entry (trellis-context 0.2.0): refreshed to the latest on every repeat.
- `manifest.declared[key].as_of`: where a declared section's date lived before 0.2.0, and still the only one in
  older manifests.

The pointer's `as_of` is not read. It says when the linked view's data is from, which is a different question.
"""
from __future__ import annotations

from datetime import datetime

#: How many repeat-compile date changes a stored manifest keeps (§3h). The first compile's own entry is kept
#: beyond the cap, so any answer older than the kept window still finds the dates it was judged by.
AS_OF_SEEN_CAP = 20


def _when(value: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else None


def is_evidence(entry: dict) -> bool:
    """A packed entry that is evidence. A missing role is read as evidence, so older manifests still read; the
    always-packed `instructions` section is not."""
    return isinstance(entry, dict) and bool(entry.get("key")) and entry.get("role", "evidence") == "evidence"


def packed_as_ofs(manifest: dict) -> dict[str, str]:
    """{evidence section: as-of} as the packed entries hold them now (the latest dates)."""
    return {p["key"]: str(p["as_of"]) for p in manifest.get("packed") or [] if is_evidence(p) and p.get("as_of")}


def section_as_ofs(manifest: dict, at: str = "") -> dict[str, str]:
    """{section: as-of} for a stored compile, never guessed: a section with no date is left out.

    `at` (an ISO time with a zone, e.g. when an answer was given) selects the history entry current at that
    moment. Without it, or when it cannot be read, the latest dates are used.
    """
    out: dict[str, str] = {}
    for key, seen in (manifest.get("declared") or {}).items():
        if isinstance(seen, dict) and seen.get("as_of"):
            out[key] = str(seen["as_of"])
    out.update(packed_as_ofs(manifest))
    when = _when(at) if at else None
    history = [h for h in manifest.get("as_of_seen") or [] if isinstance(h, dict)]
    if when is not None and history:
        chosen = None
        for h in history:
            seen_at = _when(h.get("seen_at") or "")
            if not h.get("seen_at") or (seen_at is not None and seen_at <= when):
                chosen = h  # history is appended in time order, so the last match is the newest
        if chosen is not None:
            out.update({k: str(v) for k, v in (chosen.get("as_of") or {}).items() if v})
    return out


def refreshed(stored: dict, fresh: dict, seen_at: str) -> dict | None:
    """The stored manifest with a repeat compile's dates applied, or None when nothing changed (§3h).

    A repeat compile has the same text, so it is the same compile id; only the age of its evidence can move. The
    packed entries take the fresh dates, and the change is appended to `as_of_seen`. The first time, the original
    dates go in first with an empty `seen_at`, so answers given before any repeat keep theirs.

    A section whose date the fresh compile lost (a fact read that failed, say) keeps its stored date: nothing newer
    is known, and dropping a date is not news about the evidence. Its key is left out of the new history entry, so
    a reader at a later moment falls through to the stored date.
    """
    before, after = packed_as_ofs(stored), packed_as_ofs(fresh)
    if not after or before == after:
        return None
    fresh_by_key = {p.get("key"): p for p in fresh.get("packed") or [] if is_evidence(p)}
    packed = []
    for p in stored.get("packed") or []:
        q = fresh_by_key.get(p.get("key")) if is_evidence(p) else None
        if q is not None and q.get("as_of"):
            p = {**{k: v for k, v in p.items() if k not in ("as_of", "as_of_source", "as_of_newest")},
                 **{k: q[k] for k in ("as_of", "as_of_source", "as_of_newest") if q.get(k)}}
        packed.append(p)
    history = [h for h in stored.get("as_of_seen") or [] if isinstance(h, dict)]
    if not history:
        history = [{"seen_at": "", "as_of": before}]
    history.append({"seen_at": seen_at, "as_of": after})
    if len(history) > AS_OF_SEEN_CAP:
        history = history[:1] + history[-(AS_OF_SEEN_CAP - 1):]
    return {**stored, "packed": packed, "as_of_seen": history}
