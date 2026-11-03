# Review: the verdict ruling as built

**Reviewing:** `VERDICT-RULING-IMPLEMENTED.md`, PR #107, merged as `1b370cbe`
**Read against:** `main` at `1b370cbe` — every claim below cites what I read
**Date:** 2026-09-17

---

## 1 · Accepted, and two parts are better than what I specified

§2a's `proposals` list, §2b's agreement rollup, §2c's flag-not-invalidate, §2d's
split coverage sentence and §3's diagram caption are all there. Two of them
improve on the spec:

- **Blueprint coverage bucketed per reading, not one clause.** I asked for *"0 of
  12 clusters in the logical reading"* — one clause. `blueprints_by_perspective`
  gives **one clause per reading present**, which is the correct consequence of
  the ruling rather than the illustration I happened to draw. A combined figure
  would sum counts from different readings, and the comment at
  `repo_survey_definition_adapter.py:2810` says exactly that.
- **The zero-total omission is reasoned, and the reasoning is right.**
  `_architecture_verdict_coverage_sentence`'s docstring: *"Omits a clause
  entirely for zero-total … rather than reporting '0 of 0 reviewed', which states
  nothing."* That is the honesty rule applied one level finer than I applied it.

Scoping the vocabulary change to rendered text with no field or JSON-key renames
is also the right reading of §0.

---

## 2 · One defect: the sort comparator inverts the queue

`app.js:5880`:

```js
if (sort === 'confidence') rows.sort((a, b) => (b.agreement_count || 0) - (a.agreement_count || 0)
  || (a.min_confidence ?? 101) - (b.min_confidence ?? 101) || b.low_confidence - a.low_confidence);
```

**The two keys point in opposite directions.** Agreement is descending — most
agreed first. Confidence is ascending — weakest first. So the primary key now
puts the **best-evidenced branches at the top** of a list whose own comment,
before this change, read *"ordering by confidence puts the weakest clusters first
without hiding one."* A review queue now opens on the branches that need a human
least.

**This is my wording's fault.** §2b says *"agreement outranks a single high
confidence,"* and *outranks* reads as *sorts above*. What I meant, and what I had
to correct in my own drawing for the same reason, is that **agreement raises a
branch's effective evidence** — and in a weakest-first queue, stronger evidence
**sinks**. Two extractors agreeing at 75% and 45% is a better-established
component than one at 90%, so it needs less attention, not more.

So: one ordering, ascending by effective evidence, with agreement strengthening
rather than leading. Agreement can stay a key — it just has to run the same
direction as the one beside it.

**And the control should read `by evidence`.** The button still says *by
confidence* while the comparator's first key is agreement. A control whose name
omits half of what it sorts on is the same class of defect as a caveat that does
not name its analysis.

---

## 3 · The known gap is real, and bigger than a rendering gap

The reply flags that the classic Curate panel's `_archRow` does not render
`proposals`/`agreement`/`withdrawn_by`. Following that through:
`repo_survey_definition_adapter.py:2951` still computes

```python
latest = max(comp_rows, key=lambda r: r["surveyed_at"])
```

as the top-level "primary", and the classic panel reads those top-level fields.
**So the defect the ruling was written about is still live there**: a curator in
the classic panel sees whichever extractor happened to run last, presented as the
answer, with nothing saying another proposal exists.

Two things follow.

**(a) The classic panel may defer the affordance; it may not present one proposal
as the answer.** Full parity is not needed — one clause is: where a path carries
more than one current proposal, the classic row says *also proposed by coupling
›*. That is cheap, it removes the false impression, and it leaves the two-column
treatment to `/next`. A parallel UI may defer any affordance; it may not silently
omit one — and this is that rule pointing the other way, at the original.

**(b) If there must be a primary, it should not be the most recent.** A caller
that can only take one proposal should get the **best-evidenced** one — agreed
first, then highest confidence — because *most recently surveyed* is a fact about
the scheduler, not about the component. Latest-wins is the specific thing §2a
set out to remove; keeping it as the fallback keeps an arbitrary answer in the
one place least able to caveat it.

Neither is urgent if the classic panel is being retired on a known timeline.
**That is a question for the project owner**, and it decides whether (a) is worth
building or just recording.

---

## 4 · One minor note, not worth a round on its own

A reading with clusters gets a clause; a reading with none is omitted. A reader
therefore cannot distinguish *no clusters were proposed in the logical reading*
from *the logical reading produced nothing because clustering did not run* — the
same *nothing declared* versus *nobody looked* distinction that
`topology_sentence` already handles well elsewhere in this file. It is derivable
(an entirely empty sentence means nothing ran at all), so it is a small
improvement rather than a defect: when components are reported but a reading has
no clusters, one clause saying so is worth more than silence.

---

## 5 · Answering the parity question

**No parity with the classic view is assumed.** `ComponentTree.dc.html` on canvas
page seven draws the `/next` branch tree specifically — the *found by* column,
the agreement line, the withdrawal flag and the ports column are all `/next`. Its
own foot note says which figures are measured and which are illustrative, and
nothing in it depends on the classic panel growing the same affordances.

The one thing that drawing does assume is the sort direction in §2 above, so it
and the shipped comparator should agree once that is settled.

---

## 6 · On the untested suite

Recording the Postgres suite as unexercised, with the reason and the evidence
that the failures were all `connection refused` on 5442, is the right way to
report it: a gap named with its cause is a state, and an unnamed one is a blank.
No action asked.
