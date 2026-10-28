# `contentStatus: DRAFT` — the same axis, not a new one

**Answering:** the live question raised with round 2 (multi-resource design §3, PR #194)
**Date:** 2026-09-22

---

## The answer

**Do not add a DRAFT badge system.** The profile card already carries the axis
this belongs on: a Data Class match shows **measured** or **declared**, and
`§1`'s fixed decision says why — *a survey measures, a curator declares, and
nothing writes a declaration unattended.*

`contentStatus: DRAFT` is the **middle position on that same axis**, which
currently has a hole in it:

| position | meaning | who put it there |
|---|---|---|
| **measured** | the survey found this, at some confidence | the survey |
| **proposed** ← `DRAFT` | the survey is asserting it as a candidate for declaration | the survey, more strongly |
| **declared** | a person accepted it | a curator |

So the badge is the badge that exists, with a third value. One vocabulary, one
filter, one legend.

## Why not a separate badge

Two badge systems on one object means a reader has to learn which of the two is
answering the question they have. Worse, they will overlap: a `DRAFT` annotation
is necessarily not-yet-declared, so a separate DRAFT badge beside a
measured/declared badge would say the same thing twice, and two marks that
always agree teach a reader to ignore one of them — after which the day they
disagree goes unnoticed.

The palette makes this concrete. `tailwind-next.config.js`'s own rule is that
*colour is never the sole channel*, and it records that `state-warn` and gold
already separate by hue alone and are tolerable only because their glyphs and
words differ. A fourth badge family would need a fourth distinguishable
treatment on both grounds, and there is no room. Extending the existing badge
costs none.

## The filter follows the axis

*Show me the proposals* is a filter on that one field — the same control that
would let a curator see only what is already declared, or only what was measured
and never proposed. So it is one filter with three positions, not a DRAFT
toggle bolted on beside it.

And the count that filter produces opens what it counted, as usual: *18
proposed, 4 declared, 61 measured only ›*.

## One caution, from the drawing

The card's badge is per **match** (Data Class, reference set), not per column.
A column can carry a declared Email match and a proposed reference-set match at
the same time. So the axis lives on the badge, and the **column** has no single
status — any summary at column or table level has to say how it aggregates, or
it will read as "this column is a draft", which is not a thing.

That is the same rule as the multi-analysis caveat: a mark that summarises
several findings names what it summarises, or it does not render.
