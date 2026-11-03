"""A file system's narrative passages, as one section of a chat compile (Files follow-up 1, the reader half of F11).

`narrative_docs` presses each narrative file (README, codebook, data dictionary) into `fs_<slug>_narratives`. This is
what a chat question scoped to that file system reads back: the top passages for the question, packed within the
compile's own budget and cited as `stated by <path> § <section>`, each a proposal, never a measured fact.

Three states, each in its own words, because "no narratives" is a claim this never makes:

* `not_read`   the file system was never pressed: "not read yet" (the screen's word, `narrative_docs.view`);
* `no_match`   it was pressed and no passage matched the question: the files may say it in other words;
* `unavailable` the search index could not be reached: said as a failure, never as an absence.

Rows written before the stated_by change carry a bare file name; they are cited as they hold it, with the metadata's
`file_path` added when present. Nothing is re-pressed here and nothing is written.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from trellis_artifact_tree.model import Rung

log = logging.getLogger(__name__)

SECTION_KEY = "narratives"
#: Passages searched for one question: the packer's budget decides how many words of them reach the model.
TOP_K = 5
FULL_CLIP = 900
SUMMARY_CLIP = 160
PROPOSAL = "proposal"
LABEL = "a proposal: what the file says, not a measured fact"

NOT_READ_WORDS = "not read yet"
NOT_READ = (f"Narrative files of this file system: {NOT_READ_WORDS}. Its README and notes have not been pressed into "
            "the search index, so this says nothing about what they contain.")
NO_MATCH = ("Narrative files of this file system: read, and no passage matched this question. The files may still say "
            "it in other words; do not tell the person there are no narratives.")
UNAVAILABLE = ("Narrative files of this file system: the search index could not be reached, so they were not "
               "searched. This is not a finding about the files.")


def min_score() -> float:
    """RE's calibrated retrieval threshold (`rag.min_score`, cosine): below it a nearest neighbour is not a match.
    Passed explicitly, so a hit is never just "the closest row in a collection that has rows"."""
    from resource_explorer.config import get_config

    return float(get_config().rag.min_score)


def citation(meta: dict) -> str:
    """`stated by <path> § <section>`: whatever stated_by the row holds, and for a row from an old press (a bare file
    name) the metadata's file_path as well, when it is not already in it."""
    stated = (meta.get("stated_by") or "").strip()
    path = (meta.get("file_path") or "").strip()
    if not stated:
        stated = path
        if path and meta.get("section"):
            from resource_explorer.surveyors.filesystem.narrative_docs import stated_by_of
            stated = stated_by_of(path, meta["section"])
    elif path and path not in stated:
        stated = f"{stated} ({path})"
    return f"stated by {stated}"


def passage_of(result) -> dict:
    meta = result.metadata or {}
    walk_run, path, section = meta.get("walk_run") or "", meta.get("file_path") or "", meta.get("section") or ""
    pid = ""
    if walk_run and path:
        from resource_explorer.surveyors.filesystem.narrative_docs import passage_id
        pid = passage_id(walk_run, path, section)
    return {"passage_id": pid, "walk_run": walk_run, "file_path": path, "section": section,
            "stated_by": meta.get("stated_by") or "", "status": PROPOSAL, "citation": citation(meta),
            "score": round(float(getattr(result, "score", 0.0) or 0.0), 3), "text": result.text or ""}


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _rungs(passages: list[dict]) -> dict[Rung, str]:
    head = f"Narrative passages ({LABEL}); cite one as written:"
    full = [head] + [f"- [{p['citation']} — {PROPOSAL}] {_clip(p['text'], FULL_CLIP)}" for p in passages]
    summary = [head] + [f"- [{p['citation']} — {PROPOSAL}] {_clip(p['text'], SUMMARY_CLIP)}" for p in passages]
    ids = [f"Narrative passages ({PROPOSAL}), text left out to fit the budget:"] + \
          [f"- {p['citation']} — {PROPOSAL}" for p in passages]
    return {Rung.FULL: "\n".join(full), Rung.SUMMARY: "\n".join(summary), Rung.IDENTIFIERS: "\n".join(ids)}


def build(registry, slug: str, question: str, *,
          store_factory: Callable[[], object] | None = None) -> tuple[dict[Rung, str], dict]:
    """(rungs, info) for the narratives section. `info` = {"state", "passages", "collection"}; the rungs always say
    something, so the section is never silent."""
    from resource_explorer.surveyors.filesystem import narrative_docs as nd

    try:
        pressed = bool(registry.query_step_runs(slug=slug, step_key=nd.STEP_KEY, entity_type="filesystem", limit=1))
    except Exception:
        log.warning("narrative run of %s could not be read", slug, exc_info=True)
        return {r: UNAVAILABLE for r in Rung}, {"state": "unavailable", "passages": [], "collection": ""}
    if not pressed:
        return {r: NOT_READ for r in Rung}, {"state": "not_read", "passages": [], "collection": ""}
    collection = ""
    try:
        store = (store_factory or nd.default_store)()
        collection = nd.collection_for(store, slug)
        if not store.collection_exists(collection):
            # A run row with no collection: the index lost it, so the files are not searchable — not "no hits".
            return {r: UNAVAILABLE for r in Rung}, {"state": "unavailable", "passages": [], "collection": collection}
        hits = store.search(question, [collection], top_k=TOP_K, min_score=min_score())
    except Exception:
        log.warning("narrative search of %s failed", slug, exc_info=True)
        return {r: UNAVAILABLE for r in Rung}, {"state": "unavailable", "passages": [], "collection": collection}
    passages = [passage_of(h) for h in hits[:TOP_K]]
    if not passages:
        return {r: NO_MATCH for r in Rung}, {"state": "no_match", "passages": [], "collection": collection}
    return _rungs(passages), {"state": "found", "passages": passages, "collection": collection}


# ── The Resource Locator: find a file system by what its narrative files say (owner, 2026-10-29) ────────────────────

LOCATOR_LIMIT = 10


def _not_read_words(n: int) -> str:
    return f"{n} file system{'' if n == 1 else 's'} not read yet, not searched" if n else ""


def _unknown_words(n: int) -> str:
    return (f"{n} file system{'' if n == 1 else 's'} whether its narratives were pressed could not be read, "
            "not searched") if n else ""


def locate(registry, question: str, *, store_factory: Callable[[], object] | None = None,
           limit: int = LOCATOR_LIMIT) -> dict:
    """File systems ranked by their best-scoring narrative passage for `question`.

    Only file systems whose narratives were pressed (a `narrative_docs` step run) are searched; the rest are counted and
    named, so an unread file system is never taken for a non-match. A pressed file system whose index cannot be searched
    is listed under `unavailable`, a failure and not an absence. Each match carries the same citation and proposal label
    as chat. `state`: found | no_match | none_pressed | unavailable (nothing could be searched)."""
    from resource_explorer.surveyors.filesystem import narrative_docs as nd

    pressed: list = []
    not_read: list[str] = []
    unknown: list[str] = []
    for fs in registry.list_filesystems():
        try:
            ran = bool(registry.query_step_runs(slug=fs.slug, step_key=nd.STEP_KEY, entity_type="filesystem", limit=1))
        except Exception:
            # Whether it was pressed is not known: neither "not read yet" nor searchable.
            log.warning("narrative run of %s could not be read", fs.slug, exc_info=True)
            unknown.append(fs.slug)
            continue
        (pressed if ran else not_read).append(fs)
    base = {"question": question, "not_read": [f.slug for f in not_read], "not_read_words": _not_read_words(len(not_read)),
            "searched": [], "unavailable": [], "unknown": unknown, "unknown_words": _unknown_words(len(unknown)),
            "matches": []}
    if not pressed:
        said = ". ".join(w for w in (_not_read_words(len(not_read)), base["unknown_words"]) if w) or "no file systems"
        return {**base, "state": "none_pressed",
                "words": said + ". Nothing was searched, so this says nothing about what any file says."}
    matches: list[dict] = []
    floor = min_score()
    try:
        store = (store_factory or nd.default_store)()
    except Exception:
        log.warning("narrative index unreachable for the locator", exc_info=True)
        return {**base, "unavailable": [f.slug for f in pressed], "state": "unavailable", "words": UNAVAILABLE}
    for fs in pressed:
        try:
            collection = nd.collection_for(store, fs.slug)
            if not store.collection_exists(collection):
                base["unavailable"].append(fs.slug)
                continue
            hits = store.search(question, [collection], top_k=1, min_score=floor)
        except Exception:
            log.warning("narrative search of %s failed", fs.slug, exc_info=True)
            base["unavailable"].append(fs.slug)
            continue
        base["searched"].append(fs.slug)
        if hits:
            p = passage_of(hits[0])
            matches.append({"slug": fs.slug, "display_name": getattr(fs, "display_name", "") or fs.slug,
                            "score": p["score"], "citation": p["citation"], "status": PROPOSAL, "label": LABEL,
                            "passage_id": p["passage_id"], "text": _clip(p["text"], FULL_CLIP)})
    matches.sort(key=lambda m: (-m["score"], m["slug"]))
    base["matches"] = matches[:limit]
    if not base["searched"]:
        return {**base, "state": "unavailable", "words": UNAVAILABLE}
    state = "found" if matches else "no_match"
    words = (f"{len(matches)} of {len(base['searched'])} searched file system{'' if len(base['searched']) == 1 else 's'} "
             "matched, ranked by best passage" if matches else
             "read, and no passage matched this question. The files may still say it in other words")
    extra = [w for w in (base["not_read_words"], base["unknown_words"],
                         f"{len(base['unavailable'])} could not be searched (index unreachable)" if base["unavailable"] else "")
             if w]
    return {**base, "state": state, "words": " · ".join([words, *extra])}
