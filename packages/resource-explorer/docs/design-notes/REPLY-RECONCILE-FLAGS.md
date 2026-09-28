# Two reconcile flags, answered — and where the saved search goes

**Replying to:** `RECONCILE-ADMIN-IMPLEMENTED.md` §"One noted gap" and
`DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md` §"Follow-up flagged, not built"
**Date:** 2026-09-21

---

## 1 · `get_status()` needs its route — build it

`egeria_resync.get_status()` (last scheduler run, outcome, consecutive failures)
has no HTTP route, so the "Already scheduled" row draws its state entirely from
the live scan. That is honest about *what the scan found*. It is not honest about
the thing the row is actually claiming.

**A row that says "already scheduled" without saying when it last ran cannot
distinguish two states:** the scheduler is working and found nothing, and the
scheduler has not run in three days. Those look identical, and only one of them
means the drift is handled. It is *nothing recorded yet* versus *a measured
zero*, one level up — the same distinction the charts pane keeps apart on
purpose.

So: **build the route**, mirroring `bootstrap.py`'s
`GET .../status` → `get_status()` pattern as proposed, and the row says when the
scheduler last ran and whether it succeeded. A consecutive-failure count earns a
flag on the row when it is non-zero; silence otherwise.

You were right not to add backend surface unasked. This is the ask.

## 2 · The `"scheduled"` boolean — yes, and you caught the pattern early

`resync.js` hardcodes `SCHEDULED_STEPS`, duplicating `SAFE_SCHEDULED_STEPS` in
the backend. The proposal — a server-computed `"scheduled"` boolean on
`Finding.as_dict()`, mirroring the `"expensive"` one already there — is right,
and the precedent being in the same method makes it nearly free.

**Build it, and note why it matters beyond tidiness.** Two sources of truth for
one fact is the exact structure behind three defects this project has already
paid for: `unbuilt` (read in three places, set in none, six stages rendering as
built), `#130` (deferred styling set independently of the flag gating it), and
the nav's frame/stage split carried by hue alone. Each was cheap to prevent and
expensive to find.

**This one was caught before it drifted**, by the person writing the second copy,
with the fix already modelled next to it. That is the discipline working rather
than the register catching up afterwards, and it is worth saying so.

## 3 · The saved search has a home — plan item 11

`_saveCurrentSearchAsSource` cannot be ported because `/next` has no Discover
search pane to attach it to, and classic's version lives on that search view
rather than on Admin. Correct, and correctly scoped out.

**It is not "someday": it belongs to plan item 11** (Discovery / Assessment /
Analysis), which the coordinator added on 2026-09-18 and which is likely cheap
since those three share classic's generic catalog shell. Recording the
dependency here so it does not fall off when that item is picked up:

> Item 11's Discover pane inherits *save this search as a source*. The admin-side
> plumbing (`searchDiscoveryRepos`, `createDiscoverySource`) is already built and
> in use by the admin panel's own Search & save tab, so the pane needs an entry
> point, not a mechanism.

It is worth carrying because it asks at the cheapest possible moment: the user
has just seen results they like, and turning that into a standing source costs
them one click while they already know the query is good.
