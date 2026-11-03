# A premise of mine was too broad — and what it changes

**Replying to:** `PORTS-ROUND-ONE-IMPLEMENTED.md`
**Date:** 2026-09-14 · **Against:** `origin/main` at `2e2ac17` (after `#85`)

Round one is in and I have one correction to make against myself, one ruling it
forces, and the agenda for round two.

---

## The correction is mine

I wrote that *on most repositories there are almost no ports at all*, and built
the "column, not a screen" ruling partly on it. You then measured
**71 ports on egeria-workspaces, 68 of them with an owner.**

My number came from the curation-lenses spike, which reports ports as existing
"essentially not at all" — but that is the **logical** perspective, where
components are packages and nothing declares a port. Yours comes from
**deployment artifacts** in a compose-heavy repository, which is a different
population entirely. Both are true. My generalisation across them was not, and
it was the weaker half of the argument.

**The ruling stands on its stronger half**, which your own build confirms: a
port is a reading rather than a proposal, there is nothing for a person to
agree with, and the review surface is the component tree. Ports being plentiful
somewhere makes them *more* worth a column and no more worth a screen.

## But 71 forces a plural case the drawing did not have

`8000 in, routes` works for one or two. It does not work for a component that
owns fifteen, and on a compose-heavy repository some will. So the column has two
shapes:

- **one or two** — spelled out, as you built it.
- **three or more** — `15 ports ›`, opening the list in the rail, the way every
  other count in this app opens what it counted. Counts open what they counted;
  this is one more count.

And the foot of the tree carries the two ends of the same sentence:

> No declared topology — `egeria-python` declares no ports and no wires. Ports
> are read from Dockerfiles, compose files and API documents; this repository
> has none of those with an exposed interface.

> **71 ports** across **9** components · **3** not attributable to any shown
> component, counted apart.

The second is new and needed: with 71 in play, a reader has to be able to see
the total without adding up a column.

## Three things in your build I would not have specified

- **Ports keyed by the deployment service name, not the component path**, with
  one owner each by longest prefix so the column and the diagram agree by
  construction. The drawing assumed path-keying and was wrong about the data;
  agreeing *by construction* rather than by two careful code paths is the same
  move that made the rail and pane agree.
- **The three unowned ports counted apart rather than attached to a guess** —
  which is the diagram's caption rule, applied one surface out, and is exactly
  right.
- **The price line reading *not yet measured — the first branch is what fixes
  it*.** You applied the basis rule to a place I had not thought to apply it,
  and refused a declared figure in a sentence about spending. That is the cost
  work's whole point arriving somewhere it was not asked to go.

**641 → 69 branches in 2.9 seconds** settles the scale question with a
measurement instead of an argument. Good.

---

## Round two, and its real question

In priority order, and the first is much larger than the other three:

1. **`detect` and `coupling`.** Two perspectives propose different component
   sets; a verdict is keyed by scope and lands on both. So a component accepted
   under one proposal is accepted under a proposal its curator never saw. That
   is not a display problem and it cannot be solved by a toggle — it is a
   question about what a verdict is *about*. It needs the round, and it needs to
   be settled before anyone accepts at scale, because every verdict recorded
   under the current ambiguity is a verdict whose subject is unclear.
2. **The plural ports case** above, and the tree's foot sentence.
3. **Sort by confidence** on the tree — a sort, never a filter, and the ⚠ count
   already rides on the branch, so this is small.
4. **The diagram beside the tree.** Unchanged and already verdict-aware, so this
   is layout and the two ceilings, not mechanism.

I will start with 1 unless you would rather have 2–4 first, since they are
quick and it is not.
