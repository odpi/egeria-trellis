"""Shared IR -> registry persistence for the two architecture-recovery
survey steps (repo_arch_detect, repo_arch_coupling — Phase 1 plan §4.2).

Design doc §5.4/§6.0: no new table. Evidence goes to
`project_analysis_findings` with `kind="architecture_recovery"`; a
component-scoped measure goes to `project_analysis_metrics` under the same
kind. `scope_locator` is the join key in both, and it IS the component — a
component's path prefix (§6.0), derived here from its `files` glob (or, for
a files-less deployment/compose component, its declared deployment
context) rather than its `slug`, which is a detector-qualified identifier
and not itself a path.

Both survey steps call `persist_ir()` independently (they run as separate
StepInfo entries, possibly at different times — see
ProjectRegistry.query_findings_all_runs' docstring for why that matters).
There is no cross-step merge here the way scripts/arch-spike/detect.py's
`_merge_agreeing()` does it for a single combined run: instead, two
components that land on the SAME scope_locator naturally accumulate
multiple "component" and evidence rows over time, and the results reader
(repo_survey_definition_adapter.py's `_architecture_recovery_results`)
reads them back together. That is a deliberate simplification, not an
oversight — see the port's write-up for the trade.
"""
from __future__ import annotations

import json
import logging

from resource_explorer.step_outcome import PARTIAL, RECOVERED, StepOutcome

from resource_explorer.registry import WITHDRAWN_LABEL

from .ir import IR, Component, Evidence

log = logging.getLogger(__name__)

KIND = "architecture_recovery"

#: The rendered proposal lives under its OWN kind, not under `KIND`.
#:
#: Not a filing preference — a measured consequence. `context_compile.py` calls
#: `query_findings(slug, analysis_id)`, which defaults to whole-resource scope.
#: Every `architecture_recovery` finding is written at COMPONENT scope, so that
#: query returns nothing and the compiler correctly falls through to the
#: analysis's own results reader. A whole-resource finding under `KIND` would
#: make it return exactly one row — a Mermaid blob — and **suppress that
#: fallback**, replacing the architecture section with a picture drawn for a
#: curator rather than evidence a model can reason over. Capping its size
#: downstream limits the damage but does not restore the reader.
#:
#: Same precedent, same reason: ports and wires live under
#: `architecture_interfaces` with their own results reader, because they are
#: not components either.
DIAGRAM_KIND = "architecture_diagram"


def scope_locator_for(component: Component) -> str:
    """A component's path prefix (design doc §6.0) — the join key.

    Preferred source is the component's own `files` glob, since that is
    what the component actually claims. Only a files-less component (a
    compose-derived deployment-perspective component — §8.2b's add-on/
    shared-infra tier, which owns no first-party code of its own) falls
    back to its declared deployment context, and failing that its bare
    identity value or slug.
    """
    files = component.files or []
    if files:
        g = files[0]
        for suffix in ("/**", "/*"):
            if g.endswith(suffix):
                stripped = g[: -len(suffix)]
                return stripped
        return "" if g == "**" else g
    # A files-less component (compose-derived, §8.2b's add-on and shared-infra
    # tiers, which own no first-party code) has no path prefix to use, so the
    # join key falls back to its slug.
    #
    # It must NOT fall back to `identity.deployment_context`, which an earlier
    # version did: that is the compose file's directory and is therefore SHARED
    # by every service declared in that file. Measured, it collapsed 58 distinct
    # compose-service components onto 10 join keys, and every reader — including
    # the shipped results view — kept one container per compose file and silently
    # discarded the rest. T1 deployment recall fell from 18/27 to 2/27 (spike
    # README findings 45-46).
    #
    # The slug is the right fallback because it is already unique and already
    # carries the deployment context that §8.2 requires for exactly this reason
    # ("egeria-quickstart::quickstart-pyegeria-web") — reusing that guarantee
    # rather than inventing a second one.
    return component.slug or component.identity.value


def detector_family(detector: str) -> str:
    """Coarse "which approach" family for a detector id, e.g.
    "manifest:python" -> "manifest", "ast-grep:fastapi-app-construction" ->
    "code marker", "coupling:cohesive" -> "coupling (cohesive)". This is the
    "which approach proposed each" grouping Phase 1 plan §4.4 asks for.
    """
    prefix = detector.split(":", 1)[0]
    if prefix == "manifest":
        return "manifest"
    if prefix in ("dockerfile", "compose"):
        return "deployment"
    if prefix == "ast-grep":
        return "code marker"
    if prefix == "coupling":
        shape = detector.split(":", 1)[1] if ":" in detector else ""
        return f"coupling ({shape})" if shape else "coupling"
    return prefix or "unknown"


def _scope_depth(scope: str) -> int:
    """How deep a scope locator sits, in its own terms.

    A path counts separators; an identity locator (`compose::agent`) is one
    below its deployment unit. `projection` counts this to decide what falls
    within a requested level, so a component with a parent and depth 0 would
    still project as a root — which is how a filled hierarchy can look identical
    to a flat one.
    """
    if not scope or scope == ".":
        return 0
    if "::" in scope:
        return 1
    return len([p for p in scope.split("/") if p]) - 1


#: Decision notes — WHY this run concluded what it concluded.
#:
#: Its own kind, for `DIAGRAM_KIND`'s reason: a whole-resource finding under
#: `KIND` makes `context_compile`'s `query_findings(slug, analysis_id)` return
#: exactly one row and suppress the results-reader fallback, replacing an
#: analysis's evidence with whatever that row happens to be.
#:
#: **Why persist prose at all.** Every note the discoverers produce — "2
#: declarations describe one platform … identical server sets", "qualified by
#: build module, since a shared name becomes a shared slug", "6 profile overlays
#: have no base config beside them" — went to `log.info` and nowhere else. That
#: is the *why* behind a component's name or absence, and it evaporated unless
#: someone was tailing logs at INFO when the survey ran. Evidence records say
#: what was concluded; these say what was decided and rejected, which is what a
#: reader needs when the answer looks wrong.
DECISIONS_KIND = "architecture_decisions"


def _persist_decisions(registry, slug: str, notes: list, surveyed_at: str,
                       run_label: str, run_scope: str) -> None:
    """One whole-resource row per RUN LABEL, carrying that step's notes.

    Keyed by `run_label` in the `check_name` for the reason `persist_ir` already
    prefixes its summary metric: `repo_arch_detect` and `repo_arch_coupling` are
    independent steps that need not share a `surveyed_at`, and `query_findings`
    returns only the rows at `MAX(surveyed_at)` per (slug, kind, scope). A
    shared check_name at the same scope would let whichever step ran last hide
    the other's reasoning entirely.

    Read them with `query_findings_all_runs(slug, DECISIONS_KIND, "")` and keep
    the newest row per `check_name`; a plain `query_findings` returns only the
    step that ran most recently, which is a valid but partial view.
    """
    if not notes:
        return
    kept = [str(n) for n in notes][:_MAX_DECISION_NOTES]
    try:
        registry.upsert_finding(
            slug, DECISIONS_KIND,
            [{
                "check_name": f"decisions:{run_label}",
                "label": run_label,
                "summary": f"{len(notes)} decision note(s) from {run_label}",
                "confidence": 100,
                "detail": {"notes": kept, "run_label": run_label,
                           "run_scope": run_scope,
                           "truncated": max(0, len(notes) - len(kept))},
            }],
            surveyed_at=surveyed_at, scope_locator="",
        )
    except Exception:
        # A decision trace must never be able to fail a survey that otherwise
        # succeeded — but it must say so rather than vanish, which is the exact
        # failure this whole change exists to fix.
        log.exception("%s: could not persist %s decision notes", slug, run_label)


#: What the detectors found and did NOT admit as components: services running other projects' images
#: (referenced only), directories with a Dockerfile but no first-party source, fixtures. One
#: whole-resource row per run, replaced each run, so a service that left the compose file leaves the list.
ADMISSION_KIND = "architecture_admission"


def persist_admission(registry, slug: str, referenced: list, left_out: list[str], published: list[str],
                      admitted_count: int, surveyed_at: str, run_scope: str = "") -> None:
    """Write the not-admitted record. `referenced` are Components classed referenced_only; they become
    runtime dependency rows (dependency_table) and, when RE knows the repository that builds the image,
    the Environment Deployment Blueprint's nodes. `published` are the normalised image names THIS
    repository builds or publishes, kept so another repository's referenced-only service can be linked
    to the repository that builds it."""
    from .admission import ZERO_COMPONENT_SENTENCE
    items = [{"scope": scope_locator_for(c), "name": c.name, "slug": c.slug, "image": c.image,
              "evidence": c.admission_evidence,
              "unit": c.identity.deployment_context or ""} for c in referenced]
    registry.upsert_finding(
        slug, ADMISSION_KIND,
        [{
            "check_name": "admission", "label": "referenced" if referenced else "all_admitted",
            "summary": f"{len(referenced)} referenced only · {admitted_count} admitted",
            "confidence": 100,
            "detail": {"referenced": items, "left_out": left_out, "published_images": sorted(published),
                       "admitted": admitted_count, "run_scope": run_scope,
                       "sentence": ZERO_COMPONENT_SENTENCE if (referenced and not admitted_count) else ""},
        }],
        surveyed_at=surveyed_at, scope_locator="", supersedes_previous=True,
    )


#: Cap. A note list is prose for a human; an unbounded one is a log file in a
#: findings row. Measured: egeria 2, dataflow 5, atlas 20.
_MAX_DECISION_NOTES = 200


#: `check_name` for a withdrawal. Its LABEL is what the registry keys on
#: (`WITHDRAWN_LABEL`); the check_name is for humans reading the table.
WITHDRAWN_CHECK = "component_withdrawn"


def _scopes_last_written_by(registry, slug: str, run_label: str, before: str) -> set[str]:
    """Scopes this step wrote at its most recent run STRICTLY BEFORE `before`.

    Its most recent run, not every run it ever did. Comparing against the union
    of all history would re-withdraw the same scope on every subsequent run
    forever; comparing against the previous run answers exactly R2's question —
    *scopes I used to write and did not write this time*.

    One query, not one per scope: `egeria` has ~1000 component scopes and this
    runs inside `persist_ir`.
    """
    by_run: dict[str, set[str]] = {}
    for row in registry.query_findings_history_raw(slug, KIND):
        if row.get("check_name") != "component":
            continue
        surveyed = row.get("surveyed_at") or ""
        if not surveyed or surveyed >= before:
            continue
        detail = row.get("detail_json") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail or "{}")
            except ValueError:
                continue
        # R2: unattributed rows belong to no step, so no step may withdraw them.
        # Everything written before `run_label` existed falls here, which is why
        # the pre-existing orphan population needs the step-4 backfill and can
        # never be cleared by an ordinary run.
        if detail.get("run_label") != run_label:
            continue
        by_run.setdefault(surveyed, set()).add(row.get("scope_locator") or "")
    return by_run[max(by_run)] if by_run else set()


def _withdraw_vacated(registry, slug: str, run_label: str, written: set[str],
                      surveyed_at: str, run_scope: str, outcome) -> list[str]:
    """Withdraw the scopes this step used to write and no longer does.

    **R1 — only a COMPLETE run may withdraw.** A withdrawal is inferred from
    absence, and absence means nothing if the run could not see everything. A
    scoped or partial run withdrawing the architecture outside its own scope is
    the worst failure mode in this design, and §4.1c already writes both fields
    this guards on.

    **R3 — the cause is `unclaimed`, never `removed`.** "We renamed it" and "it
    is gone from the repo" look identical from inside the pipeline and mean
    opposite things to a reader; reporting absence as removal would destroy the
    drift signal the catalog exists to carry.
    """
    if run_scope or (outcome is not None and getattr(outcome, "outcome", "") == PARTIAL):
        return []
    vacated = sorted(_scopes_last_written_by(registry, slug, run_label, surveyed_at) - written)
    for scope in vacated:
        registry.upsert_finding(
            slug, KIND,
            [{
                "check_name": WITHDRAWN_CHECK,
                "label": WITHDRAWN_LABEL,
                "summary": f"no longer proposed by {run_label}",
                "confidence": 100,
                "detail": {"cause": "unclaimed", "run_label": run_label,
                           "withdrawn_at": surveyed_at},
            }],
            surveyed_at=surveyed_at, scope_locator=scope,
        )
    return vacated


def persist_ir(
    registry,
    slug: str,
    components: list[Component],
    evidence: list[Evidence],
    surveyed_at: str,
    *,
    run_label: str = "run",
    run_scope: str = "",
    notes: list[str] | None = None,
    ports: list[dict] | None = None,
    wires: list[dict] | None = None,
    extra_metrics: dict[str, dict[str, float]] | None = None,
    outcome: StepOutcome | None = None,
) -> dict[str, str]:
    """Write one survey run's IR into the generic findings/metrics tables.

    Returns {component_slug: scope_locator} so a caller can attach further
    per-component metrics (e.g. import/co-change cohesion) after the fact
    without recomputing scope locators.

    extra_metrics: optional {scope_locator: {metric_name: value}} written
    alongside the standard confidence/evidence_count metrics — this is how
    repo_arch_coupling attaches import_cohesion/cochange_cohesion without
    this module needing to know about coupling-specific signals.

    outcome: caller-supplied StepOutcome, overriding the scoped/recovered
    default below. Only the caller knows whether this run's zero (if it was
    one) is trustworthy — repo_arch_detect and repo_arch_coupling have
    different known-positive checks (README findings 57/58), and this
    module has no opinion on either. Passed through unconditionally when
    given: a caller-supplied `unverified` for an unsupported language is a
    stronger, more specific statement than the generic `scoped_run`
    `partial` this function would otherwise compute, so it always wins.
    """
    # `run_scope` is the scope the RUN was narrowed to (D5/D6), which is a
    # different thing from a component's own `scope_locator` even though both
    # are paths and both live in the same column.
    #
    # They collide exactly: run `repo_arch_detect` scoped to `packages/foo` and
    # the component found there also keys on `packages/foo`. Because nothing
    # recorded which meaning a row carried, a SCOPED run — which by definition
    # saw only part of the repo, so its component set is necessarily incomplete
    # — wrote rows indistinguishable from a whole-repo run's. A reader merging
    # them presents a partial architecture as the whole architecture, with no
    # signal that anything is missing.
    #
    # Stamping it on every row is the minimum honest fix and needs no schema
    # change. The distinction it restores is "is this result complete?", which
    # no consumer could previously ask.
    # The shared outcome vocabulary (resource_explorer/step_outcome.py), rather
    # than a local `partial` boolean. Two spellings of one fact is the family of
    # bug this whole area has been fixing all session, so the local one goes.
    #
    # `run_scope` stays its OWN key rather than being folded into `cause`: the
    # outcome set is small and shared so it can be queried across steps, while
    # the scope is a value only this step knows how to interpret.
    if outcome is None:
        outcome = (
            StepOutcome(PARTIAL, cause="scoped_run", detail={"run_scope": run_scope})
            if run_scope else StepOutcome(RECOVERED)
        )
    outcome_row = outcome.as_row()
    slug_to_scope: dict[str, str] = {c.slug: scope_locator_for(c) for c in components}

    # Fill in the parent that the SCOPE LOCATOR already encodes, for every
    # component that has none.
    #
    # The block below only ever followed `parent_slug` values that already
    # existed, which is why persisting referenced ancestors changed nothing when
    # it was tried: measured 2026-08-25, 201 of milvus's 217 component rows
    # carry `parent_slug = ""`. Only `code::` components were ever given a
    # hierarchy; manifest-, deployment- and coupling-derived components are
    # parentless by construction, and they are the overwhelming majority.
    #
    # The locator is not opaque — `internal/agg` names a directory,
    # `compose::agent` names the compose stack that declares it — so this reads
    # recorded evidence rather than inventing structure. See
    # `scope_hierarchy.derive` for why a group needs two members and why a
    # derived ancestor is structural rather than a component.
    from . import scope_hierarchy

    all_scopes = sorted(set(slug_to_scope.values()))
    scope_parents, derived_structural = scope_hierarchy.derive(all_scopes)
    scope_to_slug = {v: k for k, v in slug_to_scope.items()}
    for c in components:
        if c.parent_slug:
            continue
        parent_scope = scope_parents.get(slug_to_scope.get(c.slug, ""), "")
        if parent_scope:
            # A structural ancestor is identified BY its scope: it has no slug
            # of its own because nothing detected it.
            c.parent_slug = scope_to_slug.get(parent_scope, parent_scope)
            # `depth` is what `projection` counts to decide whether a node is
            # within the requested level, so a filled parent with depth 0 still
            # projects as a root. Derived from the scope's own shape, which is
            # where the parent came from.
            c.depth = _scope_depth(slug_to_scope.get(c.slug, ""))

    # Candidate blueprints. Runs BEFORE the component rows are written so each
    # component's persisted detail carries the blueprint it was assigned to —
    # writing the rows first and clustering after would persist an empty
    # blueprint on every one of them, which is the state the corpus was in
    # until today (3,215 components, `blueprint` empty on all of them).
    cluster_sets = _cluster(components, slug_to_scope, extra_metrics, wires=wires, ports=ports)

    for c in components:
        loc = slug_to_scope[c.slug]
        registry.upsert_finding(
            slug, KIND,
            [{
                "check_name": "component",
                "label": c.type or "Unclassified",
                "summary": f"{c.name} ({c.type or 'untyped'})",
                "confidence": c.confidence,
                "detail": {
                    "name": c.name, "slug": c.slug, "type": c.type,
                    "identity": {
                        "method": c.identity.method, "value": c.identity.value,
                        "deployment_context": c.identity.deployment_context,
                    },
                    "run_scope": run_scope, **outcome_row,
                    # WHICH STEP wrote this row. Step 1 of
                    # `architecture-recovery-scope-tombstoning.md` and additive
                    # by design: nothing reads it yet.
                    #
                    # It is the enabler for that note's R2 — a step may only
                    # withdraw scopes IT wrote. `repo_arch_detect` and
                    # `repo_arch_coupling` write the SAME kind, so an
                    # unattributed withdrawal makes them erase each other's
                    # components alternately and forever. `persist_ir` already
                    # solved this collision once for metrics by prefixing
                    # `run_label` into the metric name (see the run-summary row
                    # below); this puts the same key where the component rows
                    # can be grouped by it.
                    #
                    # NOT `proposed_by`, which is the tempting proxy and the
                    # wrong one: it names the DETECTOR, and a detector can move
                    # between steps without any component changing.
                    "run_label": run_label,
                    "files": c.files, "blueprint": c.blueprint,
                    "proposed_by": c.proposed_by, "perspective": c.perspective,
                    "confidence_level": c.confidence_level,
                    # Granularity is not precision (approach-portfolio-model.md
                    # §2a) — the hierarchy is stored, not collapsed to one
                    # level, so a consumer can project (projection.py) rather
                    # than the generator having chosen a depth for them.
                    "parent_slug": c.parent_slug, "depth": c.depth,
                    # Whose boundary this is, and the short sentence shown on the node
                    # (DESIGN-BLUEPRINT-NODE-ADMISSION.md). Persisted on the row so a reader never
                    # re-derives the class.
                    "admission": c.admission, "admission_evidence": c.admission_evidence,
                    "image": c.image,
                },
            }],
            surveyed_at=surveyed_at, scope_locator=loc,
        )
        metrics = {"confidence": float(c.confidence)}
        if extra_metrics and loc in extra_metrics:
            metrics.update(extra_metrics[loc])
        registry.upsert_metric(slug, KIND, metrics, surveyed_at=surveyed_at, scope_locator=loc)

    # Structural nodes — ancestors that are REFERENCED by a component's
    # `parent_slug` but were never emitted as components themselves.
    #
    # Why they exist: `code_markers` emits a component per subtree that has
    # marker-rule hits, while `parent_slug` comes from `build_hierarchy`, which
    # holds every candidate subtree. An intermediate directory whose children
    # carry the markers (milvus's `internal/distributed`) is in the second set
    # and not the first, so its children pointed at a node nobody stored and
    # `projection.project_rows` had nothing to collapse — measured 2026-08-24 as
    # an identity function at every depth, on every repo.
    #
    # They are a SEPARATE check_name, not components, and deliberately carry no
    # type and no confidence. They have no marker evidence of their own; giving
    # them a type or a score would invent evidence to make a grouping look like
    # a finding, which is the failure `no metric, no number` (design §5) exists
    # to prevent. They are grouping structure and say so.
    # Shared with mermaid.py's renderer, which must draw exactly the set this
    # synthesises — see scope_hierarchy.missing_ancestors.
    present = {c.slug for c in components}
    missing = scope_hierarchy.missing_ancestors(components)
    for anc_slug in missing:
        if anc_slug in derived_structural:
            # Derived from a scope locator, so the slug IS the scope — no
            # namespace to unpack.
            anc_path = anc_slug
        else:
            ns, _, rest = anc_slug.partition("::")
            anc_path = rest.replace("::", "/") if rest else ""
        # Its own parent is the nearest OTHER referenced ancestor, derived from
        # the slug itself — never fabricated beyond what is already referenced.
        if anc_slug in derived_structural:
            anc_parent = scope_parents.get(anc_slug, "")
        else:
            parts = anc_slug.split("::")
            anc_parent = ""
            for i in range(len(parts) - 1, 1, -1):
                cand = "::".join(parts[:i])
                if cand in missing or cand in present:
                    anc_parent = cand
                    break
        registry.upsert_finding(
            slug, KIND,
            [{
                "check_name": "structural_node",
                "label": "Structural",
                "summary": f"{anc_path or anc_slug} (grouping only — no evidence of its own)",
                "confidence": 0,
                "detail": {
                    "name": anc_path.rsplit("/", 1)[-1] if anc_path else anc_slug,
                    "slug": anc_slug, "path": anc_path,
                    "structural": True,
                    "parent_slug": anc_parent,
                    "depth": anc_path.count("/") if anc_path else 0,
                    "run_scope": run_scope,
                },
            }],
            surveyed_at=surveyed_at, scope_locator=anc_path,
        )

    # Evidence rows: one per Evidence record, joined to its component via
    # subject_slug -> scope_locator. An Evidence record whose subject isn't
    # among this run's components (shouldn't happen — every Evidence in
    # this port is emitted alongside its Component) is skipped rather than
    # written with an empty/wrong scope_locator.
    by_scope: dict[str, int] = {}
    for e in evidence:
        loc = slug_to_scope.get(e.subject_slug)
        if loc is None:
            continue
        by_scope[loc] = by_scope.get(loc, 0) + 1
        registry.upsert_finding(
            slug, KIND,
            [{
                "check_name": e.assertion[:200],
                "label": detector_family(e.detector),
                "summary": f"{e.detector}: {e.assertion}",
                "confidence": e.confidence,
                "detail": {
                    "detector": e.detector,
                    "confidence_level": e.confidence_level,
                    "locations": [
                        {"path": loc_.path, "line": loc_.line, "excerpt": loc_.excerpt}
                        for loc_ in e.locations
                    ],
                },
            }],
            surveyed_at=surveyed_at, scope_locator=loc,
        )

    for loc, n in by_scope.items():
        registry.upsert_metric(
            slug, KIND, {"evidence_count": float(n)},
            surveyed_at=surveyed_at, scope_locator=loc,
        )

    # A run-summary row, scope_locator="" (whole-resource — same convention
    # every other kind uses for its own summary), so the results view has a
    # trend to read via the ordinary query_metrics_history path.
    # metric_name is prefixed with run_label ("detect"/"coupling") rather
    # than shared, because repo_arch_detect and repo_arch_coupling are two
    # independent StepInfo entries that are not guaranteed to run in the
    # same SurveyOrchestrator batch (and therefore not guaranteed to share
    # one surveyed_at) — a shared metric_name would let one step's count
    # silently overwrite the other's in query_metrics' "latest wins" read.
    #
    # This is also THE place a component-less run's outcome is recorded
    # (README finding 57). Every other row above is written per-component, so
    # zero components means zero of them — this row is the one thing
    # persist_ir writes unconditionally regardless of len(components), which
    # makes it the only reliable place for a caller's `unverified` (or any
    # other outcome) to land when there is nothing else to attach it to. The
    # alternative — a synthetic zero-confidence Component just to carry the
    # outcome — would fabricate a row in the "component" check_name that
    # every reader (and the ground-truth comparison) treats as a real
    # candidate, which is worse than the bug being fixed.
    registry.upsert_metric(
        slug, KIND, {f"{run_label}_component_count": float(len(components))},
        detail={"run_scope": run_scope, **outcome_row},
        surveyed_at=surveyed_at, scope_locator="",
    )

    # R1/R2/R3 — withdraw the scopes this step used to write and no longer does,
    # AFTER every component row for this run is in place so `written` is the
    # complete set. Returns [] for a scoped or partial run.
    withdrawn = _withdraw_vacated(registry, slug, run_label, set(slug_to_scope.values()),
                                  surveyed_at, run_scope, outcome)
    if withdrawn:
        # Never silent: a cleanup that leaves no trace is indistinguishable from
        # data loss to whoever comes looking in six months.
        log.info("%s: %s withdrew %d vacated scope(s)", slug, run_label, len(withdrawn))
        registry.upsert_metric(
            slug, KIND, {f"{run_label}_withdrawn_count": float(len(withdrawn))},
            detail={"cause": "unclaimed", "scopes": withdrawn[:50],
                    "truncated": max(0, len(withdrawn) - 50)},
            surveyed_at=surveyed_at, scope_locator="",
        )

    if ports or wires:
        name_to_scope = {c.name: slug_to_scope.get(c.slug, "") for c in components}
        _persist_interfaces(registry, slug, surveyed_at, ports or [], wires or [],
                            name_to_scope)

    _persist_blueprints(registry, slug, cluster_sets, surveyed_at, run_scope)

    # `_persist_diagram` is NOT called here any more (2026-09-08,
    # docs/curated-architecture-answers-design.md §6 items 2-4). The diagram
    # moved from persist time to read time —
    # repo_survey_definition_adapter.py's `_read_arch_recovery_ir` /
    # `_architecture_diagram_results` reconstruct the same `Component`/
    # ports/wires shape from the `component`/`architecture_interfaces`
    # findings this function already writes above, and render fresh on
    # every read — so a curator's accept/reject/retype verdict changes what
    # the diagram shows immediately, not only after the next survey re-run.
    # `_persist_diagram` itself is kept, unused, as a record of the
    # write-time design it replaces (and so the many docstring references to
    # it elsewhere in this package still resolve to real code).

    _persist_decisions(registry, slug, notes or [], surveyed_at, run_label, run_scope)

    return slug_to_scope


def _cluster(components: list[Component], slug_to_scope: dict[str, str],
             extra_metrics: dict | None = None,
             wires: list[dict] | None = None,
             ports: list[dict] | None = None) -> dict:
    """Propose candidate blueprints per perspective, and assign them.

    One clustering per §4.1 perspective, never one across all of them: the
    perspectives are different views of the same repo with different sources and
    vocabularies, and mixing them inside one blueprint repeats the Phase 0
    scoring error (a deployment detector scored against logical ground truth) at
    the level of a single proposed solution.

    `wires` (design §10 signal 2) and `ports` (design §10 signal 3) are the
    same lists `_persist_interfaces`/`_persist_diagram` already receive —
    passed straight through, not re-derived, since interfaces.propose()
    already did the detection work. Wire endpoints name components by NAME
    OR SLUG (`interfaces.propose` attributes a compose wire by service name,
    a port by slug), so `"name"` travels alongside `"slug"` below for
    `clustering._wire_weights`/`_interface_names` to resolve against — the
    same ambiguity `mermaid._resolve_endpoint` already handles for the
    diagram.

    Returns `{"clusters": {perspective: [top-level Cluster]}, "errors":
    {perspective: message}}`. Failure here must not lose the run — clustering is
    a proposal *about* the components, and the components are the finding — but
    it must not vanish either: a swallowed failure looks exactly like a
    perspective that had nothing to group.
    """
    from . import clustering

    # scope_locator is what clustering groups on, and it lives in the mapping
    # rather than on the Component, so surface it without mutating the IR.
    scoped = [
        {"slug": c.slug, "name": c.name, "scope_locator": slug_to_scope.get(c.slug, c.slug),
         "perspective": c.perspective, "identity": c.identity, "_component": c}
        for c in components
    ]
    # Affinity: import cohesion per scope, from the metrics the coupling step
    # attaches. Where present and at/above the bar, a group is carried as a
    # composed component rather than a Collection (Dan's rule: no affinity
    # leads you to collections). Absent — as it is for every run of the detect
    # step, which computes no cohesion — every group stays a Collection, which
    # is the correct default rather than a degraded one.
    cohesion = {
        scope: metrics["import_cohesion"]
        for scope, metrics in (extra_metrics or {}).items()
        if isinstance(metrics, dict) and "import_cohesion" in metrics
    }

    out: dict = {}
    errors: dict = {}
    for perspective in sorted({c.perspective for c in components if c.perspective}):
        try:
            flat = clustering.propose(scoped, perspective, cohesion=cohesion,
                                      wires=wires, ports=ports)
            if not flat:
                continue
            top = clustering.rollup(flat)
            clustering.assign(scoped, top)
            out[perspective] = top
        except Exception as exc:
            # Recorded, not just logged. A clustering failure and a perspective
            # with nothing to cluster both produce zero blueprints, and under
            # report-then-curate that difference matters to the curator: one
            # means "no grouping was found", the other means "we did not manage
            # to look". `_persist_blueprints` writes this as a finding.
            log.warning("clustering failed for perspective %r: %s", perspective, exc)
            errors[perspective] = f"{type(exc).__name__}: {exc}"

    # Copy assignments back onto the real Components so the persisted rows and
    # the rendered diagram both carry them.
    for row in scoped:
        if row.get("blueprint"):
            row["_component"].blueprint = row["blueprint"]
        if row.get("parent_slug"):
            row["_component"].parent_slug = row["parent_slug"]
    return {"clusters": out, "errors": errors}


#: Candidate blueprints live under their OWN kind, for the same reason the
#: diagram does: they are whole-resource, and every `architecture_recovery`
#: finding is component-scoped, so a whole-resource row under KIND would make
#: `context_compile.py`'s default-scope `query_findings` return exactly one row
#: and suppress its fall-through to the results reader.
BLUEPRINT_KIND = "architecture_blueprints"


def _persist_blueprints(registry, slug: str, cluster_sets: dict,
                        surveyed_at: str, run_scope: str) -> None:
    """Write candidate blueprints as findings — one row per cluster.

    `check_name` is the constant "candidate_blueprint", with the cluster's name
    in `label`. Deliberately NOT a dynamic `blueprint:{name}` check_name: ports
    and wires are written that way and it cost three wrong "zero" measurements
    in one session, because an exact-match query against a computed check_name
    silently returns nothing. A stable check_name is queryable; the name belongs
    in a field.
    """
    clusters_by_perspective = cluster_sets.get("clusters") or {}
    errors = cluster_sets.get("errors") or {}
    if not clusters_by_perspective and not errors:
        return

    def rows_for(cluster, perspective: str, parent: str = "") -> list[dict]:
        rows = [{
            "check_name": "candidate_blueprint",
            "label": cluster.name,
            # "reading", not "perspective" — RULING-WHAT-A-VERDICT-IS-ABOUT.md
            # §0: this is Component.perspective (physical/deployment/logical/
            # dev), a different axis from the chrome's own Perspective chips.
            "summary": (f"{cluster.size} component(s), {perspective} reading"
                        + (f", nested under {parent}" if parent else "")
                        + (" — OVER the ~10 goal and no further declared structure"
                           if cluster.oversized else "")),
            # Not a confidence claim about the architecture: a cluster is a
            # proposal that these components belong together, and its members
            # carry their own confidence. See the same note on the diagram.
            "confidence": 0,
            "detail": {
                "name": cluster.name,
                "perspective": perspective,
                "signal": cluster.signal,
                "carrier": cluster.carrier,
                "composed_into": cluster.composed_into,
                "size": cluster.size,
                "members": sorted(cluster.members),
                "children": [c.name for c in cluster.children],
                "parent": parent,
                "oversized": cluster.oversized,
                "target_size": clustering_target(),
                "run_scope": run_scope,
                "not_a_claim": True,
            },
        }]
        for child in cluster.children:
            rows.extend(rows_for(child, perspective, cluster.name))
        return rows

    findings: list[dict] = []
    for perspective, clusters in sorted(clusters_by_perspective.items()):
        for cluster in clusters:
            findings.extend(rows_for(cluster, perspective))

    # A perspective whose clustering raised. Written as its own check_name so a
    # reader can tell "no blueprints because nothing grouped" from "no
    # blueprints because the attempt failed" — the distinction the
    # no-silent-success ratchet exists to preserve.
    for perspective, message in sorted(errors.items()):
        findings.append({
            "check_name": "clustering_failed",
            "label": perspective,
            "summary": f"clustering raised for the {perspective} perspective: {message}",
            "confidence": 0,
            "detail": {"perspective": perspective, "error": message,
                       "run_scope": run_scope},
        })
    if findings:
        registry.upsert_finding(slug, BLUEPRINT_KIND, findings,
                                surveyed_at=surveyed_at, scope_locator="")


def clustering_target() -> int:
    from . import clustering
    return clustering.TARGET_CLUSTER_SIZE


def _persist_diagram(registry, slug: str, components: list[Component],
                     ports: list[dict], wires: list[dict],
                     surveyed_at: str, run_scope: str, run_label: str) -> None:
    """Render the proposal as Mermaid and store it, once per run.

    **Why this is written at analysis time rather than at read time.** Under
    *report, then curate* (`docs/architecture-recovery-report-then-curate.md`)
    the diagram is what a curator reads to decide whether the recovered
    architecture is a good enough fit to materialise. It therefore has to be a
    record of what THIS run proposed, captured beside the evidence it was drawn
    from — not something re-derived later from an IR that has since moved. When
    Phase 2 publishes proposals to Egeria, this value is what goes into the
    annotation; pyegeria's own `MERMAID` output cannot produce it, because a
    proposal's components are not Egeria elements yet.

    Not written when there are no components: a diagram of nothing tells a
    curator nothing, and publishing one would imply a proposal exists where the
    run in fact found none. The component-count metric above already records
    that outcome, and it is the row designed to carry it.

    `run_label` distinguishes WHICH perspective this diagram renders —
    `repo_arch_detect` and `repo_arch_coupling` both call this (via
    `persist_ir`) with their own, genuinely different component sets
    (structural/deployment detection with ports+wires, vs. import/co-change
    clustered boundaries with neither), and until 2026-09-08 both wrote under
    the identical `check_name="architecture_diagram"` with no way to tell
    them apart — `upsert_finding` is insert-only, so the rows just
    accumulated, and a reader taking "the latest" got whichever step
    happened to run last, silently discarding the other's diagram. Found live
    against egeria-workspaces_git: the chat-typed answer and the
    clicked-question answer disagreed on component counts (85 vs. 12) because
    they were, without either path or the project owner knowing it, reading
    two different diagrams. Stamped into `check_name` (not `detail`) so it is
    queryable per check_registry.yaml's own convention, matching the
    `{run_label}_component_count` metric name this same run already writes
    above.
    """
    if not components:
        return
    from . import mermaid

    ir = IR(target=slug, checkout="", components=list(components),
            ports=list(ports), wires=list(wires))
    try:
        diagram = mermaid.render(ir)
        caption = mermaid.caption(ir)
    except Exception as exc:
        # A rendering failure must not lose the run's findings — everything
        # above is already written by this point, and the diagram is a view of
        # it, not the thing itself.
        log.warning("architecture diagram not rendered for %s: %s", slug, exc)
        return

    registry.upsert_finding(
        slug, DIAGRAM_KIND,
        [{
            "check_name": run_label,
            "label": f"Diagram ({run_label})",
            "summary": caption,
            # Not a confidence claim. The diagram asserts nothing of its own —
            # it renders claims that each carry their own confidence, which is
            # drawn on the nodes. 100 here would read as certainty about the
            # architecture; anything lower would read as doubt about the
            # drawing. The value is meaningless either way, so the honest
            # reading is in `summary` and on the nodes themselves.
            "confidence": 0,
            "detail": {
                "format": "mermaid",
                "mermaid": diagram,
                "projection_depth": mermaid.projection.DEFAULT_PROJECTION_DEPTH,
                "run_scope": run_scope,
                "not_a_claim": True,
                # Stored regardless, but labelled: a consumer must be able to
                # find out that this will not draw without discovering it at
                # the moment a curator opens it. Not truncated — a silently
                # shortened architecture is a different architecture.
                "char_count": len(diagram),
                "exceeds_renderer_limit": mermaid.exceeds_renderer_limit(diagram),
            },
        }],
        surveyed_at=surveyed_at, scope_locator="",
    )


def _persist_interfaces(registry, slug: str, surveyed_at: str,
                        ports: list[dict], wires: list[dict],
                        name_to_scope: dict[str, str]) -> None:
    """Ports and wires as findings (design §5.5f, §3.2/§3.3).

    **Ports** are a property of one component, so they key on that component's
    `scope_locator` like every other component-scoped finding.

    **Wires are edges**, which the component-keyed finding shape does not
    naturally fit — and rather than invent a shape for a view that does not
    exist yet, each wire is attributed to its **source** component. That is not
    an arbitrary tie-break: a compose `depends_on` is *declared by* the
    depending service, so the source is where the evidence actually lives. The
    target travels in `detail`, so an edge list is recoverable without having
    committed to an edge table before anyone knows what reads it.
    """
    findings: list[dict] = []
    for p in ports:
        findings.append({
            "check_name": f"port:{p.get('name')}",
            "label": p.get("direction") or "Unknown",
            "summary": f"{p.get('component')} — {p.get('detail', '')}",
            "confidence": 70,
            # `additionalProperties` carries `operationCount` (finding 100).
            # Built explicitly rather than by spreading the port dict, so a new
            # key is a deliberate act — but that also means a key added upstream
            # and NOT added here is silently dropped at persistence, which is
            # exactly what happened to operationCount between finding 100 and
            # this line. Same shape as finding 89: the capability existed and
            # nothing wired it to storage.
            "detail": {"component": p.get("component"), "port": p.get("name"),
                       "direction": p.get("direction"), "protocol": p.get("protocol", ""),
                       "additionalProperties": p.get("additionalProperties") or {},
                       "evidence": p.get("evidence"), "kind": "port"},
        })
    for w in wires:
        findings.append({
            "check_name": f"wire:{w.get('target')}",
            "label": w.get("integrationStyle") or "declared-dependency",
            "summary": w.get("label") or f"{w.get('source')} -> {w.get('target')}",
            "confidence": 70,
            "detail": {"source": w.get("source"), "target": w.get("target"),
                       "protocol": w.get("protocol", ""), "oneWay": w.get("oneWay"),
                       "integrationStyle": w.get("integrationStyle", ""),
                       "frequency": w.get("frequency", ""),
                       "dataExchanged": w.get("dataExchanged", ""),
                       "evidence": w.get("evidence"), "kind": "wire"},
        })
    if not findings:
        return
    registry.upsert_finding(slug, "architecture_interfaces", findings,
                            surveyed_at=surveyed_at)
