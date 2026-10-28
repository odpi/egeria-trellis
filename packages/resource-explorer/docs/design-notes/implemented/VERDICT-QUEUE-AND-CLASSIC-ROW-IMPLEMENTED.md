# Implemented: sort-direction fix and the honest classic row

**Replies to:** `REVIEW-VERDICT-RULING.md` §2 (sort direction) and §3 (classic
row); `RULING-WHAT-A-VERDICT-IS-ABOUT.md` §2b (why agreement matters); and
`RULING-CLASSIC-AND-NEXT.md` §3 (the classic-row requirement itself).
**Read against:** `main` at `c3e47d4a`, 2026-09-20.
**Combined into one doc** — both are small fixes to the same underlying defect
family (agreement treated as "outranks" rather than "raises effective
evidence, which sinks a weakest-first row").

---

## 1 · Sort-direction fix

**Confirmed the defect as described.** `next/stages/curate.js` (the current
home of the comparator the review cited at `app.js:5880`), lines ~520–521:

```js
if (sort === 'confidence') rows.sort((a, b) => (b.agreement_count || 0) - (a.agreement_count || 0)
  || (a.min_confidence ?? 101) - (b.min_confidence ?? 101) || b.low_confidence - a.low_confidence);
```

Agreement ran descending (most-agreed first) while confidence ran ascending
(weakest first) — two keys pointing in opposite directions on a queue that's
supposed to be weakest-first throughout. The review's own diagnosis matches
the code exactly: *"outranks"* (§2b's wording) was implemented as *"sorts
above"* rather than *"raises effective evidence, and effective evidence sinks
a branch in a weakest-first queue."*

**Fix:** flipped the primary key to ascending, same direction as the
secondary keys:

```js
if (sort === 'confidence') rows.sort((a, b) => (a.agreement_count || 0) - (b.agreement_count || 0)
  || (a.min_confidence ?? 101) - (b.min_confidence ?? 101) || b.low_confidence - a.low_confidence);
```

Less agreement and lower confidence both now sort first — the branches
needing the most attention open the list, agreement breaking in the same
direction confidence already did rather than against it.

**Label and comment.** The toggle button's visible text changed from *"by
confidence"* to *"by evidence"* (the `data-tree-sort="confidence"` attribute
and the `state.componentSort` value were left as-is — internal identifiers,
not user-visible, and renaming them buys nothing here). The comment block
above the comparator, which had been describing the inverted behavior as
intentional ("Agreement outranks a single high confidence… so it sorts
first"), now states the corrected rule and includes a dated note pointing at
this doc, so a future reader hitting `git blame` lands on the reasoning
rather than the bug.

**Test coverage:** none exists for this comparator specifically — it's
client-side JS embedded in a template-string-generated file
(`next/stages/curate.js`), and this codebase's test suite
(`tests/test_component_tree.py`, `tests/test_next_component_review.py`) tests
the backend fields the comparator consumes (`agreement_count`,
`min_confidence`, `low_confidence` — see `component_tree.py`) but has no
harness for the JS sort itself. Noting the gap rather than standing up a new
JS test harness for one comparator; the backend fields it reads are already
covered.

---

## 2 · Honest classic row

**Confirmed the defect and the ruling.** `RULING-CLASSIC-AND-NEXT.md` §3:
classic retires only if `/next` earns it (project owner, 2026-09-17), so
there's no timeline — meaning the classic panel's `_archRow`
(`web/static/index.html`, around line 4172) could not keep presenting one of
two current proposals as the answer indefinitely. `_archRow` rendered
`c.type`/`c.confidence` (the backend's `latest` = max-by-`surveyed_at` pick,
`repo_survey_definition_adapter.py:2951`) with nothing indicating a different
extractor currently proposes the same path with a different reading, even
though the backend already computes and sends `c.proposals` (one entry per
extractor currently proposing this path — type/confidence/`run_label`/
`surveyed_at`) and `c.agreement` (true when 2+ agree) on every component row.
`/next`'s branch-tree leaf rows already read these correctly (per
`VERDICT-RULING-IMPLEMENTED.md` §2a/§2b); `_archRow` never read them at all.

**Fix — one clause, not the two-column parity display** (per §2 of the
ruling: capability may live in `/next` alone; honesty may not). When
`c.proposals` has more than one entry, `_archRow` now renders:

```
also proposed by <other run_label(s)> ›
```

naming the extractor(s) besides the one whose type/confidence is shown as
the row's primary reading. Implementation: sort `c.proposals` by
`surveyed_at` descending and treat everything after the first as "other" —
this mirrors, client-side, how the server picks `latest` (max by
`surveyed_at`) closely enough to name the right extractor(s) in the normal
case, without needing a new field just to carry the primary's `run_label`
explicitly.

**Primary selection — deferred, not skipped.** The ruling also asks that
where a single primary must be shown, it should be the best-evidenced
(agreement first, then confidence) rather than most-recently-surveyed, since
*"most recently surveyed is a fact about the scheduler, not about the
component."* Left `repo_survey_definition_adapter.py:2951`'s `latest =
max(comp_rows, key=lambda r: r["surveyed_at"])` as-is for this pass: it's a
shared computation feeding both classic and `/next`'s payload
(`components[]`), so changing the selection rule is a wider-blast-radius
backend change than a one-clause client-side addition, and `/next` isn't
depending on `latest` for its own correctness (it already reads
`proposals`/`agreement` directly). Filed as its own Backlog.md entry
("Classic panel: primary component pick is still `latest`, not
best-evidenced") rather than silently leaving it undone — the honesty clause
above is satisfied either way, but the primary-selection question is
separate and still open.

**Test coverage:** same gap as above — `_archRow` is template-string HTML
generation with no existing test harness (`test_registry_withdrawal.py:243`
references `_archRow`/`_archSubmitVerdict` only in a docstring comment, not
as a test target). The backend fields it now reads (`proposals`) are covered
by `test_component_tree.py`'s `agreement_count`/proposal-group assertions
and by the `_architecture_recovery_results` proposals-grouping logic itself,
which was already exercised when `VERDICT-RULING-IMPLEMENTED.md` shipped.
