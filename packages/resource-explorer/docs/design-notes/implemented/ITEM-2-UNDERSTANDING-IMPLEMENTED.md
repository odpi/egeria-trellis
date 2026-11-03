# Item 2 — Understanding: implemented

**Replies to:** `PLAN-FINISH-REPOS.md` item 2 — *"Understanding — not really
started. Owns `next/stages/understanding.js`. Done when: the stage answers a
question a user actually brings to it; the `:145` comment claiming charts is
either substantiated or removed. Needs: Part 2 (the app.js split — already
done, merged to main)."*

**Branch:** `re/understanding`, worktree `.claude/worktrees/wt-understanding`.
Nothing was written in the main checkout.

---

## The plan doc's premise was already stale

The item's own "not really started" was wrong at the point this branch was
cut. `UNBUILT-STAGES-IMPLEMENTED.md` (reply to
`DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md`) had already added `built: true`
to `understanding` in `app.js`'s `STAGES` array, with the note: *"it renders
real charts via `loadChartsPane()`, unconditionally, already; the flag was
just never added when that landed."* And `next/stages/understanding.js`
(131 lines, moved out of `app.js` by the Part 2 split) already had a
complete `loadChartsPane()`: it probes all eight `REPO_CHARTS` kinds via
`getChart`, counts points rather than trusting `fig.data.length` (so a
single-point trace isn't mistaken for a usable series), keeps a failed
probe, a zero-point series, and a single-observation series as three
distinct sentences, flags stale data, and lets the user pick a chart and see
it actually drawn.

So this is **not new feature work**. It's verification that the done-test
is actually met, plus the test coverage and doc that make "looks done" into
"provably done" — the gap PLAN-FINISH-REPOS.md's own status line hadn't
closed.

## What was verified

**A concrete question, traced end to end.** "Is this repo's star growth (or
commit volume) trending?" is answered by `stars` or `commits`/
`weekly_commits` in `REPO_CHARTS` (`re-api.js:340`), each backed by a real
route in `web/routes/stats.py` (`stars_chart`, `commits_chart`,
`weekly_commits_chart` — all eight `REPO_CHARTS` kinds have a working
backend route; none is a stub). Traced live in a browser (below): the
question is reachable, the chart that answers it draws, and it does not
mislead — the y-axis states outright when it does not start at zero
(`chartAxes` in `app.js`), and the x-axis is plotted to real dates rather
than evenly spaced when the series has one (`timeAxis`/`timeAxisData`), so
an irregular survey cadence reads as gaps rather than a false steady climb.

**The honesty rules the code claims, confirmed in the running app, not just
read:**

- A single-observation series (`amundsen-io/amundsen`'s `top_committers`,
  which had exactly one recorded commit) is offered in the chart index
  labelled **"first measurement"** rather than the point count, and drawing
  it shows *"One observation. This is a value, not a trend — the shape of a
  chart with a single point is drawn by the axes, not by the data."* — not a
  fabricated trend line.
- Stale data is flagged with the same 7-day threshold in both the chart-index
  chip and the drawn chart's caption (*"nothing newer has been recorded"*);
  confirmed live against `stars`, whose latest recorded point (2026-09-02)
  is older than 7 days as of today (2026-09-17).
- The `health` radar chart carries its own live caveat distinguishing its
  five 0–10 axes from the Dashboard's four-part 0–100 `repository_health`
  score, confirmed rendering under the chart in the browser.
- Chart selection is real: clicking an index chip calls `drawChart` with the
  matching probed entry, confirmed by switching between `stars`,
  `top_committers`, and `health` and seeing each one's distinct figure and
  caption replace the last.

**What was NOT found broken.** No `REPO_CHARTS` entry errored or was
unreachable against a real repo (`amundsen-io/amundsen`, freshly surveyed);
the empty/no-data states were legible rather than confusing on inspection
of the source (distinct sentences for "no resource selected," "this series
has nothing in it yet," "this probe failed," and "nothing recorded for any
chart on this resource" — the last explicitly stated as a fact about
history collected, not about the resource). The one open question — whether
`commits` and `weekly_commits` legitimately warrant being two separate
`REPO_CHARTS` entries when both routes call `weekly_commits_plotly` — is a
pre-existing naming/dedup question in `stats.py`, not something this item's
done-test asks about, and is out of scope here.

## What was added

**`tests/test_next_understanding_pane.py`** — there was no test file at all
for this stage before this branch (`test_next_understanding*.py` did not
exist). Follows the established pattern for testing `/next` JS without a
browser (grep/slice function bodies from the concatenated `app.js` +
`stages/*.js` source; see `test_next_curate_pane.py`,
`test_next_rail_states.py`). Eleven tests across five classes pin:

- `TestOneObservationIsNotATrend` — the index chip's "first measurement"
  label, the drawn chart's "value, not a trend" caveat, and that the probe
  counts points (not trace count) so a one-point trace isn't miscounted as
  a usable series.
- `TestStaleDataIsFlagged` — the same `chartIsStale` threshold (`d >= 7`)
  gates both the chip and the drawn chart's caption.
- `TestNoDataAndErrorMessagingAreDistinctSentences` — the four distinct
  no-data/error/unselected states stay four different sentences.
- `TestChartSelectionActuallyDraws` — clicking an index chip's click
  handler resolves to `drawChart(results.find(...))`, and the first
  chart with data is auto-selected on load.
- `TestEveryCatalogedChartHasAWorkingProbe` — `REPO_CHARTS` names all
  eight kinds the probe actually fetches via `getChart`, including both
  `commits` and `weekly_commits` (either answers a commit-volume question).

All eleven pass. The full suite (`uv run pytest tests/ -q -k "not
Postgres"`) was run for regressions; see the session's final report for the
pass/fail count against this branch.

## Live browser verification

Ran the actual app: `uv run resource-explorer web --port 8811` from this
worktree, against the shared Postgres/Egeria instance, with
`TRELLIS_ANONYMOUS_READ=true` (the documented dev-box override, CLAUDE.md)
set for the session only — not written to any `.env`, not a config change
to the branch. Signed in via "Continue without signing in," selected
`amundsen-io/amundsen` in Scouting, opened the Understanding tab, and
confirmed:

1. The pane loads with no sub-tab row (as designed — Understanding "has one
   pane") and probes all eight chart kinds, showing real point counts per
   kind (`Stars over time · 12`, `Commits over time · 13`, `Top committers
   · first measurement`, etc.).
2. `Stars over time` draws a real line chart over real dates
   (2026-08-21 – 2026-09-02), y-axis correctly labelled *"stars — axis does
   not start at zero"* since the visible range (4779–4783) does not include
   zero.
3. `Top committers` (1 observation) draws a single bar and shows the
   "value, not a trend" caveat rather than a misleading trend line.
4. `Health` draws a five-axis radar (0–10) with the `CHART_CAVEATS` text
   about the Dashboard's different `repository_health` composition.
5. Switching between chips redraws the body each time with the matching
   figure and caption.

Nothing was written to the registry or to Egeria in this session — all
reads.

## What I scoped out / could not verify

- **No new REPO_CHARTS kinds, no new UI.** The done-test is "answers a
  question a user actually brings to it," which the existing eight kinds
  already do; adding more chart types is a separate design decision, not
  implied by this item.
- **Did not audit every one of the corpus's ~60 repos' charts**, only
  `amundsen-io/amundsen`. The per-kind probing logic is generic (it treats
  every repo identically), so a systematic per-repo failure would show as a
  test failure or an `unavailable` chip rather than needing repo-by-repo
  browser verification.
- **The `commits`/`weekly_commits` duplicate-backend question** (both routes
  call `weekly_commits_plotly`) is left as an observation, not a fix — it is
  not what item 2's done-test asks about, and changing either chart's data
  source is a product decision for whoever owns that distinction.
