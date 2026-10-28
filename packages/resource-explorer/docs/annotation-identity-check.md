# Can a measurement reach its survey report?

**Status:** implemented. Answered 2026-09-11 from the schema and live registry; the answer is applied in code.

**Asked by:** the design session, `NEXT-ROUND.md` §0 — scoped as "an hour, not
a work item", on the grounds that answering it might retire the backlogged
run-id persistence change and the timestamp-correlation hazard with it.

**Answered:** 2026-09-11, from the schema and the live registry.

## The model this question sits in

RE is **two stores on purpose**, not one store with a cache.

- **RE's own persistence** is shaped for the work it does: analytics,
  trends, question answering and presentation. That shape is deliberately
  ODS-like rather than graph-like, because those jobs are awkward against a
  graph and cheap against tables. It also holds what is needed to
  (re)populate Egeria.
- **Egeria** holds what is worth sharing, published when the user has said a
  project is more than ad hoc — not everything, and not automatically.
- **Sync mechanisms** run both directions: `EgeriaPublisher` outward,
  `egeria_annotation_materializer` inward.

The design question is therefore **not** "should a measurement live in the
graph instead". It is: *do the two stores record enough to know which row
corresponds to which annotation?*

They do not. That is the finding.

## What is missing: the correspondence key

`project_analysis_findings.egeria_annotation_guid` exists for exactly this —
added by `docs/annotation-linking-plan.md` Phase 1, with
`ProjectRegistry.mark_finding_guid()` to write it and seven tests covering it.

Measured on the live registry, 2026-09-11:

| | rows | carry a correspondence key |
|---|---|---|
| `project_analysis_findings` | 247,332 | **0** |
| `project_analysis_metrics` | 126,037 | **0** (no column exists) |

`mark_finding_guid()` is called by **nothing outside its own tests**. Both
publish paths hold the GUIDs and drop them:

- direct — `publish_annotations()` returns `guids`, passed to
  `_link_evidence_direct()` for linking and never persisted
  (`egeria_publisher.py:886`);
- outbox — the GUIDs sit on the outbox rows, read back by
  `get_outbox_guids()` for the same linking purpose, keyed by outbox row
  rather than by finding.

So the identity is computed on every publish, used once, and discarded.

## Why that costs more than a popup

Without the key, the sync between the two stores is **one-directional and
unverifiable**. Specifically:

1. **Nothing can answer "was this published, and as what?"** for a given
   local row. The publish either happened or it did not, and the row cannot
   say which.
2. **Re-publishing cannot be idempotent by correspondence.** It has to reason
   about qualifiedName conventions instead — and `annotation_qualified_name`
   collisions are already a known trap in this codebase.
3. **The inward direction has to defend itself by exclusion.** The
   materializer skips reports RE itself published, because otherwise it would
   write a second local copy of a run it already has. With a correspondence
   key it could reconcile instead of avoid. (It has never run here: zero
   `egeria_materialized::` markers.)
4. **The design question §0 actually asked — measurement to run — stays
   unanswerable**, for anything unpublished and for every metric.

The materializer already demonstrates the convention, in the other direction:
every row it writes carries `"source": "egeria"` and `"egeria_guid"` in
`detail_json`, with a comment saying why — *"a finding whose provenance is
ambiguous is exactly the confusion this direction of data flow could
introduce."* The outward direction has the same need and does not do it.

## A second gap, worth recording separately

`egeria_database_surveyor.py` and `egeria_filesystem_surveyor.py` write
**zero** rows to the local tables. They build Egeria annotation types
(`ResourceMeasureAnnotation`, `SchemaAnalysisAnnotation`, mapped in
`annotation_props.py` to the Egeria Jackson subtypes) and attach them to a
`SurveyReport` via `ReportSubject`, and that is all.

Under a single-store reading that looks like the tidier design. Under the
two-store model it is a **gap**: databases and filesystems have no local
analytic shape at all, so trends, comparison and question answering over them
have nothing to read, and the matrix cannot include them the way it includes
repos. Repos are the reference implementation here, not the deviation.

Not proposed as this round's work — recorded so it is not rediscovered as a
surprise when the matrix is asked to span resource types.

## A second consumer, reported independently

The context-compiler session reached the same gap from the other side: a
compiled section cannot say which survey run produced it beyond a timestamp,
because the compiler's provenance is *analysis id + surveyed_at* — which is
precisely the timestamp correlation rejected above, and for the same reason
(the two clocks disagree). It does not affect the current experiment, but it
is the gap underneath the compiled-context design's as-of-time section if
evidence ever needs to point at a specific survey report.

Two independent consumers now want the same missing key: the measurement
detail in `/next`, and the compiler's evidence manifest.

## Recommendation

1. **Write the correspondence key on publish.** Call `mark_finding_guid()`
   from both publish paths with GUIDs already in hand. No schema change, no
   new vocabulary, already under test.
2. **Give metrics the same key.** This is the one real schema change, and the
   one `/next` depends on — its charts and counts render metrics, not
   findings.
3. **Keep the run-id backlog item open, rescoped.** Not "add run provenance"
   but "finish the two-store correspondence the publish path already computes
   and throws away". Timestamp correlation stays rejected: the two clocks that
   disagree are exactly what it would correlate.
