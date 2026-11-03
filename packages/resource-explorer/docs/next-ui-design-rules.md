# `/next` — design rules

**Status:** live. Design rules the Next UI is built to.

Rules the experimental UI is built to, each earned by something that went
wrong or by a design round that settled an argument. Short on purpose: a rule
nobody can recall is not a rule.

Sources: the design handoffs in the `claude_design` project
(`Resource Explorer Restyle`, `Next UI Regressions`, `FIX-ROUND`,
`Resource Explorer Matrix Response`), and use of the matrix on real work
lists. Round-by-round narrative lives in
[`next-ui-matrix-round.md`](archive/snapshots/next-ui-matrix-round.md).

---

## 1. The grid carries state, the popup carries meaning

**The round's central finding**, and what licenses nearly every simplification
in the matrix. A cell can be a single mark because the meaning has somewhere
else to live: click it and you get that question's latest results for that
resource — each contributing analysis, its state, its answer, its own
measurement date, and a re-run.

The corollary is the useful half: **stop trying to make the cell say more than
a glyph can.** Every proposal to encode one more fact into a 26px cell should
first be asked whether it belongs in the thing the cell opens.

It is also what makes the cost work. Reading full facts for one cell is
instant; reading them for 27 × 12 is a minute. You pay for the cell someone is
actually looking at.

## 2. A fast path must not lie

Where a cheap read knows less than the expensive one, the interface **gains a
state** rather than borrowing the nearest confident one.

The matrix's state projection can tell "there is stored output" from "there is
none"; it cannot tell `measured` from `partial`. So those cells render `□ has
results · not read yet` — never `✓`, which would claim a state nobody
established.

Applies at every level, not just cells: the refresh plan reports pairs whose
state is unknown separately, rather than folding them into a confident "never
run".

## 3. There is no per-resource "as of" date

Each analysis ran when it ran — on one row, one measurement from eight hours
ago and another from twenty days. Any single date per resource is wrong
whichever end it takes: the oldest libels the fresh cells, the newest flatters
the stale ones.

**Dates belong to cells.** A row-level age signal is a **count** of stale
cells — a fact about the row — not a date and not a span. A span is two dates
to interpret in a narrow column, where the first reads as "the" date.

## 4. An action must name what it would do, before it does it

Both bulk actions show a plan and queue nothing until confirmed: what is
stale, what is already current and will be skipped, what has never run (left
out by default — a first run is not a refresh), and what is unknown.

`Run across N` and `Bring up to date` share one component deliberately. Both
answer "what am I about to spend", and if they answer it in two shapes people
learn to read one and skim the other.

## 5. Three channels, and they must not collide

- **glyph** — which state
- **hue** — whether a person is needed
- **rule under the glyph** — how old

Age grades rather than flags: nothing under 7 days, a hairline past 7, the
full cell width past 30. Never recolour for age — "this answer is old" and
"this answer is bad" are different claims, and the warn colour is already
spent on the second.

**7 and 30 are placeholders, and are marked as such in the code.** They are
calendar habits, not facts about surveys: a language classification is fresh
at ninety days and a release check is stale at fourteen. The honest measure is
*older than this analysis's own re-run cadence*, a value Automate will own.
Until it exists the rounds are a stand-in, and the comment is what stops them
quietly becoming policy.

Rendered at all three levels, with faked dates, in
[`assets/age-rule.html`](assets/age-rule.html) — open it in a browser. It
copies the real CSS rather than drawing a picture of it, so it stays a true
rendering.

**Render a threshold you cannot observe.** The 30-day rule was implemented and
unseen, and faking a date found two defects in it: the button was 14px inside
a 26px cell, so "full cell width" was 54% of the cell and adjacent rules never
met; and moving the rule to the `td` made it 1700px wide, because a
`position: relative` table cell did not establish the containing block.
Implemented-and-unseen is where this kind of bug lives.

## 6. Scale is a selection problem, not a rendering one

The matrix earns its cost by holding a cohort you can keep in your head, and
nobody holds fifty. So the grid does not grow. Above it, the **column digest**
inverts the view: one line per question showing how its states distribute,
sorted by unresolved, and clicking a line narrows the grid to that question.

Rows are in **triage order** — least resolved first, banded by disposition —
because name order is a filing convention and means nothing here.

**The digest becomes primary on unresolved AREA, not row count.** Fifty rows
nearly all answered scans perfectly well — the eye hunts the few marks that
break the pattern, and there are few; twenty rows of unknown is a wall. Area
also decays correctly: a fresh list opens on the digest, and the grid earns
its place back as the session fills cells in.

Decided **once, at list open**. A view that flips underneath someone
mid-session is worse than either view.

## 7. Narrow is a different view, not a smaller one

Below ~720px the matrix **transposes**: one resource, questions down the page,
with a filter row (All / Unresolved / Stale) replacing the scan. Below 780px
both side panes become drawers over a full-width content pane.

A drawer needs its own ground, a narrow shell must not restore a wide shell's
open-rail preference, and the way into a drawer must live somewhere that does
not scroll off a 375px screen. All three were found by looking at it.

## 8. Absence and failure are states, never blanks

- "Could not read" is not "no results" — and it **names its cause** (timed
  out, not in the catalog, unparsable, not permitted, unreachable), with an
  explicit fallback that admits when the cause is not recognised. A confident
  wrong label would defeat the point.
- "Never run" is a claim, and needs evidence; absent evidence the answer is
  "unknown".
- A question with no surveyor is settled, not loading.
- **An absence that holds for EVERY resource is a bug report about the reader,
  not a finding about the corpus.** `survey_history` rendered "nothing
  recorded yet" on ten out of ten repositories while the registry held ten
  points each — the endpoint returns a raw series rather than a figure, and
  the reader looked for the wrong shape. An empty state is a claim like any
  other, and it is the one claim that looks like diligence when it is wrong.
  Check across resources before believing it.
- **`□` and `?` are different facts.** `□` is a promise that a read is still
  owed; `?` is "we looked and could not read it". A failed read therefore
  converts to `?`, and only a *cancelled* one stays a square — cancellation
  learned nothing, so the promise is still good. There is no such thing as a
  permanent square, only a permanent failure that says so.
- **"Not measurable, and here is why" is a result**, not an absence — often
  the most decision-relevant one on a pane, because it says what a line of
  enquiry would cost to open. It ranks as a peer of a measurement, never as a
  footnote under one.

## 9. Provenance is not decoration

Two real data defects surfaced only because the matrix tried to show
measurement dates — a word written into a timestamp column that had made an
analysis unreadable on five resources, and two clocks disagreeing about when
something was measured. Both had been in the data for weeks and were invisible
from every other surface.

This extends to **human** judgements: the sequence of dispositions is
provenance too, and is reachable from the matrix rather than only from a
picker on another pane.

## 10. A stored preference carries the context that produced it

A preference is not a fact about the user; it is a fact about the user **in a
situation**. Replayed outside that situation it is noise wearing the costume
of a choice.

Three defects in one round had this single root, all of them rules written for
one context and replayed in another:

- the chat rail's open state was a *wide-screen* preference, replayed on a
  phone as though it meant something there;
- CSS written for a column, replayed on an overlay (the specificity bug);
- a pane's own background, assumed from its parent, replayed when it floated
  free of it (the invisible drawer).

So: **store the context alongside the preference, or scope the key to it.**
The digest toggle is keyed per work list, not globally, for exactly this
reason — a choice made on a fifty-repo scouting sweep says nothing about a
four-repo assessment. This generalises past layout, and will come back for
column widths and sort order.

## 11. Cost a deferral against the adapter, not against the design

Three panes were deferred as unbuilt — Understanding, Survey, Dashboard — and
all three turned out to be one GET away from working. Three for three is a
pattern, not bad luck.

The cause: each deferral was costed against **the design's** model of what the
pane would need, and a design model is always the more expensive one, because
it imagines the finished thing. The adapter had already been built to a
narrower, sufficient shape.

**The check is cheap: before deferring a pane, ask what a single GET already
returns.** Three times the answer was "the whole pane".

The sub-tab rail now counts what is *not* built rather than asserting
"questions only" — a deferral that has to display its own count is harder to
forget.

## 12. "Missing" and "unrouted" are different defects

A capability can be present and unreachable, and the two want opposite fixes:
missing means build it, unrouted means route it. Calling one the other sends
the work to the wrong place.

Disposition history was reported missing from `/next`. It was not — it
rendered inside a picker popover on the resource header. What was true is that
the **matrix**, where triage actually happens, had no route to it. The fix was
a route, not a feature.

The property that matters on a working surface is **reachable from where the
decision is made**, which is not the same as *present in the product*.

## 13. When two analyses report the same name with different values, say so

Not an alert — a mark and a phrase, the way a stale cell gets a rule.

Found by the Dashboard's own provenance: two cards both titled SYMBOL COUNT,
adjacent, unlabelled as related, neither aware the other existed. A reader
either does not notice, or notices and cannot tell which is wrong.

**Both were right.** `api_structure.symbol_count` is a live `COUNT(*)` of
`project_code_symbols`; `code_symbol_extraction.symbol_count` is what the
extractor recorded when it ran. The table carries
`UNIQUE(project_slug, file_path, qualified_name)`, so symbols sharing a
qualified name within a file collapse on insert — measured, stored rows equal
distinct `(file_path, qualified_name)` exactly, and the gap is 2.7% on
`egeria_git` (40,994 vs 42,116) and 5.8% on `kafka` (82,852 vs 87,907).

So they answer different questions under one name: *how many distinct symbols
are stored* and *how many the extractor found*. The interface could not tell
anyone that, and it should be the thing that asks.

The detection is worth more than the one case: it found three on the first
repo, including `total` — `dependency_analysis` 72 against
`data_file_profiling` 45 — which is two unqualified fields that were never the
same measurement, a naming defect rather than a disagreement. Hence the
careful wording: *"report this name with different values; they may not be
measuring the same thing."*

**The durable fix is a declared definition per analysis.** Worth having
regardless of whether any given disagreement is legitimate — and if one turns
out not to be, the mark has found the next defect.

This is the third time a surface that showed provenance found a defect nobody
was looking for: the `'probe'` strings, the two clocks, and now two counts of
one name. Three is a pattern, which is why this is a rule and a mechanism
rather than a fixed bug.

## 14. Name things for what they are

The repo-discovery panel is "Find repos", not "Search". Inheriting a wrong
label teaches the wrong noun on first contact, and the current UI's "Search"
is not a search of the selected resource.

---

## Deliberately not built

- **Stage-spanning matrices.** Columns do not survive the crossing, so it
  would be stacked per-stage matrices sharing one frozen resource column — and
  wanting two stages at once is usually a sign a stage boundary is wrong,
  which is worth diagnosing before it is worth rendering.
- **Header group bands.** They need a question topic axis this project
  deliberately deferred; see the deferral's own triggers before adding one.
