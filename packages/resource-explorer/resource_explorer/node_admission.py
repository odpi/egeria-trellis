"""What admits a node to a blueprint, as RE records and shows it (DESIGN-BLUEPRINT-NODE-ADMISSION.md).

The detectors tag every component with an evidence class (`surveyors/arch_recovery/admission.py`):
built here, shipped here, or referenced only. This module is the registry side:

* `reclassify`: a person may say a node is built here after all, or only referenced, WITH A REASON. The
  trail is append-only in `app_settings`, ONE KEY PER ENTRY (`repo_node_reclassifications::<slug>::<time>-<id>`,
  written once, never updated; the latest entry per scope wins), no schema change, and it is read both at survey time (so the next survey
  honours it) and at read time (so the move shows at once).
* `referenced_rows`: the referenced-only services that are runtime dependencies, with the person's
  reclassifications applied. `dependency_table` lists them as kind `runtime`.
* `summary`: what was admitted by class, what was found and not admitted (the blueprint manifest's
  lines), and the zero-component sentence.
* `environment`: the Environment Deployment Blueprint's nodes: services that are other projects'
  deployment units, each linked to the repository RE knows to build the image.

Nothing here contacts Egeria.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone

from resource_explorer.surveyors.arch_recovery import admission as adm

log = logging.getLogger(__name__)

ADMISSION_KIND = "architecture_admission"
#: A person can move a node to either side of the line; "shipped here" is evidence, not a decision.
RECLASSIFY_TO = (adm.BUILT, adm.REFERENCED)


MAX_REASON = 500
MAX_SCOPE = 1024


def _prefix(slug: str) -> str:
    # The slug is encoded so one with colons in it cannot reach into another slug's entries.
    return f"repo_node_reclassifications::{slug.replace('%', '%25').replace(':', '%3A')}::"


def _valid(e) -> bool:
    return (isinstance(e, dict) and e.get("to") in RECLASSIFY_TO
            and all(isinstance(e.get(k), str) and e.get(k) for k in ("scope", "reason", "by", "at")))


def _read_trails(registry, slug: str) -> tuple[dict, int]:
    """({scope: [{to, reason, by, at}, ...]} oldest first, how many stored entries were unreadable).

    Each entry is its OWN setting (`repo_node_reclassifications::<slug>::<time>-<id>`), written once and
    never updated, so two people reclassifying at the same moment cannot lose each other's entry and a
    damaged entry cannot take the history with it. An unreadable or malformed entry is skipped with a
    logged warning and counted, never silently."""
    out: dict[str, list[dict]] = {}
    bad: list[str] = []
    for key, raw in registry.list_settings_with_prefix(_prefix(slug)):
        try:
            e = json.loads(raw)
        except (ValueError, TypeError):
            e = None
        if not _valid(e):
            bad.append(key)
            continue
        out.setdefault(e["scope"], []).append(
            {k: e[k] for k in ("to", "reason", "by", "at")} | ({"unit": e["unit"]} if isinstance(e.get("unit"), str) else {}))
    if bad:
        log.warning("%d unreadable node reclassification(s) for %s ignored: %s", len(bad), slug, ", ".join(bad[:5]))
    return out, len(bad)


def read_reclassifications(registry, slug: str) -> dict:
    return _read_trails(registry, slug)[0]


def unreadable_count(registry, slug: str) -> int:
    return _read_trails(registry, slug)[1]


def latest(reclass: dict, scope: str) -> dict | None:
    trail = reclass.get(scope) or []
    return trail[-1] if trail else None


def _unit_of(component: dict) -> str:
    return ((component.get("identity") or {}).get("deployment_context") or "")


def effective(reclass: dict, scope: str, node_unit: str, node_name: str = "") -> tuple[dict | None, str]:
    """(the entry that applies to this node, a sentence when one exists but does not). A reclassification
    belongs to the DIRECTORY (unit) its node was in when the person made it:

    * an entry WITH a directory applies only to a node in that directory (a node whose directory is not
      known never receives one);
    * an entry with NO directory (made before directories were stored) applies to a path-keyed node, and
      to a slug-keyed (compose) node only when its scope is the PLAIN slug (`<dir basename>::<name>`):
      a qualified slug exists only because several directories share the name, so which one the person
      meant cannot be known.

    Anything that does not apply is said on the row, never silently."""
    entry = latest(reclass, scope)
    if not entry:
        return None, ""
    eu = entry.get("unit") or ""
    if eu:
        if not node_unit:
            return None, f"reclassification belongs to {eu}; this node's directory is not known; not applied"
        if eu != node_unit:
            return None, f"reclassification belongs to {eu}; not applied"
        return entry, ""
    if "::" in scope:
        from resource_explorer.surveyors.arch_recovery.detectors import _slug
        import os
        if not node_unit:
            return None, ("reclassification has no directory recorded and this node's directory is not "
                          "known; not applied")
        if scope != _slug(os.path.basename(node_unit), node_name):
            return None, ("reclassification has no directory recorded and this name is shared by several "
                          "directories; not applied")
    return entry, ""


def _stored(registry, slug: str) -> dict:
    rows = registry.query_findings(slug, ADMISSION_KIND) or []
    for r in rows:
        if r.get("check_name") != "admission":
            continue
        d = r.get("detail")
        if not isinstance(d, dict):
            try:
                d = json.loads(r.get("detail_json") or "{}")
            except ValueError:
                d = {}
        return d or {}
    return {}


def _component_paths(registry, slug: str) -> dict[str, dict]:
    from resource_explorer.surveyors.repo_survey_definition_adapter import _architecture_recovery_results
    res = _architecture_recovery_results(registry, slug, max_depth=None) or {}
    return {c["path"]: c for c in (res.get("components") or []) if c.get("path")}


def component_paths(registry, slug: str) -> dict[str, dict]:
    """{path: component} from ONE read of the recovery results -- what a request that calls several readers
    here (`summary`, `environment`, `referenced_rows`) reads once and passes to each as `paths`."""
    return _component_paths(registry, slug)


def reclassify(registry, slug: str, scope: str, to: str, reason: str, by: str) -> dict:
    """Record that a person moved the node at `scope` to `to` ("built_here" or "referenced_only"), with the
    reason. The scope must be a node RE has: an admitted component or a referenced-only service."""
    if to not in RECLASSIFY_TO:
        raise ValueError(f"a node can be reclassified to {' or '.join(RECLASSIFY_TO)}, got {to!r}")
    if not (reason or "").strip():
        raise ValueError("a reclassification needs a reason")
    if len(reason.strip()) > MAX_REASON:
        raise ValueError(f"the reason is limited to {MAX_REASON} characters")
    if len(scope or "") > MAX_SCOPE:
        raise ValueError(f"the node's scope is limited to {MAX_SCOPE} characters")
    if not by:
        raise ValueError("a reclassification needs a person who made it")
    stored = _stored(registry, slug)
    units = {i["scope"]: i.get("unit", "") for i in stored.get("referenced", [])}
    units.update({p: _unit_of(c) for p, c in _component_paths(registry, slug).items()})
    if scope not in units:
        raise ValueError(f"not a node of this repository: {scope!r}")
    now = datetime.now(timezone.utc)
    entry = {"to": to, "reason": reason.strip(), "by": by, "at": now.isoformat(timespec="seconds")}
    # Its own key, written once: time_ns orders entries, the random suffix keeps two writers apart.
    key = f"{_prefix(slug)}{time.time_ns():020d}-{uuid.uuid4().hex[:8]}"
    registry.add_setting_once(key, json.dumps({**entry, "scope": scope, "unit": units[scope]}))
    entry["unit"] = units[scope]
    return entry


def apply_to_components(components: list, reclass: dict) -> None:
    """Survey time: honour the trail on freshly detected Components (mutates them)."""
    for c in components:
        from resource_explorer.surveyors.arch_recovery.persist import scope_locator_for
        entry, _note = effective(reclass, scope_locator_for(c), c.identity.deployment_context or "", c.name)
        if entry:
            c.admission = entry["to"]
            c.admission_evidence = f"reclassified by {entry['by']} · {entry['reason']}"


def referenced_rows(registry, slug: str, paths: dict | None = None) -> list[dict]:
    """The referenced-only services, reclassifications applied: a stored referenced-only service a person
    moved to built-here is gone from this list; an admitted component a person moved to referenced-only
    is on it."""
    reclass = read_reclassifications(registry, slug)
    rows = []
    for i in _stored(registry, slug).get("referenced", []):
        entry, note = effective(reclass, i["scope"], i.get("unit", ""), i.get("name", ""))
        if entry and entry["to"] == adm.BUILT:
            continue
        rows.append({**i, "reclassified": entry, "reclassification_note": note})
    have = {r["scope"] for r in rows}
    moved = [s for s, t in reclass.items() if t and t[-1]["to"] == adm.REFERENCED and s not in have]
    if moved:
        comps = paths if paths is not None else _component_paths(registry, slug)
        for scope in moved:
            c = comps.get(scope)
            if c is None:
                continue
            entry, note = effective(reclass, scope, _unit_of(c), c.get("name") or "")
            if entry is None or entry["to"] != adm.REFERENCED:
                continue
            rows.append({"scope": scope, "name": c.get("name") or scope, "slug": scope,
                         "image": c.get("image") or "", "evidence": c.get("admission_evidence") or "",
                         "unit": _unit_of(c), "reclassified": entry, "reclassification_note": ""})
    return rows


def apply_to_tree_components(registry, slug: str, comps: list[dict]) -> list[dict]:
    """Read time (component_tree): a node moved to referenced-only leaves the list; a referenced-only
    service moved to built-here joins it, shown as a node with its class and the reason. Every node
    carries `reclassified` when a person moved it."""
    reclass = read_reclassifications(registry, slug)
    if not reclass:
        return comps
    out = []
    for c in comps:
        entry, note = effective(reclass, c.get("path", ""), _unit_of(c), c.get("name") or "")
        if entry and entry["to"] == adm.REFERENCED:
            continue
        if entry:
            c = {**c, "reclassified": entry, "admission": entry["to"],
                 "admission_evidence": f"reclassified by {entry['by']} · {entry['reason']}"}
        elif note:
            c = {**c, "reclassification_note": note}
        out.append(c)
    present = {c.get("path") for c in out}
    for i in _stored(registry, slug).get("referenced", []):
        entry, _note = effective(reclass, i["scope"], i.get("unit", ""), i.get("name", ""))
        if entry and entry["to"] == adm.BUILT and i["scope"] not in present:
            out.append({"path": i["scope"], "name": i["name"], "type": "Third Party Process", "confidence": 60,
                        "perspective": "deployment", "proposed_by": [], "proposals": [], "agreement": False,
                        "withdrawn_by": [], "admission": adm.BUILT, "image": i.get("image", ""),
                        "admission_evidence": f"reclassified by {entry['by']} · {entry['reason']}",
                        "reclassified": entry})
    return out


def summary(registry, slug: str, paths: dict | None = None) -> dict:
    """What was admitted by class, what was found and not admitted, and the zero-component sentence."""
    stored = _stored(registry, slug)
    # `paths` is the caller's own read of the components ({path: component}); a request that already has
    # it passes it so the recovery results are not rebuilt for each reader.
    if paths is None:
        paths = _component_paths(registry, slug)
    comps = apply_to_tree_components(registry, slug, [
        c for c in paths.values()
        if not c.get("structural") and c.get("path") not in ("", ".", "*")])
    counts = {adm.BUILT: 0, adm.SHIPPED: 0}
    for c in comps:
        k = c.get("admission") or adm.BUILT
        if k in counts:
            counts[k] += 1
    refs = referenced_rows(registry, slug, paths)
    admitted = sum(counts.values())
    left_out = list(stored.get("left_out") or [])
    # The referenced-only line is derived from the rows as they stand now (a reclassification moves one),
    # not from the line written at survey time.
    left_out = [l for l in left_out if "referenced only" not in l]
    if refs:
        left_out.insert(0, f"{len(refs)} service{'' if len(refs) == 1 else 's'} referenced only · "
                           f"listed as runtime dependencies")
    # Entries that exist but do not apply: say which, never silently.
    reclass = read_reclassifications(registry, slug)
    units = {i["scope"]: (i.get("unit", ""), i.get("name", "")) for i in stored.get("referenced", [])}
    units.update({p: (_unit_of(c), c.get("name") or "") for p, c in paths.items()})
    for scope in sorted(reclass):
        if scope not in units:
            left_out.append(f"reclassification of {scope} no longer matches any node · not applied")
        else:
            _entry, note = effective(reclass, scope, *units[scope])
            if note:
                left_out.append(f"reclassification of {scope}: {note}")
    bad = unreadable_count(registry, slug)
    if bad:
        left_out.append(f"{bad} unreadable reclassification{'' if bad == 1 else 's'} · ignored, not applied")
    return {"admitted": admitted, "counts": counts, "referenced": len(refs), "left_out": left_out,
            "unreadable_reclassifications": bad,
            "sentence": adm.ZERO_COMPONENT_SENTENCE if (refs and not admitted) else "",
            "surveyed": bool(stored)}


def image_builders(registry, slug: str) -> dict[str, str]:
    """{normalised image name: the slug of another repository whose own survey recorded it among the images
    it builds or publishes}. The first repository to claim an image keeps it."""
    builders: dict[str, str] = {}
    for p in registry.list_all():
        if p.slug == slug:
            continue
        for img in _stored(registry, p.slug).get("published_images", []):
            builders.setdefault(img, p.slug)
    return builders


def builder_of(image: str, builders: dict[str, str]) -> str:
    """The repository that builds `image`, or '' when RE knows none (never guessed)."""
    for pub, owner in builders.items():
        if image and adm.same_image(image, pub):
            return owner
    return ""


def environment(registry, slug: str, paths: dict | None = None) -> dict:
    """The Environment Deployment Blueprint's nodes: each referenced-only service as another project's
    deployment unit, linked to the repository that builds its image when RE knows one (that repository's
    own survey recorded the image among those it builds or publishes). A link is a fact RE read from two
    surveys, carried as an annotation until Egeria has the type; no link is said as "builder not known"."""
    rows = referenced_rows(registry, slug, paths)
    builders = image_builders(registry, slug) if rows else {}
    nodes = []
    for r in rows:
        img = r.get("image") or ""
        builder = builder_of(img, builders)
        nodes.append({"name": r["name"], "image": img, "source": r.get("evidence", ""), "scope": r["scope"],
                      "built_by": builder,
                      "words": f"built by {builder}" if builder else "builder not known"})
    return {"nodes": nodes, "linked": sum(1 for n in nodes if n["built_by"])}
