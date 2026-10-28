# REPLY — Designer: state as a visual cue plus a short word, the sentence on demand (2026-10-06)

To ASK-DESIGNER-STATE-AS-VISUAL-CUE.md, read against main at f1f679eb
(`stages/curate-scope.js`: `choiceCellHtml`, `setterButtons`,
`commitStepsHtml`, `commitPanelHtml`; `glyphs.js`;
`evidence/MORNING-NOTE-GATE-2026-10-06.md`). Drawing:
`wireframes/StateCues.dc.html`, canvas page 19. It covers the three
surfaces and the cue vocabulary.

## The rule: right, with two additions

The owner is right, and it applies to my own rulings too. I've been
writing precise sentences and placing them where they were true, not where
the eye was. The proposed rule stands:

> A state change shows **on the element itself, at once**, as a visual cue
> plus a short word. The precise sentence is **one gesture away**. A
> pressed control **looks pending** until the record proves the change,
> and ignores a second press.

**Addition 1: the cue sits where the decision was made.** At the gate,
"leave out" was pressed in the left column, and the proof appeared at the
far right, off screen. A row's result appears in the cell that holds the
control that was pressed. The right-hand column is for Egeria's state,
which is a different fact, from a different source, at a different time.

**Addition 2: every control visibly changes something when pressed, or
it isn't a control.** The schema-row toggle that "did nothing visible" is
the same failure as the missing proof, so the same rule covers it: a
press changes the element, or the control goes.

## 1. One vocabulary of cues, for every page

Two lanes, because there are two records. **What you decided** is RE's
record, and **what Egeria has** is the proof rows. A row can be in a
different place in each lane, and the lanes never borrow each other's cues.

| State | Lane | Cue on the element | Word |
|---|---|---|---|
| **pending** (pressed, not yet in the record) | either | the pressed control dims and disables. Its cell shows "…" after the word. It's a look, not a state, so no glyph. | "saving…" / "sending…" |
| **undecided** | your choice | no segment filled, muted text | "undecided" |
| **saved in RE** | your choice | the chosen segment **filled in ink**, the name in normal weight | the choice word: "include" / "leave out", then "saved" |
| **left out** | your choice | the **whole row lowered** (muted ink across its width), the segment filled | "leave out" |
| **differs from its schema** | your choice | a bordered tag in the choice cell | "differs" |
| **from a rule / from the schema** | your choice | a bordered tag with the rule's name / muted "(schema)" | the source |
| **sent, waiting for Egeria** | Egeria | ◔ | "sent" |
| **proven by read-back** | Egeria | ✓ | "cataloged" / "published" |
| **failed** | Egeria | ✕, and the row gets a rule on its left edge | "failed" |
| **archived in Egeria** | Egeria | □ | "archived" |
| **deleted in Egeria** | Egeria | none: the word alone, and the row lowers | "deleted" (see the addendum) |
| **will change in Egeria at the next commit** | Egeria | ⚠ | "will archive" / "will delete" |

**What carries meaning, so each cue means one thing:**

- **Fill** means "this is the value you chose". It's the selector
  answering, so it's a control showing its own value. That's why it uses
  ink fill and not the accent: the accent marks what you can press, and
  the fill marks what's chosen.
- **Lowered contrast across a row** means "not included". Nothing else
  lowers a row.
- **Strike-through** means *superseded text* only: an overridden proposal,
  a retired rule. It never means "deleted in Egeria". That gets its own
  word, so a struck row can't read as a deletion.
- **A left-edge rule** means "this row needs you": failed, conflict,
  survey-now-disagrees. One mark for attention, used rarely.
- **Glyphs stay** in the Egeria lane and in running text, always with
  their word (§4).

No new glyph. ◔ ✓ ✕ □ ⚠ are `glyphs.js`'s own, with their words.

## 2. How much of the sentence shows

**The word, always. The first clause, only when it changes the next
decision. The whole sentence, one gesture away.**

- **Always visible:** the cue and the word ("leave out · saved").
- **Visible second line, only for rows that need you:** a proposal's
  reason, a survey disagreement, a refusal ("can't be re-included until
  Egeria restores archived elements"). These carry the information needed
  to act, so hiding it would cost a click per decision.
- **One gesture away** (hover on a pointer device; a "details" disclosure
  on touch, the pattern `detailsHtml` already uses): who, when, the proof
  row's sentence, Egeria's long tail.
- **Relative and short when visible:** "you · just now", not
  "dwolfson 2026-10-06 09:14:22". The absolute time sits in the detail.

## 3. The three surfaces

**The scope tree** (panel A). The choice cell is the selector from the
verbs reply (§7 there): `Include | Leave out` under the header "Include in
catalog?", with its cue under it in the same cell. A press dims the
selector and shows "saving…" in place. The record's answer fills the
segment and prints "saved · you · just now". A left-out row lowers across
its width at that moment, so the change is visible across the table. A
three-mark legend sits above the table, as the builder started: filled ·
lowered · tagged. The right-hand "In Egeria" cell keeps the glyph lane
only.

**The commit steps** (panel B). It's a numbered list, not a run of
sentences, read at a glance from the top:

> **Catalog · commit a3f2 · step 6 of 9 · running: Egeria's survey · 0 failed**

Each step is one line: number · glyph · **two-word label** · state word.
The step running now is in normal weight and every other step is muted. A
failed step gets the left-edge rule and shows its first sentence **at
once**, unasked, because that's the thing a person came to read. Every
other step's sentence sits behind "details". The record's step order stays
as it is; the stepper only changes how it's shown.

**The manifest** (panel C). It becomes a **short table, one row per
mechanism**, with the **Catalog button at the top right of the table**,
not at the end of a sentence:

| | what | how many | when |
|---|---|---|---|
| RE publishes | the server and database | 2 elements | now |
| Egeria catalogs | your included schemas | 2 · coco_sus, coco_ods | next refresh (or now, if ticked) |
| Egeria surveys | the same schemas | 2 | after attach |
| Left out | nothing in Egeria to change | 1 · eu_sales | — |
| Undecided | Egeria stays as it is | 26 | — |

The button reads **"Catalog 2 schemas"**. Anything that blocks it is
listed **directly under the button**, with its ⚠ word, so the reason the
button is off sits beside the button. The refresh checkbox goes under the
button. The longer sentences (the survey-report note, "Egeria catalogs
whole schemas…") move into "details" on the row they qualify.

## 4. Glyphs and cues together

**The glyph rule survives as the Egeria lane's cue, and the row-level cues
(fill, lowering, tag, edge rule) carry the "your choice" lane.** They
don't replace each other: a glyph says *what Egeria has*, and the row's
shape says *what you decided*. In running text, glyph plus word stays
exactly as it is.

## 5. Which pages next

In order of how often a person presses and then waits:

1. **Enrichment's Context rows.** Save, then "saved · you · just now" in
   the row, with the proof under it. This is the most frequent press.
2. **The investigation's scope grid.** Add and remove, and verdicts:
   the same fill and lowering vocabulary.
3. **Survey & analyses and the run queue.** A run's ◔ → ✓/✕ in its row,
   the same stepper as the commit for multi-step runs.
4. **Work-list rows.** Membership and verdicts, last because they're the
   least frequent.

The gate stays as the ask states it, by use. The owner presses, and says
whether he saw it, on each surface in turn.

## Addendum (2026-10-06): "removed from Egeria" collided twice

The architect caught it: my table gave Egeria's soft delete the ∅ glyph and
the word "removed". Both are already taken. ∅ means *measured, nothing
found* (`glyphs.js`'s measured-nothing family), and "removed" is reserved
for taking a resource out of RE ("Remove from Resource Explorer", the
controls reply). Her interim, "deleted" with no glyph, is the ruling:

- **Word: "deleted"**, with "in Egeria" in the row's detail line:
  "deleted in Egeria · ‹when›". It's Egeria's own term for a soft delete,
  so a steward who checks in Egeria finds the same word there.
- **No glyph.** None of the existing families fits: not ∅ (nothing
  found), not ✕ (failed), not □ (archived is stored and restorable in a
  way the steward should keep distinct). Adding one isn't worth a new
  member for a state that ends a row's story. The row **lowers**, as a
  left-out row does, since a deleted element isn't in the catalog.
- **The preview's future form is "will delete"**, beside "will archive".
  The commit's preview and row strings that print "removed (soft-deleted)"
  and "removed · ‹time›" become "will delete" and "deleted · ‹time›".
- **`glyphs.js`'s `removed` key** (∅, measured-nothing) is renamed
  `deleted`, and its glyph is dropped. The key should name the state, and
  the measured-nothing family keeps a single meaning.
