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
                "SELECT id, author, written_at, body, suggested_to FROM resource_journal "
                "WHERE entity_type = ? AND entity_slug = ? ORDER BY written_at DESC",
                (entity_type, entity_slug),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r) if not isinstance(r, dict) else r
            try:
                d["suggested_to"] = json.loads(d.get("suggested_to") or "[]")
            except ValueError:
                d["suggested_to"] = []
            out.append(d)
        return out

    def write(self, entity_type: str, entity_slug: str, *, author: str, body: str,
              suggest_to: list[str] | None = None) -> dict:
        """Append one entry, and route it. Returns the entry plus the work
        lists the suggestion landed in, so the UI can say where it went
        rather than "sent"."""
        if not author:
            raise ValueError("a journal entry needs an author")
        body = (body or "").strip()
        if not body:
            raise ValueError("a journal entry needs a body")
        targets = [t.strip() for t in (suggest_to or []) if t and t.strip()]
        entry_id = uuid.uuid4().hex
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO resource_journal (id, entity_type, entity_slug, author, written_at, body, suggested_to) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry_id, entity_type, entity_slug, author, now, body, json.dumps(targets)),
            )
        landed = [self._route(target, entity_type, entity_slug, author, body) for target in targets]
        return {"id": entry_id, "author": author, "written_at": now, "body": body,
                "suggested_to": targets, "work_lists": landed}

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
            wl.set_member(slug, entity_slug, rationale=body)
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
