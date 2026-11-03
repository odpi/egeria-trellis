"""The journal: why a resource matters, written to be read by someone else.

Everything else RE holds about a resource is written to be CORRECT — a
measurement, a verdict, a field with an author. A journal entry is written
to be READ: prose, dated, addressed to someone. Curation is not only getting
the record right; it is advocacy, and a curator who catalogues a resource
accurately and tells nobody has done half the job.

Three properties follow, and each is a relief:

* **Append-only.** An entry is a statement someone made on a date. Later
  events do not invalidate it — they get a later entry. So it sits outside
  the durable/perishable split entirely: no review flags, nothing to
  reconcile, never overwritten.
* **It has an audience, and the product already knows who.** Suggesting a
  resource is a routing question, and perspectives answer it — a note on a
  data-heavy repo is for Data Experts and Consumers, the same tags on every
  question row. So suggestion is "pick a perspective, or pick a person",
  not a share dialogue.
* **A suggestion arrives as a work-list entry, not a notification.** Work
  lists already exist, carry counts, and survive being ignored for a
  fortnight; a notification is a thing you dismiss. One list per audience —
  "Suggested to Data Expert" — the resource as a member, the entry as its
  rationale. The same promote mechanism the sub-resources and selections
  already use.

What this deliberately does not do: block cataloguing on an entry. Advocacy
written to satisfy a required field produces "useful library" on two
hundred assets. The empty state is visible instead — catalogued, never
written about — which a corpus view can count.

The author is stamped by the caller from the signed-in identity; this
module never takes one on trust from a payload.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from resource_explorer.registry import ProjectRegistry
from resource_explorer.work_lists import WorkLists

SUGGESTION_PREFIX = "suggested-to-"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# The resource_journal table is created by ProjectRegistry._init_schema (a migration), not here.


def audience_slug(target: str) -> str:
    """`Data Expert` -> `suggested-to-data-expert`; `peterprofile` ->
    `suggested-to-peterprofile`. One work list per audience, found by slug."""
    return SUGGESTION_PREFIX + re.sub(r"[^a-z0-9]+", "-", target.strip().lower()).strip("-")


class Journal:
    def __init__(self, registry: ProjectRegistry | None = None):
        self.registry = registry or ProjectRegistry()

    def _conn(self):
        return self.registry._conn()

    def entries(self, entity_type: str, entity_slug: str) -> list[dict]:
        """Newest first. Every entry ever written; there is no delete."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, author, written_at, body, suggested_to, compile_id, note, turn_json FROM resource_journal "
                "WHERE entity_type = ? AND entity_slug = ? ORDER BY written_at DESC",
                (entity_type, entity_slug),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r) if not isinstance(r, dict) else dict(r)
            try:
                targets = json.loads(d.get("suggested_to") or "[]")
            except (ValueError, TypeError):
                targets = []
            # Always a plain list, for every row (an older build packed an object here; none was ever released).
            d["suggested_to"] = targets if isinstance(targets, list) else []
            raw_turn, note = d.pop("turn_json", None), d.pop("note", None)
            # Only an entry made from a chat turn carries these; an older or hand-written entry has neither key.
            if raw_turn:
                try:
                    d["turn"] = json.loads(raw_turn)
                except (ValueError, TypeError):
                    pass
            if note:
                d["note"] = note
            out.append(d)
        return out

    def write(self, entity_type: str, entity_slug: str, *, author: str, body: str,
              suggest_to: list[str] | None = None, compile_id: str | None = None,
              turn: dict | None = None, note: str | None = None) -> dict:
        """Append one entry, and route it. Returns the entry plus the work
        lists the suggestion landed in, so the UI can say where it went
        rather than "sent"."""
        if not author:
            raise ValueError("a journal entry needs an author")
        body = (body or "").strip()
        if not body:
            raise ValueError("a journal entry needs a body")
        targets = [t.strip() for t in (suggest_to or []) if t and t.strip()]
        compile_id = (compile_id or "").strip() or None
        note = (note or "").strip()
        entry_id = uuid.uuid4().hex
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO resource_journal (id, entity_type, entity_slug, author, written_at, body, suggested_to, "
                "compile_id, note, turn_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, entity_type, entity_slug, author, now, body, json.dumps(targets), compile_id,
                 note or None, json.dumps(turn) if turn is not None else None),
            )
        landed = [self._route(target, entity_type, entity_slug, author, body) for target in targets]
        out = {"id": entry_id, "author": author, "written_at": now, "body": body,
               "suggested_to": targets, "compile_id": compile_id, "work_lists": landed}
        if turn is not None:
            out["turn"] = turn
        if note:
            out["note"] = note
        return out

    def _route(self, target: str, entity_type: str, entity_slug: str, author: str, body: str) -> dict:
        """One work list per audience, created on first use; the resource
        joins it with the entry as its rationale. A second suggestion of the
        same resource to the same audience updates the rationale rather than
        adding a second row — set_member upserts."""
        wl = WorkLists(self.registry)
        slug = audience_slug(target)
        name = f"Suggested to {target}"
        existing = wl.get(slug)
        if existing is None:
            wl.create(name, [entity_slug], slug=slug, entity_type=entity_type,
                      created_by=author, derived_from="journal",
                      description="Resources someone wrote about and suggested to this audience. "
                                  "Each member's rationale is their journal entry.",
                      rationale=body)
        else:
            # As its own kind: an audience's list collects resources of any kind (§5).
            wl.set_member(slug, entity_slug, rationale=body, entity_type=entity_type)
            name = (existing.get("display_name") if isinstance(existing, dict)
                    else getattr(existing, "display_name", None)) or name
        # The NAME is where it landed; the slug is how the server finds it.
        return {"target": target, "work_list": slug, "name": name}

    def suggested_targets(self, entity_type: str, entity_slug: str) -> list[str]:
        """Everyone this resource has ever been suggested to."""
        seen: list[str] = []
        for e in self.entries(entity_type, entity_slug):
            for t in e.get("suggested_to") or []:
                if t not in seen:
                    seen.append(t)
        return seen


# ── the entry IS the chat turn ──────────────────────────────────────────────────────────────────────────────
# The journal entry is derived from the turn alone: the question as typed, the answer as shown, the evidence the
# compile packed, its as-of and confidence words, and the scope. The person adds a confirmation and, optionally,
# one free-text note kept apart from the derived text. The pane's per-turn control and the chat's `write_journal`
# tool both end in `write_turn_entry`, so they cannot write different rows for the same turn.

NO_INVESTIGATION = "no investigation is open · choose one on the rail"
PROPOSAL_WORDS = "Add this turn to the investigation journal? [Add] [Not now]"
NO_EARLIER_ANSWER = "There is no earlier answer in this chat to add."
SIGN_IN_WORDS = "Sign in to write in the journal — an entry needs an author."
SAVED_WORDS = "Added to the investigation journal."
ALREADY_WORDS = "Already in the investigation journal."

#: A request for the journal, in the shapes the pane has a control for: "save this to the journal", "add that to the
#: investigation journal", "put it in my journal", "note this in the journal", "journal this". It needs the verb,
#: an object that points at the last answer (this/that/it/the answer), and "to/in/into … journal" (or the bare
#: "journal this"). It starts at the verb, so a question ("What did I add to the journal yesterday?", "How do I log
#: findings in the journal?") and a statement that merely contains the words ("Note that the orders table is
#: partitioned; how many rows?", "add a column named journal") never match, and it takes no question mark.
_OBJECT = r"(?:this|that|it)(?:\s+(?:answer|turn|result|response|one))?|the\s+(?:last\s+|previous\s+)?(?:answer|turn|result|response)"
JOURNAL_VERB = re.compile(
    rf"^\s*(?:(?:please|pls)[\s,]+)?(?:"
    rf"(?:save|add|put|write|record|log|file|store|keep|enter|note)\s+(?:{_OBJECT})\s+(?:to|in|into|on)\s+"
    rf"(?:(?:the|my|our|this)\s+)?(?:[a-z-]+\s+){{0,2}}journal"
    rf"|journal\s+(?:{_OBJECT})"
    rf")(?:[\s,]+(?:please|pls|thanks))?[\s.!]*$",
    re.IGNORECASE,
)

#: What the chat says when it only handled a journal request. A stored exchange carrying one of these answers is a
#: journal exchange, never "this" (see `last_turn_exchange`).
SERVICE_ANSWERS = (PROPOSAL_WORDS, SAVED_WORDS, ALREADY_WORDS, NO_EARLIER_ANSWER, SIGN_IN_WORDS, NO_INVESTIGATION)


def is_journal_request(query: str) -> bool:
    return bool(JOURNAL_VERB.search(query or ""))


def is_service_exchange(ex: dict) -> bool:
    """A journal exchange itself (the request, the proposal, the saved/refused words): never the turn to add."""
    return (ex.get("answer") or "").strip() in SERVICE_ANSWERS or is_journal_request(ex.get("question") or "")


def exchange_is_skipped(exchanges: list[dict], i: int) -> bool:
    """Whether exchange `i` of a NEWEST-FIRST list is one "this" can never mean: a journal exchange, or the person's
    reply to a proposal. After "yes" the model answers in its own words, which are no service answer, so the
    exchange right after a proposal is skipped by position. The chat tool and the pane's route both ask this."""
    ex = exchanges[i]
    if is_service_exchange(ex):
        return True
    older = exchanges[i + 1] if i + 1 < len(exchanges) else None
    return older is not None and (older.get("answer") or "").strip() == PROPOSAL_WORDS


def last_turn_exchange(exchanges: list[dict]) -> dict | None:
    """The turn "this" means, from the session's exchanges NEWEST FIRST: the first one not skipped."""
    for i, ex in enumerate(exchanges):
        if not exchange_is_skipped(exchanges, i):
            return ex
    return None


def _as_ofs_of(compile_row: dict | None) -> dict[str, str]:
    """{section: the as-of the compile declared for it}, never guessed. A declared section's as-of is in
    `manifest.declared`; a packed section's pointer may carry one. A section with none is left out, not filled in."""
    manifest = (compile_row or {}).get("manifest") or {}
    out: dict[str, str] = {}
    for p in manifest.get("packed") or []:
        if isinstance(p, dict) and p.get("key") and (p.get("pointer") or {}).get("as_of"):
            out[p["key"]] = str(p["pointer"]["as_of"])
    for key, seen in (manifest.get("declared") or {}).items():
        if isinstance(seen, dict) and seen.get("as_of"):
            out[key] = str(seen["as_of"])
    return out


def _rows_of(compile_row: dict | None) -> dict[str, list[str]]:
    """{section: the stored row ids its text was built from}, as the compile recorded them (`manifest.rows_seen`)."""
    seen = ((compile_row or {}).get("manifest") or {}).get("rows_seen") or {}
    out: dict[str, list[str]] = {}
    for key, kinds in seen.items():
        ids = [i for v in (kinds or {}).values() for i in (v if isinstance(v, list) else (v or {}).get("FULL") or [])]
        if ids:
            out[key] = [str(i) for i in ids]
    return out


def _as_of_words(as_ofs: dict[str, str]) -> str:
    """One as-of when every section agrees, else each section's own (never one picked for them)."""
    if not as_ofs:
        return "not recorded"
    if len(set(as_ofs.values())) == 1:
        return next(iter(as_ofs.values()))
    return "differs by section: " + "; ".join(f"{k} {v}" for k, v in as_ofs.items())


def _evidence_of(compile_row: dict | None) -> tuple[list[str], str | None, str]:
    """(evidence keys, as-of, confidence words) read from a stored compile, never guessed. A turn with no compile
    says so in the confidence words; an as-of nobody recorded is None, which the body renders as "not recorded"."""
    if not compile_row:
        return [], None, "no compile recorded, so confidence is not established"
    manifest = compile_row.get("manifest") or {}
    packed = [p for p in (manifest.get("packed") or []) if isinstance(p, dict)]
    keys: list[str] = []
    for p in packed:
        # A multi-resource compile packs the same analysis for several resources; each entry names its resource.
        key = p.get("key") and (f"{p['key']} ({p['resource_slug']})" if p.get("resource_slug") else p["key"])
        if key and key not in keys:
            keys.append(key)
    as_of = max(_as_ofs_of(compile_row).values(), default=None)
    gaps = [g.get("key") for g in (manifest.get("gaps") or []) if isinstance(g, dict) and g.get("key")]
    if (manifest.get("coverage") or {}).get("kind") == "none":
        confidence = "no stored analysis answers this question"
    else:
        confidence = f"answered from {len(keys)} stored result{'' if len(keys) == 1 else 's'}"
        if gaps:
            confidence += f"; {len(gaps)} not measured ({', '.join(gaps)})"
        # The weakest declared confidence form the answer rests on (DESIGN-DECLARED-EXTENSIBILITY §4: "reports the
        # weakest link, not an average"), named with the sections that carry it. Absent from compiles before slice 2.
        conf = manifest.get("confidence") or {}
        weakest = conf.get("weakest") if isinstance(conf, dict) else ""
        if weakest:
            by = [k for k, f in (conf.get("by_section") or {}).items() if f == weakest]
            confidence += f"; rests at weakest on {'an' if weakest[0] in 'aeiou' else 'a'} {weakest} result"
            confidence += f" ({', '.join(by)})" if by else ""
    return keys, as_of, confidence


def _passages_of(compile_row: dict | None) -> list[str]:
    """The ids (walk run | path | section) of the narrative passages a stored compile packed, never guessed: an entry
    that names no passage adds nothing. Two README.md files differ here by path, which `Evidence` keys alone cannot say."""
    packed = [p for p in ((compile_row or {}).get("manifest") or {}).get("packed") or [] if isinstance(p, dict)]
    out: list[str] = []
    for p in packed:
        for q in p.get("passages") or []:
            pid = q.get("passage_id") if isinstance(q, dict) else ""
            if pid and pid not in out:
                out.append(pid)
        ptr = p.get("pointer") if isinstance(p.get("pointer"), dict) else {}
        pid = p.get("passage_id") or ptr.get("passage_id")
        walk_run, path = p.get("walk_run") or ptr.get("walk_run"), p.get("file_path") or ptr.get("file_path")
        if not pid and walk_run and path:
            from resource_explorer.surveyors.filesystem.narrative_docs import passage_id
            pid = passage_id(walk_run, path, p.get("section") or ptr.get("section") or "")
        if pid and pid not in out:
            out.append(pid)
    return out


def turn_key(question: str, answer: str) -> str:
    """The dedupe identity of a turn: the same question and answer always give the same key. (Not the compile id
    alone: a compile id hashes the compile's inputs, so two different questions can share one.)"""
    return hashlib.sha256(f"{(question or '').strip()}\x00{(answer or '').strip()}".encode()).hexdigest()[:32]


def derive_turn_entry(registry, *, question: str, answer: str, compile_id: str | None,
                      investigation: str, resource_slug: str | None) -> dict:
    """The derived entry for one chat turn: {"body", "turn"}. Pure of the person's say-so: the same turn always
    derives the same body, which is what lets two write paths agree."""
    compile_id = (compile_id or "").strip() or None
    row = None
    if compile_id:
        try:
            row = registry.get_compile(compile_id)
        except Exception:
            row = None
    keys, as_of, confidence = _evidence_of(row)
    passages = _passages_of(row)
    as_ofs, rows = _as_ofs_of(row), _rows_of(row)
    if compile_id and row is None:
        confidence = f"compile {compile_id[:8]} is not on file, so confidence is not established"
    scope = f"investigation {investigation}" + (f" - resource {resource_slug}" if resource_slug else "")
    body = "\n".join([
        "Per chat",
        "",
        f"Question: {(question or '').strip()}",
        "",
        "Answer:",
        "",
        (answer or "").strip(),
        "",
        f"- Scope: {scope}",
        f"- As of: {_as_of_words(as_ofs)}",
        f"- Confidence: {confidence}",
        f"- Evidence: {', '.join(keys) if keys else 'none recorded'}",
        f"- Compile: {compile_id[:8] if compile_id else 'none recorded'}",
        *([f"- Passages: {'; '.join(passages)}"] if passages else []),
        *([f"- Rows: {'; '.join(f'{k}: ' + ', '.join(v) for k, v in rows.items())}"] if rows else []),
    ])
    turn = {"question": (question or "").strip(), "answer": (answer or "").strip(),
            "resource_slug": resource_slug or None, "evidence": keys, "as_of": as_of, "as_ofs": as_ofs,
            "rows_seen": rows, "confidence": confidence, "scope": scope, "compile_id": compile_id, "passages": passages, "turn_key": turn_key(question, answer)}
    return {"body": body, "turn": turn}


def find_turn_entry(registry, *, investigation: str, author: str, key: str) -> dict | None:
    """The entry this author already made from this turn in this investigation, or None."""
    for e in Journal(registry).entries("investigation", investigation):
        if e.get("author") == author and (e.get("turn") or {}).get("turn_key") == key:
            return e
    return None


def write_turn_entry(registry, *, investigation: str, author: str, question: str, answer: str,
                     compile_id: str | None, resource_slug: str | None, note: str | None = None) -> dict:
    """The one write for a chat-turn entry. Called by the route behind the pane's control and by the chat tool.
    Idempotent per (investigation, author, turn): a second call writes no second row and returns the first entry
    with `already: True`."""
    if not (investigation or "").strip():
        raise ValueError(NO_INVESTIGATION)
    if not (answer or "").strip():
        raise ValueError(NO_EARLIER_ANSWER)
    derived = derive_turn_entry(registry, question=question, answer=answer, compile_id=compile_id,
                                investigation=investigation, resource_slug=resource_slug)
    existing = find_turn_entry(registry, investigation=investigation, author=author, key=derived["turn"]["turn_key"])
    if existing is not None:
        return {**existing, "already": True}
    entry = Journal(registry).write("investigation", investigation, author=author, body=derived["body"],
                                    compile_id=compile_id, turn=derived["turn"], note=note)
    return {**entry, "already": False}
