# REPLY — Designer: work lists and investigations (2026-09-29, extended 2026-10-01)

To ASK-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md (#354), all eight questions.
§1–§4 were read against main at 8c6f1845. §5–§8 (question 5 and the data-lens
addendum) were read against main at fffcc3a0, together with
`DESIGN-FIND-AND-INTEGRATE-PURPOSES-AND-THE-DATA-LENS.md`. Drawings, on canvas
page 15: `wireframes/WorkListsAndScope.dc.html` (§1–§4) and
`wireframes/InvestigationLensFitPairs.dc.html` (§5–§8).

## The answer to the owner, in one sentence

*An investigation says why, and its **scope** says which resources, of
any kind; a **work list** is a bench: one kind of resource, laid out
against one stage's questions, so you can decide which belong in a
scope.*

The confusion doesn't come from two names for one mechanism. It comes from
one name, "work list", used for four things, plus a third name,
"Members", for scope on the investigation page. That sentence should appear
on screen once, on the list of work lists (§1).

## First: the ask's premise doesn't hold

The ask says "one mechanism with two names" and sets the constraint "one
`working_sets` table, an investigation owning one". The code has **two
mechanisms in two modules**:

| | Investigation's scope | Work list |
|---|---|---|
| Tables | `working_sets` + `working_set_members` + `investigation_resource_lists` (registry.py) | `work_lists` + `work_list_members` + `work_list_runs` (work_lists.py) |
| Created by | `get_or_create_working_set(slug)` → `get_or_create_folio` | `WorkLists.create()` |
| Resource types | mixed: repos, databases and filesystems in one scope | exactly one; `work_lists.entity_type`, default `repo` |
| Member reason | `membership_rationale`, which gets **overwritten** on re-add (`ON CONFLICT … DO UPDATE`) | `rationale` + `confidence` |
| Narrowing | by verdict: per-disposition WorkingSets (`get_or_create_disposition_set`) | by a new list: `promote()` records `derived_from` |
| In Egeria | Project → ResourceList → **Folio** Collection | **WorkingSet** Collection, `WorkingSet::resource-explorer::<slug>` |
| Link between them | none | `work_lists.investigation` (TEXT), set on save if an investigation is current; nothing reads it except an unused filter (`list_all(investigation=)`) |

So an investigation does *not* use a work list for its resources. It uses a
Folio, and a work list is a separate row that can be tagged with an
investigation it doesn't feed. Everything below keeps both tables as they
are. The constraint holds for my answers; the few route changes they need
are listed at the end.

### "Work list" names four things

| Where on screen | What it creates today | What it is |
|---|---|---|
| Select bar → **save as work list** (app.js ~2147) | a list of N resources of one type | **a work list** (the bench) |
| Work list pane → **promote N →** | a narrower list with `derived_from` | **a work list** (a shortlist) |
| A report's acts → **add to work list** (app.js ~3664, `projects.py` ~2031, repos and databases alike) | a **new one-resource list on every click**, named after the report; the picked rows survive only as the rationale sentence | "I'll deal with this," beside "raise RFA" (someone must) and "note in journal". **This is the trigger**: on a database's report, the act that reads "work list" is the one a person reaches for to say "this belongs in my investigation." |
| An analysis's member list → **add to work list** (app.js ~5228, `projects.py` ~1714) | the same: one list holding only the repo, with the analysis members (files, components) reduced to prose | the same to-do act |
| Journal → **suggest to…** (`journal.py`, `SUGGESTION_PREFIX`) | one list per audience, `suggested-to-data-expert` | an inbox |

The sidebar's "Work lists · N" shows all of them together, unfiltered
(`listWorkLists()`, app.js ~2450). A steward sees their comparison bench
next to a list with one member that's really a to-do, and a list named
`suggested-to-…` that's really someone's inbox. `work_lists.py`'s own
docstring rules out the second use: activities belong in `WorkItemList`,
*"a list of activities such as ToDos"*, and a work list holds "the
RESOURCES being worked on, not the activities being done to them."

I couldn't read the live counts (the `/api` calls returned 401; the
browser session has expired), so I can't say which of these the owner's
"Work lists · 3" were. The code shows every kind can appear there.

## 1. Vocabulary and the sidebar

**Ruling: (b), except "working set" never appears on screen.** Three words,
one for each thing:

- **investigation**: why. Purposes, charter, verdicts. Egeria: Project.
- **scope**: which resources, of any type. This is already the word on
  screen: the "In scope" chip, "＋ scope / − scope", and the crumb. The one
  holdout is the investigation page's **"Members (N)"** heading, which
  becomes **"Scope · N"**. Egeria: the Folio, reached through ResourceList.
- **work list**: the bench. One resource type, compared as rows ×
  questions, narrowed by shortlisting. Egeria: WorkingSet, whose own
  definition, "a list of elements that are being worked on", fits. The name
  stays, and from now on it means only this.

"Working set" is the code's word and Egeria's type name. It appears where
provenance goes: in the publish evidence ("published as a WorkingSet
collection"), never as a label.

Why not the other two options:

- **(a) "an investigation is a work list with a purpose"** reverses the
  relationship. An investigation *has* a scope and may keep several lists
  beside it: shortlists, per-disposition sets, and the bench it started
  from. Under (a), the suggestion inbox would also be "a work list with a
  purpose."
- **(c) every list starts as an investigation, purpose Explore.** Explore
  is a real purpose, not a default. `egeria_binding` defaults to `egeria`,
  so every scratch comparison would become a Project candidate. It also
  loses the order of work: people compare *before* they know why, and the
  bench is where they find out.

How the three words read by perspective. A **steward** reads "scope" as a
boundary they can govern. A **data owner** sees their database as "in scope
for Customer 360", which answers the question they actually have (who is
looking at my thing, and why). An **app builder** uses the bench and
rarely needs the investigation at all. Nobody has to learn "working set".

**The sidebar** (drawing, panel A), top to bottom:

1. Investigation selector, unchanged.
2. When one is current, two lines directly under it:
   - **Scope · 7**, which opens the "In scope" chip. A count opens what it
     counted.
   - **Work lists for Customer 360 · 2**, using the `list_all(investigation=)`
     filter that already exists.
3. **Other work lists · 3**, folded to that one line while an investigation
   is current. With none current, the section reads **Work lists · N** and
   lists them all, as today.
4. **Suggestions · N** (the `suggested-to-*` inboxes), on their own line,
   not among work lists. They stay present and explained, not hidden.

The list of work lists (the ▦ front door) gets the one-sentence definition
as its opening line, not just in the empty state.

## 2. The two missing actions

Both go on the **work list pane's action row**, beside "publish to Egeria",
and on each row of the list of work lists. They don't go on the sidebar
row: one click there opens a list, as it does now.

**"Add these 9 to Customer 360"** (the button names the current
investigation, never "scope" alone). With rows ticked, it reads
**"Add 3 selected to Customer 360"**. With no investigation current,
it reads **"Add to an investigation…"** and opens a picker listing the open
investigations plus "start a new one". Each member's `rationale` goes in as
`membership_rationale`, so the reason carries over; a member with no rationale gets "from work list *Sales databases*". A member already in
scope is **skipped, not re-added**: re-adding overwrites that member's
existing reason. The note says "2 were already in scope; their reasons were
kept."

**"Start an investigation from this list…"** In two clicks: this button,
then **Start** in a dialog prefilled with name = list name, description =
list description, the purpose chips unselected (purposes are optional in
`InvestigationCreate`), and the **binding shown and chosen** (local or Egeria;
default Egeria, per the model). "Start" creates the investigation, adds the
members as above, tags the list with the investigation, and makes it
current.

**Afterwards, the list stays.** It doesn't disappear, as the ask proposed.
It moves to "Work lists for Customer 360" in the sidebar. The list is the
record of how the scope was chosen: its `derived_from` trail and its grid
of answers are the evidence behind the decision. Deleting the bench would
delete that evidence.

What the list shows once linked (panel B):

- A header line, **"7 of 9 in scope for Customer 360 · 2 not added"**,
  computed live against `list_investigation_members` and never stored. The
  two tables are separate, so this is a copy, not a share. If someone later
  removes a member from scope, the line becomes "6 of 9". Drift shows up; it
  doesn't hide.
- A scope column on each row with the words **in scope** / **not in scope**,
  in ink. No new glyph and no accent color: it's a state, and `glyphs.js`
  doesn't need a sixth table.

## 3. The comparison grid

**Yes: the investigation page carries the grid for its scope.** The page's
Scope section *becomes* the grid, and the members table merges into it
rather than sitting beside it (panel C).

- **One grid per resource type.** A scope is mixed and each type has its
  own question catalog (`listAnalyses(entity_type, { intent: stage })`).
  Tabs: **Repos 4 · Databases 2 · Filesystems 0**. The zero tab stays,
  muted, with the line "nothing of this kind in scope".
- The members table's columns (disposition, reason, remove) become the
  grid's left columns: one table instead of two.
- The section's heading line links the work lists tagged to this
  investigation ("work lists: Sales databases, Candidate repos"), so the
  bench is one click from the scope it fed.
- The questions come from one stage at a time, with a stage row above the
  grid, defaulting to the last stage the person was on. That's how the work
  list pane already works.
- Batch runs need no new table: `enqueue_batch(work_list_slug="")` already
  runs without a list.

**The grid isn't the only thing a list is for.** A work list still has
things a scope can't have: it's one type throughout, it has no purpose (you
can compare before you know why), it narrows by shortlist with a
`derived_from` trail, and it can be published on its own. The two narrow
differently, and that difference is worth keeping: **a list narrows by
making a new list; a scope narrows by verdict** (disposition). The
investigation grid's narrowing control is "mark as…", not "promote".

If the slice has to be small, ship §1, §2 and §4 first. The gate doesn't
depend on the grid.

## 4. Adding from the sidebar, and from a finding

**The select bar.** With an investigation current: **"＋ add to Customer
360"** and **"− remove from Customer 360"**. Long names truncate at 24ch,
with the full name in the title. **"save as work list…"** comes after them.
With none current: **"＋ add to an investigation…"** is *enabled* and opens
the same picker as in §2. After adding, that investigation becomes current,
and the note says so: "Added 3 to Customer 360, now your current
investigation." "− remove" stays visible but disabled, with the reason as
visible text in the bar ("no investigation selected"), not only in a
tooltip. Today both buttons are dashed and disabled, and only the tooltip
explains why.

**The report and member-list acts (the actual trigger).** "add to work
list" becomes **"add to Customer 360"**. It puts the resource in scope,
with the act's provenance line (already computed as `line`) as
`membership_rationale`, and the record learns it was used (`add_use`,
act `scope`). With none current, it reads **"add to an investigation…"**. If
the resource is already in scope, the act reads **"already in Customer
360's scope"** as text. It doesn't re-add, because re-adding overwrites the
existing reason. "Note in journal" remains the way to record the finding
against it.

This means these acts stop minting one-resource work lists. The "I'll deal
with it" to-do they were standing in for belongs in `WorkItemList`, as the
module's own docstring says. That's a separate ask, and I haven't folded it
in here.

The gate as stated then passes. A person adds a database to an
investigation from the sidebar, or from its report, without seeing the words
"work list". They can turn a saved list into an investigation in two clicks.

## 5. A stage click while a work list is open

**Ruling: it depends on the stage's class, which `STAGES` in app.js already
records.**

What happens today: `loadPane()` checks `state.workListSlug` before
anything else, and `openWorkList()` is passed `stage: state.stage`. So after
a stage click the list does re-render, with the new stage's questions
(`listAnalyses(entity_type, { intent: stage })`). On a run stage that's
correct. On Investigation, Understanding or Automate, the person gets the
bench with an empty question menu and the sentence "no analyses are
catalogued for the investigation stage", under a nav highlight that promised
something else.

- **Run stages** (Scouting, Discovery, Assessment, Analysis, Enrichment,
  Curate): **the list stays open and re-scopes to that stage's questions.**
  That's what the bench is for: the same nine databases, now against
  Assessment's questions. The pane's title line has to say so, because the
  person just changed what the grid means: "Sales databases · 9 databases ·
  **Assessment's questions**". Enrichment keeps its present, explained empty
  state; its catalog is empty by construction.
- **Frame and cross-cutting stages** (Investigation, Understanding,
  Automate): **the click goes to the stage, and the list closes** into the
  "▦ back to Sales databases" nav link that `lastWorkListSlug` already
  drives. These stages aren't about questions, so keeping the bench would
  leave the person's click unanswered.
- **One special case:** clicking Investigation while the open list is linked
  to an investigation (§2) opens *that* investigation's page with its Scope
  section in view, even if it isn't the current one. The bench and the scope
  it fed stay one click apart in both directions; the header link from §3
  ("work lists: Sales databases …") is the way back.

The routing fix in 2c66adf4 deliberately left the work-list branches first.
That stays true for run stages. The change is one check in front of them: a
frame or cross-cutting stage clears `workListSlug` into `lastWorkListSlug`.

## 6. "What we're looking for": the data lens on the investigation page

**Placement: between the purposes and the scope.** The page reads top to
bottom in the order a person reasons:

1. **Why**: name, classification, binding, purposes, description.
2. **What we're looking for**: the data lens.
3. **Which**: Scope, which is the grid from §3. Verdicts are its left
   columns, not a section of their own.
4. **Between them**: pair questions (§8).
5. **Next steps.** This moves down from the top. Its items ("declare a data
   lens", "run column profiles on crm_prod") follow from the sections above,
   and read better after them.
6. **Egeria**: binding, sync, relink. This is housekeeping, so it goes last.

The data lens comes before the scope because the scope is measured against
it. Under Find, the grid's fit columns *are* the data lens's rows (§7), in the
same order, so a reader goes down the lens and then across a candidate's row
and sees the same list twice.

**The rows** (panel E). There is one row per declared dimension, using the
shared row anatomy:

| Column | Content |
|---|---|
| dimension | subject, data classes, location, organization, time range, grain, currency, quality, terms of use |
| value | the literal, or the referenced element's display name |
| what it is | **"Egeria GlossaryTerm"** (or DataClass, Location, …) for a reference, with the qualified name as its title; **"literal · not in the glossary"** for a literal |
| provenance | "dwolfson · 09-30", in the provenance size |
| control | edit, remove |

The literal-or-reference distinction is written out in words. It's
provenance, not state, so it gets no glyph and doesn't add to the tables in
`glyphs.js`.

**Undeclared dimensions get one line, not nine empty rows:** "Not declared:
location, organization, quality. Find won't rank on these."
Each name in that line links to declaring it. That keeps the dimensions
present and explained without making the lens look three-quarters empty.

**The section's own state line** sits under the title, beside its
provenance: "9 dimensions · 6 declared · local, not yet a DataLens in Egeria"
→ after publishing, "published as a DataLens · *time*", which appears only
once the published element has been read back, as with every other publish.

**With no data lens**, the section remains as one sentence and a control:
"Nothing declared. Find can't rank candidates until a data lens says what
you're looking for. **Declare a data lens ›**". If Find is one of the
investigation's purposes, the same line also appears as the first item in
Next steps.

**Signing.** Declaring needs a signed-in person, as the report acts do. The
Context tab's human-question answers have no author today
(`state.contextAnswers`, entered through `window.prompt()`). The data lens
must not repeat that, because a requirement with no author can't be
questioned.

**Three wording fixes that ship with it:**

- Context's link (context.js:99) is a `<span>` with the title "declare a lens
  on the investigation — not built in /next". It becomes a real link to
  `#data-lens` on the current investigation's page, titled "declare a data
  lens".
- The `needs-lens` state's word in glyphs.js, "needs a person: declare a
  lens", becomes "needs a person: declare a data lens". It's the same state,
  pointing at the same place.
- `resource_questions.csv` row 12 says bare "lens" for the scope-similarity
  question. The CSV isn't screen text, but its question text is, so check it
  when the catalog next changes.

## 7. A candidate's fit, as a row

**The row is the data lens's dimensions, one cell each, at equal widths, in
the lens's order** (panel F). Two strong dimensions can't hide four silent
ones, because each silent dimension takes up as much space as a strong one.
A cell can't be dropped, merged or shrunk, and nothing turns a row into a
bar or a percentage.

**The four states map onto existing glyph families. None is new:**

| State | Glyph and family | Word on the cell |
|---|---|---|
| fits | ✓ measured | "fits", plus what matched ("Sales ← *orders, invoices*") |
| does not fit | ∅ measured nothing | "doesn't fit", plus what was found ("2015–2018") |
| could not check | ? not established | "couldn't check: *row counts not established*" |
| not measured | ◌ no answer here | "no reader yet" |

Two choices here need defending:

- **"Doesn't fit" uses ∅, not ✕.** In glyphs.js, ✕ means *error*: something
  failed. A measured mismatch didn't fail. It was measured, and none of what
  was asked for was there, which is exactly the ∅ family. A ✓ beside
  "doesn't fit" would read as approval.
- **"Not measured" can't be the on-screen word.** glyphs.js already has a
  `not_measured` key, in the **?** family ("rows not measured"), which means
  something else. Showing the ask's "not measured" with ◌ would put one word
  on two glyphs. ◌'s existing word, "no reader yet", is the right one, and
  the headline uses it too.

**The headline** sits in the cell beside the name, and its counts always add
up to the number of declared dimensions:

> **3 of 6 fit** · 1 doesn't · 1 couldn't check · 1 no reader yet

There are three rules. The denominator is what was *declared*, never what
was measured, so every row in a ranked list is out of the same number. Every
state with a nonzero count is named. And the counts add up. The ask's
form, "matched on 4 of 6 · 2 not measured", names only two of the four
states. On a row with one doesn't-fit and one couldn't-check, it would have
to drop one of them or misname it. Naming all four is what stops "4 of 6"
being read as "4 of 6, and the other two are fine"

**Ranking.** The order is: most fits, then fewest doesn't-fits, then
currency (design §4). Ties are broken by name, never by a hidden score.
Above the list, the order is stated in words: "Ordered by dimensions that
fit, then fewest that don't, then most recently written to". Silence never
raises a row: a candidate with 2 fits and 4 "no reader yet" sits below one
with 3 fits and 3 "doesn't fit". That's correct, and the headline shows the
reader why.

**Verdict controls go on the left edge, beside the name, not beside the
headline.** The verdict is a decision about the resource; the headline is a
measurement. If they sat next to each other, a reader would take one for the
other. It's the same "mark as…" control as the scope grid, single and bulk.
Rows with a verdict don't move in the ranking, with one exception: **Ignored**
rows fold into a closing line ("Ignored · 3 — show"), still present.

**The ask's verdicts, adopt, archive and ignore, aren't the vocabulary.**
`VALID_DISPOSITIONS` is undecided, tracking, investigating, recommended,
using, abandoned, ignored, and Find should use it: "recommended" or "using"
covers adopt, and "ignored" is ignore. **"Archive" shouldn't be a verdict at
all.** The design doc's own rule (§5.1) is that "probably an archive is a
finding with its evidence, never a verdict". It belongs in the *currency*
cell ("∅ doesn't fit: no writes since 2019 · probably an archive"), where its
evidence is visible. Making it a verdict would let a person file a database
as an archive with no evidence on the row.

**Per type.** `preliminary_fit` is database-only (`resource_types:
["database"]`). In the Repos tab of the scope grid, the fit view says so once
in the column header, "◌ fit is measured for databases only", not with eight
◌ cells per row.

**Where the view lives.** It's an option of the scope grid's view switch from
§3: "**Fit against the data lens** · Scouting's questions · Assessment's
questions · …". It comes first when Find is a purpose (purposes rank and
never exclude) and is still available when Find isn't. With no data lens, the
option is shown with a dashed underline and reads "Fit — no data lens
declared".

## 8. A question about a pair

**Pair questions get their own section, "Between resources", under the scope
grid, not columns in it** (panel G). The grid's rows are resources, and a
pair question added there would show up on two rows, as if it were about
each of them. That's the misreading the ask wants to avoid.

**One row per pair. The subject cell holds both names, stacked and joined by
a bracket on the left**, so the row's subject is visibly the two together:

```
┌ laz_local_adventureworks      shared identifiers   grain           time coverage
└ region_north_forecast         ✓ 2 keys agree       ∅ month ≠ week  ✓ overlaps 2023–2025
```

Answer cells use the four states from §7. Where a question is about a
relation, as in the scope-similarity row of `resource_questions.csv` (same,
contains, contained by, overlaps, disjoint, not established), the relation is
the word in the cell. No arrow glyph is added; "contains" reads the same in
either direction once the pair's order is fixed (alphabetical, and stated in
the section header).

**Parity is a precondition per question, not per row.** One question may need
column profiles and another only table counts, so a pair can be comparable
on one and not the other. A blocked cell reads "○ can't compare yet", in the
not-run family because an analysis hasn't been run, and its detail names it:
"crm_prod lacks column profiles · **run column profile on crm_prod ›**". If
every cell in a row is blocked by the same gap, the cells merge into one
sentence across the row, still in the same family. That merge is the only one
allowed, and only because it says the same thing once rather than four times.

**Scale.** Seven resources in scope make 21 pairs. The section shows:

- **pairs per kind pairing**, with tabs mirroring the grid's (Databases ×
  databases · 3 pairs, Repos × databases · 8 pairs), so a question appears
  only where its `subject_shape: pair` applies;
- a **"pairs with: [resource]"** filter, defaulting to every pair;
- **groups**: "Comparable · 2" first, then "Can't compare yet · 1", whose
  line names the single most common missing analysis ("1 pair waits on
  column profiles for crm_prod") as a next step.

A pair question never appears on a single resource's pages. Compilers that
skip pair questions (design §5.3) say so in their output, and a resource's
Questions tab says "Questions about this resource and another are on the
investigation's page", as one line with a link, only when the resource is in
a scope that has any.

## What the slice changes

| Change | Kind |
|---|---|
| "Members (N)" → "Scope · N"; select-bar, report-act and member-act labels as above | wording |
| Sidebar: scope line, lists for this investigation, other lists folded, suggestions separated | UI; `list_all(investigation=)` exists; the suggestion split is a prefix filter |
| `RecordAct` and the member-promote body accept `action: "scope"` + `investigation` | route |
| Set `investigation` on an existing work list (the column exists; there's no route to set it) | route |
| "7 of 9 in scope" | computed client-side from two existing reads |
| Investigation grid | UI; reuses worklist.js's renderer with a scope source |
| §5: a frame or cross-cutting stage click closes the open list | one check in `loadPane()` |

No schema change for §1–§5. The rest follows the design doc's own slicing
(§9 there): §6 is its slice 2 (the `data_lens` record, which *is* new
storage, owned by that design, not this ask), §7 is its slice 3, and §8 is
its slice 6. The "Members" → "Scope" rename and the page order from §6 are
worth doing in the first slice, so the data lens has a place to land.

## Left open, for the architect

1. When an investigation is promoted to Egeria and a work list linked to it
   is published, both collections exist, but nothing relates the WorkingSet
   to the Project. `investigation_resource_lists` keys on `working_sets`,
   not `work_lists`, so relating them isn't possible without a model change.
   Is that link wanted?
2. The one-resource lists and `suggested-to-*` inboxes already created:
   leave them (they show under "Other work lists" and "Suggestions"), or
   migrate them once the to-do ask lands?
3. Adding from the sidebar with no investigation current makes the chosen
   one current. I think that's right, because the crumb and every stage page
   follow the current investigation. The owner should confirm.
4. Partial fit. A lens asking for 2024 onward against a resource covering
   2023–2025 is neither "fits" nor "doesn't fit". The four states don't
   cover it. ◐ (limited, "fits partly") is the obvious fifth, but should a
   partial fit count toward "of 8 fit"? My suggestion is that it shouldn't;
   it gets its own count in the headline. That's a decision for the owner,
   not for me.
5. "Archive" as a verdict (§7): is the ask using it as a finding, as the
   design doc does, or does the owner want a disposition that means "keep,
   but don't integrate"? If it's the latter, that's a seventh value in
   `VALID_DISPOSITIONS`, and it needs its own definition.

Not folded: `stages/investigation.js` has its own emoji `DISPOSITION_GLYPH`
table (👁🔬👍✅🪦🚫), another glyph map outside `glyphs.js`. I'm noting it for
the glyph consolidation, not for this slice.
