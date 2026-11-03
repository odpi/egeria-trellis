# Matrix response — implemented

**Status:** implemented. Matrix response round, 2026-09-10; kept as a record.

**From:** the implementation session, 2026-09-10
**Against:** `Resource Explorer Matrix Response.dc.html`
**Branch:** `re-experimental-ui` (PR #21, draft) · full suite 4,222 passed, 0 failed

All six are answered. Four are built as specified, one is built with a
deviation worth naming, and one is deliberately not built on your own advice.
Two things you flagged separately are also done, and one of them needs a
correction to what we told you.

---

## 1 · Scale — built

The grid does not grow. The **column digest** sits above it, collapsed by
default: one line per question, a distribution bar across the rows in view,
sorted by unresolved, with a legend. Clicking a line narrows the grid to that
question — the same move as opening a cell, one axis up — and the narrowed
state says so with a way back.

Rows are in **triage order**: least resolved first, banded by disposition, in
the order a triage session works through them (undecided → investigating →
tracking → recommended → using → abandoned → ignored) rather than
alphabetically. Banding only appears when there is more than one band; one
band is not a grouping.

That last part needed a server change. Work-list members carried no
disposition at all, so `WorkLists.get()` now joins it — best-effort, so an
unreachable disposition store yields `undecided` rows rather than no rows.

**Not yet built:** the digest becoming the primary view past some row count.
The inversion is implemented as an interaction, not as a breakpoint; nobody
here has a fifty-row list to calibrate against, and picking the threshold from
imagination seemed worse than leaving it manual.

## 2 · Small screens — built, and the grid was not the problem

The matrix transposes below 720px exactly as described: one resource,
questions down the page, arrows between resources in the same triage order,
and the filter row (All / Unresolved / Stale) replacing the scan. The answer
rides alongside the question where it is short.

**But the transposed view was correct and invisible for the first hour.** At
375px the shell put a squeezed content strip under an open chat drawer, so
none of it could be seen. Below 780px both side panes are now drawers over a
full-width content pane, and three separate defects had to go with that:

- `#app-grid.rail-closed` out-specifies a bare `#app-grid`, and a media query
  adds no specificity — so the narrow rule left a phantom 277px sidebar column
  and a 98px content pane.
- The rail opened itself on a phone, because the stored preference is a
  *wide-screen* preference. A narrow shell now starts closed, and opening it
  there is not written back over that preference.
- The sidebar drawer inherited no background. As a column it sits on chrome;
  as a drawer it floats over the paper content pane, so its chrome-light text
  landed on white. It opened correctly and looked like a blank screen.

Worth passing back as a general note: **a transposed view is only half the
answer.** The shell has to give the view its width first, and every rule that
assumed a two- or three-column grid has to be re-examined, not just the one
that draws the content.

## 3 · The hollow square — built, including the stronger move

`□` replaced `▪`, for your reason: the inversion was a shape problem.

Background resolution is built too. A third pass reads the expensive analyses
two resources at a time after everything else has rendered, yields between
chunks, is invalidated the moment another list is opened, and on failure
leaves the squares as squares — which is what they honestly are.

**Measured, since you asked whether it proves affordable.** On the
four-resource Discovery matrix, whose two architecture analyses are the most
expensive readers in the catalog:

| | |
|---|---|
| grid readable (state projection) | ~1s |
| cheap columns filled | shortly after |
| all squares resolved | ~70s total, 33s per resource |

Affordable, but only because nothing waits on it. It is announced while it
runs — the notice renders *before* the first chunk, since the first chunk is
the slowest thing in the load and announcing it on completion left the longest
silence unexplained.

**Deviation:** it is not "viewport first, then outward". The grid is already
in triage order, so row order puts the least resolved rows at the top of each
band, which is where attention goes. Claiming viewport precision we had not
implemented seemed worse than saying this plainly.

On your closing question — whether `□` should persist long enough to need a
legend entry — it does, and not only briefly. Any resource whose expensive
read fails keeps its squares, and that is the case the legend is for.

## 4 · Staleness — built, graded

The appended `·` is gone. Age is a rule on the cell's baseline, at three
levels: nothing under 7 days, a 40% hairline past 7, full width past 30. No
recolouring. The three channels stay separate, as you set them out.

A row's own age signal is a **count** of stale cells, per your amendment —
not a span, and not a date.

## 5 · Numbered columns — readout built, bands not

The readout line is built as specified: a fixed slot above the grid, filled
from hover, keyboard focus, *or* an open cell, holding until another column is
touched, carrying the question and the analyses behind it, and marking the
live column's number. It never covers cells and never times out.

**The header group bands are not built.** They need a question topic axis this
project deliberately deferred, with recorded triggers for when to add one.
Grouping twenty-seven questions by theme without that axis would mean
inventing the themes in the UI layer, which is the wrong place for a
vocabulary the catalog should own.

## 6 · Stage-spanning — not built, as advised

Taken as written, including the reasoning that wanting two stages at once is
usually a symptom of a stage boundary in the wrong place.

---

## Your four amendments

- **The fast path generalises.** Correct, and it did not hold: the plan
  preview counted "never run" confidently for pairs the projection had never
  established anything about. It now reports those separately as *unknown*,
  and leaves them out of the plan rather than claiming a first run for
  something it has not looked at.
- **No span for a row's age.** Taken; it is a count.
- **Write the rule down.** Done — `docs/next-ui-design-rules.md`, ten rules,
  with "the grid carries state, the popup carries meaning" first, and the two
  deliberately-unbuilt things recorded with their reasons.
- **One component for both plans.** Done. `Run across N` now goes through the
  same preview as `Bring up to date`, and names how many targets are already
  fresh and that they will be re-run anyway — which is the honest difference
  between the two actions.

## What you held the draft on

**Disposition history — we owe you a correction.** It was not missing from
`/next`. It has been there, rendering the dated trail, inside the picker
popover on the resource header. What was missing is what your argument was
actually about: **the matrix had no route to it at all.** On the surface where
triage happens, a reviewer could see what was decided and not that it was
decided twice.

A resource name in the grid is now a button onto its verdicts, oldest first —
the sequence is the point, and a sequence read backwards is a list of values —
with each verdict's reason, age and date, and a count of how many times it
changed. Verified on a repo that went `tracking` → `abandoned — archived`.

**The Search panel** is renamed. It is "Find repos".

**Empty states saying when silence is suspicious** — `?` now names its own
cause: timed out, not in the catalog, unparsable, not permitted, unreachable,
with an explicit fallback that admits when the cause is not recognised. We
kept the fallback deliberately: a confident wrong label would defeat the
purpose, which is to make a real defect recognisable on sight.

---

## Since you last saw it: two more panes

Survey and Dashboard are no longer deferred. Both were deferred against data
that was already one GET away — the same mistake Understanding turned out to
be, which is now three for three and probably a pattern worth naming.

- **Survey** lists the Survey Definitions the adapter says can run against the
  resource, with descriptions and step counts, and launches one.
- **Dashboard** shows headline tiles and each registered dashboard's actual
  measurements. Three analyses — `maturity`, `community_support`,
  `chaoss_metrics` — carry no numbers at all, only written sentences, and a
  first version rendered those as "nothing scalar to show". That threw away
  the most readable content on the pane, including the sentence the
  side-by-side walk singled out: *"Not measurable from what is collected —
  issue and pull-request response times are not gathered, and an open-issue
  count says nothing about whether anyone replies."*

The sub-tab rail now counts what is **not** built rather than claiming
"questions only", which had stopped being true.

---

## Open, and worth your eye next time

1. **The digest's threshold.** It is an interaction, not a breakpoint. What
   should make it the primary view — a row count, a screen height, or a user
   choice that persists?
2. **`□` when it is not transient.** A failed background read leaves real
   squares. Should a cell that failed to resolve look different from one that
   has not been reached yet? They are different facts and currently share a
   glyph.
3. **Two panes, two shapes.** Survey and Dashboard were built to the data
   rather than to a design. They are honest and they are not designed; a pass
   over them would probably find the same kind of thing this round found in
   the matrix.
4. **The stale rule at 30 days.** Nothing in our corpus is old enough to
   exercise the full-width variant yet, so it is implemented and unseen.
