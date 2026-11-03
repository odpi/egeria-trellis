# Publish state after a redeploy

**Answers:** the open question in `REPLY-RETRACTION-WITHDRAWN.md` §4
**Read against:** `main` at `342e0ca3` (after `#105`) — verified in the code
**Date:** 2026-09-17 · **Revised** the same day, twice, on the project owner's correction

---

## 1 · The defect is confirmed

Publish state is **stored locally, and nothing ever checks it against Egeria.**
Three tables in the project registry carry it:

| table | `registry.py` | what it stores |
|---|---|---|
| `project_egeria_surveys` | ~1015 | `egeria_report_guid`, `published_at`, `annotation_count` |
| `project_published_annotation_types` | ~1052 | `annotation_type`, `published_at`, `egeria_report_guid` |
| `project_published_analyses` | ~1109 | `analysis_id`, `published_at`, `egeria_report_guid` |

`get_last_published_analyses`, `get_last_published_annotation_types`,
`has_published_annotation_types_for_report` and
`get_published_annotation_types_for_report` read them. **No function anywhere
resolves a stored `egeria_report_guid` to check it still exists.**

**Decision (project owner, 2026-09-14):** rather than retract or migrate the
published `SourceControlLibrary` elements, wipe and redeploy Egeria once the
publish code is corrected — dev environment, no real users to protect.

That decision is right, and it is also why these rows are now false: what this
app publishes are elements it **creates**, and a created element's GUID is
persisted with the element, so a wiped database takes all of them with it. The
UI has been reporting them as published ever since.

## 2 · Not the 09-14 case — the general one

The store will be wiped again; that is what a dev environment is for, and the
09-14 ruling established wipe-and-redeploy as this project's accepted answer to
a bad publish. So the fix is not a migration. It is the app being able to tell
that what it published is no longer there.

## 3 · The mechanism: ask, once per connection

**Two corrections against myself before the design, because both changed it.**

*First, I asked the wrong question.* I asked whether Egeria exposes a stable
store identity that changes on a redeploy, and built the spec around stamping it
on each publish row. That is a **proxy** for the thing actually in question.
What the interface needs to know is not *which store is this* but **do the
elements we published still exist** — and that can be asked directly, so it
should be. A proxy can be right while the proposition it stands for is wrong;
this one would have missed elements deleted from a store that was never wiped.

*Second, not all stored GUIDs are equally perishable*, which I did not know:

| origin of the element | GUID after a wipe-and-redeploy |
|---|---|
| **created** by this app — SurveyReports, annotations, ToDos | **gone**, with the database |
| **loaded from a content pack / archive** | **survives** — archives carry pre-defined GUIDs, stable and very rarely changed |

So *"a stored Egeria GUID may be stale"* is not a property of stored GUIDs in
general. It is a property of **created-element** GUIDs. The three publish tables
hold nothing but created-element GUIDs, which is what makes one check sound for
all of them — and it is also why the same check must not be pointed at
archive-sourced GUIDs, which are durable by construction.

**The check.** On connecting, resolve the newest stored `egeria_report_guid`
with **`ClassificationExplorer.get_element_by_guid`**
(`pyegeria/omvs/classification_explorer.py:3181`). If it does not resolve, every
publish row recorded before this connection is suspect, and the UI says so.

Two details from the signature, both worth getting right:

- **Pass a minimal `graph_query_depth`.** It defaults to `3`, which pulls a
  graph. An existence check wants the element and nothing around it.
- **The return type is the answer.** The docstring: *"Returns a string if no
  elements found; otherwise a dict of the element."* So `isinstance(result, dict)`
  is the test — not an exception, and not a truthiness check on a string that
  happens to be non-empty.

**A correction, because I recommended the wrong client and for a bad reason.** I
specified `MetadataExpert.get_metadata_element_by_guid`, and
**`MetadataExpert` is for special situations** — a differently-shaped response,
and most of its methods have equivalents on `ClassificationExplorer`, which is
the client to reach for. `ClassificationExplorer` is already imported in
`worker.py`.

What made me confident was the wrong kind of evidence: I cited
`rfa_egeria_sync.py`'s construction of `MetadataExpert` *"for verification/
reconciliation"* as precedent, and treated the precedent as validation.
**Existing usage is evidence about what happened, not about what is correct.**
That is the same mistake as deriving this board's state from reply documents
instead of from the code — trusting a record of a decision in place of the thing
it describes. Twice in two days, in two forms.

What still holds, and is why this shape is right:

- **One call per connection, not one per render.** The cost rules say a price is
  either named or not paid; one call at connect time is not worth naming.
- **No new Egeria concept, and no operator discipline.** Nothing has to be
  recorded when someone wipes the store, so nothing is wrong the first time
  someone forgets.

## 4 · What it says on screen — and what it must not claim

Publish state gains a third reading. Not *published* and not *never published*:

> **published** · <span>09-12</span> · <span>14</span> annotations
> ⚠ these elements are no longer in the store · *publish again ›*

**The sentence states what was observed, not why.** My first draft read *"the
store was redeployed on 09-14 — these elements no longer exist"*, which asserts
a cause the check cannot establish: a failed resolve means the elements are gone,
and a wipe and an individual deletion look identical from here. Naming the wrong
cause is worse than naming none, because it sends someone to check the wrong
thing. **If the redeploy date is independently known, the row may name it; if it
is inferred from the failed resolve, it may not.**

The rest are rules this project already applies:

- **Flag, do not delete.** The row is the only evidence that a publish happened
  and what it contained. It stays, exactly as a withdrawn proposal leaves its
  verdict standing with a flag. Deleting the history to tidy the screen destroys
  the record that the elements ever existed.
- **`publish again` is honest about being a re-publish**, not a repair: it
  creates new elements with new GUIDs, and the cost ladder applies to it as to a
  first publish.
- **Absence is a state, and there are now four of them** — *never published*;
  *published*; *published, and no longer in the store*; *publish failed*. None of
  them is a blank.

## 5 · Where else created GUIDs are stored — one look before building

`rfa_egeria_sync.py` stores the GUIDs of ToDos it creates, and `members.py`
holds Egeria GUIDs too. ToDos are created elements, so they have exactly this
problem; `members.py` may hold a mixture, and **a mixture is the case that
matters**, because a check that flags archive-sourced GUIDs as stale would be
wrong in the opposite direction.

So before building: find every place a created-element GUID is persisted, and
confirm whether any store mixes created with archive-sourced. If they do mix,
**the origin has to be recorded alongside the GUID** — one column, written at
insert, where the writer knows the answer for free. If they do not mix, the
per-table check in §3 is sufficient and nothing more is needed.
