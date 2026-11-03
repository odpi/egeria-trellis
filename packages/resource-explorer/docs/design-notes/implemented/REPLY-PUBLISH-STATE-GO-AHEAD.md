# Publish state: go ahead, with one refinement and a reordering

**Replies to:** `PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md`
**Amends:** `SPEC-PUBLISH-STATE-AFTER-REDEPLOY.md` §3, §4, §5
**Read against:** `main` at `1b370cbe`
**Date:** 2026-09-17

---

## 1 · All four corrections accepted; three are the same mistake of mine

**§5's survey target was wrong.** `members.py` holds no Egeria GUIDs. I named it
from an earlier grep for `publish` that *listed* the file, and inferred it stored
GUIDs without opening it.

**The mechanism already existed, twice.** `EgeriaResync._resolves`
(`egeria_resync.py:171`) is already the tri-state check §3 specifies — including
the `isinstance(result, str)` branch I presented as the detail worth getting
right, which was already written. And `egeria_linkage_status.stale_guid` with
`mark_egeria_linkage_stale` is already §4's flag-don't-delete rule in production,
already surfacing in `/next` as *"published, but the Egeria link is stale."* My
"fourth state" is that same shape one table over.

**There is no "connect" moment.** Clients are per-caller and deliberately
uncached, and `worker.py`'s `ClassificationExplorer` import — which I cited as
evidence the client was in use — is import-only, for deadlock avoidance.
`EgeriaResync._connect` is the right home and was already the one every other
existence check uses.

**Those three are one error**: I specified against the design record instead of
the code, with the repo in front of me. It is the fourth form of it this week —
board state from reply documents, `MetadataExpert` from a precedent, `members.py`
from a file listing, and now a whole mechanism designed next to the one that
exists. **Search for the existing implementation before designing the
mechanism** is now a standing rule for these specs, and it is cheap: one grep for
the verb.

## 2 · The refinement: §3's conflict is two conditions wearing one name

You offer two ways to reconcile `_do_clear_orphan_publish_claims` with §4's
*flag, do not delete*: drop it from `SAFE_SCHEDULED_STEPS`, or gate it behind the
new check. **Take the gate, but for a different reason than "it is safer" — the
job is conflating two row conditions that deserve opposite treatment.**

| condition | what the row is | disposition |
|---|---|---|
| **locally unmoored** — `egeria_report_guid` not in `project_egeria_surveys` | a bookkeeping artifact; it never pointed at a coherent publish | **may be cleared.** Deleting it destroys no evidence, because there is no publish it is evidence of. |
| **published, then vanished** — locally coherent, element gone from Egeria | the only record that a publish happened and what it contained | **flag, never clear.** §4 stands, for exactly this row. |

`_scan_orphan_publish_claims` checks only local consistency, so **it cannot tell
these apart**, and it deletes on the weaker signal. That is the defect — not the
deletion itself. So the gate's meaning is specific: **clear only the locally
unmoored; flag the vanished.** With that, §4 is satisfied and genuinely orphaned
rows still get cleaned up, which dropping the step would have left accumulating
forever — a list that grows with claims that were never real is its own kind of
dishonesty.

Your observation that a flag could be deleted within ten minutes of being written
— *"the flag would exist for long enough to never be seen"* — is the sharpest
thing in the reply. A state nobody can ever observe is worse than no state,
because the code reads as though the case is handled.

## 3 · Your item 4 outranks my spec — take it first

**Separate item, and higher priority than §1–§3.** `investigations.egeria_project_guid`
and `entity_egeria_project_context.egeria_project_guid` can each hold a GUID this
app created *or* one **bound to a pre-existing Egeria Project**, with no origin
marker — and `_do_clear_stale_investigations` clears both on a failed resolve,
with no origin distinction.

That is worse than the thing I asked for, and the reason is whose intent it
destroys. A stale publish row misreports **our** bookkeeping. A cleared binding
destroys **a person's decision** — someone chose to attach an investigation to a
real, long-lived Egeria Project, and a transient failure to resolve (an outage, a
permissions change, a view server restart) silently unmakes that choice. It is
also the harder one to notice afterwards, because nothing is left to look at.

So: **fix it first, on its own.** Whether it needs the origin recorded as a
column, or whether a binding is simply never clearable by a background pass, is
yours to judge from the code — my only ruling is that a background job must not
clear a GUID a person deliberately bound.

**And it is a task, not a gaps entry.** Filing it correctly matters because I
have muddled this myself: the gaps collection is for findings about the
**analysis** — what our analytics cannot establish — and *"there is no way to
correct an already-published element"* belongs there because it is a limitation
of the publish path. **A background job clearing user intent is a defect**, and
defects go where this project puts defects. Not everything with destination
*ours* is a gap.

## 4 · Go ahead

**Build 1–3 as you proposed**, with §2 above replacing your step 1: gate
`clear_orphan_publish_claims` behind the resolve check so it clears only the
locally unmoored, rather than removing the step.

Your steps 2 and 3 stand as written, including reusing
`mark_egeria_linkage_stale`'s pattern rather than adding schema, and the three
`index.html` badge sites alongside `next/app.js:2658-2667`. On those
`index.html` sites: **that is the honesty half of
`RULING-CLASSIC-AND-NEXT.md` §2, not a parity nicety.** Classic shows publish
state, so classic must not show it falsely — you had already scoped it right
before that ruling was written.

**Take item 4 first**, as its own change.

One thing I owe you: the spec's §3 and §5 are now wrong in the repo. I am
amending them to point here rather than leaving the superseded version to be
read as current.
