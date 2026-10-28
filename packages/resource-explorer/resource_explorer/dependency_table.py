"""Dependencies as ONE table with a kind column, every row with TWO ENDS (DESIGN-DEPENDENCY-ROW-TWO-ENDS.md).

The owner (2026-10-07): "You show a source but not a target, so they are not a wire." A dependency is a
relationship with two ends and a kind; a row that names only where it was read is evidence, not a dependency.
Every row names

  dependent  the thing that depends: a component of this repository (by locator, and GUID once accepted and
             materialised), a service the deployment artifacts declare, or the repository itself
  relation   `requires` (build-time package) / `runs` (a service runs an image) / `connects_to` (a declared
             link between services) -- `reads` / `writes` / `deployed_by` are in the vocabulary, but no reader
             produces them yet (nothing in a compose file says read or write; `deployed_by` is the stated
             cross-repository fact, never a row's relation today)
  target     typed: package / image / service / component / resource (an RE-registered repository, with slug
             and the asset's GUID when it has one)
  kind       `build-time` (a manifest), `runtime` (a deployment artifact), `data` (a connection string whose
             scheme names a data store); a new kind is a new value in `KINDS`
  evidence   file and line
  state      `measured` / `proposed` / `confirmed` / `withdrawn`, and `not established` with its reason when an
             end cannot be told apart from another node (never guessed)

Build-time rows are read from the dependency surveyor's stored rows. Runtime and data rows are read from the
architecture recovery IR's own wires (`architecture_interfaces` findings), so this table and the blueprint
diagram share ONE record: a row's `drawn` says how the diagram draws it, and the diagram draws exactly those.

A CONFIRMED runtime or data row publishes as ONE ResourceMeasureAnnotation carrying the whole row
(`runtime_annotations`): Egeria's planned "deployed by" relationship types are not available, nothing here
writes a relationship, and no word on screen says "lineage" (owner, 2026-10-05: a dependency, not lineage).
When the relationship types land a migration reads `target_guid` from these annotations; nothing is
re-measured.

Everything here reads RE's own records; nothing contacts Egeria. A count RE does not have is not given as
zero: a kind with no rows says WHY (`runtime_state`).

Confirmations are kept in `app_settings` (no schema change), ONE KEY PER ENTRY written once and never updated:
`repo_dependency_confirmations::<slug>::<time_ns>-<id>` -> `{key, verdict, by, at}`; the latest entry per row
wins. The older single key `repo_dependency_confirmations::<slug>` is still read.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

KIND_BUILD = "build-time"
KIND_RUNTIME = "runtime"
KIND_DATA = "data"
#: The kinds, in display order. A new kind is added here (and a reader below), never as a new section.
KINDS = (KIND_BUILD, KIND_RUNTIME, KIND_DATA)
HEADING = "Dependencies · by kind"

#: The relation vocabulary; it grows. `reads` / `writes` / `deployed_by` have no reader today (see the module
#: docstring): they are listed so a row never invents a word outside the list.
REQUIRES, RUNS, CONNECTS_TO, READS, WRITES, DEPLOYED_BY = (
    "requires", "runs", "connects_to", "reads", "writes", "deployed_by")
RELATIONS = (REQUIRES, RUNS, CONNECTS_TO, READS, WRITES, DEPLOYED_BY)
TARGET_TYPES = ("package", "image", "service", "component", "endpoint", "resource")

#: A connection-string scheme that names a data store or a broker. Only a scheme the artifact STATES counts: a
#: port number or a service name is never read as "this is a database".
DATA_SCHEMES = frozenset({"redis", "rediss", "postgres", "postgresql", "mysql", "mariadb", "mongodb",
                          "mongodb+srv", "jdbc", "amqp", "amqps", "kafka", "memcached", "cassandra",
                          "elasticsearch", "s3"})

VERDICT_CONFIRMED = "confirmed"
VERDICT_WITHDRAWN = "withdrawn"

STATE_NOT_ESTABLISHED = "not established"

#: How the blueprint draws a row (the diagram reads exactly these; see `mermaid.render(edge_ports=...)`).
DRAWN_WIRE = "wire"                    # both ends are admitted nodes
DRAWN_PORT = "edge port"               # one end is a referenced-only service: a port on the edge, not a box
DRAWN_OUTSIDE = "outside"              # one end is a service that is no node of this repository
DRAWN_ENVIRONMENT = "environment wire"  # both ends referenced-only: a wire in the Environment blueprint
DRAWN_CROSS = "cross-blueprint"        # the target is another repository's build
DRAWN_LISTED = "listed"                # in the table, not in a diagram
DRAWN_NOT = "not drawn"
#: What the component blueprint's diagram draws as an edge.
DIAGRAM_EDGES = (DRAWN_WIRE, DRAWN_PORT, DRAWN_OUTSIDE)
DRAWN_WORDS = {
    DRAWN_WIRE: "a wire between two nodes",
    DRAWN_PORT: "a port on the blueprint's edge",
    DRAWN_OUTSIDE: "a wire to a box outside this analysis",
    DRAWN_ENVIRONMENT: "a wire in the Environment Deployment Blueprint",
    DRAWN_CROSS: "a link to another repository's blueprint",
    DRAWN_LISTED: "listed here only",
    DRAWN_NOT: "not drawn",
}

#: What the table does NOT read, said in its header so an absence is never read as "none".
NOT_DERIVED = ("module-to-module requires not yet derived · "
               "reads/writes, endpoints and host:port not derived")

NO_ARTIFACT_SENTENCE = "no deployment artifact found"
NOT_SURVEYED_SENTENCE = "runtime not surveyed"


def _key(source: str, target: str, path: str) -> str:
    return f"{source}->{target}@{path}"


# ── confirmations: one setting per entry, written once ────────────────────────────────────────

def _legacy_key(slug: str) -> str:
    return f"repo_dependency_confirmations::{slug}"


def _prefix(slug: str) -> str:
    # The slug is encoded so one with colons in it cannot reach into another slug's entries.
    return f"repo_dependency_confirmations::{slug.replace('%', '%25').replace(':', '%3A')}::"


def read_confirmations(registry, slug: str) -> dict:
    """{row key: [{verdict, by, at}, ...]} oldest first: the old single-key trail, then each entry."""
    out: dict[str, list[dict]] = {}
    raw = registry.get_setting(_legacy_key(slug))
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            # Said, not hidden: every runtime row would otherwise silently read "proposed" again.
            log.warning("the stored dependency confirmations for %s are unreadable; treating none as confirmed", slug)
            data = {}
        if isinstance(data, dict):
            out.update({k: list(v) for k, v in data.items() if isinstance(v, list)})
    # A global order across keys: the old single key first, then each entry in the order its key sorts
    # (time_ns), so the latest verdict of a row collapsed from several keys is the latest by when it was made.
    n = 0
    for trail in out.values():
        for e in trail:
            e["n"] = n
            n += 1
    bad = 0
    for _k, value in registry.list_settings_with_prefix(_prefix(slug)):
        try:
            e = json.loads(value)
        except (ValueError, TypeError):
            e = None
        if not (isinstance(e, dict) and isinstance(e.get("key"), str) and e.get("verdict") in (VERDICT_CONFIRMED, VERDICT_WITHDRAWN)
                and e.get("by") and e.get("at")):
            bad += 1
            continue
        out.setdefault(e["key"], []).append({"verdict": e["verdict"], "by": e["by"], "at": e["at"],
                                             "reason": e.get("reason") or "", "n": n})
        n += 1
    if bad:
        log.warning("%d unreadable dependency confirmation(s) for %s ignored", bad, slug)
    return out


MAX_KEYS = 200
MAX_KEY_LENGTH = 512
MAX_REASON = 500


def record_confirmations(registry, slug: str, keys: list[str], verdict: str, by: str, reason: str = "",
                         ctx: "Context | None" = None) -> int:
    """Append one confirmation (or withdrawal) per DISTINCT row, by `by`, with an optional reason. A key may be
    the row's own key or any of the per-evidence keys it was collapsed from; two keys of one row are one
    confirmation. Only confirmable rows of this repository are accepted (a row whose end is not established
    is not); returns how many were recorded. Append-only: a change is a new setting and the trail keeps both.
    Limits, each a ValueError with a sentence: at most 200 keys of at most 512 characters, a reason of at
    most 500."""
    if verdict not in (VERDICT_CONFIRMED, VERDICT_WITHDRAWN):
        raise ValueError(f"verdict must be '{VERDICT_CONFIRMED}' or '{VERDICT_WITHDRAWN}', got {verdict!r}")
    if not by:
        raise ValueError("a confirmation needs a person who made it")
    if len(keys) > MAX_KEYS:
        raise ValueError(f"a request carries at most {MAX_KEYS} keys, got {len(keys)}")
    if any(len(k) > MAX_KEY_LENGTH for k in keys):
        raise ValueError(f"a key is at most {MAX_KEY_LENGTH} characters")
    reason = (reason or "").strip()
    if len(reason) > MAX_REASON:
        raise ValueError(f"the reason is limited to {MAX_REASON} characters")
    ctx = ctx or Context(registry, slug)
    canonical = {k: r["key"] for r in _runtime_rows(ctx, {}) if r["state"] != STATE_NOT_ESTABLISHED
                 for k in r["keys"]}
    unknown = [k for k in dict.fromkeys(keys) if k not in canonical]
    if unknown:
        raise ValueError(f"not confirmable runtime dependencies of this repository: {unknown}")
    distinct = list(dict.fromkeys(canonical[k] for k in keys))
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for k in distinct:
        # Its own key, written once: time_ns orders entries, the random suffix keeps two writers apart.
        entry = {"key": k, "verdict": verdict, "by": by, "at": at}
        if reason:
            entry["reason"] = reason
        registry.add_setting_once(f"{_prefix(slug)}{time.time_ns():020d}-{uuid.uuid4().hex[:8]}", json.dumps(entry))
    return len(distinct)


# ── the nodes a row's ends are matched against ────────────────────────────────────────────────

class Context:
    """One request's reads, each made ONCE: the recovery results (a full rebuild, the expensive one), the
    nodes matched against, the referenced-only rows, and the architecture_interfaces findings. Pass it down
    to every consumer (`build_table`, `record_confirmations`, `runtime_annotations`, `environment_wires`) so
    a request pays for one rebuild, not one per reader. Confirmations are NOT held: they change within a
    request (a confirm, then the table re-read), and reading them is cheap."""

    def __init__(self, registry, slug: str):
        self.registry, self.slug = registry, slug
        self._recovery = self._paths = self._nodes = self._referenced = self._artifacts = None

    @property
    def recovery(self) -> list[dict]:
        if self._recovery is None:
            from resource_explorer.surveyors.repo_survey_definition_adapter import _architecture_recovery_results
            res = _architecture_recovery_results(self.registry, self.slug, max_depth=None) or {}
            self._recovery = list(res.get("components") or [])
        return self._recovery

    @property
    def paths(self) -> dict:
        """{path: component} as node_admission reads it."""
        if self._paths is None:
            self._paths = {c["path"]: c for c in self.recovery if c.get("path")}
        return self._paths

    @property
    def referenced(self) -> list[dict]:
        if self._referenced is None:
            from resource_explorer import node_admission
            self._referenced = node_admission.referenced_rows(self.registry, self.slug, self.paths)
        return self._referenced

    @property
    def artifacts(self) -> list[dict]:
        if self._artifacts is None:
            self._artifacts = _artifact_rows(self.registry, self.slug)
        return self._artifacts

    @property
    def nodes(self) -> "_Nodes":
        if self._nodes is None:
            self._nodes = _Nodes(self)
        return self._nodes


class _Nodes:
    """The admitted components and the referenced-only services of one repository, as the blueprint has
    them (reclassifications applied). `resolve(name)` says what a name in a deployment artifact IS."""

    def __init__(self, ctx: Context):
        from resource_explorer import node_admission

        registry, slug = ctx.registry, ctx.slug
        not_a_path = {"", ".", "*"}
        comps = node_admission.apply_to_tree_components(
            registry, slug, [c for c in ctx.recovery if (c.get("path") or "") not in not_a_path])
        comps = [c for c in comps if not c.get("structural") and (c.get("path") or "") not in not_a_path]
        materialized = registry.get_materialized_components("repo", slug)
        verdicts = registry.get_component_verdicts("repo", slug)
        self.admitted = []
        for c in comps:
            v = verdicts.get(c["path"]) or {}
            accepted = v.get("verdict_target", "component") == "component" and v.get("verdict") in ("accepted", "retyped")
            self.admitted.append({"name": c.get("name") or c["path"], "slug": c.get("slug") or "", "locator": c["path"],
                                  "guid": (materialized.get(c["path"]) or {}).get("guid", "") if accepted else "",
                                  "accepted": accepted})
        self.referenced = [{"name": r["name"], "slug": r.get("slug") or "", "locator": r["scope"],
                            "image": r.get("image") or "", "evidence": r.get("evidence") or ""}
                           for r in ctx.referenced]
        self.referenced_names = sorted({r["name"] for r in self.referenced})

    def resolve(self, value: str) -> dict:
        a = {n["locator"]: n for n in self.admitted if value and value in (n["name"], n["slug"])}
        r = {n["locator"]: n for n in self.referenced if value and value in (n["name"], n["slug"], n["locator"])}
        total = len(a) + len(r)
        if total > 1:
            return {"class": "ambiguous", "name": value, "n": total}
        if a:
            return {"class": "admitted", **next(iter(a.values()))}
        if r:
            return {"class": "referenced", **next(iter(r.values()))}
        return {"class": "unknown", "name": value, "locator": "", "guid": ""}

    def owner_of(self, directory: str) -> dict | None:
        """The admitted component whose path contains `directory` (longest first), or None."""
        best = None
        for n in self.admitted:
            loc = n["locator"]
            if directory == loc or directory.startswith(loc + "/"):
                if best is None or len(loc) > len(best["locator"]):
                    best = n
        return best


def _dependent_of(res: dict) -> dict:
    if res["class"] == "admitted":
        return {"dependent": res["name"], "dependent_type": "component", "dependent_locator": res["locator"],
                "dependent_guid": res.get("guid", "")}
    return {"dependent": res["name"], "dependent_type": "service",
            "dependent_locator": res.get("locator", "") if res["class"] == "referenced" else "",
            "dependent_guid": ""}


def _target_of(res: dict) -> dict:
    if res["class"] == "admitted":
        return {"target_type": "component", "target_name": res["name"], "target_guid": res.get("guid", ""),
                "target_slug": ""}
    return {"target_type": "service", "target_name": res["name"], "target_guid": "", "target_slug": ""}


def _drawn(dep: dict, tgt: dict) -> tuple[str, str]:
    classes = (dep["class"], tgt["class"])
    if "ambiguous" in classes:
        return DRAWN_NOT, "not drawn · a name is shared by several nodes"
    if classes == ("admitted", "admitted"):
        return DRAWN_WIRE, DRAWN_WORDS[DRAWN_WIRE]
    if set(classes) == {"admitted", "referenced"}:
        return DRAWN_PORT, DRAWN_WORDS[DRAWN_PORT]
    if classes == ("referenced", "referenced"):
        return DRAWN_ENVIRONMENT, DRAWN_WORDS[DRAWN_ENVIRONMENT]
    if "admitted" in classes:
        return DRAWN_OUTSIDE, DRAWN_WORDS[DRAWN_OUTSIDE]
    return DRAWN_LISTED, DRAWN_WORDS[DRAWN_LISTED]


def _split_evidence(evidence: str) -> tuple[str, int | None]:
    path, _, line = (evidence or "").rpartition(":")
    return (path, int(line)) if path and line.isdigit() else (evidence or "", None)


def _finish(row: dict) -> dict:
    """Fill what every row carries and derive the fields the older readers (and the sort) use."""
    row.setdefault("dependent_type", "repository")
    for k in ("dependent_locator", "dependent_guid", "target_version", "target_guid", "target_slug", "target_image",
              "protocol", "resolution", "cross_fact", "state_reason", "ecosystem", "style", "by", "at", "scope"):
        row.setdefault(k, "")
    row.setdefault("evidence_path", "")
    row.setdefault("evidence_line", None)
    row.setdefault("reason", "")
    # One link declared in several artifacts is ONE row with every piece of evidence, each with its line
    # where known (a link is identified by its two ends and the relation, not by where it was read).
    row.setdefault("evidences", [{"path": row["evidence_path"], "line": row["evidence_line"]}])
    row["evidence_path"], row["evidence_line"] = row["evidences"][0]["path"], row["evidences"][0]["line"]
    row["evidence"] = ", ".join(f"{e['path']}:{e['line']}" if e["line"] else e["path"] for e in row["evidences"])
    row.setdefault("keys", [row.get("key", "")])
    row["target_ref"] = f"{row['target_type']}:{row['target_name']}"
    # The older columns: the dependent, the target's name, and where it was read.
    row["name"], row["target"], row["source"] = row["dependent"], row["target_name"], row["evidence"]
    row.setdefault("drawn", DRAWN_LISTED)
    row.setdefault("drawn_words", DRAWN_WORDS.get(row["drawn"], row["drawn"]))
    return row


def _latest_of(confirmations: dict, keys: list[str]) -> dict | None:
    """The latest verdict over every key a row answers to (its per-evidence keys, old and new), by the order
    the verdicts were made."""
    entries = [e for k in keys for e in confirmations.get(k, [])]
    return max(entries, key=lambda e: e.get("n", 0)) if entries else None


def _lifecycle(row: dict, latest: dict | None, artifact: str, extra: str = "") -> None:
    if latest and latest["verdict"] == VERDICT_CONFIRMED:
        row["state"], row["state_words"] = "confirmed", f"confirmed · by {latest['by']} · from {artifact}"
    elif latest and latest["verdict"] == VERDICT_WITHDRAWN:
        # A withdrawal changes what the NEXT survey publishes; what is already in Egeria stays there.
        row["state"], row["state_words"] = "withdrawn", f"withdrawn · by {latest['by']} · from {artifact} · future surveys only"
    else:
        row["state"], row["state_words"] = "proposed", f"proposed · from {artifact}{extra}"
    row["by"], row["at"] = (latest or {}).get("by", ""), (latest or {}).get("at", "")
    row["reason"] = (latest or {}).get("reason", "")


def _not_established(row: dict, reason: str) -> None:
    row["state"], row["state_reason"] = STATE_NOT_ESTABLISHED, reason
    row["state_words"] = f"{STATE_NOT_ESTABLISHED} · {reason}"
    row["drawn"], row["drawn_words"] = DRAWN_NOT, DRAWN_WORDS[DRAWN_NOT]


# ── build-time rows: a manifest says the dependent requires a package ─────────────────────────

def _build_rows(ctx: "Context") -> list[dict]:
    registry, slug = ctx.registry, ctx.slug
    deps = registry.query_dependencies(slug) or []
    if not deps:
        return []
    project = registry.get(slug)
    repo_name = getattr(project, "display_name", "") or slug
    rows = []
    for d in deps:
        src = d.get("source_file") or ""
        directory = src.rsplit("/", 1)[0] if "/" in src else ""
        manifest = src.rsplit("/", 1)[-1] or "a manifest"
        owner = ctx.nodes.owner_of(directory) if directory else None
        if owner:
            dependent = {"dependent": owner["name"], "dependent_type": "component",
                         "dependent_locator": owner["locator"], "dependent_guid": owner["guid"]}
        else:
            dependent = {"dependent": repo_name, "dependent_type": "repository"}
        rows.append(_finish({
            "kind": KIND_BUILD, **dependent, "relation": REQUIRES,
            "target_type": "package", "target_name": d.get("dep_name") or "",
            "target_version": d.get("dep_version") or "", "ecosystem": d.get("ecosystem") or "",
            # The stored row names the manifest, not the line: the line is not recorded, and is not invented.
            "evidence_path": src, "state": "measured", "state_words": f"measured · from {manifest}",
            "key": f"{d.get('ecosystem') or ''}:{d.get('dep_name') or ''}@{src}",
            "drawn": DRAWN_LISTED,
        }))
    return rows


def _artifact_rows(registry, slug: str) -> list[dict]:
    out = []
    for r in registry.query_findings(slug, "architecture_interfaces") or []:
        d = r.get("detail") if isinstance(r.get("detail"), dict) else None
        if d is None:
            try:
                d = json.loads(r.get("detail_json") or "{}")
            except ValueError:
                d = {}
        out.append(d or {})
    return out


# ── runtime and data rows ─────────────────────────────────────────────────────────────────────

def _referenced_rows(ctx: "Context", confirmations: dict) -> list[dict]:
    """The services this repository's deployment artifacts RUN but whose images it neither builds nor
    publishes (DESIGN-BLUEPRINT-NODE-ADMISSION.md): not components, runtime dependencies of the deployment it
    describes. "<service> runs <image>"; the image resolves to the repository that builds it when RE knows
    one, and says "builder not known" when not. Reclassifications a person made are already applied."""
    from resource_explorer import node_admission

    registry, slug = ctx.registry, ctx.slug
    rows_in = ctx.referenced
    builders = node_admission.image_builders(registry, slug) if rows_in else {}
    rows = []
    for r in rows_in:
        image = r.get("image") or ""
        key = f"referenced:{r['scope']}"
        latest = _latest_of(confirmations, [key])
        evidence = r.get("evidence") or ""
        artifact = evidence.split("from ", 1)[-1].split(" ")[0] if "from " in evidence else "a deployment artifact"
        moved = r.get("reclassified")
        tail = f" · reclassified by {moved['by']}: {moved['reason']}" if moved else ""
        if r.get("reclassification_note"):
            tail += f" · {r['reclassification_note']}"
        row = {"kind": KIND_RUNTIME, "dependent": r["name"], "dependent_type": "service",
               "dependent_locator": r["scope"], "relation": RUNS, "key": key, "scope": r["scope"],
               "style": "referenced only", "evidence_path": artifact}
        if not image:
            # A service moved to referenced-only that names no image has no target to state.
            row.update({"target_type": "image", "target_name": ""})
            _not_established(row, "the service names no image")
        else:
            builder = node_admission.builder_of(image, builders)
            if builder:
                row.update({"target_type": "resource", "target_name": builder, "target_slug": builder,
                            "target_guid": registry.get_egeria_asset_guid(builder) or "", "target_image": image,
                            "resolution": f"built by {builder}",
                            "cross_fact": f"{builder} {DEPLOYED_BY} {slug} · stated, not written to Egeria",
                            "drawn": DRAWN_CROSS})
            else:
                row.update({"target_type": "image", "target_name": image, "resolution": "builder not known",
                            "drawn": DRAWN_LISTED})
            _lifecycle(row, latest, artifact, f" · image {image}")
            row["state_words"] += tail
        rows.append(_finish(row))
    return rows


def _wire_rows(ctx: "Context", confirmations: dict) -> list[dict]:
    nodes = ctx.nodes
    groups: dict[tuple, dict] = {}
    for d in ctx.artifacts:
        if d.get("kind") != "wire":
            continue
        ev = d.get("evidence") or {}
        path = ev.get("path", "")
        source, target = d.get("source", ""), d.get("target", "")
        if not (source and target):
            continue
        key = _key(source, target, path)
        proto = (d.get("protocol") or "").strip()
        dep, tgt = nodes.resolve(source), nodes.resolve(target)
        row = {"kind": KIND_DATA if proto.lower() in DATA_SCHEMES else KIND_RUNTIME,
               **_dependent_of(dep), "relation": CONNECTS_TO, **_target_of(tgt), "protocol": proto,
               "evidence_path": path, "evidence_line": ev.get("line") or None,
               "style": d.get("integrationStyle") or ""}
        gid = (row["kind"], identity(row))
        g = groups.get(gid)
        if g is None:
            row["ends_accepted"] = bool(dep.get("accepted") and tgt.get("accepted"))
            groups[gid] = g = {"row": row, "dep": dep, "tgt": tgt, "keys": [], "evidences": [], "artifacts": []}
        elif proto and not g["row"]["protocol"]:
            g["row"]["protocol"] = proto
        if key not in g["keys"]:
            g["keys"].append(key)
            g["evidences"].append({"path": path, "line": ev.get("line") or None})
            artifact = path.rsplit("/", 1)[-1] or "a deployment artifact"
            if artifact not in g["artifacts"]:
                g["artifacts"].append(artifact)
    rows = []
    for g in groups.values():
        row, dep, tgt = g["row"], g["dep"], g["tgt"]
        order = sorted(range(len(g["keys"])), key=lambda i: g["keys"][i])
        row["keys"] = [g["keys"][i] for i in order]
        row["evidences"] = [g["evidences"][i] for i in order]
        row["key"] = row["keys"][0]
        ambiguous = [r for r in (dep, tgt) if r["class"] == "ambiguous"]
        if ambiguous:
            a = ambiguous[0]
            _not_established(row, f"the name {a['name']} is shared by {a['n']} nodes")
        else:
            _lifecycle(row, _latest_of(confirmations, row["keys"]), ", ".join(g["artifacts"]))
            row["drawn"], row["drawn_words"] = _drawn(dep, tgt)
        rows.append(_finish(row))
    return rows


def _runtime_rows(ctx: "Context", confirmations: dict) -> list[dict]:
    """Runtime AND data rows: both are read from the deployment artifacts (the kind is the row's)."""
    return _referenced_rows(ctx, confirmations) + (_wire_rows(ctx, confirmations) if ctx.artifacts else [])


def _runtime_section_state(registry, slug: str, has_artifacts: bool) -> str:
    """Why a repository has no runtime rows, or '' when it has some. A section that is empty because
    the step never ran is not the same statement as one that ran and found nothing."""
    from resource_explorer.surveyors import survey_snapshot

    if has_artifacts:
        return ""
    ran = "repo_arch_detect" in ((survey_snapshot.latest(registry, slug) or survey_snapshot.Snapshot(slug)).steps) or \
        bool(registry.query_findings(slug, "architecture_recovery"))
    return NO_ARTIFACT_SENTENCE if ran else NOT_SURVEYED_SENTENCE


def build_table(registry, slug: str, ctx: "Context | None" = None) -> dict:
    """The one table: heading, per-kind counts, every row, and the sentence when a kind is empty."""
    ctx = ctx or Context(registry, slug)
    confirmations = read_confirmations(registry, slug)
    rows = _build_rows(ctx) + _runtime_rows(ctx, confirmations)
    counts = {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS}
    runtime_state = "" if counts[KIND_RUNTIME] else _runtime_section_state(registry, slug, bool(ctx.artifacts))
    from resource_explorer import node_admission
    deploys_only = node_admission.summary(registry, slug, ctx.paths)["sentence"]
    # A repository with deployment artifacts that declare no dependency between services still says why.
    if not counts[KIND_RUNTIME] and not runtime_state:
        runtime_state = ("deployment artifacts found · every declared link is to a data store" if counts[KIND_DATA]
                         else "deployment artifacts found · none declares a dependency between services")
    data_state = "" if counts[KIND_DATA] else (
        runtime_state if runtime_state in (NO_ARTIFACT_SENTENCE, NOT_SURVEYED_SENTENCE)
        else "no connection string to a data store found in the deployment artifacts")
    drawn: dict[str, int] = {}
    for r in rows:
        if r["kind"] != KIND_BUILD:
            drawn[r["drawn"]] = drawn.get(r["drawn"], 0) + 1
    return {"heading": HEADING, "kinds": list(KINDS), "counts": counts, "rows": rows,
            "runtime_state": runtime_state, "data_state": data_state, "drawn": drawn,
            "not_derived": NOT_DERIVED,
            # "this repository deploys other software and builds none of its own", when that is the case.
            "deploys_only": deploys_only,
            "summary": " · ".join(f"{counts[k]} {k}" for k in KINDS)}


def environment_wires(registry, slug: str, ctx: "Context | None" = None) -> list[dict]:
    """The Environment Deployment Blueprint's wires: the rows whose two ends are both referenced-only
    services, read from the same rows the table shows."""
    ctx = ctx or Context(registry, slug)
    return [{k: r[k] for k in ("key", "dependent", "relation", "target_name", "target_type", "kind", "protocol",
                               "evidence", "state")}
            for r in _runtime_rows(ctx, read_confirmations(registry, slug)) if r["drawn"] == DRAWN_ENVIRONMENT]


def diagram_edge_ports(registry, slug: str) -> list[str]:
    """The names the component blueprint draws as a port on its edge: the referenced-only services."""
    from resource_explorer import node_admission
    return sorted({r["name"] for r in node_admission.referenced_rows(registry, slug)})


# ── publication: one annotation per confirmed row ─────────────────────────────────────────────

def identity(row: dict) -> str:
    """The row's identity from its TWO ENDS and the relation: dependent (name AND locator), relation, target.
    The locator is in it so a later re-homing of the annotation onto the component's own asset is a move,
    not a rewrite. Never a GUID (a GUID appears later, or changes) and never the survey run, so a second
    publish reuses the same qualifiedName."""
    dep = f"{row['dependent_type']}:{row['dependent']}"
    if row.get("dependent_locator"):
        dep += f"@{row['dependent_locator']}"
    return f"{dep}|{row['relation']}|{row['target_type']}:{row['target_name']}"


def runtime_annotations(registry, slug: str, ctx: "Context | None" = None) -> list:
    """The CONFIRMED runtime and data rows as annotations, and nothing else: ONE ResourceMeasureAnnotation per
    row (a link declared in several artifacts is one row, so one annotation, with every piece of evidence in
    `evidence_list`), carrying relation, target type, name and GUID, kind, evidence and the person who
    confirmed it. A proposed, withdrawn or not-established row publishes nothing; nothing here is a
    relationship."""
    from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

    ctx = ctx or Context(registry, slug)
    out = []
    for r in _runtime_rows(ctx, read_confirmations(registry, slug)):
        if r["state"] != "confirmed":
            continue
        props = {"relation": r["relation"], "kind": r["kind"],
                 "dependent": r["dependent"], "dependent_type": r["dependent_type"],
                 "dependent_locator": r["dependent_locator"], "dependent_guid": r["dependent_guid"],
                 "target_type": r["target_type"], "target_name": r["target_name"], "target_guid": r["target_guid"],
                 "evidence": r["evidence"], "evidence_list": r["evidences"],
                 "confirmed_by": r["by"], "confirmed_at": r["at"]}
        if r["target_image"]:
            props["target_image"] = r["target_image"]
        if r["protocol"]:
            props["protocol"] = r["protocol"]
        if r["reason"]:
            props["confirm_reason"] = r["reason"]
        out.append(ResourceMeasureAnnotation(
            summary=f"{r['kind']} dependency: {r['dependent']} {r['relation']} {r['target_type']} {r['target_name']}",
            analysis_step="repo_dependency", check_name=f"{r['kind']}_dependency", item_key=identity(r),
            expression=f"declared in {r['evidence']}",
            explanation=f"{r['state_words']}. Read from a deployment artifact; a person confirmed it.",
            confidence=100, resource_properties=props,
            json_properties={**props, "source": r["dependent"], "target": r["target_name"], "artifact": r["evidence"]}))
    return out
