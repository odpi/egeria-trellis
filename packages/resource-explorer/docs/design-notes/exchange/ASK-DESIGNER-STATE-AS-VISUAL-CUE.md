# ASK — Designer: state as a visual cue plus a short word, the precise sentence on demand (2026-10-06)

From the design session, for the designer session, at the owner's request.
Reply as `REPLY-DESIGNER-STATE-AS-VISUAL-CUE.md` in this folder; drawings of
the three surfaces named below under `wireframes/` are the most useful form.
Background: the gate walk of the Curate catalog commit on coco_pharma
(`evidence/MORNING-NOTE-GATE-2026-10-06.md`), your rulings of 2026-09-29
(accent colours are for controls, never states; tabs never disappear) and the
glyph rule (one glyph, one word; `glyphs.js`).

## What happened at the gate

The owner pressed "leave out" on a schema row. The press registered, and the
only sign was a sentence at the far right of the row, scrolled off screen, so
he pressed again. Nothing was sent to Egeria; a choice is only RE's record.
Earlier on the same page he could not find the commit control, which is a
link-styled phrase at the end of a long line, and a toggle on a schema row
did nothing visible.

His words, which he says apply to much of the UI:

> user feedback is mostly in the form of written descriptions rather than
> visual cues or a combination. The precision of the words sometimes gets in
> the way of understanding — you have to figure out exactly what is being
> said.

## What we think the principle is, for you to correct

Every status word on these pages derives from a proof row, which is why the
sentences are exact. That rule stays. The problem is where and how the
sentence is shown: a person scanning a table reads position, shape and
weight before words, and a sentence that is right but far from the thing it
describes is a sentence the eye misses.

Proposed rule: a state change shows on the element itself, at once, as a
visual cue (mark, strike-through, weight, opacity, badge, position; never an
accent colour for a state) plus a short word; the precise sentence stays one
gesture away (tooltip, expand, a detail row). A control that has been
pressed looks pending until the record proves the change, and ignores a
second press.

## The three surfaces to start with

1. **The Curate scope tree** (schemas and tables): the CHOICE cell at the
   left must carry the state; a left-out row reads as left out across its
   width; a row with a choice that differs from its schema shows that in the
   same cell. The builder is putting a first version in now (state mark in
   the CHOICE cell, muted row with a badge, a short "saved" with the long
   sentence as a tooltip, a three-mark legend above the table); your drawing
   can supersede it.
2. **The commit steps list** (publish, ownership, attach, survey, refresh,
   read-back, zones): today each step is a glyph (✓ ◔ ∅ ◌) followed by a
   sentence of up to thirty words. What should a person see at a glance:
   which step is running, which failed, and how far the whole commit is?
3. **The manifest** ("What this commit does"): a block of sentences that
   ends in the commit control. Where does the control go, and what does the
   block become (a list of counts, a short table, a sentence per mechanism)?

## The questions for you

1. Is the proposed rule right, and what is the vocabulary of cues: which
   mark, weight and position mean pending, saved in RE, sent to Egeria,
   proven by read-back, failed, left out, archived? One set for every page.
2. How much of the sentence stays visible by default: the short word only,
   the first clause, or the whole sentence in a quieter weight?
3. Which pages after these three: the Enrichment Context tab rows, the
   work-list rows, the investigation scope, the run queue?
4. Does the glyph rule survive as the visible word's companion, or do the
   cues replace glyphs on rows and glyphs stay for inline text only?

## Constraints

Status words still derive from proof rows; a cue never claims more than the
row proves (pending is a look, not a state). No accent colour for state. No
new glyph without a word. The precise sentence must remain reachable for
anyone who needs it, including the gate walker.

## What we do with the reply

A cross-page slice per surface, gated by the owner on 8813 by use rather
than by reading: he presses, and says whether he saw it.
