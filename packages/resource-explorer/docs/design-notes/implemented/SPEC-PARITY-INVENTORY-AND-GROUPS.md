# The parity inventory, and collapsible groups

**Supersedes:** `RULING-CLASSIC-AND-NEXT.md` §4
**Read against:** `main` at `1b370cbe`
**Date:** 2026-09-17

---

## 1 · My §4 asked the wrong question

I asked what would have to be counted for *"if `/next` gets more traction"* to be
answerable. The project owner's answer: **the two cannot be compared at all
yet, because `/next` is not feature complete — and it is missing features present
in classic that have never been discussed.**

Which makes traction the second question, not the first. Measuring adoption of a
surface that cannot yet do the job would measure the wrong thing and could only
mislead: low traction would read as a verdict on `/next`'s design when it is a
verdict on its completeness.

**The prior question is: what does classic do that `/next` does not?** Nobody has
written it down. So:

- *"feature complete"* has no definition,
- *"more traction"* has no meaning until it does,
- and I have been specifying **new** capability for rounds while the gap to
  **existing** capability is of unknown size.

That last one is mine, and it is the same defect as everything else this week: I
worked from the design record rather than from the thing. The design notes
describe what we decided to add. Nothing describes what is already there and
absent.

**This is my own rule, applied to the roadmap instead of to a row.** Absence is a
state, never a blank — and an undiscussed missing feature is exactly a blank. A
count of them is what turns "not complete" from an impression into a number, the
same way the gaps collection does for the analytics.

## 2 · The inventory

One pass over the classic panel, enumerating what it can do. Each entry gets one
of three states — and the third is the one that matters:

| state | meaning |
|---|---|
| **present** | `/next` does this |
| **deferred** | missing, and deliberately: a decision exists and can be named |
| **undiscussed** | missing, and nobody has ruled on it |

The value is entirely in separating **deferred** from **undiscussed**. A deferred
feature is a decision; an undiscussed one is a surprise waiting for whoever
finally tries to switch. Today every missing feature looks alike, which is why
collapsible groups surfaced in conversation rather than from a list.

Two rules for the pass, both from this project's own doctrine:

- **Name the behaviour, not the widget.** *"Groups are collapsible"* is a widget.
  *"A large group can be folded away, and a filter still finds what is inside
  it"* is the behaviour, and it is the second half that gets lost when someone
  rebuilds from the first.
- **An entry with no evidence is not an entry.** Cite the classic code, the way a
  ready-to-start row now cites what it was checked against.

This is a survey, not a design round, and it is small: one read of `index.html`.
It should happen before the next feature round, because it may well reorder it.

## 3 · The first entry: collapsible resource groups

**State: partly present.** `/next` builds groups — `next/app.js:1579-1586` maps
resources by `group_slug` with an `Ungrouped` bucket, and the header at `:1678`
renders the group name and a count. What is missing is collapse: the header is a
static label, with no toggle and no persisted state.

Classic has three behaviours here, and only the first is the obvious one:

1. **Collapse, persisted.** `_COLLAPSED_GROUPS_KEY = 're_collapsed_sidebar_groups'`
   (`index.html:5169`) with a rotating caret at `:5768`. The state survives a
   reload, so a curator's shape of the sidebar is theirs and stays.
2. **Group-level selection that respects collapse.** `_toggleGroupSelected`
   (`:5473`), with the comment noting a collapsed group's count still includes
   its members — so selecting a group selects what it counted, whether or not it
   is showing.
3. **A filter force-expands every group** (`:5748-5749`), *"otherwise a match
   sitting inside a collapsed group"* is invisible.

**The third is load-bearing and must not be rebuilt without it.** Adding collapse
without the force-expand rule introduces a defect classic already solved: a
filter that reports matches the reader cannot see. That is the blank-versus-state
rule in the one place a user would never think to check.

And collapse is not really a new affordance here. The `/next` header already
shows a count, and **counts open what they counted** — so the group header is
already making a promise it does not keep. Folding is the other half of the
affordance that is half-built.

**What to build:** the caret and persisted collapse on the `/next` group header,
the force-expand-on-filter rule with it, and group-level selection if Select mode
is meant to reach a whole group. Small, and the only new decision is where the
persisted key lives so the two surfaces do not fight over one localStorage entry
with different shapes.

## 4 · What this does to the board

The inventory goes **above** the three open items, because it may change what
they are worth. Nothing on the current board is wrong, but a board assembled
from rulings while an unmeasured set of missing features sits beside it is a
board that answers the wrong question well.
