# Nav grouping: three classes, declared in the data

**Ruling** · answers a peer critique of the nine-item intent row
**Read against:** `main` after `#130`
**Date:** 2026-09-18

---

## 1 · The decision

**Decision (project owner, 2026-09-18):** Understanding can be used at any time;
ultimately it will let users configure and display local dashboards with content
relevant to them.

So Understanding leaves the run. That answers the one question the nav critique
could not settle, and it settles it more firmly than the critique proposed:
Understanding is not a milder stage or a later one — **it is a surface the user
configures**, which is a different kind of thing from an operation on a corpus.

`STAGES`' comment — *"The eight intents, in their canonical order, plus
Investigation as the frame"* — is now wrong in its count. There are **six
ordered intents**, not eight.

## 2 · Three classes, and they go in `STAGES`

The nav already carries a frame/stage distinction: `frame: true` renders
Investigation in `text-accent-on-dark`, and the comment beside Work lists says
they sit at the row's end *"because, like Investigation, they are a FRAME around
the stages rather than a stage: Investigation is why a body of work exists, a
work list is which."* The model was right; it had two classes and needs three.

| class | members | what it is |
|---|---|---|
| **frame** | Investigation, Work lists | why this body of work exists, and which cohort you are working |
| **the run** | Scouting, Discovery, Assessment, Analysis, Enrichment, Curate | six ordered intents; sequence is real |
| **cross-cutting** | Understanding, Automate | order does not apply — one is what you choose to look at, the other makes the run repeat |

**The class is declared in `STAGES` and the nav derives grouping from it.** Not
regrouped in the renderer while the array still calls them ordered intents.

That is not fussiness. Two sources of truth for one fact is the exact structure
that produced `unbuilt` — read in three places, set in none, six stages
rendering as built — and `#130`, where deferred styling was set independently of
the flag gating the behaviour. **Third instance this week.** The rule holds:
declare the fact once, derive appearance from it.

## 3 · The separator does the teaching

**Chevrons inside the run, middots outside it.** `1 Scouting › 2 Discovery ›
3 Assessment › 4 Analysis › 5 Enrichment › 6 Curate` — then `Understanding ·
Automate · ▦ Work lists`. Numbering only where sequence is real.

This is the critique's proposal and it is right, for a reason worth recording:
**the frame/stage distinction is currently carried by hue alone.**
`tailwind-next.config.js` states the governing rule — *"colour is never the sole
channel here"* — and tolerates the `state-warn`/gold collision only because the
two also differ in glyph and in legend wording. The nav breaks that rule today.
The separator change is not decoration; it supplies the second channel the
palette doctrine requires.

Investigation additionally becomes a **scope control** in the header row,
showing the active investigation and whether it is ad hoc or bound to an Egeria
Project. That binding deserves permanent visibility: it is the difference
between private scratch work and something the project can see.

## 4 · The state dot belongs to the run only — and now there is a second reason

A small per-tab dot (*measured / partial / never run*) was proposed for the
funnel tabs, on the grounds that "have I done this yet" is only meaningful
there. Endorsed, with two constraints.

**It must not collapse "never run" into "ran and found nothing."** This app is
careful about exactly that distinction — the charts pane keeps three outcomes
apart on purpose: *a figure with series → render it; a 200 with no series →
"nothing recorded yet", NOT an error and NOT an empty chart, which would read as
a measured zero; a failed call → say the call failed, name the reason.* A
three-state dot that merges the first two undoes that.

**And the ruling above gives a second reason the dot stops at the run.**
Understanding's state is **per user**, not per app: once it is a configured
dashboard, *"you have not set one up yet"* is a different state from *"this is
not implemented"* and from *"it ran and found nothing."* A dot whose meaning is
per-corpus on six tabs and per-person on a seventh means two things at once.
Keep it on the six.

On the dark chrome ground the dot needs the `-on-dark` token variants, which
exist. One caution from the Curate pass: `⚠` is already firing on roughly two
rows in five there, so the dot should be the only new colour in that row rather
than an addition on top of another emphasis.

## 5 · What this does not decide

- **Whether Understanding and Automate want one label or two.** They share only
  the property the separator teaches — that order does not apply. One is a view
  the user owns; the other is a schedule over the run. If a label is needed
  beyond the visual grouping, it is the owner's to pick.
- **The dashboard itself.** Configurable local dashboards are a round of their
  own, and `app.js:145`'s note that Understanding *"renders charts now — the
  catalog rows it lacks were never what fed it"* is consistent with that
  direction: it was never catalogue-driven, so nothing needs undoing first.
- **`Automate`'s position within cross-cutting.** The critique notes it "makes
  the funnel repeat"; whether that earns it a place adjacent to the run's end or
  not is a visual judgement, and either reads correctly once the separator
  distinguishes the groups.
