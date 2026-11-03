# Curated architecture answers — design

**Status:** implemented in part (code cites §6 items 1-4). Originally a design pass, not yet built. Opened 2026-09-08 from a question raised
while fixing the `architecture_diagram` chat answer (see `docs/Backlog.md`'s
entry for that day and `tests/test_architecture_diagram_results.py`).

## 1. The question this answers

**Decision (project owner, 2026-09-08):** raised while reviewing the newly
split "how do its components relate to each other?" chat question — its
answer, and every other architecture chat answer, currently describes a
detector's *proposal*, with no way to tell whether any of it has actually
been reviewed. "I think that a simple thing we could do is to query egeria
or the curator results" — i.e. answer from what's been curated/materialized,
not only from the raw finding.

This doc scopes what that would take, given what already exists.

## 2. What already exists (verified 2026-09-08, not assumed)

The accept/reject/materialize *mechanism* is real and shipped — this is not
a green-field build:

- **Verdicts.** `architecture_component_verdicts` (`registry.py:1463-1481`,
  migrated `verdict_target` column at `:1517-1527`) records
  `accepted`/`rejected`/`retyped` per `(entity_type, entity_slug,
  scope_locator)`, `decided_by`, timestamp. The same table holds both
  component verdicts (`verdict_target='component'`, the default) and
  blueprint verdicts (`verdict_target='blueprint'`, keyed by
  `scope_locator = f"{perspective}::{cluster_name}"` —
  `web/routes/curate.py:369` and `:397-410`).
- **Materialization.** `materialize_component_if_accepted`
  (`workflows/curate.py:197-231`) fires only on `verdict == "accepted"`,
  finds-or-creates a real Egeria `SolutionComponent` via
  `ComponentMaterializer.materialize`
  (`surveyors/arch_recovery/materializer.py:215-`), and records the result
  in `architecture_materialized_components` (`registry.py:1488-1497`) —
  idempotent, privacy-zone-gated. `materialize_blueprint_if_accepted`
  (`workflows/curate.py:290-`) is the parallel path for blueprints, backed
  by `architecture_materialized_blueprints` (`registry.py:1535-1550`) and
  `BlueprintMaterializer`.
- **The write path (`curate.py`'s `add_component_verdict`,
  `web/routes/curate.py:326-347`) already reads back the verdict + whether
  materialization succeeded**, so Curate's own UI can show "accepted →
  published as `<qualified_name>`".
- **`_architecture_recovery_results` (the "what components exist" reader)
  already merges verdict state in** — one whole-resource
  `registry.get_component_verdicts("repo", slug)` call, attached per
  component as `verdict`/`materialized` (`repo_survey_definition_adapter.py
  :2494-2530`). Its own comment is explicit about why this is a join, not a
  rewrite: *"a curator's verdict is evidence of a different kind, not a
  rewrite of what the detectors said"* — worth preserving as a constraint
  below.

**What was designed on purpose and rejected**, per
`materializer.py`'s own docstring (`:1-6`): a "Phase 2 projects everything to
Egeria at Draft ContentStatus, curator promotes per element" scheme
(`docs/architecture-recovery.md:1350-1362`, an earlier iteration) was
replaced with the current shape — RE never writes an unaccepted proposal
into Egeria at all; only an accepted verdict materializes anything. That
choice is why "query Egeria" and "query the curator results" are close to
the same question for accepted elements (Egeria only ever holds what was
accepted), but diverge for everything not yet decided (Egeria has nothing to
say about a still-pending proposal; only the local verdict table does).

`docs/architecture-recovery-report-then-curate.md` is referenced by name in
7 places in code and docs (including `persist.py`'s own docstring) but
**does not exist as a file anywhere in this repo** — its content appears
folded into `docs/architecture-recovery.md` §17. Not this doc's problem to
fix, but worth a line in `docs/Backlog.md` so the next reader isn't sent
chasing a 404 the way this design pass was.

## 3. What's actually missing

Given §2, the gap is narrower than "build curation": **the read/answer
surfaces never look at verdict state.**

1. **`_architecture_diagram_results` and the Mermaid diagram itself carry no
   verdict information at all.** The diagram is rendered once, at *persist*
   time (`persist.py::_persist_diagram`, called from inside `persist_ir`),
   from the raw `components`/`ports`/`wires` that step detected — before any
   curator could possibly have looked at them. There is structurally no way
   for a persisted-at-survey-time diagram to reflect verdicts recorded
   afterward, short of re-rendering.
2. **`_architecture_summary_results`** (the field-dump-turned-sentence
   answer) is computed at survey time too (`repo_arch_summary` step) and
   also carries no verdict join.
3. **`_architecture_recovery_results` merges verdicts into the data but the
   chat-facts renderer never surfaces them as prose.** `f.value.components[
   i].verdict` exists in the envelope sent to the browser today, but
   `_summariseFactValue`/`_factAnswerText` (index.html) have no
   verdict-aware summarization — a repo with 0% curated and one with 100%
   curated currently read identically in chat.
4. **Blueprint verdicts exist but nothing in the diagram/summary answer
   paths reads `verdict_target='blueprint'` rows at all** — the coupling
   diagram's "Blueprint: X" subgraphs are the same objects a curator can
   accept/reject in Curate, but the chat answer has no way to say "2 of 5
   blueprints accepted."

## 4. Design options

### Option A — annotate answers with curation coverage (no diagram change)

Add a coverage figure to `_architecture_recovery_results`'s existing
verdict join — count of `accepted`/`rejected`/pending components (and,
separately, blueprints) — and surface it as a headline/caption fragment:
*"24 of 109 components reviewed (18 accepted, 6 rejected)."* Cheapest
option; changes prose only, not the diagram; reuses data already being
fetched (`get_component_verdicts` is already called for every
`architecture_recovery` read).

Does not touch `architecture_diagram` or `architecture_summary` — those
stay proposal-only unless combined with an option below.

### Option B — verdict-aware diagram (visual accepted/pending/rejected)

Move diagram rendering from *persist* time to *read* time: instead of
`_persist_diagram` baking a static Mermaid blob into the finding row,
`_architecture_diagram_results` would re-render from the current component
list plus current verdict state on every read — coloring/styling nodes by
verdict (e.g. solid border = accepted, dashed = pending, struck through or
omitted = rejected), the way `classDef structural` already exists as a
styling hook in the current renderer (`persist.py`'s
`_persist_diagram`/`mermaid.py`).

This is the option that actually answers "how do the *accepted* components
relate," not just "how many are accepted" — but it's a real architecture
change: the diagram stops being a snapshot-of-what-this-run-found and
becomes a live view, which changes what "re-run repo_arch_detect to refresh
this" means (today that note is about detection changing; it would also
need to become true of verdicts changing, or the diagram needs its own
lighter-weight recompute path that doesn't require a full re-survey).

Blueprint verdicts (`verdict_target='blueprint'`) would need their own join
here — confirmed keyed by `f"{perspective}::{cluster_name}"`, the same
naming the coupling diagram's `subgraph bp_n_*["Blueprint: ..."]` blocks
already use, so the join key exists; it isn't wired to anything yet.

### Option C — read live from Egeria instead of the local materialization cache

For elements that **have** been materialized, query Egeria directly
(pyegeria) rather than `architecture_materialized_components`/
`_materialized_blueprints`, on the theory that Egeria is the actual source
of truth and the local cache could drift.

Given §2's confirmed shape — RE writes to the cache table in the same call
that materializes to Egeria (`ComponentMaterializer.materialize`,
idempotent find-or-create) — the cache is not an independent copy that can
silently diverge the way, say, RAG-ingested prose can; it's written at the
moment of the Egeria write it describes. A live Egeria read would add a
network round-trip per answer for no correctness the cache doesn't already
provide, unless a specific scenario surfaces where the two are known to
disagree (e.g. someone deletes a `SolutionComponent` directly in Egeria,
bypassing RE entirely). Recommend treating this as **not needed now** —
revisit only if such drift is actually observed.

## 5. Decisions (project owner, 2026-09-08)

All three open questions from the first draft of this doc were answered in
the same sitting:

- **Unreviewed proposals stay visible.** *"Keep showing proposals, with
  coverage stated"* — nothing disappears; the answer states how much has
  actually been reviewed (e.g. *"24 of 109 reviewed (18 accepted, 6
  rejected)"*) alongside the existing *"(proposed by a detector, not yet
  validated)"* caveat, which stays as-is for anything still pending.
- **The verdict-aware diagram (Option B) is in scope now**, not deferred —
  *"Include the verdict-aware diagram now."* Diagram rendering moves from
  persist-time (baked into the survey run) to read-time (recomputed from
  current verdict state on every read).
- **Blueprints and components are wired together, in the same pass** —
  *"Do both together."* Both verdict shapes already share one table and one
  join key convention (`scope_locator`, `f"{perspective}::{cluster_name}"`
  for blueprints); splitting the read-time rewrite into two passes would
  mean revisiting the same rendering code twice.

Net effect: this is the full Option A + B scope, for both components and
blueprints, in one pass — not the cheapest phased version §5 originally
proposed as a starting point.

## 6. What the full-scope pass needs to build

1. **A coverage summary function**, reused by both the diagram's caption and
   `_architecture_recovery_results`'s own answer: given a slug, return
   `{components: {total, accepted, rejected, pending}, blueprints: {total,
   accepted, rejected, pending}}` from one `get_component_verdicts` call
   (already fetched for `architecture_recovery`; the diagram reader gains
   its own call).
2. **Move `_persist_diagram` off the write path.** `persist_ir` stops
   calling it; `repo_arch_detect`/`repo_arch_coupling` keep writing
   `components`/`ports`/`wires` findings exactly as now (those stay the
   proposal record — a verdict is evidence *about* them, not a rewrite,
   per `_architecture_recovery_results`'s existing constraint, §2 above).
   `_architecture_diagram_results` becomes the render site: read the latest
   component set the same way `_architecture_recovery_results` does
   (`query_finding_scopes`/`query_findings_all_runs`, per perspective), join
   `get_component_verdicts`, then call `mermaid.render`/`mermaid.caption`
   itself at read time.
3. **Verdict styling in the Mermaid output.** `mermaid.py`'s renderer
   already has a `classDef` styling hook (`classDef structural
   stroke-dasharray:4 3,fill:none;` in the current persisted output) —
   extend with `classDef accepted`/`classDef pending`/`classDef rejected`
   (solid/dashed/struck-through per the two node-level questions below,
   both answered):
   - A rejected component **does not appear** in the diagram at all — the
     cleaner reading the design doc flagged, now the more actionable one:
     it composes with the coverage summary (item 1) rather than needing a
     second, diagram-specific rejected-count.
   - An accepted vs. still-pending component is the one real visual
     distinction left in the diagram itself (solid vs. dashed border, via
     the existing `classDef` mechanism).
4. **Blueprint verdicts join the same way**, keyed by
   `f"{perspective}::{cluster_name}"` against the diagram's own
   `subgraph bp_n_*["Blueprint: ..."]` naming (§4 confirmed this key
   already lines up) — a rejected blueprint's subgraph is omitted the same
   way a rejected component's node is; an accepted one renders solid.
5. **The "shown: coupling · also on file: detect" note (2026-09-08's prior
   fix) keeps working unchanged** — perspective selection is orthogonal to
   verdict filtering; both survive the diagram becoming read-time.
6. **`can_run`/"re-run to refresh" semantics need a second sentence.**
   Today "re-run `repo_arch_detect`, `repo_arch_coupling` to refresh this"
   means new detection. Post-change it's still true for that, but the
   diagram *also* changes the moment a verdict changes — with no survey
   re-run required. The chat answer should say both: detection is as of the
   last survey; curation state is live.

Not attempted in this pass (both intentionally, from the answered
questions): live-Egeria reads in place of the materialization cache
(Option C — no observed drift to justify it) and hiding unreviewed
proposals by default (kept visible, per decision 1 above).

## 7. Sequencing note

This is now a real code change, not a doc. The natural build order matches
§6's numbering: (1) coverage summary is small and independently useful even
before the diagram moves — ship it first and get it into
`_architecture_recovery_results`'s answer, verified against a repo with a
mix of accepted/rejected/pending state; then (2)-(4) together, since moving
the diagram to read-time and adding verdict styling are the same edit to
the same function; (5) is a regression check, not new work; (6) is a
one-line note-text change once (2)-(4) land.
