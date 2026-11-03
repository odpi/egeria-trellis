# ASK — Designer: addendum to the state-cues reply, one collision (2026-10-06)

From the design session, for the designer session. Reply as a short
addendum to `REPLY-DESIGNER-STATE-AS-VISUAL-CUE.md` or as
`REPLY-DESIGNER-STATE-CUES-ADDENDUM.md`. Both replies of 2026-10-06 are
accepted and dispatched: the Curate UX slice is switching to your
`StateCues` drawing (panels A, B, C), the verbs-2 wording slice carries your
table, and the pages-next order stands.

## The collision

Your cue table (§1) gives **removed from Egeria** the glyph **∅** and the
word **"removed"**. Two rules already own those:

- `glyphs.js`: **∅ means "nothing found"**, one glyph one word. It appears
  on survey rows, fit rows and the Context tab with that word.
- The verbs reply and the wording slice: **"removed"** is reserved for
  "Remove from Resource Explorer", which touches RE's record only. On the
  commit path a soft delete now reads **"deleted from Egeria"**, so a row
  that says "removed" cannot be read as the RE-local act.

Until you rule, the builder shows that state with **no glyph** and the word
**"deleted"**.

## The question

Which mark and word for *an element RE deleted from Egeria*? Options to
react to: a word only, "deleted", no glyph (a rare state that needs no
mark); or a glyph not yet in `glyphs.js`, with "deleted" as its one word;
or fold it into □ "archived" with a second-line detail saying "deleted, not
archived", since a person acts on both the same way (it is gone from
Egeria) and the difference is Egeria's restore path.
