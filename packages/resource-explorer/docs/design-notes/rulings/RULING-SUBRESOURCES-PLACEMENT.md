# Ruling: Sub-Resources needs neither a fifth tab nor a bespoke mount

**Replying to:** `ASK-ANALYSIS-SUBRESOURCES-PLACEMENT.md`
**Read against:** `main` at `22bf65bd`, and classic's implementation directly
**Date:** 2026-09-22 · No drawing, per the ask.

---

## The answer, in the requested sentence

**Neither.** Sub-Resources is two features wearing one tab's name, and both
halves already have a home in `/next`'s existing four: the **selection and
catalogue UI belongs on Survey & analyses**, beside the Run button it is
missing; **its results belong in By analysis**, like every other analysis's
results. Nothing is added to the strip, so the uniform-strip rule is never
tested.

The rest of this is why, plus two corrections to the ask's premises that change
the size of the job.

---

## 1 · Two corrections first, because both affect scoping

### 1.1 · Classic's Analysis strip is already five tabs, not four

`_analysisSubnavHtml` (`index.html:2062`) builds:

> **📊 Survey · 🗂 Sub-Resources · 📈 Dashboard · ❓ Questions · 🧭 Disposition**

So "a fifth sub-tab breaks the uniform rule" compares `/next`'s four against a
classic that never had four *here*. The uniform-four is a `/next` convention
with no classic ancestor at this stage — which is fine, but it means the choice
isn't "keep classic's shape or break our rule." Neither shape is inherited.

### 1.2 · Sub-Resources is repo-only, and that is declared, not incidental

`analysis_catalog.yaml:550` — `resource_types: ["repo"]`. Classic's own comment
says the same: *"Sub-Resources is repo-specific (SubResourceSurveyor only exists
for repos)."* And the surveyor reads `project_file_inventory`, which is a repo
table.

So the ask's *"now that `/next` is being brought to parity across all three
resource types … or a database's tables or a filesystem's files"* describes a
feature **that does not exist**. Sub-resources for databases or filesystems
would be a new surveyor, new storage and a new catalog entry — not a port.
Worth catching before someone scopes this at three times its real size.

---

## 2 · Why it decomposes

The ask assumes Sub-Resources is one thing needing one home. It is two, and they
answer different questions:

| half | what it is | where that already lives |
|---|---|---|
| the candidate list, with results | the output of an analysis — `sub_resource_survey`, `intent: analysis`, emitting `ClassificationAnnotation` / `ResourceMeasureAnnotation` like any other | **By analysis**, whose entire job is results grouped by analysis |
| select / catalogue / dispatch scoped | **run configuration** — choosing what to act on before dispatching | **Survey & analyses**, where the Run button already is |

And classic's own comment names the second half as the defect that forced the
last move:

> *"The generic card in this stage's own Survey grid was a dead end without it —
> no selection affordance, just a bare Run button."*

That is not an argument for a tab. It is an argument for **putting the
selection affordance on the card that has the Run button** — which is the thing
classic couldn't do, because its selection UI had been built as a separate view
in a different stage and was expensive to move again.

If `sub_resource_survey`'s results don't appear under By analysis today, that is
a gap in By analysis for one analysis, not a placement question — and fixing it
there fixes it for whatever analysis is next.

### Against each offered option, briefly

- **A fifth tab** would put results and run-configuration in one place, which is
  the shape every other analysis in the product deliberately avoids. It also
  spends the strip's fifth slot on the first feature that asked, rather than on
  whichever feature turns out to need it.
- **A bespoke mount** invents a one-off pattern for a feature that isn't
  special. `sub_resource_survey` is an ordinary catalog analysis with an
  ordinary intent; the only thing unusual about it is that its inputs need
  picking, and plenty of analyses will eventually want that.

The precedent is the same one that already moved this feature once: classic
relocated it from Assessment to Analysis **because its catalog intent said
where it belonged** (`analysis_catalog.yaml:551-558`). The same principle one
level down says which tabs. This ruling is that principle applied again, not a
new rule.

---

## 3 · Does the uniform-strip rule need updating?

**No** — nothing joins the strip, so it isn't tested.

But the ask's second question — *was the rule always meant to bend?* — deserves
an answer, because the bend already exists and should be written down rather
than rediscovered:

> **`/next` already has a strip member that does not apply to every resource
> type.** `app.js:5049`: *"The Questions pane is built for repositories.
> Databases and filesystems …"* The tab is **present and explains itself**; it
> is not hidden.

So the established handling is **present-and-explained, never absent** — the
parallel-UI rule one level down: *a parallel UI may defer any affordance; it may
not silently omit one.* A tab that vanishes on some resources teaches the reader
that the strip is a different length in different places, and the next missing
thing reads as normal.

If a future feature genuinely needs a fifth member, that is the precedent to
follow: add it everywhere, and let it say where it does not apply. Not
"four, except Analysis."

---

## 4 · Two things found while reading, both cheap

**(a) Do not port classic's fallback.** `index.html:2088`:

```js
if (tab === 'sub-resources' && !selectedProject) tab = 'catalog';
```

The user clicks Sub-Resources and silently lands on Survey, with nothing saying
why. That is the same silent-substitution shape the honesty rules exist to stop
— and it is exactly what §3's present-and-explained rule prevents. If any
`/next` affordance can't apply to the current selection, it says so in place.

**(b) The honest note is currently on the wrong tab.** `renderAnalysisNote`
mounts into `enrichment-form` and is called from `loadPane()`
(`app.js:5155`) — the **Questions** checklist engine. So the one sentence
explaining a Survey-and-By-analysis feature is rendering on Questions, which is
neither of the tabs the feature will land on.

Not urgent: the note is temporary and disappears when the two halves ship. But
if it lives longer than expected, it should point from Survey & analyses, where
the reader looking for the Run button will be standing.
