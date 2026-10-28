# Legibility and charts — implemented

**Status:** implemented. Legibility and charts round, 2026-09-10; kept as a record.

**From:** the implementation session, 2026-09-10
**Against:** the "Legibility and charts" section of
`Resource Explorer Survey and Dashboard.dc.html`
**Branch:** `re-experimental-ui`, pushed through `fc14510`

Both parts built to the table and to the four chart points. Verification is on
egeria-workspaces rather than kafka, per the calibration note.

---

## 1 · Contrast and size

Your second cause is the one worth having written down, because no spec sheet
would have produced it: **the dark chrome surrounds the paper pane, so after
the eye adapts to a dark surround, mid-greys on white read lighter than they
measure.** That is now in the token file as the reason `ink-muted` is set
firmer than a normal light UI would need, so the next person to "fix" it back
toward a measured-fine grey has to argue with it first.

Built to the table:

| role | was | now |
|---|---|---|
| body & findings | 15px `#201f1d` | unchanged — it was already right |
| values & figures | 13px, muted | **15px `#201f1d`**, tabular |
| dates, ids, analysis names | 11.5px `#605d5d` | **13px `#4a463e`** |
| caps labels | 11px | **12px** |
| rules, borders, tints | — | `#d8d5cd` only |

Measured in place afterwards: body 15px `rgb(32,31,29)`, provenance 13px
`rgb(74,70,62)`, caps 12px.

**The mono shrink was the best catch in the section.** Ours was not a fraction
— it was hard-coded `text-[11px]` in six places — but the effect was identical
and worse for being deliberate-looking: qualified names, the strings people
read character by character, were the smallest text on the pane. They are on
the 13px informational token now, and there are no hard-coded sizes under it
left in `/next`.

**The text-size control** is in the masthead: 100 / 112 / 125%, persisted. The
whole scale moved to `rem` against `html { font-size }`, so one control scales
every size — chrome included, deliberately, so the shell never stays at a size
the content has outgrown. At the 125% step: body 18.75px, provenance 16.25px.

Stored globally, not per work list. Unlike the digest toggle, a text size is a
fact about the reader rather than about a list — which is rule 10 read in the
other direction, and worth noting since the same rule produced the opposite
answer two rounds ago.

## 2 · Charts

Done selector-first, and you were right that it half-fixes the other two by
context.

**Selected state** in the stage tabs' own language — accent ink plus an accent
underline, one visual vocabulary rather than two.

**Title names the measurement and its range** — "Languages — share of code,
0–100%", "Survey history — total files". The range lives in a per-chart
descriptor, so the radar's "0–10, five axes" is declared beside the others
rather than remembered.

**Both axes labelled, and the truncated scale admits it.** Computed per chart
rather than captioned: where the data does not reach zero, the y-axis itself
reads

    total files — axis does not start at zero

**A real time axis**, for date-shaped series only. Each measurement marked,
the last labelled with its value.

On egeria-workspaces it earns itself on the first chart: the flat stretch from
11 to 21 August reads as a gap rather than as a slow climb. Your line for the
x-axis is the one in the code — *"plotted to scale, so gaps are real"* — and
it is the staleness rule drawn instead of marked, exactly as you put it.

Categorical charts are left alone. A language mix has no time to be true to,
so it takes the title and the y label and keeps its own x-axis.

---

## One decision I made rather than asked

The y-axis admission appears **only when the data genuinely does not reach
zero**, computed per chart. The alternative reading of "say so on the axis" is
that every axis states its zero either way. I went with the conditional
because a chart that does start at zero saying so is noise on every chart to
catch the few, but it is a one-line change if you meant the other.

## Still open, unchanged by this round

- The measurement → run link, waiting on a run id in the results rows.
- The digest threshold, still collecting.
- The radar's proper fix — plot the four published sub-scores on their own
  scale — still needing an endpoint. It now declares its range in its title,
  which makes the collision visible rather than resolved.
