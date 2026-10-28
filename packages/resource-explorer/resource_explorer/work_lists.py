"""Work lists — a named set of resources you are working through, and the
batch runs launched across one.

This is the storage and Egeria half of the `/next` Scouting slice: search →
select → **save as a work list** → **run one survey across the whole set,
concurrently** → compare → disposition in bulk → promote the survivors.

Self-contained on purpose. Everything the feature needs is here — its two
tables, its Egeria publish, its batch enqueue — so that if the `/next`
experiment is dropped, this module and its route file go with it and nothing
else has to be unpicked. It reaches into the registry for a connection and
for the run queue rather than growing a second one of either.

Egeria
------
**The type is `WorkingSet`, not `WorkList`.** The design asked for a
"`WorkList`-classified collection"; `WorkList` does not exist in Egeria — it
appears nowhere in the type archive or the framework's `OpenMetadataType`.
`WorkingSet` does, and its own description is exactly this feature:

    "Defines a list of elements that are being worked on by a process or by
     a group of people."  — OpenMetadataType.WORKING_SET_COLLECTION

It is a Collection SUBTYPE, not a classification, so it goes through
`typeName` (see `egeria_investigation_publisher._create_typed_collection`,
which this reuses rather than re-deriving).

`WorkItemList` is the other candidate and is the wrong one here — *"Defines a
list of activities such as ToDos, Tasks, etc..."*. A work list holds the
RESOURCES being worked on, not the activities being done to them. If the runs
launched across a set ever want cataloguing in their own right, that is what
`WorkItemList` would be for, and it would be a second collection beside this
one rather than a change to it.

RE already creates `WorkingSet` collections for an investigation's
per-disposition membership (`registry.get_or_create_disposition_set`), so this
is a second producer of the same subtype. They are told apart by
qualifiedName: work lists are namespaced `WorkingSet::resource-explorer::<slug>`.

`CollectionMembership` genuinely does carry `membershipRationale` — it is a
real `OpenMetadataProperty` — and pyegeria's `add_to_collection` takes an
optional relationship body, so a member's reason travels with it rather than
being local-only. So does its confidence, as Egeria's `confidence` (an int,
0-100). Until 2026-10-13 it was sent as `expectedConfidence`, which is not an
Egeria property: Egeria dropped it and the membership read `confidence: 0`.
The names are built in one place, `egeria_outbox.collection_membership_properties`.

**Publishing is explicit, never automatic.** Creating a work list writes
locally and nothing else. `publish_work_list()` is what enqueues the Egeria
writes, and even then they are queued in the outbox and applied by whichever
worker drains it — which is the pattern every other Egeria write here uses,
and is what keeps a half-finished publish from being invisible.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from resource_explorer.resource_types import SURVEYED_RESOURCE_TYPES

log = logging.getLogger(__name__)

#: The Egeria Collection subtype. See the module docstring for why this is not
#: `WorkList`.
EGERIA_TYPE_NAME = "WorkingSet"

#: The states a run can END in.
#:
#: MEASURED, not guessed. The first version of this invented `done` and left
#: out `claimed`, so a set whose runs had all SUCCEEDED reported
#: `finished 0/2, complete false` — forever. The UI polls on `complete`, so a
#: finished batch would have polled until the tab closed and the grid would
#: never have refreshed. Nothing errored; the numbers were simply always
#: wrong, which is the shape this project keeps finding.
#:
#: `ProjectRegistry.RUN_STATES` is the authority. This is kept as an explicit
#: subset rather than "everything that is not active", so that a NEW state
#: added upstream shows up here as unrecognised instead of being silently
#: counted as finished.
TERMINAL_RUN_STATES = ("succeeded", "failed", "cancelled")

#: What a run is doing while it is not finished. `claimed` is a real state —
#: a worker has taken the row but not started it — and omitting it is how a
#: run in flight gets counted as neither running nor done.
ACTIVE_RUN_STATES = ("queued", "claimed", "running")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Keeping the published WorkingSet in step (the Portal's ask, 2026-10-12) ──

#: The additionalProperties key the Portal's Resource Browser reads to link a WorkingSet back to RE.
RESOURCE_EXPLORER_URL_KEY = "resourceExplorerUrl"

#: Outbox states of a row that may still be sent (cancellable), and of one being sent now.
_UNSENT = ("pending", "failed")
_SENDING = ("running",)

#: At most this many removals are applied inside one publish request; the rest are left to the background drain.
INLINE_DETACH_LIMIT = 50

#: At most this many adds are applied inside one publish request (owner's ruling 2026-10-13: adds drain inline, as
#: removals do); the rest are left to the background drain and said so.
INLINE_ATTACH_LIMIT = INLINE_DETACH_LIMIT

#: The per-member states a publish's adds come back in, read from each outbox row AFTER the inline drain.
ATTACHED, ATTACH_NOT_PERMITTED, ATTACH_FAILED, ATTACH_LEFT = (
    "attached", "not permitted", "could not attach", "left to the background drain")


def public_base_url() -> str:
    """`RE_PUBLIC_URL`, checked: an http(s) URL with a host, without a trailing slash. Anything else is treated as
    not configured (with a warning), so a typo never becomes a link the Portal shows."""
    from urllib.parse import urlsplit

    from resource_explorer.config import WebUIConfig

    raw = (WebUIConfig().public_url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        log.warning("RE_PUBLIC_URL %r is not an http(s) URL with a host; no link back is written", raw)
        return ""
    return raw.rstrip("/")


def resource_explorer_url(slug: str, entity_type: str = "repo") -> str:
    """The /next work-list page for `slug` on RE's configured public URL (`RE_PUBLIC_URL`), in the shape the page
    writes itself (`next/app.js` `writeUrl`: `type=` only for a non-repo kind, then `worklist=`). "" when no valid
    public URL is configured: the key is then left out, never a guessed localhost."""
    from urllib.parse import urlencode

    base = public_base_url()
    if not base:
        return ""
    query = {"type": entity_type} if entity_type and entity_type != "repo" else {}
    query["worklist"] = slug
    return f"{base}/next?{urlencode(query)}"


def _additional_properties(element) -> dict:
    """The additionalProperties of a collection read back with `get_collection_by_guid(output_format="JSON")`:
    {} when it has none. Raises when the answer is not an element (pyegeria answers a string for "not found")."""
    if isinstance(element, list) and len(element) == 1:
        element = element[0]
    if not isinstance(element, dict):
        raise ValueError(f"no collection element in the answer: {str(element)[:120]}")
    props = element.get("properties") if isinstance(element.get("properties"), dict) else element
    found = props.get("additionalProperties") or props.get("additional_properties") or {}
    return dict(found) if isinstance(found, dict) else {}


#: The resource types a work list's members can be, and so the ones `member_egeria_guid` knows how to read.
MEMBER_ENTITY_TYPES = SURVEYED_RESOURCE_TYPES


class MemberUnresolved(Exception):
    """RE could not say whether a work-list member has an Egeria element: an unsupported type, a member that is
    not in RE's registry, or a registry read that failed. Never the same answer as "not published to Egeria yet"."""


def member_egeria_guid(registry, entity_type: str, slug: str) -> str:
    """A work-list member's own Egeria asset GUID, by its type; "" means RE holds the resource and it has no
    Egeria element yet. The ONE lookup every work-list path uses (publish's attach split, the removals' map).

    The getters are `curation_egeria._entity`'s, the per-type read the curation paths already use
    (`registry.get` / `get_database` / `get_filesystem`). Raises `MemberUnresolved` when the answer is not known:
    a type with no getter, a slug RE does not hold, or a read that failed. A bare `registry.get(slug)` answered
    None for every database and file-system member, which read as "never published" (wl-0c09fe4404, 2026-10-13)."""
    from resource_explorer.curation_egeria import _entity

    if entity_type not in MEMBER_ENTITY_TYPES:
        raise MemberUnresolved(f"members of type '{entity_type or '(none)'}' are not supported")
    try:
        entity = _entity(registry, entity_type, slug)
    except Exception as exc:  # noqa: BLE001 - a failed read is "could not resolve", never "not published"
        raise MemberUnresolved(f"could not read RE's record · {type(exc).__name__}: {exc}"[:300]) from exc
    if entity is None:
        raise MemberUnresolved(f"no {entity_type} '{slug}' in RE's registry")
    return str(getattr(entity, "egeria_asset_guid", "") or "").strip()


def not_published_words(entity_type: str) -> str:
    return f"not published to Egeria yet · {entity_type}"


def _refused(row: dict) -> bool:
    """An add that was REFUSED (the drain's zone re-check, or Egeria's security) was never applied: it cannot have
    landed, so it is not "maybe attached"."""
    from resource_explorer.egeria_outbox import REFUSED_BY_SECURITY, SECURITY_REFUSAL_MARKER

    words = (row.get("last_error") or "").strip()
    return words.startswith("not permitted") or SECURITY_REFUSAL_MARKER in words or REFUSED_BY_SECURITY in words


def recorded_memberships(registry, slug: str, collection_guid: str) -> dict[str, dict]:
    """What RE's OWN outbox says about each member it has written to this WorkingSet, by member GUID. The one
    source both the publish's removals and their counts are taken from (never a fresh Egeria read).

    Rows are replayed oldest first, for this collection only (an Egeria reset gives the list a new GUID):

    * `attached`: the last settled write was a done add (a done removal clears it);
    * `maybe`: an add was ATTEMPTED and not confirmed (failed, dead or cancelled with attempts > 0): a timed-out add
      may have landed, so it counts as possibly attached (a done removal clears it). Not for a REFUSED add (the
      drain's "not permitted", or Egeria's security refusal): it was never applied;
    * `unsent_adds`: add rows not yet applied (pending, or failed and backing off): cancellable;
    * `never_sent_adds`: those of them with no attempt at all;
    * `adding`: an add row is being sent right now;
    * `last_add_id`: the newest add row, which names the removal that follows it (one removal per add cycle);
    * `unsent_removals`: removal rows not yet applied (pending, or failed): cancellable;
    * `removing`: a removal row is unsettled (pending, failed or running).

    A cancelled or dead row never attempted changes nothing: it was never sent."""
    from resource_explorer.egeria_outbox import COLLECTION_MEMBERSHIP_DETACH

    rows = registry.list_outbox_rows_for_kinds("work_list", slug,
                                               ("collection_membership", COLLECTION_MEMBERSHIP_DETACH))
    out: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: int(r["id"])):
        try:
            payload = json.loads(row.get("payload_json") or "{}") or {}
        except (TypeError, ValueError):
            continue
        if str(payload.get("collection_guid") or "") != collection_guid or not payload.get("member_guid"):
            continue
        rec = out.setdefault(str(payload["member_guid"]), {
            "entity_slug": "", "attached": False, "maybe": False, "unsent_adds": [], "never_sent_adds": [],
            "adding": False, "last_add_id": 0, "unsent_removals": [], "removing": False})
        rec["entity_slug"] = str(payload.get("member_entity_slug") or rec["entity_slug"])
        status, row_id = row.get("status") or "", int(row["id"])
        attempted = int(row.get("attempts") or 0) > 0
        if row.get("element_kind") == "collection_membership":
            rec["last_add_id"] = row_id
            if status == "done":
                rec["attached"] = True
            elif status in _SENDING:
                rec["adding"] = True
            if status in _UNSENT:
                rec["unsent_adds"].append(row_id)
                if not attempted:
                    rec["never_sent_adds"].append(row_id)
            if status in ("failed", "dead", "cancelled") and attempted and not _refused(row):
                # tried at least once and not refused: may have landed
                rec["maybe"] = True
        elif status == "done":
            rec["attached"] = rec["maybe"] = False
        elif status in _UNSENT + _SENDING:
            rec["removing"] = True
            if status in _UNSENT:
                rec["unsent_removals"].append(row_id)
    return out


# The work-list tables are created by ProjectRegistry._init_schema (a migration), not here.


class WorkLists:
    """Work-list storage, over the shared registry's connection."""

    def __init__(self, registry=None):
        from resource_explorer.registry import ProjectRegistry

        self.registry = registry or ProjectRegistry()

    def _conn(self):
        return self.registry._conn()

    # ── Work lists ──────────────────────────────────────────────────────

    def create(self, display_name: str, entity_slugs: Iterable[str], *,
               description: str = "", entity_type: str = "repo",
               investigation: str = "", created_by: str = "",
               derived_from: str = "", rationale: str = "",
               slug: str = "") -> dict:
        """Create a work list. LOCAL ONLY — nothing reaches Egeria here."""
        slug = slug or f"wl-{uuid.uuid4().hex[:10]}"
        # Materialise ONCE: `entity_slugs` may be a generator, and the old
        # shape consumed it in `dict.fromkeys` then counted it again, which
        # logs "0 members" for a list that has members.
        slugs = list(dict.fromkeys(entity_slugs))
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO work_lists (slug, display_name, description, entity_type,"
                " investigation, created_by, created_at, derived_from)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (slug, display_name, description, entity_type, investigation,
                 created_by, now, derived_from),
            )
            for entity_slug in slugs:
                conn.execute(
                    "INSERT INTO work_list_members (work_list_slug, entity_slug,"
                    " rationale, confidence, added_at) VALUES (?, ?, ?, ?, ?)",
                    (slug, entity_slug, rationale, None, now),
                )
        log.info("work list %s created with %d member(s)", slug, len(slugs))
        return self.get(slug)

    def get(self, slug: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM work_lists WHERE slug = ?", (slug,)).fetchone()
            if not row:
                return None
            members = conn.execute(
                "SELECT entity_slug, rationale, confidence, added_at"
                " FROM work_list_members WHERE work_list_slug = ? ORDER BY added_at, entity_slug",
                (slug,)).fetchall()
        out = dict(row)
        out["members"] = [dict(m) for m in members]
        self._attach_dispositions(out["members"], out.get("entity_type") or "repo")
        return out

    def _attach_dispositions(self, members: list[dict], entity_type: str = "repo") -> None:
        """Add each member's current disposition, for grouping in the matrix.

        Dispositions are keyed by github_url, not by slug, so this is a
        two-step lookup per member. It is best-effort by design: a work list
        whose disposition store is unreachable must still open, with rows
        that say `undecided` rather than no rows at all. The matrix groups on
        this and a missing value is a legitimate group.
        """
        # The registry this instance already holds: a bare ProjectRegistry()
        # here re-ran the whole registry init on every get (a real cost on SQLite).
        registry = self.registry
        for m in members:
            m.setdefault("disposition", "")
            m.setdefault("github_url", "")
            if entity_type != "repo":
                # Dispositions are keyed by a repo's github_url; `registry.get` reads repos only, so a database or
                # file-system slug that happens to name a repo would borrow that repo's verdict.
                continue
            try:
                # registry.get() returns a Project DATACLASS, not a dict —
                # a .get() call on it raises rather than returning None, and
                # the broad except below would have swallowed that into a
                # silent empty disposition for every row.
                project = registry.get(m["entity_slug"])
                url = getattr(project, "github_url", "") or ""
                if not url:
                    continue
                # The URL travels with the member too: dispositions and their
                # history are keyed on it, so a client that has only the slug
                # cannot ask for the verdict trail.
                m["github_url"] = url
                held = registry.get_disposition(url) or {}
                m["disposition"] = held.get("disposition") or ""
            except Exception:                               # pragma: no cover
                continue

    def list_all(self, *, investigation: str = "") -> list[dict]:
        with self._conn() as conn:
            if investigation:
                rows = conn.execute(
                    "SELECT * FROM work_lists WHERE investigation = ?"
                    " ORDER BY created_at DESC", (investigation,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM work_lists ORDER BY created_at DESC").fetchall()
            out = []
            for row in rows:
                d = dict(row)
                d["member_count"] = conn.execute(
                    "SELECT COUNT(*) AS n FROM work_list_members WHERE work_list_slug = ?",
                    (d["slug"],)).fetchone()["n"]
                out.append(d)
        return out

    def set_investigation(self, slug: str, investigation: str) -> None:
        """Link a saved list to an investigation (or, with '', unlink it).

        Only `create()` ever set `work_lists.investigation`, and only when an
        investigation was current at save time; this is the one later write.
        The caller checks that the investigation exists."""
        with self._conn() as conn:
            conn.execute("UPDATE work_lists SET investigation = ? WHERE slug = ?",
                         (investigation, slug))

    def set_member(self, slug: str, entity_slug: str, *,
                   rationale: str = "", confidence=None) -> None:
        """Add a member, or update the reason it is there.

        `confidence` is checked by the same function the outbox sends it with
        (`egeria_outbox.membership_confidence`: an int 0-100; ValueError otherwise), so a value stored here
        is one Egeria can hold. None leaves an existing member's confidence as it is (a rationale-only
        update, e.g. the journal's, must not wipe it). Clearing a confidence is not offered: Egeria merges
        relationship properties, so a cleared value would need its own write (follow-up)."""
        from resource_explorer.egeria_outbox import membership_confidence

        if confidence is not None:
            confidence = membership_confidence(confidence)
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT 1 FROM work_list_members WHERE work_list_slug = ? AND entity_slug = ?",
                (slug, entity_slug)).fetchone()
            if existing and confidence is None:
                conn.execute(
                    "UPDATE work_list_members SET rationale = ?"
                    " WHERE work_list_slug = ? AND entity_slug = ?",
                    (rationale, slug, entity_slug))
            elif existing:
                conn.execute(
                    "UPDATE work_list_members SET rationale = ?, confidence = ?"
                    " WHERE work_list_slug = ? AND entity_slug = ?",
                    (rationale, confidence, slug, entity_slug))
            else:
                conn.execute(
                    "INSERT INTO work_list_members (work_list_slug, entity_slug,"
                    " rationale, confidence, added_at) VALUES (?, ?, ?, ?, ?)",
                    (slug, entity_slug, rationale, confidence, _now()))

    def remove_member(self, slug: str, entity_slug: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM work_list_members WHERE work_list_slug = ? AND entity_slug = ?",
                (slug, entity_slug))

    def delete(self, slug: str) -> None:
        """Remove the work list locally.

        Does NOT delete the Egeria Collection. Removing a published element
        is a catalog decision with its own consequences, and doing it as a
        side effect of tidying a local list is how a catalog loses history
        nobody meant to spend.

        If an unpublish is ever added, it goes here, and in this order: Egeria
        refuses to delete a WorkingSet that still has members
        (OMAG-GENERIC-HANDLERS-403-005, which pyegeria misreports as an
        AUTHORIZATION 401), so every membership is removed first (the
        `collection_membership_detach` rows `publish` queues), and only then
        the collection.
        """
        with self._conn() as conn:
            conn.execute("DELETE FROM work_list_members WHERE work_list_slug = ?", (slug,))
            conn.execute("DELETE FROM work_lists WHERE slug = ?", (slug,))

    # ── Promotion ───────────────────────────────────────────────────────

    def promote(self, slug: str, survivors: Iterable[str], *,
                display_name: str = "", rationale: str = "",
                created_by: str = "") -> dict:
        """The survivors of one work list become a new, narrower one.

        The new list records `derived_from`, so the funnel keeps its own
        trail: a shortlist that cannot say what it was shortlisted FROM is a
        list of names, not a decision.
        """
        parent = self.get(slug)
        if not parent:
            raise KeyError(slug)
        keep = [s for s in survivors if any(m["entity_slug"] == s for m in parent["members"])]
        return self.create(
            display_name or f"{parent['display_name']} — shortlist",
            keep,
            description=f"Survivors of {parent['display_name']}",
            entity_type=parent["entity_type"],
            investigation=parent["investigation"],
            created_by=created_by,
            derived_from=slug,
            rationale=rationale,
        )

    # ── Batch runs ──────────────────────────────────────────────────────

    def enqueue_batch(self, analysis_id: str, entity_slugs: Iterable[str], *,
                      work_list_slug: str = "", requested_by: str = "",
                      entity_type: str = "repo", refusal=None) -> dict:
        """Enqueue one analysis across a SET of resources, and return a set id.

        The queue already does the hard half of this — `SKIP LOCKED` claims
        mean each extra worker is extra throughput, per-user fairness is live,
        and a dead worker is reconciled. What was missing was a caller that
        enqueues a set and a way to watch the set finish, which is all this is.

        One row per resource, so a single failure is one row's failure and the
        rest still run. The set id is ours, not the queue's: the queue has no
        concept of a set and does not need one.

        `entity_type` defaults to "repo" for existing callers — a batch is
        homogeneous (one work list, one entity_type, same as the `work_lists`
        table's own column), and this is stored on each row's `target` dict
        so `run_queue.py::_handle_analysis_run` resolves the right resource
        type's analysis steps instead of always the repo's.
        """
        slugs = list(dict.fromkeys(entity_slugs))
        if not slugs:
            raise ValueError("a batch needs at least one resource")
        # Brief Z3: `refusal(slug) -> str` checks each member's Egeria write as the caller
        # (`egeria_work_access.batch_refusal`). A refused member gets no run and comes back in `not_permitted`
        # with its words; the rest run. Nothing permitted at all raises `CurationDenied` (the route's 403).
        not_permitted: list[dict] = []
        if refusal is not None:
            from resource_explorer.workflows.curate import CurationDenied
            from resource_explorer.workflows.egeria_work_access import per_member

            slugs, refused = per_member(slugs, refusal)
            not_permitted = [{"entity_slug": s, "words": why} for s, why in refused]
            if not slugs:
                reasons = sorted({why.removeprefix("not permitted · ") for _, why in refused})
                raise CurationDenied(reasons[0] if len(reasons) == 1 else "; ".join(reasons))
        set_id = f"set-{uuid.uuid4().hex[:12]}"
        now = _now()
        enqueued: list[dict] = []
        for entity_slug in slugs:
            run_id = self.registry.enqueue_run(
                "analysis_run",
                {"slug": entity_slug, "analysis_id": analysis_id, "entity_type": entity_type},
                requested_by=requested_by,
            )
            enqueued.append({"run_id": run_id, "entity_slug": entity_slug})
        with self._conn() as conn:
            for row in enqueued:
                conn.execute(
                    "INSERT INTO work_list_runs (set_id, run_id, work_list_slug,"
                    " entity_slug, analysis_id, enqueued_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (set_id, row["run_id"], work_list_slug, row["entity_slug"],
                     analysis_id, now))
        log.info("batch %s: enqueued %s across %d resource(s)", set_id, analysis_id, len(slugs))
        return {"set_id": set_id, "analysis_id": analysis_id,
                "work_list_slug": work_list_slug, "runs": enqueued, "not_permitted": not_permitted}

    def batch_progress(self, set_id: str) -> dict | None:
        """What every run in this set is doing right now.

        Derived from the `runs` rows every time. Nothing about a set's
        progress is stored, so a worker that updates a run without knowing
        about sets cannot leave a stale summary behind — which is the failure
        a cached count would guarantee.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT wr.run_id, wr.entity_slug, wr.analysis_id, wr.enqueued_at,"
                "       r.state, r.error, r.result_ref, r.started_at, r.finished_at"
                "  FROM work_list_runs wr LEFT JOIN runs r ON r.id = wr.run_id"
                " WHERE wr.set_id = ? ORDER BY wr.entity_slug", (set_id,)).fetchall()
        if not rows:
            return None
        runs = []
        counts: dict[str, int] = {}
        for row in rows:
            d = dict(row)
            # A run row that has vanished is NOT a finished run. Saying
            # "unknown" keeps it out of the done count, where it would read as
            # a success nobody observed.
            state = (d.get("state") or "unknown")
            d["state"] = state
            counts[state] = counts.get(state, 0) + 1
            runs.append(d)
        terminal = set(TERMINAL_RUN_STATES)
        unrecognised = sorted(
            {r["state"] for r in runs}
            - set(TERMINAL_RUN_STATES) - set(ACTIVE_RUN_STATES) - {"unknown"})
        if unrecognised:
            # Neither finished nor in flight by this module's reckoning. Said
            # out loud rather than bucketed, because guessing is what produced
            # the `done` bug.
            log.warning("run set %s has unrecognised state(s): %s", set_id, unrecognised)
        return {
            "set_id": set_id,
            "total": len(runs),
            "counts": counts,
            "finished": sum(n for s, n in counts.items() if s in terminal),
            "complete": all(r["state"] in terminal for r in runs),
            "unrecognised_states": unrecognised,
            "runs": runs,
        }

    # ── Egeria ──────────────────────────────────────────────────────────

    def publish(self, slug: str, *, run_id: str = "") -> dict:
        """Publish a work list to Egeria as a `WorkingSet` Collection.

        Two halves, deliberately different:

        * the Collection is created SYNCHRONOUSLY, because its GUID is needed
          to attach anything to it and a caller that cannot record the GUID
          has published nothing it can find again. Same reasoning as the
          investigation publisher, which is where `_create_typed_collection`
          comes from.
        * the memberships are ENQUEUED to the outbox, each carrying its
          `membershipRationale`, and then applied here and now by the same
          drain the scheduler runs (`_apply_adds`, up to INLINE_ATTACH_LIMIT;
          owner's ruling 2026-10-13). `attach` reports each by its row's own
          state afterwards; whatever is left stays for the background drain.

        A member whose resource was never published to Egeria has no GUID to
        attach and is REPORTED, not retried — that is a fact about the
        resource, not a failed write.

        **A re-publish also removes** (the Portal reads this collection, and
        membership is uni-link, so adds converge and removals happen only if RE
        makes them): members RE recorded attaching and has since dropped are
        detached, one `collection_membership_detach` row each, DETACH-checked
        on both ends, applied before this returns (`_apply_removals`), and
        reported in `removals` by each row's own outcome.

        **The link back.** With `RE_PUBLIC_URL` configured, the WorkingSet
        carries `additionalProperties.resourceExplorerUrl`, the /next
        work-list page: on create, and merged in again on every re-publish
        (UPDATE_PROPERTIES, checked; `link_back` says what happened). Without
        it the key is left out.

        **Zone access (Brief Z3).** Every write is checked first, as the caller,
        with the one decision (`workflows.egeria_work_access` ->
        `curation_access`): the collection (CREATE for a new one, ATTACH) and
        each member's end of its membership (ATTACH). A refused member is NOT
        queued and comes back in `not_permitted` with its words ("not permitted
        · <reason>"); the rest proceed. Raises `CurationDenied` (the route's
        403) before anything is written when the collection is refused, or when
        the collection exists already and every member to attach is refused
        (nothing at all would be permitted). The drain re-checks each queued
        row as the same person (`egeria_outbox._collection_refusal`).
        """
        from resource_explorer.workflows.curate import CurationDenied
        from resource_explorer.workflows.egeria_work_access import (
            access_words, collection_detach_write, member_detach_write, member_write, per_member,
            work_list_collection_write, work_list_link_back_write,
        )

        wl = self.get(slug)
        if not wl:
            raise KeyError(slug)
        from resource_explorer.zone_access import EgeriaAccessReader

        reader = EgeriaAccessReader()      # one press: controls and accounts read once, elements fresh per check
        existing = (wl.get("egeria_guid") or "").strip()
        refused_collection = access_words(self.registry, [work_list_collection_write(existing)],
                                          entity_type="work_list", slug=slug, reader=reader)
        if refused_collection:
            raise CurationDenied(refused_collection.removeprefix("not permitted · "))

        attachable, unpublished, unresolved = [], [], []
        for m in wl["members"]:
            try:
                member_guid = member_egeria_guid(self.registry, wl["entity_type"], m["entity_slug"])
            except MemberUnresolved as exc:
                unresolved.append({"entity_slug": m["entity_slug"], "entity_type": wl["entity_type"],
                                   "words": f"could not resolve · {exc}"})
                continue
            if member_guid:
                attachable.append({
                    "member_guid": member_guid,
                    "entity_type": wl["entity_type"],
                    "entity_slug": m["entity_slug"],
                    "rationale": m.get("rationale") or "",
                    "confidence": m.get("confidence"),
                })
            else:
                unpublished.append(m["entity_slug"])
        attachable, refused = per_member(attachable, lambda m: access_words(
            self.registry, [member_write(m["member_guid"])], entity_type=m["entity_type"], slug=m["entity_slug"],
            reader=reader))
        not_permitted = [{"entity_slug": m["entity_slug"], "words": why} for m, why in refused]

        # Removals (re-publish only): what RE recorded attaching to this collection, minus the current members.
        # Every current member's GUID counts, refused or not: a refused add is not a removal.
        # A member still in the list whose asset GUID does not resolve right now (links forgotten, stale assets
        # cleared, a cache emptied) is NOT removed: only one that now resolves to a DIFFERENT GUID leaves its old
        # GUID behind as a removal.
        current_guids = {m["member_guid"] for m in attachable} | {m["member_guid"] for m, _ in refused}
        # An unresolved member (unknown type, unreadable) is held like an unpublished one: never removed on a guess.
        resolved = {**{s_: "" for s_ in unpublished}, **{u["entity_slug"]: "" for u in unresolved},
                    **{m["entity_slug"]: m["member_guid"] for m in attachable + [m for m, _ in refused]}}
        recorded = recorded_memberships(self.registry, slug, existing) if existing else {}
        removed = {g: r for g, r in recorded.items()
                   if g not in current_guids and not (r["entity_slug"] in resolved and not resolved[r["entity_slug"]])}
        detachable, detach_refused = per_member(
            [{"member_guid": g, "entity_slug": r["entity_slug"] or g} for g, r in removed.items()
             if r["attached"] or r["adding"] or r["maybe"]],
            lambda m: access_words(
                self.registry, [collection_detach_write(existing, "the collection"),
                                member_detach_write(m["member_guid"])],
                entity_type=wl["entity_type"], slug=m["entity_slug"], reader=reader))
        if (refused or detach_refused) and not attachable and not detachable and existing:
            reasons = sorted({why.removeprefix("not permitted · ") for _, why in refused + detach_refused})
            raise CurationDenied(reasons[0] if len(reasons) == 1 else "; ".join(reasons))

        from resource_explorer.egeria_outbox import _default_clients
        from resource_explorer.surveyors.egeria_investigation_publisher import (
            _create_typed_collection,
        )

        # `_default_clients()` returns a TUPLE — (clients, find_element_guid) —
        # not an `OutboxClients`. Unpacking it wrong gets you
        # `'tuple' object has no attribute 'require'` at publish time, which
        # is only reachable with a live platform and so is exactly the kind of
        # line a stub test does not exercise.
        from resource_explorer.egeria_clients import current_principal

        # The person who pressed publish (Brief I), not the outbox daemon.
        who = current_principal()
        clients, _find_element_guid = _default_clients(who)
        if not callable(_find_element_guid):
            # Every add is looked up by name before it is created; a drain handed no look-up cannot tell "absent"
            # from "could not check". Said before anything is written, never passed on as None.
            raise RuntimeError("publish has no Egeria look-up to check what already exists, so nothing was sent")
        cm = clients.require("collection_manager")

        # The link back to RE's work-list page (the Portal's Resource Browser reads it). Written on create, and
        # again on every re-publish, so a list published before the URL was configured gains it.
        url = resource_explorer_url(slug, wl["entity_type"])
        link_back = {"url": url, "state": "set" if url else "not configured", "words": ""}
        guid = (wl.get("egeria_guid") or "").strip()
        if not guid:
            guid = _create_typed_collection(
                cm, EGERIA_TYPE_NAME, wl["display_name"],
                wl.get("description") or f"Work list {slug}",
                qualified_name=f"{EGERIA_TYPE_NAME}::resource-explorer::{slug}",
                additional_properties={RESOURCE_EXPLORER_URL_KEY: url} if url else None,
            )
            if not guid:
                raise RuntimeError("Egeria returned no GUID for the collection")
            with self._conn() as conn:
                conn.execute(
                    "UPDATE work_lists SET egeria_guid = ?, published_at = ?"
                    " WHERE slug = ?", (guid, _now(), slug))
        elif url:
            link_back.update(self._set_link_back(cm, guid, url, access_words(
                self.registry, [work_list_link_back_write(guid)], entity_type="work_list", slug=slug,
                reader=reader)))

        row_ids = enqueue_work_list_members(
            self.registry, slug, guid, attachable, run_id=run_id)
        attached, attach_error = self._apply_adds(list(zip(row_ids, (m["entity_slug"] for m in attachable))),
                                    clients, _find_element_guid, who)
        removals = self._apply_removals(slug, guid, wl["entity_type"], removed, detachable, detach_refused,
                                        clients, _find_element_guid, who, run_id=run_id)

        return {
            "slug": slug,
            "egeria_guid": guid,
            "type_name": EGERIA_TYPE_NAME,
            "queued_members": len(row_ids),
            "outbox_row_ids": row_ids,
            # Named, not counted: "3 could not be attached" is not actionable
            # and "3 could not be attached: a, b, c" is.
            "members_without_an_egeria_asset": unpublished,
            # The groups, by name: queued to attach; held in RE with no Egeria element (with its type); and ones
            # whose element RE could not establish (never folded into "not published").
            "queued_to_attach": [m["entity_slug"] for m in attachable],
            # Each queued member's add, applied here and now (owner's ruling 2026-10-13), by its outbox row's own
            # state after the inline drain: attached / not permitted / could not attach / left to the background
            # drain. One entry per queued member, in order.
            "attach": attached,
            # "" when every inline drain ran; else what raised, in words (the rows stay for the background drain).
            "attach_error": attach_error,
            "not_published": [{"entity_slug": s_, "entity_type": wl["entity_type"],
                               "words": not_published_words(wl["entity_type"])} for s_ in unpublished],
            "not_resolved": unresolved,
            # Brief Z3: each member the caller may not attach, with its words; the rest were queued.
            "not_permitted": not_permitted,
            # Members removed in RE since the last publish, each by its outbox row's own outcome.
            "removals": removals,
            "link_back": link_back,
        }

    @staticmethod
    def _set_link_back(cm, guid: str, url: str, refused: str) -> dict:
        """Put `resourceExplorerUrl` on an existing WorkingSet (UPDATE_PROPERTIES, checked first). The current
        additionalProperties are read and merged here, so keys someone else added survive and nothing is sent
        when the link is already right. Its own outcome: a refusal or a failure does not stop the memberships."""
        if refused:
            return {"state": "not permitted", "words": refused}
        try:
            # depth 0: the collection alone, not its members and their graph (the default depth is 3).
            current = _additional_properties(cm.get_collection_by_guid(guid, graph_query_depth=0,
                                                                       output_format="JSON"))
        except Exception as exc:  # noqa: BLE001 - said on the result, the publish goes on
            log.warning("work list link back: could not read %s: %s", guid, exc)
            return {"state": "failed",
                    "words": f"could not read its current properties · {type(exc).__name__}: {exc}"[:300]}
        if current.get(RESOURCE_EXPLORER_URL_KEY) == url:
            return {"state": "unchanged", "words": ""}
        try:
            cm.update_collection(guid, {
                "class": "UpdateElementRequestBody",
                "mergeUpdate": True,
                "properties": {"class": "CollectionProperties",
                               "additionalProperties": {**current, RESOURCE_EXPLORER_URL_KEY: url}},
            })
        except Exception as exc:  # noqa: BLE001 - said on the result, the publish goes on
            log.warning("work list link back not set on %s: %s", guid, exc)
            return {"state": "failed", "words": f"could not be set · {type(exc).__name__}: {exc}"[:300]}
        return {"state": "set", "words": ""}

    def _apply_adds(self, rows: list[tuple[int, str]], clients, find_element_guid, who) -> tuple[list[dict], str]:
        """Apply this publish's queued `collection_membership` rows now, one scoped drain per row, as the person
        who pressed publish (owner's ruling 2026-10-13: adds drain inline, as removals already do; before it the
        person waited up to 15 minutes for the scheduler).

        The SAME drain the scheduler runs (`drain_outbox(..., element_id=row_id)`), only earlier: the Z3 re-check
        as the row's requester, the membership's confidence/rationale properties, `verify_membership_landed`,
        `OutboxNotKept`, retry with backoff for a failed add, and `mark_outbox_done(note=)` are all its own.

        `find_element_guid` is the publisher's real look-up, checked callable by `publish` before anything is
        written, and passed as given: never None, never a stand-in that answers "" (a look-up that cannot run must
        not read as "absent").

        Each member's words come from its row as it stands afterwards (the proof row), never from the branch
        taken here: "attached" (done; the read-back note, if any, follows), "not permitted · <why>" (refused at
        the re-check or by Egeria's security), "could not attach · <why>" (`will_retry` says whether the
        background drain sends it again), or "left to the background drain" (over the cap, still pending, or
        being sent by the background drain). A drain that raises fails no publish: its rows stay in the outbox,
        and the second value says so in words ("" when every inline drain ran), shown on the page as `attach_error`.
        """
        from resource_explorer.egeria_outbox import NOT_RETRIED, REFUSED_BY_SECURITY, drain_outbox

        failures: list[str] = []
        for row_id, member in rows[:INLINE_ATTACH_LIMIT]:
            try:
                drain_outbox(self.registry, clients, find_element_guid, element_id=row_id, identity=who)
            except Exception as exc:  # drain_outbox never raises by contract; whatever does is said, not hidden
                log.exception("work list publish: inline drain of outbox row %s failed; left to the background",
                              row_id)
                failures.append(f"{member}: {type(exc).__name__}: {exc}")
        error = (f"the inline send failed for {len(failures)} member(s) · left to the background drain · "
                 + "; ".join(failures))[:500] if failures else ""
        out: list[dict] = []
        for n, (row_id, member) in enumerate(rows):
            row = self.registry.get_outbox_element(row_id) or {}
            status, said = row.get("status") or "", (row.get("last_error") or "").strip()
            entry: dict[str, Any] = {"entity_slug": member, "outbox_row_id": row_id, "status": status}
            if status == "done":
                entry.update(state=ATTACHED, words=f"{ATTACHED} · {said}" if said else ATTACHED)
            elif status in ("dead", "failed") and said.startswith((ATTACH_NOT_PERMITTED, REFUSED_BY_SECURITY)):
                entry.update(state=ATTACH_NOT_PERMITTED,
                             words=said if said.startswith(ATTACH_NOT_PERMITTED)
                             else f"{ATTACH_NOT_PERMITTED} · {said}")
            elif status in ("dead", "failed"):
                entry.update(state=ATTACH_FAILED, words=f"{ATTACH_FAILED} · {said or 'Egeria gave no sentence'}",
                             will_retry=status == "failed" and not said.startswith(NOT_RETRIED))
            elif status == "running":
                entry.update(state=ATTACH_LEFT, words=f"{ATTACH_LEFT} · being sent by it now")
            elif status == "pending" and n >= INLINE_ATTACH_LIMIT:
                entry.update(state=ATTACH_LEFT,
                             words=f"{ATTACH_LEFT} · over {INLINE_ATTACH_LIMIT} in one publish")
            elif status == "pending":
                entry.update(state=ATTACH_LEFT, words=f"{ATTACH_LEFT} · {said}" if said else ATTACH_LEFT)
            else:                                      # withdrawn or superseded meanwhile, or a row not found
                entry.update(state=ATTACH_FAILED, will_retry=False,
                             words=f"{ATTACH_FAILED} · {status or 'its outbox row was not found'}"
                                   + (f": {said}" if said else ""))
            out.append(entry)
        return out, error

    def _apply_removals(self, slug: str, guid: str, entity_type: str, removed: dict, detachable: list,
                        detach_refused: list, clients, find_element_guid, who, *, run_id: str = "") -> dict:
        """Take members removed in RE out of the WorkingSet, from RE's own record (`recorded_memberships`).

        * An add never attempted is withdrawn (cancelled): it never reached Egeria, so there is nothing to detach.
        * An add attempted and not confirmed (failed or dead after a try) may have landed: it is cancelled if it
          would retry, and the member gets a removal (Egeria's detach of an unlinked pair changes nothing).
        * A member added (or being added) gets ONE `collection_membership_detach` row, as the person who pressed
          publish, applied here and now so the result is the row's own outcome: "detached", "not permitted"
          (re-checked at the drain), "could not be detached · <why>" (destructive: never re-sent; the next
          publish queues a new row), or "waiting" (an earlier add of the member is still being sent).
        * A member re-added in RE whose removal was never sent has that removal withdrawn.
        * A member never published to Egeria is not "detached": it has no recorded add.

        These rows are also what any future unpublish must send first (see `delete`)."""
        from resource_explorer.egeria_outbox import (
            COLLECTION_MEMBERSHIP_DETACH, collection_membership_qn, drain_outbox, requester_fields,
        )

        withdrawn_before_sent: list[str] = []
        still_sending: list[dict] = []
        for g, rec in removed.items():
            cancelled = [self.registry.cancel_outbox_row(i, "member removed from the work list before it was sent")
                         for i in rec["unsent_adds"]]
            if rec["attached"] or rec["adding"] or rec["maybe"] or not cancelled:
                continue                               # detached below (or nothing was ever queued)
            if all(cancelled):                         # never attempted: it cannot have reached Egeria
                withdrawn_before_sent.append(rec["entity_slug"] or g)
            else:                                      # it started sending as we looked: not this press's to undo
                still_sending.append({"entity_slug": rec["entity_slug"] or g,
                                      "words": "its add is being sent now · publish again to remove it"})
        for g, rec in recorded_memberships(self.registry, slug, guid).items():
            if g not in removed:                       # a current member: a removal not yet sent is superseded
                for i in rec["unsent_removals"]:
                    self.registry.cancel_outbox_row(i, "member added back to the work list before it was sent")

        # Re-read after the withdrawals: an add that started sending meanwhile could not be cancelled and needs
        # the removal after all; one already being removed is not queued twice.
        now = recorded_memberships(self.registry, slug, guid)
        out = {"detached": [], "not_permitted": [{"entity_slug": m["entity_slug"], "words": why}
                                                 for m, why in detach_refused],
               "failed": [], "waiting": still_sending, "withdrawn_before_sent": withdrawn_before_sent}
        rows: list[tuple[int, str]] = []
        for m in detachable:
            rec = now.get(m["member_guid"]) or {}
            if rec.get("removing"):
                out["waiting"].append({"entity_slug": m["entity_slug"], "words": "removal already queued"})
                continue
            if not (rec.get("attached") or rec.get("adding") or rec.get("maybe")):
                continue                               # removed meanwhile by another press: nothing left to do
            payload: dict[str, Any] = {
                "collection_guid": guid, "member_guid": m["member_guid"], "member_entity_type": entity_type,
                "member_entity_slug": m["entity_slug"],
            }
            payload.update(requester_fields())
            # One removal per add cycle: the key names the newest add it follows, so two presses queue one row
            # (`enqueue_outbox_element_once`), and a member added again and removed again gets a new one.
            key = f"{collection_membership_qn(guid, m['member_guid'])}::after-add-{rec.get('last_add_id') or 0}"
            rows.append((self.registry.enqueue_outbox_element_once(
                "work_list", slug, COLLECTION_MEMBERSHIP_DETACH, key, payload, run_id=run_id), m["entity_slug"]))
        for row_id, _ in rows[:INLINE_DETACH_LIMIT]:
            drain_outbox(self.registry, clients, find_element_guid, element_id=row_id,
                         identity=who)
        for n, (row_id, member) in enumerate(rows):
            row = self.registry.get_outbox_element(row_id) or {}
            status, words = row.get("status") or "", (row.get("last_error") or "").strip()
            if status == "done":
                out["detached"].append(member)
            elif status in ("dead", "failed") and words.startswith("not permitted"):
                out["not_permitted"].append({"entity_slug": member, "words": words})
            elif status in ("dead", "failed"):
                out["failed"].append({"entity_slug": member, "words": words})
            elif status == "running":
                out["waiting"].append({"entity_slug": member, "words": "being sent by the background drain"})
            elif n >= INLINE_DETACH_LIMIT:
                out["waiting"].append({"entity_slug": member, "words":
                                       f"left to the background drain (over {INLINE_DETACH_LIMIT} in one publish)"})
            else:
                out["waiting"].append({"entity_slug": member, "words": words or "queued for the background drain"})
        out["outbox_row_ids"] = [i for i, _ in rows]
        return out


def enqueue_work_list_members(registry, work_list_slug: str, collection_guid: str,
                              members: list[dict], *, run_id: str = "") -> list[int]:
    """Queue one `collection_membership` write per member, WITH its rationale.

    Deliberately not `egeria_outbox.enqueue_collection_members`: that one is
    the investigation path and carries no rationale, and widening it would
    change what every existing queued row means. This adds two optional keys
    the shared creator passes through only when they are present.
    """
    from resource_explorer.egeria_outbox import requester_fields

    row_ids: list[int] = []
    for m in members:
        payload: dict[str, Any] = {
            "collection_guid": collection_guid,
            "member_guid": m["member_guid"],
            "member_entity_type": m.get("entity_type", ""),
            "member_entity_slug": m.get("entity_slug", ""),
        }
        if m.get("rationale"):
            payload["membership_rationale"] = m["rationale"]
        if m.get("confidence") is not None:
            # The payload key queued rows already carry; the creator sends it as Egeria's `confidence`.
            payload["expected_confidence"] = m["confidence"]
        payload.update(requester_fields())       # Brief Z3: the drain re-checks the person who pressed publish
        row_ids.append(registry.enqueue_outbox_element(
            "work_list", work_list_slug, "collection_membership",
            f"CollectionMembership::{collection_guid}::{m['member_guid']}",
            payload, run_id=run_id,
        ))
    return row_ids
