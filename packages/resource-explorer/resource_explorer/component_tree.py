"""Component review at the branch, not in a queue.

The designer's ports round (2026-09-14): at recovery scale a flat
per-component queue fails -- kafka recovers 609 components at a median node
size of 1 and has zero verdicts -- and no human-accepted component is
cohesive under any bar: curators accept named packages. Six hundred
decisions is an abandonment; twenty named branches is a morning. So rows
are branches of the path the components are already keyed by, a branch
verdict inherits and a component's own verdict wins, and none of it needs
a schema change: verdicts are per scope_locator and append-only, so a
branch verdict is one row at the branch's scope and the reader resolves
the longest prefix.

Ports are a column on the component, not a screen: on most repositories
there are almost none (Prometheus, one of the better cases, has one port
and no wires), and an absence that holds for every resource is a bug
report about the reader. Ports get no verdict -- a port is a line in a
Dockerfile, not a proposal about the world. Components are proposals,
ports are readings.

Ports and wires are READ from the repository's deployment artifacts at
survey time; acceptance changes which get drawn. Nothing here claims
otherwise.
"""
from __future__ import annotations

import json
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar

from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.arch_recovery import scope_hierarchy

LOW_CONFIDENCE = 50


#: Recovery keys a few components on non-paths -- the repository root as '',
#: '.', or '*' -- which are not branches anyone can name.
_NOT_A_PATH = {"", ".", "*"}


#: An `_architecture_recovery_results(max_depth=None)` the caller on this context already built and keeps (for
#: its `blueprints`), so `_components` does not build it again. A context variable, not a parameter, because
#: `_components(registry, slug)` is replaced by two-argument stand-ins in many tests.
_shared_recovery_results: ContextVar[dict | None] = ContextVar("_shared_recovery_results", default=None)


@contextmanager
def sharing_recovery_results(results: dict):
    token = _shared_recovery_results.set(results)
    try:
        yield
    finally:
        _shared_recovery_results.reset(token)


def _components(registry: ProjectRegistry, slug: str) -> list[dict]:
    from resource_explorer.surveyors.repo_survey_definition_adapter import _architecture_recovery_results
    shared = _shared_recovery_results.get()
    res = (shared if shared is not None else _architecture_recovery_results(registry, slug, max_depth=None)) or {}
    comps = [c for c in (res.get("components") or []) if (c.get("path") or "") not in _NOT_A_PATH]
    # A person's reclassification moves a node between the components and the runtime dependencies at once
    # (DESIGN-BLUEPRINT-NODE-ADMISSION.md), without waiting for the next survey.
    from resource_explorer import node_admission
    return node_admission.apply_to_tree_components(registry, slug, comps)


def _assign_ports(comps: list[dict], ports_by_service: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """{component path: [ports]}. Ports are keyed by the deployment SERVICE
    name the artifact declares (a compose service, a Dockerfile's image);
    the diagram gives each port exactly one owner. Here: the components
    whose name or last path segment is the service name, the most specific
    path winning, so the column agrees with the drawing and 71 ports are
    counted 71 times."""
    by_key: dict[str, list[dict]] = {}
    for c in comps:
        for k in {str(c.get("name") or ""), str(c.get("path") or "").rsplit("/", 1)[-1]}:
            if k:
                by_key.setdefault(k, []).append(c)
    out: dict[str, list[dict]] = {}
    for service, plist in ports_by_service.items():
        owners = by_key.get(service) or []
        if not owners:
            continue
        owner = max(owners, key=lambda c: len(c.get("path") or ""))
        out.setdefault(owner["path"], []).extend(plist)
    return out


def _ports_by_component(registry: ProjectRegistry, slug: str) -> tuple[dict[str, list[dict]], int, int]:
    """{component path: [ports]}, total ports, total wires -- from the
    architecture_interfaces findings, as the deployment reader reads them."""
    ports: dict[str, list[dict]] = {}
    n_ports = n_wires = 0
    for r in registry.query_findings(slug, "architecture_interfaces"):
        try:
            d = json.loads(r.get("detail_json") or "{}") if not isinstance(r.get("detail"), dict) else r["detail"]
        except ValueError:
            d = {}
        if d.get("kind") == "port":
            n_ports += 1
            ports.setdefault(str(d.get("component") or ""), []).append(
                {"name": d.get("port", ""), "direction": d.get("direction", ""), "protocol": d.get("protocol", "")})
        elif d.get("kind") == "wire":
            n_wires += 1
    return ports, n_ports, n_wires


#: A verdict row recorded for "this component only" (owner, 2026-10-09: he wanted a parent alone and accepted
#: two children by accident). It is kept in the row's `retyped_to` (unused by an accepted/rejected verdict; no
#: new column) and it does not pass down to the components under it.
ONLY_THIS = "__only__"      # no component type can be spelled this, so it is never a real retype


def resolve_verdict(path: str, verdicts: dict[str, dict]) -> dict | None:
    """The verdict that applies to `path`: its own row if there is one, else
    the nearest ancestor branch's, longest prefix first. An inherited verdict
    says so rather than posing as a decision someone made about that file.
    An ancestor's "this component only" row (ONLY_THIS) is not inherited."""
    own = verdicts.get(path)
    if own:
        return {"verdict": own["verdict"], "inherited_from": "", "only": own.get("retyped_to") == ONLY_THIS,
                "note": own.get("note", ""),
                "decided_by": own.get("user_id") or own.get("decided_by") or "", "at": own.get("created_at", "")}
    parts = path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        anc = "/".join(parts[:i])
        row = verdicts.get(anc) or verdicts.get(anc + "/")
        if row and row.get("retyped_to") != ONLY_THIS:
            return {"verdict": row["verdict"], "inherited_from": anc, "note": row.get("note", ""),
                    "decided_by": row.get("user_id") or row.get("decided_by") or "", "at": row.get("created_at", "")}
    return None


def _component_verdicts(registry: ProjectRegistry, slug: str) -> dict[str, dict]:
    return {k: v for k, v in registry.get_component_verdicts("repo", slug).items()
            if v.get("verdict_target", "component") == "component"}


def _total_and_accepted(comps: list[dict], verdicts: dict[str, dict]) -> tuple[int, int]:
    """(components, accepted components) -- the two counts `component_tree` reports as
    `total_components` / `accepted`, defined once so the tree and `totals` cannot drift."""
    total = sum(1 for c in comps if not c.get("structural"))
    accepted = sum(1 for c in comps if not c.get("structural") and (resolve_verdict(c["path"], verdicts) or {}).get("verdict") == "accepted")
    return total, accepted


def totals(registry: ProjectRegistry, slug: str) -> dict:
    """`{"total_components", "accepted"}` exactly as `component_tree(registry, slug)` reports them, without
    building the branches, the port assignment or the topology sentence -- for a caller (the catalogue
    depth offer) that wants only the two counts and used to rebuild the whole tree for them."""
    comps = [c for c in _components(registry, slug) if c.get("path")]
    total, accepted = _total_and_accepted(comps, _component_verdicts(registry, slug))
    return {"total_components": total, "accepted": accepted}


def component_tree(registry: ProjectRegistry, slug: str, prefix: str = "") -> dict:
    """The branches directly under `prefix` ('' = the root), each with what a
    curator needs before opening it: how many components, their type mix,
    how many sit at or below 50% confidence, their declared ports, and the
    verdict that applies -- own or inherited. Leaves (components whose path
    IS the branch) come with their branch. A grouping node -- a directory
    that holds components but is not one -- is marked with the classic
    UI's own words."""
    comps = [c for c in _components(registry, slug) if c.get("path")]
    verdicts = _component_verdicts(registry, slug)
    by_service, n_ports, n_wires = _ports_by_component(registry, slug)
    ports = _assign_ports(comps, by_service)
    pre = prefix.rstrip("/")
    under = [c for c in comps if (c["path"] == pre or c["path"].startswith(pre + "/"))] if pre else comps

    branches: dict[str, dict] = {}
    for c in under:
        rel = c["path"][len(pre) + 1:] if pre else c["path"]
        if not rel:
            continue                        # the prefix's own component is the caller's row
        seg = rel.split("/", 1)[0]
        bpath = f"{pre}/{seg}" if pre else seg
        b = branches.setdefault(bpath, {"path": bpath, "name": seg, "components": 0, "low_confidence": 0,
                                        "types": Counter(), "ports": 0, "own": None, "children": 0,
                                        "accepted": 0, "rejected": 0, "undecided": 0, "structural": False,
                                        "in_egeria": 0, "rejected_in_egeria": 0,
                                        "min_confidence": None, "agreement_count": 0})
        if c["path"] == bpath:
            b["own"] = c
            b["structural"] = bool(c.get("structural"))
            if c.get("structural"):
                continue
        if c.get("structural"):
            continue
        b["components"] += 1
        if c["path"] != bpath:
            b["children"] += 1
        conf = c.get("confidence") or 0
        if conf <= LOW_CONFIDENCE:
            b["low_confidence"] += 1
        b["min_confidence"] = conf if b["min_confidence"] is None else min(b["min_confidence"], conf)
        # RULING-WHAT-A-VERDICT-IS-ABOUT.md §2b: two extractors landing on the
        # same path is the strongest signal the recovery has — stronger than
        # one extractor's high confidence — so it has to be sortable at the
        # branch level, not just visible on the leaf row.
        if c.get("agreement"):
            b["agreement_count"] += 1
        if c.get("type"):
            b["types"][c["type"]] += 1
        b["ports"] += len(ports.get(c["path"], []))
        v = resolve_verdict(c["path"], verdicts)
        if not v:
            b["undecided"] += 1
        elif v["verdict"] == "accepted":
            b["accepted"] += 1
            b["in_egeria"] += 1 if c.get("materialized") else 0       # from the element's cache row
        elif v["verdict"] == "rejected":
            b["rejected"] += 1
            b["rejected_in_egeria"] += 1 if c.get("materialized") else 0      # a reject deletes nothing
        else:
            b["undecided"] += 1

    out = []
    for b in sorted(branches.values(), key=lambda x: (-x["components"], x["path"])):
        own = b.pop("own")
        b["types"] = dict(b["types"].most_common(4))
        b["verdict"] = resolve_verdict(b["path"], verdicts)
        b["grouping_only"] = own is None or bool(b["structural"])
        b["type"] = (own or {}).get("type") or ""
        b["confidence"] = (own or {}).get("confidence") if own else None
        b["own_ports"] = ports.get(b["path"], [])
        out.append(b)

    owned = sum(len(v) for v in ports.values())
    total, accepted = _total_and_accepted(comps, verdicts)
    return {
        "slug": slug, "prefix": pre, "branches": out,
        "total_components": total, "accepted": accepted,
        "reviewed": sum(1 for c in comps if not c.get("structural") and verdicts.get(c["path"])),
        "ports": n_ports, "wires": n_wires,
        # The foot's two ends: what was declared, and what could not be
        # attributed to any shown component -- counted apart, never attached
        # to a guess (the diagram's caption rule, one surface out).
        "ports_owned": owned, "ports_unowned": n_ports - owned,
        "components_with_ports": sum(1 for v in ports.values() if v),
        "topology": topology_sentence(registry, slug, n_ports, n_wires),
        "topology_totals": totals_sentence(n_ports, n_wires, sum(1 for v in ports.values() if v), n_ports - owned),
    }


def totals_sentence(n_ports: int, n_wires: int, n_components: int, unowned: int) -> str:
    """'71 ports across 9 components · 3 not attributable to any shown
    component, counted apart.' With 71 in play, a reader has to see the
    total without adding up a column. '' when there is no topology."""
    if not n_ports and not n_wires:
        return ""
    head = f"{n_ports:,} port{'s' if n_ports != 1 else ''} across {n_components:,} component{'s' if n_components != 1 else ''}"
    if n_wires:
        head += f" · {n_wires:,} wire{'s' if n_wires != 1 else ''}"
    if unowned:
        head += f" · {unowned:,} not attributable to any shown component, counted apart"
    return head + "."


def topology_sentence(registry: ProjectRegistry, slug: str, n_ports: int, n_wires: int) -> str:
    """One sentence at the foot of the tree when a repository declares
    nothing, saying what it looked in -- so a reader can tell nothing
    declared from nobody looked. '' when there is a topology."""
    if n_ports or n_wires:
        return ""
    project = registry.get(slug)
    name = (project.display_name if project else slug) or slug
    return (f"No declared topology — {name} declares no ports and no wires. Ports are read from "
            f"Dockerfiles, compose files and API documents; this repository has none of those with an "
            f"exposed interface.")


def leaves(registry: ProjectRegistry, slug: str, branch: str) -> list[dict]:
    """The components under a branch, each with its resolved verdict and
    its declared ports -- what expands under a row. The empty branch is the
    root, as `component_tree(prefix='')` already treats it: every component,
    so a search can look across the whole repository through the same rows
    a branch shows."""
    comps = _components(registry, slug)
    verdicts = {k: v for k, v in registry.get_component_verdicts("repo", slug).items()
                if v.get("verdict_target", "component") == "component"}
    by_service, _, _ = _ports_by_component(registry, slug)
    ports = _assign_ports(comps, by_service)
    from resource_explorer.workflows.curate import NODE_PROMOTION_COMPONENT, promotion_by_scope
    promos = promotion_by_scope(registry, slug, NODE_PROMOTION_COMPONENT)
    b = branch.rstrip("/")
    real = sorted((c for c in comps if c.get("path") and not c.get("structural")), key=lambda x: x["path"])
    accepted_paths = {x["path"] for x in real if ((resolve_verdict(x["path"], verdicts) or {}).get("verdict")) == "accepted"}
    out = []
    for c in sorted(comps, key=lambda x: x.get("path", "")):
        p = c.get("path", "")
        if not p or not (not b or p == b or p.startswith(b + "/")) or c.get("structural"):
            continue
        # What a verdict on this row reaches with its children (the same counts a branch row carries): a leaf
        # that is itself a component with components beneath it gets the "this component only / with its N
        # children" choice, so its own verdict does not silently reach them.
        reach = [x for x in real if x["path"] == p or x["path"].startswith(p + "/")]
        out.append({"path": p, "name": c.get("name") or p, "type": c.get("type") or "",
                    "children": len(reach) - 1,
                    "reach_low": sum(1 for x in reach if (x.get("confidence") or 0) <= LOW_CONFIDENCE),
                    "reach_accepted": sum(1 for x in reach if x["path"] in accepted_paths),
                    "confidence": c.get("confidence"), "low_confidence": (c.get("confidence") or 0) <= LOW_CONFIDENCE,
                    "proposed_by": c.get("proposed_by") or [], "ports": ports.get(p, []),
                    "verdict": resolve_verdict(p, verdicts), "materialized": bool(c.get("materialized")),
                    # §2a/§2b/§2c: one card entry per extractor CURRENTLY
                    # proposing this path (both, when both do), whether two
                    # of them agree, and which extractor used to propose this
                    # path and no longer does — a flag on an accepted verdict,
                    # never an invalidation of it (the verdict itself is
                    # untouched; see resolve_verdict above).
                    "proposals": c.get("proposals") or [],
                    "agreement": bool(c.get("agreement")),
                    "withdrawn_by": c.get("withdrawn_by") or [],
                    # Whose boundary this is: the class, the short "from <file>" / "image <name>" sentence,
                    # and the person's reclassification with its reason when there is one.
                    "admission": c.get("admission") or "built_here",
                    "admission_evidence": c.get("admission_evidence") or "",
                    "reclassified": c.get("reclassified"),
                    "reclassification_note": c.get("reclassification_note") or "",
                    # What accepting did to the element's zones, from the promotion's proof row.
                    "promotion": promos.get(p)})
    return out


def _verdict_counts(rows: list[dict]) -> tuple[int, int, int]:
    """(accepted, rejected, undecided) over a list of leaf dicts, as `leaves()`
    already shapes them (each row's `verdict` is `resolve_verdict`'s own
    resolved shape or `None`)."""
    accepted = rejected = undecided = 0
    for row in rows:
        v = (row.get("verdict") or {}).get("verdict")
        if v == "accepted":
            accepted += 1
        elif v == "rejected":
            rejected += 1
        else:
            undecided += 1
    return accepted, rejected, undecided


def group_leaves(leaf_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(groups, ungrouped)` for a branch's flat leaf list (2026-09-17, the
    Curate tree restructuring).

    The designer's finding: a branch like `packages/` can hold 64 of a
    repository's ~69 components as one flat list, which is legible at the
    branch level but not at the leaf level -- and the missing middle level
    already exists. `scope_hierarchy.derive()` (`surveyors/arch_recovery/
    scope_hierarchy.py`) computes exactly this grouping already, from the
    same scope-locator paths `leaves()` returns here -- it is the same
    function `clustering.py`'s `propose()` calls (its `parents, _structural
    = scope_hierarchy.derive(scopes)` first pass, `clustering.py` ~line 497)
    to build the candidate-blueprint clusters shown as "scope-hierarchy ·
    collection" rows in the blueprints panel. Recomputed here directly over
    the branch's own leaf paths rather than joined from the persisted
    `candidate_blueprint` findings, because those are scoped to one
    perspective at a time (RULING-WHAT-A-VERDICT-IS-ABOUT.md §0) while this
    branch's leaves mix every perspective -- same algorithm, same input
    shape (a flat set of scope locators), applied directly so no perspective
    has to be chosen or components silently dropped for lacking one.

    `derive()`'s own `MIN_GROUP = 2` rule is inherited unchanged: a "group"
    of one component collapses nothing, so it is not a group -- it comes
    back in `ungrouped` and renders as a plain row, exactly like a
    component whose branch has no further structure today.

    A first-pass group left over `TARGET_CLUSTER_SIZE` (10 -- the designer's
    "the size the clustering was tuned for") is re-derived within its own
    members, the same way `clustering.py`'s `_subdivide()` does for an
    oversized blueprint cluster: re-running `derive()` on a *subset* can
    surface a deeper level the full-branch pass had no reason to look for
    (nothing groups it against the branch's OTHER members). A subset that
    still finds no further structure is left as one oversized group rather
    than truncated -- same "no signal, no cluster" contract as the
    blueprints panel.
    """
    from resource_explorer.surveyors.arch_recovery.clustering import TARGET_CLUSTER_SIZE

    by_path = {row["path"]: row for row in leaf_rows}
    parents, _structural = scope_hierarchy.derive(sorted(by_path))
    first_pass: dict[str, list[str]] = {}
    ungrouped: list[dict] = []
    for path, row in by_path.items():
        parent = parents.get(path)
        if parent:
            first_pass.setdefault(parent, []).append(path)
        else:
            ungrouped.append(row)

    def _split(name: str, member_paths: list[str], depth_left: int) -> list[tuple[str, list[str]]]:
        if len(member_paths) <= TARGET_CLUSTER_SIZE or depth_left <= 0:
            return [(name, member_paths)]
        sub_parents, _ = scope_hierarchy.derive(sorted(set(member_paths)))
        if not sub_parents:
            return [(name, member_paths)]                    # no deeper structure to find
        sub: dict[str, list[str]] = {}
        for p in member_paths:
            sub.setdefault(sub_parents.get(p, p), []).append(p)
        if len(sub) <= 1:
            return [(name, member_paths)]                     # a "split" into one bucket splits nothing
        out: list[tuple[str, list[str]]] = []
        for sub_name, sub_paths in sorted(sub.items()):
            out.extend(_split(sub_name, sub_paths, depth_left - 1))
        return out

    groups = []
    for name, member_paths in sorted(first_pass.items()):
        for group_name, group_paths in _split(name, member_paths, depth_left=3):
            members = [by_path[p] for p in group_paths]
            accepted, rejected, undecided = _verdict_counts(members)
            groups.append({"name": group_name, "members": members,
                           "accepted": accepted, "rejected": rejected, "undecided": undecided})
    return groups, ungrouped


FINDER_CAP = 5000


def finder_rows(registry: ProjectRegistry, slug: str, limit: int = FINDER_CAP) -> dict:
    """What a component search needs and nothing more: path, name, type and the readings each component was
    proposed in ('' = none recorded). No verdicts, ports or grouping. `total` is the whole count, from the same
    population as `component_tree`'s `total_components`; `truncated` is true when `limit` cut the list, so a
    caller can say "first N of M" instead of searching a silent subset."""
    comps = sorted((c for c in _components(registry, slug) if c.get("path") and not c.get("structural")),
                   key=lambda c: c["path"])
    total = len(comps)
    rows = []
    for c in comps[: max(0, limit)]:
        readings = sorted({(p.get("perspective") or "physical") for p in (c.get("proposals") or [])}) or [""]
        rows.append({"path": c["path"], "name": c.get("name") or c["path"], "type": c.get("type") or "",
                     "readings": readings})
    return {"branch": "", "leaves": rows, "total": total, "shown": len(rows), "truncated": len(rows) < total,
            "limit": limit}
