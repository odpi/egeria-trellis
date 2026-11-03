# PLAN — Bordered cards and big-number tiles across /next (2026-10-20)

From the designer session, for the builder, at the project owner's request.
Read against main at 82f50726, after the three looks landed (`themes.css`,
`look.js`, PRs of 2026-10-20). Background: `RULING-THEMES-PORTAL-DARK-LIGHT.md`
(cards and tiles were deferred there "surface by surface"), the state-cue
rulings (two lanes, the left-edge rule, lowered rows), and the existing
pieces this builds on: `stages/understanding-glance.js` (the At a glance
figures, each with its state), `stages/repo-header.js` (the scouting
tiles), and app.js's `headlineHtml` (one big number, and the note on why
the old summary tiles were removed). Drawing:
`wireframes/CardsAndTiles.dc.html`, canvas page 24.

**Decision (project owner, 2026-10-20):** users reviewed the Dark look and
voted for it. Go ahead with the structural half of the Portal's look:
bordered cards and big-number tiles.

## The idea in one paragraph

Two components, defined once and used everywhere. A **card** is one thing a
person reads or acts on as a unit: a definition, a run, a proposal, a rule,
a fact, a finding, or a whole list. A **tile** is one count that is the
answer, drawn large, opening what it counted. Everything else stays as it
is: rows stay rows, sentences stay sentences. The change is mostly
containers and one new kind of figure, so it lands surface by surface
without touching what any screen says.

## 1. The card

**Anatomy** (drawing, panel A):

- **Header:** the title (heading size, ink), its short meta beside it
  (muted), and the card's actions at the top right. Actions always live
  here, never mid-card.
- **Body:** whatever the card holds today: a run line, a fact, a table, a
  list of rows.
- **Footer:** the provenance line (who, when, from what), muted, one
  line, always last. It's what makes the card's claim checkable.

**Look:** a fill one step off the page ground, a visible border, the look's
card radius (8px in Dark and Light, 2px in Paper), and 14px padding. Two
new tokens, per look, so the border can be stronger than the Portal's
(the owner likes clear contrasts, and the Portal's border is only 1.4:1
against its ground):

| Token | Dark | Light | Paper |
|---|---|---|---|
| `card` (fill) | `#16213e` | `#ffffff` | `#fbfafa` |
| `card-line` (border) | `#2c4a7c` (1.9:1 on the ground) | `#b4bdd0` (1.9:1) | `#c9c6c4` (1.5:1) |

**States, using the existing cue rules, not new ones:**

| State | On the card |
|---|---|
| ordinary | border only |
| **needs you** (failed, conflict, survey-now-disagrees, a fixable refusal) | a 3px **left edge** in `state-warn`. This is the state-cue ruling's "a left-edge rule means this needs you", now on the card. **It means nothing else.** The mock's blue left edge on the "active" definition card is dropped, because it would give the edge two meanings. |
| **lowered** (left out, retired, gone from the source) | the whole card's text in `ink-muted`, never lighter. The border stays solid. |
| **off** (a control that can't be used here) | the *control* is dashed, as today. The card itself is unchanged. |
| **chosen or selected** | `accent-tint` fill, as a selected row has today |

**Rules for the builder:**

1. **One level only. Never a card inside a card.** A list of definitions
   is a stack of cards. A list of 30 analyses is **one card** holding 30
   rows. A row doesn't become a card because it has a button.
2. **A table is wrapped in one card**, with its header row inside. The
   table's own row hairlines stay.
3. **Section labels sit outside the card**, above it, as today ("SURVEY
   DEFINITIONS · 3"). The label names the group, and the card holds a member.
4. **Spacing:** 12px between cards in a stack, 24px between sections.

## 2. The tile

**Anatomy** (drawing, panel B): a caps label ("TABLES"), the figure (30px,
bold, tabular figures), and a one-line sub ("3 readable by the surveyor").
The whole tile is the control that opens what it counted.

**Rules: the house rules, applied to a figure drawn large.** Some of these
are why the old summary tiles were removed (`headlineHtml`'s note), so
they're stated here as tests:

1. **A tile opens what it counted.** Every tile has a target: the rows,
   the list, the filter. A figure that opens nothing isn't a tile; it
   goes in a sentence.
2. **Same value, same precision, same source as anywhere else on the
   page.** The old tiles said "Health 82/100" while the card below said
   82.2. A tile reads the same server field as the place it opens, and a
   test asserts the two agree.
3. **Absence is a state, never 0 and never blank.** The figure slot shows
   the state's glyph and word in place of a number: "? not read",
   "○ not measured", "◐ partial · within this credential". This uses the
   glyphs `understanding-glance.js` already uses. A real zero is "0", and
   "∅ 0" where glyphs.js says measured-nothing.
4. **Source and as-of are one hover away**, and on the sub line when they
   change the reading ("≈ estimate · analyzed 10-13").
5. **Color only from a state word.** The figure is in ink, except when it
   *is* a state count: "Need you 2" takes `state-warn`. Nothing is green
   for being large.
6. **At most six in a row**, wrapping to two columns at narrow widths.
   Four is better.
7. **Only where the count is the answer.** A tile row answers "how much,
   and is anything wrong?" at the top of a surface. Counts inside prose,
   tables and run lines stay where they are.
8. **No trend arrows or sparklines** unless a recorded series backs them
   (the Over time tab's charts). A tile doesn't invent a direction.

**Two sizes:** **figure tiles** (as above) and **fact tiles**, small, for
values that aren't counts: the repository header's Website, Language and
Last pushed. Fact tiles keep today's scouting-tile size and take the card
border and radius. They don't get the big figure.

## 3. Where, surface by surface

In order of how much a person reads the surface, with what each gains.
"Tiles" names the figures. Each one's target is in brackets.

| # | Surface | Tiles | Cards |
|---|---|---|---|
| 1 | **Understanding · At a glance** | db: schemas, tables, rows (≈), size, last survey [the Contents tab, filtered]. repo: files, lines, languages, contributors. All from `understanding-glance.js`'s figures, unchanged. | the "since the last run" sentence; each chart |
| 2 | **Analysis · Survey & analyses** | analyses run "13 of 30" [the analyses list], need you [those rows], last survey [its report] | one card per survey definition; the analyses list as one card |
| 3 | **Curate · Contents** (database) | schemas published, chosen, undecided, needs you [the tree, filtered] | the rules strip (one card per rule); the manifest table; each publish-module slot (What we say, Parts, Depth, Detail) |
| 4 | **Curate · Describe and publish** | none, since this tab is about words, not counts | the facts as one card; the publish state; the Egeria reports list |
| 5 | **Questions** | answered "18 of 30", needs you, no reader here [the rows, filtered] | the checklist as one card; the rail's answer detail |
| 6 | **Investigation scope grid and work lists** | in scope, by verdict (recommended, using, …) [the grid, filtered] | the grid as one card; each work list's header |
| 7 | **Enrichment · Context** | none | each group of facts as one card, each row keeping its signature |
| 8 | **Automate · Schedules** | schedules, failing, next run [the rows] | the table as one card; the add-a-schedule form |
| 9 | **Repository header** | fact tiles (the scouting tiles migrate) | none |
| 10 | **Admin pages** | only where a page already leads with counts (feedback per look, outbox) | each table as one card |
| 11 | **Chat** | none | **each answer is a card** (owner, 2026-10-20): header with the answer's subject and its actions at the top right (save to the journal, open the evidence); body with the answer as written; footer with what it cites and when (the compile id, the records read). The question as asked stays above the card, unboxed, as the person's own words. |

## 4. Slices

| Slice | Contains | Gate (by use, the owner) |
|---|---|---|
| **C1 · the components** | `ui-cards.js`: `cardHtml({title, meta, actions, body, footer, state})`, `tileHtml({label, figure, sub, opens, state})`, `tileRowHtml(tiles)`, plus `factTileHtml`. The two tokens per look in `themes.css`, with a contrast-test entry for each. A render-harness test per state (ordinary, needs you, lowered, chosen; tile measured, partial, not read, zero, state count). **Applied to surfaces 1 and 2.** | At a glance and Survey & analyses on coco_pharma in Dark: every tile opens what it counted, and "◐ partial" and "? not read" show as states, not blanks. Then once in Light and once in Paper. |
| **C2 · Curate and Questions** | surfaces 3, 4 and 5 | publishing a schema from Contents with the tiles moving as rows are decided |
| **C3 · the rest of the daily path** | surfaces 6, 7, 8 and 11 (chat answers), and the repository header's migration to fact tiles | one investigation walked end to end, with two chat answers read as cards |
| **C4 · sweep** | Admin (10), plus any leftover hairline-separated sections the sweep finds (`border-t border-rule` used as a section divider: 31 in app.js, 20 in curate.js, today) | a list of what was left alone, and why |

Each slice moves existing markup into the components **without changing
any words**, the same rule as the split-by-job slices. A tile is new only
where §3 names one, and every tile's figure already exists on that
surface today, in a sentence or a header.

## 5. Two small things worth doing in C1

- **Raise the provenance size from 13px to 14px.** The owner found detail
  text the hardest to read. The sans in the new looks helps, and one more
  pixel on the smallest informational text helps again. It's one token
  (`provenance`), and it moves every footer line at once.
- **Make the tile figure size a token** (`figure`: 30px), so the
  text-size control scales it with everything else.

## Decisions (project owner, 2026-10-20)

1. **Paper gets cards and tiles too**, from the same markup with Paper's
   tokens. There's no second markup path.
2. **Chat answers become cards** (surface 11, slice C3). The card is the
   answer: the question stays above it in the person's own words, and the
   footer carries what the answer cites, so the chat-first application's
   "about this resource" answer reads the same as At a glance.
