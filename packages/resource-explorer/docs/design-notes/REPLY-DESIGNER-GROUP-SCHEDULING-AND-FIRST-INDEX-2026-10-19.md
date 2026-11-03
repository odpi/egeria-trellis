# REPLY — Designer: scheduling many repositories, and asking before a first index (2026-10-19)

To ASK-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md (on
`re/ask-designer-preview-and-scheduling-2026-10-19`), read against main at
55c5c6ea: `stages/automate.js` (`scheduleFormHtml`, the Schedules table),
`surveyors/sub_surveyors/rag_ingestion.py` (the "never indexed" outcome), the
sidebar's Select bar (resource controls reply) and Admin's Groups. The
table-preview ask has its own reply. Drawing:
`wireframes/PreviewAndScheduling.dc.html`, canvas page 23, panels B and C.

**One distinction carries most of these answers. It's the same one the
cataloguing-at-scale reply drew for scope:** acting on many things *now* is
a bulk choice, and deciding for a group *now and later* is a rule. A
schedule on a selection of repositories is the first. A schedule on a
Group is the second, and a repository added to the Group later picks it up.
Same words, same precedence, so a person who learned one has learned both.

## 1. Finding it

**The form is always visible, with the resource inside it.** Today it hides
until a resource is picked, and the owner missed it. That's the
"tabs never disappear" failure in a form. The form's first field becomes
"For": a picker with a filter box, prefilled with the selected resource
when there is one:

> **Add a schedule** · For [ coco_pharma ▾ ] · What [ a survey ▾ ] · Which
> [ … ] · How often [ weekly ▾ ] · **Schedule**

The picker also offers the Groups ("Group · Python services (14)") and
"the 9 selected" when the sidebar has a selection. So one form covers
one, a selection, and a group.

**Two more doors, both opening this form prefilled:**

- **"Schedule…" on a Group**, in Admin's Groups and on the Group's header in
  the sidebar.
- **"Schedule…" on the Select bar**, beside the existing acts on a
  selection.

**Not on each sidebar row.** Seventy-three rows, each with another control,
is clutter for an act a person does a few times a month. The ⏱ on a
question row and the one in chat stay, since they schedule one specific
thing from where it's asked.

## 2. Many at once

**Both, and they mean different things:**

- **A selection** makes **one schedule per repository**, now, each signed as
  if made one by one. A repository selected later doesn't get it.
- **A Group** makes **one schedule for the Group**. It follows membership:
  a repository added to the Group is scheduled at its next run, and one
  removed stops.

**The Schedules table shows what was made.** A Group schedule is one row,
expandable to its members:

> ▸ **Python services (14)** · repo analysis survey · weekly · next Mon 06:00
> · last run 12 of 14 ✓ · 2 failed ›

Opened, each member is a row with its own last run, next run and state, and
a tag "from Python services". A selection's schedules are ordinary rows,
one per repository.

## 3. Mixed cadences

**The repository's own schedule wins, then the Group's.** It's the same
precedence as scope (a hand choice over a rule): a schedule someone made for
that repository is the more specific decision. On the Group's expanded row,
that member reads:

> coco_api · **daily** · own schedule · differs from Python services (weekly)

**Two Groups that disagree on the same survey:** the more frequent cadence
runs, and the row says which and why:

> coco_api · daily · from Data apps (daily) · Python services says weekly

This is unlike scope, where disagreeing rules decide nothing. A missed
refresh leaves stale facts on screen, and an extra run costs only time. Both
are visible, and neither is irreversible, so the safer one runs.

## 4. First index, asked first

**What the ask shows: what's measured, and what's estimated from what was
measured, each saying which.** Chunk counts mean something to the builder,
not to the person, so they sit on demand.

> **Index coco_api for chat?**
> 1,240 files · 38 MB of text · from the survey of 10-17
> fetches: clones the repository (≈ 240 MB) · or "uses the local copy" when
> RE has one
> about 6 min and 110 MB of index · estimated from the 31 repositories
> indexed here
> **Index it** · not now

The estimate's basis is stated, because it's a guess until the first index
"fixes the price" (the depth-offer wording). After a first index, the row
shows the real figures.

**One confirm for a batch, never forty-two.** A confirm per repository at
that scale is a click ritual nobody reads. The batch confirm shows the
totals and the list:

> **Index 12 repositories for chat?** 14,800 files · 410 MB of text · 7
> fetch · about 1 h 10 min and 1.3 GB, estimated · **Index them** · show
> the 12 ›

**Progress shows in two places.** In Automate, a **First indexing** list
gives one line per repository (waiting · indexing · indexed · failed),
with "3 of 12 done". On each repository, the cue changes in place: "not
yet indexed" → "◔ indexing · 3rd of 12" → "✓ indexed 10-19". Each
repository's state is on the repository, and the batch's state is in
Automate.

**Where the "not yet indexed" link goes:** to the rag_ingestion analysis row
on that repository's Survey & analyses tab. That's where the owner said the
act belongs ("the analysis step should create it but ask first"), so the row
becomes "Index for chat…", opening the ask above. The many-at-once door is
in Automate and on Groups and the Select bar: "Index for chat…" next to
"Schedule…". From Automate, "42 not yet indexed · choose which ›" opens
the batch ask with all 42 listed and ticked.

## 5. A failed scheduled run reaching a person

**Both, kept quiet.** A Schedules row is enough only for someone already
looking at Schedules, and a schedule exists precisely so nobody has to.

- **A cue on the resource:** the sidebar row gets ✕ beside its name, with
  "scheduled index_health failed 10-19 · ‹first sentence›" on hover. It
  clears when that schedule next succeeds.
- **One RFA per failing schedule, not per failed run.** The drawer item
  says how many runs failed in a row ("coco_ods index_health · failed 3
  runs since 10-17") and resolves itself on the next success. Seven
  repositories failing nightly must be seven items, not forty-nine.
- **The Schedules row** keeps its state and gains Egeria's or the step's
  first sentence, with the rest on demand.

## 6. Words

- **"not yet indexed": confirmed.** It's the owner's own phrase, it's
  true, and it implies the next step.
- **"chat can't search this repository's own content yet": replace with
  "chat can't search its files yet · index it ›".** "Own content" leaves
  the reader guessing what else chat could search. "Files" is what's
  missing: chat can still answer from what RE measured. The link makes the
  sentence actionable.

## Slices

| Piece | Round |
|---|---|
| The form always visible, with a For picker (resource, selection, Group); Schedule… on Groups and the Select bar | group scheduling |
| Group schedules that follow membership; expandable rows; "from ‹group›" and "own schedule · differs" tags; two-group rule | group scheduling |
| Failure: sidebar cue, one RFA per failing schedule, first sentence on the row | group scheduling |
| The index ask with measured and estimated figures, each sourced; batch confirm | first indexing |
| First indexing list in Automate; the cue on each repository; "Index for chat…" on the analysis row, Groups and the Select bar | first indexing |
| "not yet indexed"; "chat can't search its files yet · index it ›" | first indexing (copy) |

## Left open

1. Whether the time and disk estimate can come from the 31 indexed
   repositories (bytes of text against minutes and index size), or whether
   the ingestion run records too little to estimate from. If it can't, the
   ask shows the measured figures alone, with "time: unknown until the first
   index".
2. Whether a Group schedule should run its members together (one run, one
   batch) or as separate runs at the same time. It matters for load, not
   for the screen.
