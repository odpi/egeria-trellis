# Reviewed — the rail sentence

**Replying to:** `rls-pr.md` (`#60`, on top of `#59`)
**Date:** 2026-09-13 · **Read against:** `origin/main` at `c0b8c15`
**Drawn at:** `design_handoff_list_answers/` — `RailSentence.dc.html` is the sheet
this reviews against

The mechanism is better than the drawing. `manifest.lists[section][field] =
{total, shown: {FULL, SUMMARY}}` recorded by the compiler and **read, never
recounted**, by the shell means the rail and the pane agree *by construction*
rather than by two code paths being careful. I drew agreement; you made it
structural. Summing nested lists per section and counting their sub-keys —
*68 dependencies · in 3 ecosystems* — is better than what I drew, and the reason
you give is the right one: a person asked about the dependencies, not about
Java's.

One sentence per packed section, rather than the one sentence I drew, is also
your improvement. Three defects, all in the copy.

---

## 1 · The rung is leaking into the copy

`app.js:2162`:

```js
`· <span class="text-chrome-muted"><span class="tnum">${l.shown}</span> shown to the model at ${esc(l.rung.toLowerCase())}</span>`
```

which renders **“26 shown to the model at full”**. `FULL` and `SUMMARY` are the
packer's rungs — internal vocabulary, and the one word in that sentence a reader
cannot interpret. *At full* invites the reading "shown fully", which is the
opposite of what it says.

Cut it: **“26 shown to the model”**. The number already carries the whole claim,
and 26 against 68 is the elision. If the rung matters to someone — and it does,
to you — it belongs in the provenance line where the compile is already
described, not in the sentence a person reads to decide whether to open the list.

## 2 · A section with no member reader goes silent

The link is a ternary ending `: ''`, so a section the pane cannot open renders
the sentence and then nothing. Your PR body describes this as intended — *“3
findings · security scan · all shown to the model”* — and it is the one place the
implementation departs from the sheet on purpose, so it is worth arguing.

The house rule is yours: *a parallel UI may defer any affordance; it may not
silently omit one.* A reader who has learned that lists open cannot tell
**“this list has no reader yet”** from **“I misread — that wasn't a list”**, and
the absence is in the place where the answer to that question should be. Case
four on the sheet:

> No list to open — `architecture_recovery` has no member reader yet.

in the slot the link would occupy, styled as metadata rather than as a control.
One line, and the absence becomes a fact about that analysis instead of a
property of this answer. It is also the thing that tells you which readers to
write next.

## 3 · `›` is a text glyph in an icon's job

*“the full list is in the pane ›”* — the chevron is a character, in a project
that dropped `☁ 📊 🆕` for Lucide and records why at `app.js:1424`. It will not
inherit stroke weight, it will not scale with the text-size control the way the
SVG chevrons beside it do, and it sits on a different baseline from the
`chevron-right` two artboards away. Same 13px inline SVG as everywhere else.

---

## On the wording, since you changed it

I drew two sentences and you wrote one middot-composed line:

> **68** dependencies · in **3** ecosystems · **26** shown to the model at full ·
> *the full list is in the pane ›*

With the rung removed I prefer yours, and the composition rule (` · `, never a
bare space) is the app's own. One adjustment: *the full list is in the pane*
describes where the list is; the control should say what pressing it does —
**open the full list** — since it is the only clickable part of the line. The
middot before it is then doing the work the sentence break did in the drawing.

Nothing else in the sheet needs redrawing. `SaveReport`, `Report` and `Records`
are untouched by this PR and still stand as the next two rounds.
