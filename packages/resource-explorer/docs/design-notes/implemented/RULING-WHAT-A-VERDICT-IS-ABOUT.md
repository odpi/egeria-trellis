# What a component verdict is about

**Ruling** · item 1 of the work plan
**Date:** 2026-09-16 · **Read against:** `origin/main` at `05cb63c0` (after `#104`)

---

## 0 · The question was unanswerable for a reason: three things are called "perspective"

Before the ruling, the naming, because I could not decide this until the research
separated them:

| in the code | values | what it is |
|---|---|---|
| `run_label` | `detect`, `coupling` | **which survey step wrote the row.** Two different detectors in two different `StepInfo`s, needing different resources — detect takes `zipball_root`, coupling takes `git_clone_root` for real history. |
| `Component.perspective` | `physical`, `deployment`, `logical`, `dev` | **which architectural reading** the component is. `clustering.py` is explicit that this is §4.1's axis and *not* the other one. |
| the chrome's Perspective row | `Admin`, `Architecture`, `Consumer`, … (12) | **whose question you are asking** — the role filter across the top of every stage. |

Blueprint verdicts key on the second. The diagram's preference keys on the
first. The chrome's filter is the third and is unrelated to both. **One word,
three axes, and the question "does a verdict apply across perspectives?" has a
different answer for each.** That is the defect under the defect, and it is this
project's own favourite kind: a name that means two things, one walk away from
another that reads it.

**So: the word goes.** In the UI, `run_label` is **found by** and
`Component.perspective` is **reading**. *Perspective* keeps its one meaning —
the role chips in the chrome — because that is the one users already hold.

---

## 1 · The ruling

**A component verdict is about the component — the thing at that path — not
about the proposal that surfaced it. It stays keyed by `scope_locator`, with no
schema change.**

Three reasons, none of them mine:

- **The codebase already says so, at the moment it matters.** The materializer's
  own comment: *"Confidence and perspective are evidence ABOUT the proposal, not
  properties of the real element a curator just decided is real — accepting is
  the point at which that evidence stops mattering to what gets written."* That
  is the ruling, written by whoever wrote `materializer.py`, and it is right.
- **`curation-lenses-design.md` §4.3 already ruled it on the neighbouring
  axis** — *"One verdict per component, reused by every lens"* — because *"this
  is a real thing" is true or false regardless of why you asked*. Extending that
  from Purpose to extractor is consistency, not new doctrine.
- **Identity is already path-only end to end.** `qualified_name_for` is
  `SolutionComponent::{type}::{slug}::{scope_locator}`; the materialised row is
  keyed `(entity_type, entity_slug, scope_locator)`; `run_label` reaches Egeria
  nowhere at all. Two extractors proposing one path, accepted once, correctly
  produce one element. Making a verdict proposal-scoped would mean two verdicts
  that can disagree about whether one thing exists.

And the coverage fraction survives it: **"4 of 87" is a statement about paths**,
and the denominator is a union of distinct `scope_locator`s. It is coherent as
written. It would *stop* being coherent under the other ruling, because the
headline count beside it in the same sentence is a path count.

---

## 2 · What follows, and this is the design work

The ruling is one line. Four things have to change because of it.

### 2a · The row shows both proposals; the verdict is recorded once

Today the card's displayed type, confidence and reading come from
`max(comp_rows, key=surveyed_at)` — **whichever step wrote last.** So a curator
can rule on a card whose attributes came from the other extractor than the
drawing they clicked from, and nothing says so. That is the real defect the
ambiguity was hiding.

> `pyegeria/commands/` · **cli**
> found by **detect** — a console entry point · deployment reading · 75%
> found by **coupling** — an import boundary · logical reading · 45%
> **two extractors agree this is a component** · accept · reject · retype

One verdict, recorded against the path. The trail notes which proposals stood
when it was made, so a verdict read next year says what was on screen.

### 2b · Agreement is the strongest signal in the data, and the UI discards it

`latest wins` throws away the fact that two independent extractors — one reading
manifests and Dockerfiles, one reading twenty-four months of co-change —
landed on the same path. Nothing else in the recovery is evidence of that
quality.

So **surface it**, and notice that this is the owner's point 4 arriving from the
other direction: *"it isn't clear what we are showing in terms of components,
potential components, why we think they are which kind"*. The answer to "why do
we think this is a component" is often **because two unrelated methods agreed**,
and the fix for the ambiguity and the fix for the owner's question are the same
fix.

Ordering follows: where the tree sorts by confidence, agreement outranks a single
high confidence. Two detectors at 75% and 45% is a better bet than one at 90%.

### 2c · Withdrawal is already proposal-scoped, and needs a state rather than a schema

This asymmetry is shipped: `_withdraw_vacated` lets a step withdraw only scopes
*it* wrote (`if detail.get("run_label") != run_label: continue`), while verdicts
are path-keyed. So **coupling can withdraw its proposal on a path that still
carries an accepted verdict earned from detect's** — and today that reads as
nothing at all.

It is not "still accepted" and not "unaccepted". It is *accepted, and no longer
proposed by one of the extractors that proposed it.* Which is the perishability
treatment the enrichment judgements already have, third time out:

> **accepted** · 2d ago · dwolfson · ⚠ review — no longer proposed by coupling

**Flag, do not invalidate.** The verdict stands, because the thing at that path
either exists or does not and a re-run of a detector is not new information about
that. But the person who accepted it deserves to know the ground moved.

### 2d · The coverage sentence puts two identity regimes in one breath

*"4 of 87 components reviewed (4 accepted); 0 of 12 blueprints reviewed"* —
components are keyed by path, blueprints by `perspective::cluster_name`. Both
are right, and the sentence hides that they are different kinds of count. Under
the new vocabulary it should say which:

> 4 of 87 component paths reviewed · 0 of 12 clusters in the logical reading
> reviewed

Because a cluster only exists within a reading, and a component exists whatever
you were reading when you found it. That is the whole ruling in one sentence,
said where it is load-bearing.

---

## 3 · And the diagram must say which reading it is drawing

`_DIAGRAM_PERSPECTIVE_PREFERENCE = ("coupling", "detect")` is a display
preference whose only justification is a code comment — *"reads as the more
digested view"* — and I am not overruling it, because it is a reasonable default
and nothing better is known. But it is invisible, and under this ruling a verdict
earned on one drawing styles nodes on the other. So the diagram states its own
scope, next to the caption it already has:

> found by **coupling** · detect also on file ›

What is already recorded as deliberate — and stays — is that the two are **never
merged in the diagram**. `_read_arch_recovery_ir`'s docstring is right that
merging would silently undo the fix that separated them.

---

## 4 · What this unblocks, and what it does not

**Unblocked:** the component branch tree (item 2) can be built, with *found by*
and the agreement line as the type evidence the owner asked for; and the layer-2
catalogue pane, since accepting is now unambiguously an act about a thing rather
than about a drawing.

**Not settled, and deliberately out of scope:** `curation-lenses-design.md` §4.4
proposes an *overlap object* with three resolutions — same component, nested,
distinct per reading — for the case where two extractors propose components over
the same scope at **different granularities** (one composes the other). This
ruling handles the same-path case, which is the common one. Nested proposals need
that object, and it is a round of its own. The §4.3 claim of a
`component_verdict_history` that *"records the lens and the cut node each verdict
came through"* does not exist in the shipped table; the trail note in 2a is the
cheap version of it.

**No schema change anywhere.** One migration-free ruling, four copy-and-render
changes, and one sort-order change.
