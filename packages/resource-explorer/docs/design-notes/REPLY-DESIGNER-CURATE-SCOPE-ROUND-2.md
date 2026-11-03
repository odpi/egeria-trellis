# REPLY — Designer: the catalogue scope tree in use, round 2 (2026-10-05)

To ASK-DESIGNER-CURATE-SCOPE-ROUND-2.md, read against main at e1770aec:
`catalogue_scope.py` and its routes (slice A), `db_derived.derive_change_rates`,
the `database_tables` rows (`source`, `surveyed_at`, `state`),
`evidence/CATALOGUE-LEVER-FINDINGS.md` and `CURATE-CATALOGUE-SCOPE-A-IMPLEMENTED.md`.
Drawing: `wireframes/CatalogueScopeRound2.dc.html`, beside round 1 on
canvas page 18.

## First, two corrections to round 1

**The coco_pharma premise.** Round 1 opened with "the 2026-10-03 reset
erased the 7-schema scope". The corrected ask shows that nothing was
erased: the database **grew**, with 22 schemas loaded on purpose during the
redeploy. The rule I drew from the wrong story still holds, for a better
reason. Without a declared scope, what gets catalogued is whatever the
database contains on the day. With one, an extension arrives as "new since
your scope" and a person decides. §4 below is built on that.

**The data-lens proposal, as built, matches by name.** Slice A's
`data_lens_match` proposes "catalogue" when a table *name* contains one of
the lens's subject terms. The lens design rules that out ("matched through
the glossary, never by string"), and round 1 called it "a measured match",
which a name substring isn't. Until the glossary path exists, a lens term
found in names becomes **a suggested rule** (§3), not a proposal: "The
lens names *Sales*: 14 table names contain it · make that a rule? ›". A
person writing the rule is exactly the owner's "catalogue every table with
Sales in its name". It's signed, which is what a name match needs to be.

## 1. Reuse what was measured, and date every number

**The tree reads only stored rows. Opening Curate never queries Postgres.**
The sources, in order of preference for each fact:

| Fact | Sources (stored) | Preference |
|---|---|---|
| rows | `database_tables.row_count`, keyed by `source` (RE's survey; Egeria's where its annotations are stored there — the builder confirms which path writes them), each with its `state` (`measured` scan, `catalog_estimate`) and `surveyed_at` | a measured count over an estimate; then the newer |
| size | `database_tables.size_bytes`, same sources | the newer |
| writes | `database_table_activity`: the cumulative `rows_inserted/updated/deleted` and `stats_reset`; `db_change_rates` between the last two surveys | see the window rule below |
| data classes, PII | `data_class_match` annotations | as stored, with their run date |

**Every number carries its source and as-of on hover and in the row's
detail line.** In the column itself it shows its kind: "≈3,400" for an
estimate, "3,412" for a scan. When the two surveys disagree by more than a
factor of two (pg_stat's 0 live rows against RE's ≈3,400 on coco_sus), the
cell reads **"◐ sources disagree"** and its detail gives both, each dated.
It never silently picks one.

**The activity word and its window.** This is the main correction to
slice A. Slice A reads "idle" from `db_change_rates`, which differences
*the last two surveys*. On coco_pharma that's a three-day window, so the
row's "no writes since 09-27" claimed more than three days could show.
The window needs a stricter rule:

- **The evidence is the cumulative counters since `stats_reset`,** not the
  two-survey delta. `n_tup_ins + n_tup_upd + n_tup_del` since the last
  reset is the longest window Postgres offers. The window is
  `stats_reset → the survey's date`, and it's printed on the row.
- **The word:**
  - **active**: writes counted in the window. "active · 1,204 writes since
    counters reset 06-02".
  - **dormant**: zero writes in a window at least as long as the
    **dormancy threshold** (90 days by default, shown in the column header
    and settable on the scope). "dormant · 0 writes in 338 days (counters
    reset 2025-11-02)".
  - **can't tell**: the window is shorter than the threshold, the counters
    reset inside it, or they weren't measured. "can't tell · counters reset
    10-03 · 2 days of evidence". Coco_pharma's old schemas land here today,
    and that's the honest answer.
- **"Can't tell" never proposes.** "Dormant" proposes leave out, and the
  proposal's reason *is* the window: "⏵ proposed: leave out · dormant, 0
  writes in 338 days". The two-survey delta stays where it belongs, in
  Understanding's activity chart, as a rate.

## 2. The facts beside the choice

**Columns, left to right:** choice · name · **rows** · **size** ·
**activity** (the word, then its window) · **data classes** (PII as a
mark) · state in Egeria. Schema rows add **tables** (a count that opens
them) and show the sums of their tables. A schema with any unmeasured table
has a sum that says so ("≥ 2.1 M rows · 3 tables not measured").

**No blank, no zero for what wasn't measured:**

| Case | Cell |
|---|---|
| never measured | "not measured" |
| measured, not readable with that credential | "? not established" |
| sources disagree | "◐ sources disagree" |
| measured zero | "0", which is a real zero |
| activity without enough window | "can't tell · ‹reason›" |

The names stay in mono, and the numbers align right in tabular figures, so
a column of row counts reads as a column.

## 3. Hundreds of tables: filter, sort, and rules

**Confirmed, with one sharpening: there are two different acts, and the
screen keeps them apart.**

- **A bulk choice** is a hand choice on many rows *now*. Filter, then "Leave
  out these 14". Each row gets its own event, signed, exactly as if ticked
  one by one. Tables that arrive later aren't touched.
- **A rule** is a stored, signed, dated criterion that decides rows *now
  and later*. That's the design session's position, confirmed.

**The filter bar** sits above the tree: *name contains* · *schema* ·
*activity* (active / dormant / can't tell) · *has PII* · *rows* (≥ / ≤) ·
*choice* (undecided / proposed / new since… / differs from rule). Sorting
is by clicking any column header. With a filter on, the bar offers both acts
side by side, and the difference is in the words:

> **14 tables match** · Catalogue these 14 · Leave out these 14 ·
> **Make this a rule…**

**Writing a rule** turns the current filter into the rule's criterion,
read back in a sentence the person names and confirms:

> Rule: *Sales tables* — **catalogue** every table whose name contains
> "sales" · by dwolfson 10-05

Activity criteria are allowed ("leave out every schema dormant at 90
days"). Since activity is re-measured, the rule's matches can change on
the next survey (below).

**Rules are listed** in a strip above the filter bar, one line each:
name, sentence, author and date, **"matches 14 · 2 new since 10-05"**
(both counts open their rows), and edit and retire controls. A retired
rule stays in the history, struck through. Its rows fall back to whatever
decides them next.

**What decides a row, in order.** This is the precedence, and the row
always says which one applied:

1. a hand choice on the table, "catalogue · dwolfson 10-04";
2. a rule matching the table, "catalogue · rule *Sales tables*";
3. the table's schema, by hand or by rule, "catalogue (from schema)";
4. undecided: it keeps what's in Egeria now (round 1).

**On screen, a rule's match differs from a hand choice by its source
tag**: the rule's name in a small bordered tag, linking to the rule. A hand
choice shows the person and date. Both are in ink, because both are
decisions. Only inheritance from the schema is muted. Where a hand choice
differs from a matching rule, the row says "differs from rule *Sales
tables*", so an override is visible.

**Two rules that disagree on a row decide nothing** for it. The row reads
"⚠ rules disagree: *Sales tables* catalogue · *Dormant schemas* leave
out", and it's counted in the manifest like a name conflict. A hand choice
on the row resolves it.

**A rule whose match changes after a survey** (a dormant schema becomes
active) is reported, not applied silently. The row reads "no longer matches
*Dormant schemas* · active since survey 10-12" and goes back to the next
decider in the precedence. The manifest lists every row whose decision
changed because a rule re-evaluated.

**Compiling to plain names.** At commit, every rule is evaluated in RE into
concrete per-node choices, and those compile into Egeria's lists, which hold
plain names only, with no wildcard (lever findings §1). The findings add a
risk the manifest must check. The cataloguer passes names to JDBC *as
patterns*, so `_` and `%` in a name are wildcards there: `order_items` also
matches `orderXitems`. Before commit, the manifest compares every compiled
name with `_` or `%` against the inventory. If another name would match,
the row says "⚠ Egeria would also match `orderXitems`". If none does, it
says nothing.

## 4. The fifth visit

**The first visit shows the whole tree. Every later visit opens on what
changed:**

> **Your scope** · declared 10-04 by dwolfson · 3 commits · last 10-11 ·
> 2 rules · Egeria in step with the commit of 10-11, except 2 tables
> waiting for its next refresh · [history]
>
> **Since 10-11:** 2 tables new in `sales` (matched by *Sales tables*) ·
> 1 schema new, `forecast_v2` (undecided · first seen in survey 10-14) ·
> 1 row no longer matches *Dormant schemas* · 1 survey-now-disagrees

The tree below opens **filtered to "needs you"**: new and undecided rows,
rows whose rule match changed, disagreements, conflicts, and waiting rows.
"Show the whole scope (266)" switches to everything. Rows that are decided
and in step aren't shown on a return visit, because nothing about them is
news.

**New since your scope** is measured against the baseline (slice A
already stores it), and each new node says when it was first seen:
"new · first seen in survey 10-14". Where the extension can be traced to a
source commit (the egeria-workspaces PRs that carried the Coco data, the
corrected ask's point), the line carries it: "arrived with
egeria-workspaces@a1b2c3d". With no trace it says nothing about a source,
rather than guessing. **This is the same moment as Understanding's "since
the last run" sentence and an Automate change subscription. The three read
the same baseline and use the same count words**, so they can't disagree
about how many schemas are new.

**History** opens a list of commits, each with its manifest summary ("10-11
· 3 tables added · 1 removed · by dwolfson"), and the rule edits between
them. It's the scope record's events, grouped by commit.

## 5. Decide earlier

**My call: no "worth cataloguing" mark from any survey, and no second
record.** It falls on the judgement side of the line. "Worth cataloguing"
depends on what the catalogue is *for*, and a survey can't know that. The
earlier stages already contribute what they can, in the right form:

- **Surveys at Scouting and Assessment** produce the facts: rows, size,
  activity with its window, data classes. Curate reads them. That's
  "deciding earlier" in the only way a survey may.
- **The data lens** (Find) contributes a measured fit once the glossary
  path exists, and a suggested rule before then (correction above).

**A person may record the choice earlier, into the same record.** The
Schema Inventory tab is already on every stage for a database. It gains the
same choice control as Curate's tree, writing the **same scope events**. So
a steward at Discovery who sees `archive_2019` can say "leave out" there
and then. Curate then shows that choice where it was made: "leave out ·
dwolfson 10-02 · **set on Schema Inventory, Discovery**". One record, two
doors, the same pattern as the journal on Disposition and Curate. A second
"worth cataloguing" flag would be a record that disagrees with the scope
the first time anyone changes one and not the other.

**A verdict still gates and proposes nothing.** A resource marked
`recommended` doesn't propose a scope. It enables the commit.

## The slices

| Piece | Slice |
|---|---|
| Facts from stored rows with source and as-of; sources-disagree; the activity word from cumulative counters and its window; dormancy threshold; "can't tell" never proposes | A2 (points 1–2) |
| Lens-in-names becomes a suggested rule, not a proposal | A2 |
| Filter bar, sort, bulk choice vs rule; rules strip; precedence and source tags; rules-disagree; re-evaluation report; `_`/`%` check at commit | rules slice (points 3–4) |
| Return visit opens on changes; "since <last commit>" strip; history by commit; one baseline shared with Understanding and Automate | rules slice |
| The scope choice on Schema Inventory, writing the same events, with "set on …" | its own small slice (point 5) |

The owner's gate passes on coco_pharma as stated: filter to names like
Sales, write one rule, see the matched rows carry its tag, then revisit
after a new table appears and see it picked up and flagged. One addition:
on the same database today, the old schemas' activity should read "can't
tell · counters reset 10-03", not "no writes since", and propose nothing.

## Left open

1. The dormancy threshold's default (90 days) and whether it's per scope or
   per deployment. I've drawn it per scope, shown in the header.
2. Whether a rule may use data classes ("leave out every table with PII").
   It's expressible, but it turns a governance policy into a filter. I'd
   allow it, and have the rule's sentence say plainly what it does.
3. Tracing an extension to a source commit needs a reader that doesn't
   exist yet. Until it does, the line is absent, not "unknown source".
