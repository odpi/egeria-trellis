"""The layer-2 catalogue-depth offer — DepthOffer's three rules, applied to
component materialization instead of never-run analyses (owner's ruling,
2026-09-15, on the designer's REPLY-CATALOGUE-IN-LAYERS.md §3).

Layer 1 is the catalogue commit itself (`workflows/curate_commit.py`) — the
repository's own capability, published. Layer 2 is promoting the ACCEPTED
architecture-recovery verdicts into real Egeria `SolutionComponent`s
(`surveyors/arch_recovery/materializer.py`). This module answers "should the
pane offer that next step, and what would it cost" — never whether to do it;
the offer is read-only, same as `build_depth_offer`.

DepthOffer's three rules, verbatim:

- **Not a nag** — offered once per catalogue record, in the pane, never a
  modal (`Curations.record_layer2_offer` refuses a second write, the same
  shape `registry.record_depth_offer` already refuses).
- **Not a gate** — layer 1 is already recorded when this offer appears;
  nothing here waits on an answer, and declining changes nothing about what
  was already committed.
- **Not a scold** — "N components recovered, M not catalogued" is a fact
  about the record, not an instruction. This module states the fact; the
  imperative voice belongs to nobody.

**The price has a basis DepthOffer's sibling in curate_plan.py's bulk-accept
dialog does not (yet)**: `egeria_call_timings` (added alongside this module,
owner's ruling the same day) records real per-write Egeria timing, keyed on
`call_name`/`kind` — `create_solution_component`/`write` is exactly the write
this offer prices. Until enough of those rows exist, the price is the SAME
honest fallback the bulk-accept dialog already uses ("not yet measured — the
first materialization is what fixes it") — never a declared or invented
number standing in for a real measurement, the same rule `RunCost.basis`
enforces for analysis runs.

Kept pure and registry-only (no FastAPI import), same reasoning as
`depth_offer.py`'s own docstring gives for the identical choice.
"""
from __future__ import annotations

from resource_explorer.workflows.analysis import _humanise_duration

#: Same vocabulary as DepthOffer's (`depth_offer.DEPTH_OFFER_OUTCOMES`) --
#: `accepted`/`chose` both mean "materialized something", `declined` means
#: the pane's "Not now". A separate set, not a shared import, because the
#: two offers are recorded on different tables or a schema change on one
#: doing sympathy work on the other would go unnoticed by the type checker.
LAYER2_OFFER_OUTCOMES = {"declined", "accepted", "chose"}

#: The Egeria call this offer prices. A single name, not a list — component
#: materialization writes exactly one kind of element today (materializer.py
#: creates a SolutionComponent, alone; see that module's own "Scope,
#: deliberately narrow" docstring section for why nothing else is written).
_PRICED_CALL = "create_solution_component"


def build_catalogue_depth_offer(registry, slug: str) -> dict:
    """The GET /api/projects/{slug}/catalogue-depth-offer payload.

    Raises `LookupError` for an unknown slug, mirroring `build_depth_offer`'s
    own 404 shape (caught and translated in the route, kept out of this pure
    function so it stays FastAPI-free)."""
    from resource_explorer.component_tree import totals as component_totals
    from resource_explorer.curate_plan import Curations

    project = registry.get(slug)
    if not project:
        raise LookupError(f"Project {slug!r} not found")

    curations = Curations(registry).for_resource("repo", slug)
    # `for_resource` orders by requested_at DESC, so the first "catalogue"
    # kind record (a report has no layer-2 act; `kind` distinguishes them)
    # is the latest one -- the record this offer would attach to.
    latest = next((c for c in curations if c.get("kind", "catalogue") == "catalogue"), None)
    layer1_done = bool(latest and latest.get("state") == "done")

    # Only the two counts: the whole tree (branches, ports, topology) was being built for them.
    tree = component_totals(registry, slug)
    remaining = max(0, tree.get("total_components", 0) - tree.get("accepted", 0))

    already_decided = bool(latest and latest.get("layer2_offer")) if latest else False

    stats = registry.read_egeria_call_timing_stats(_PRICED_CALL, "write")
    if stats["count"] and remaining:
        total_seconds = stats["median"] * remaining
        p90_clause = f", p90 {stats['p90']:.1f}s" if stats["p90"] is not None else ""
        sentence = (f"about {_humanise_duration(total_seconds)} of Egeria writes "
                    f"(measured, {stats['median']:.1f}s median{p90_clause})")
        cost = {"seconds": total_seconds, "basis": "measured",
                "median": stats["median"], "p90": stats["p90"], "sentence": sentence}
    else:
        cost = {
            "seconds": None, "basis": "unknown", "median": None, "p90": None,
            "sentence": "not yet measured — the first materialization is what fixes it",
        }

    return {
        "slug": slug,
        "curation_id": latest["id"] if latest else None,
        "layer1_done": layer1_done,
        "already_decided": already_decided,
        # Not a scold: a fact about the record. "N components recovered,
        # M not catalogued" is exactly this shape, composed by the caller
        # (the /next pane) from these two counts rather than as a sentence
        # here, so the wording stays with the surface that reads it.
        "total_components": tree.get("total_components", 0),
        "accepted": tree.get("accepted", 0),
        "remaining_components": remaining,
        "cost": cost,
    }
