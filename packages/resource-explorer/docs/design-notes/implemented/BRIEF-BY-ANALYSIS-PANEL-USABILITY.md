# Brief — the By-analysis panel is hard to read (2026-09-27)

Owner's verdict after the Section A gate on `laz_local_adventureworks`,
Discovery stage, `/next` By-analysis sub-tab: "hard to distinguish one card
from another, no table of contents at the top, large blocks of details —
this needs more usability work." Second usability slice after Slice 22.
Same gate style: tasks a person performs, not sentences that are true.

Where it lives: `web/static/next/app.js` `loadByAnalysisPane()` (~line
5297), which renders one `<section>` per analysis with title, the full
catalog description, a "never run" warning, an optional headline when the
analysis has a numeric `overall`, a findings list and a COUNTS table.

## What is wrong, in the order a reader meets it

1. **No overview.** The page opens on the first card's description. Nothing
   says how many analyses there are, which ran, which didn't, or lets the
   reader jump to one.
2. **Cards do not read as cards.** Title, description, and COUNTS run
   together in one column with the same rules between rows as between
   cards. On the screenshot the eye cannot find where Relationship Graph
   ends and Table Grain begins.
3. **The description is the loudest thing on the card.** Six to twelve lines
   of catalog prose per analysis (what it would do, design section refs,
   caveats) before any result. The Questions tab already solved this: the
   result sentence leads and the prose is behind "what it does".
4. **The result sentence is missing.** Every analysis now has a headline
   (Slices 17d, 21a, 21b, headline-gaps) but this panel shows it only when
   `results.overall` is numeric. Relationship Graph's "91 FK edges across
   68 tables · 2 components…" never appears here; the reader reconstructs
   it from the COUNTS rows.
5. **Cross-analysis warnings repeat.** "confidence — 3 analyses report this
   name with different values" is rendered inside each of the three cards
   (three times on the screenshot), and the same for `table count`.
6. **COUNTS mixes levels.** Whole-resource scalars, per-schema rollups and
   diagnostic counters sit in one flat list with no grouping.

## The redesign

### Top: a table of contents that is also the status board

One row per analysis in this stage, in the same order as the cards below:

```
✓ Database Classification   transactional · 46% (4 of 4 families)   run 2m ago
✓ Relationship Graph        91 edges · 2 components · 1 isolated      run 2m ago
✓ Table Grain               68 of 68 tables grain-determined          run 2m ago
○ Coverage Signals          never run                                  run →
```

- Glyph = the fact state (measured ✓, nothing found ○ with note, not
  established ◐, never run ○, no reader ◌), the same glyphs the Questions
  tab uses. Same `_state_for` truth; never a second implementation.
- Second column = the analysis's own headline, truncated to one line,
  exactly the sentence the Questions tab shows for its lead question.
- Clicking a row scrolls to the card and opens it. The row stays visible
  (sticky) while scrolling so the reader can move between cards.
- A one-line summary above the table: "7 analyses · 5 ran · 2 never run ·
  3 shared names disagree (see below)".

### Cards

- **Visible boundary.** Card = bordered box with a header band. Header:
  state glyph, name, run time, and the row's actions (`evidence`,
  `re-run`, `the numbers behind this`, `notify me`), identical to the
  Questions tab's row footer so the two tabs feel like one product.
- **Headline first.** The first line of the body is the analysis's
  headline, full length, in the answer style. Reuse the same registry the
  Questions tab reads (`_headline_for` / `DATABASE_ANALYSIS_HEADLINE_MAP`
  and the repo equivalents). No headline → the honest sentence the fact
  layer gives ("ran; no summary reader yet"), never the description.
- **Description collapsed.** The catalog description moves behind a
  "what it does ▸" disclosure, closed by default, as on the Survey &
  analyses pane.
- **Cards collapsed by default except the first**, or all open when the
  stage has three or fewer. Open state remembered per tab in
  `localStorage` (per-viewer convenience only).
- **COUNTS grouped.** Three groups with small caps labels when more than
  one is present: *Result* (the scalars a person quotes: edge count,
  component count, determined count…), *Coverage* (table count,
  unmeasured, keys captured, measured count, estimated count), *Diagnostics*
  (everything else). Numbers right-aligned, tabular figures, unit shown
  (`99.1 MB`, `761,184 rows`), never raw bytes next to pretty bytes.

### Shared-name disagreements once, not per card

The "3 analyses report this name with different values" rows move to a
single "Shared names" block after the table of contents, one row per
name: `confidence · db_classification 46 · subject_signals 60 ·
preliminary_fit 0 — different measures share a name`. Each card's COUNTS
keeps its own value with a small `≠` mark linking to that block. When the
three values are legitimately different measures (they are, here), the
block's sentence says so and proposes the rename as an RFA, rather than
warning forever.

### Order

Cards in the order the stage's questions ask for them (the questions CSV's
order, first question that names the analysis), so By-analysis and
Questions agree on sequence. Never-run analyses last within the stage.

### Progressive render — never a blank pane for a minute

Added 2026-09-28, from the Section D agent's live-render check during the
`renders-text-cross-check` work: `loadByAnalysisPane`'s "Reading the
dashboards…" state can sit with no progress for up to ~109s on
`laz_local_adventureworks` before anything appears.

The contents board (glyph + headline + run time per analysis) is cheap —
it reads only the per-analysis state already resolved for the Questions
tab, no dashboard fetch required — and must render immediately, before any
card body. Card bodies fill in as their dashboard reads complete, each
row's glyph flipping from a loading state to its real one as it lands.
While any read is outstanding, show a visible "still reading N of M" line
near the top, not a fixed spinner with no count. The gate below (task 1,
"under ten seconds, without scrolling") must be met from the contents
board alone, independent of how long the slowest card's dashboard read
takes.

## Out of scope here

Charts (the per-schema bar chart is its own Backlog entry), the legacy `/`
UI, and any change to what the analyses compute.

## Gate, on `laz_local_adventureworks` and `localhost_docker_coco_pharma`

Task-based, timed by the owner, on a second port:

1. From the top of the By-analysis tab, without scrolling, say which
   analyses ran and which did not, and read Relationship Graph's edge and
   component counts. Target: under ten seconds.
2. Jump to Table Grain from the table of contents, read its headline, open
   "what it does", close it, and return to the top. Nothing should require
   scrolling past another card's prose.
3. Say how many names are shared across analyses and what the three
   confidence values are, from one place.
4. On coco_pharma, the not-established cards must read as such in the
   table of contents (◐) with their reason in the headline, and the tab
   must not look emptier than the Questions tab for the same stage.

Screenshots of tasks 1 and 3 go into the IMPLEMENTED doc.

## Tests

- A DOM-harness test (the pattern Slice 22 used) that renders the pane for
  a fixture with five analyses in mixed states and asserts: the contents
  table has five rows with the right glyphs and headlines; each shared name
  appears once in the Shared names block; each card's first body line is
  the headline, not the description; descriptions are inside closed
  `<details>`.
- A backend test that the headline shown per analysis equals the one the
  Questions tab computes for that analysis's lead question (same function,
  same inputs).
