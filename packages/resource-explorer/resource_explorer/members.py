"""The things a count counted.

Every count on the dashboard names a set — 18 advisories, 68 dependencies,
90 data files, 26,563 symbols, 114 components — and until 2026-09-11 nothing
opened the set. The measurement popup opens the NUMBER's history, so a
reader asking "which 18?" learned that 18 had been 18 for a fortnight. This
module opens the members.

Members are read from the registry, not from the display-shaped results the
dashboard renders: the results readers drop `detail_json`, and that is where
cve_scan keeps its advisory ids. The registry keeps everything.

Members nest, and purpose decides how much of the tree is shown by default.
`scope="public"` is what someone intending to USE the resource wants — the
surface it exposes; `scope="all"` is what someone MAINTAINING it wants — that
plus the internal structure. Maintain is a superset, so this is one tree
with two default expansions, not two screens. Today only symbols carry a
public/internal marker (`is_private`); for every other set the scope is
recorded on the response and changes nothing, so the caller can see that it
was asked for and not honoured rather than assume it was.

`public` is a judgement in a repository, not a fact: `is_private` is inferred
from naming conventions, and the response says so.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from resource_explorer.artifact_provenance import commit_label, extraction_commit, role_label
from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.repo_survey_definition_adapter import ANALYSIS_KINDS

DEFAULT_LIMIT = 200


@dataclass
class Group:
    name: str
    count: int
    members: list = field(default_factory=list)   # [{name, detail, children?, count?}]
    truncated: bool = False


@dataclass
class MemberSet:
    analysis_id: str
    metric: str
    title: str
    total: int | None
    scope: str
    scope_honoured: bool
    groups: list = field(default_factory=list)
    note: str = ""
    source: str = ""              # what was read: a table or the findings
    inventory: str = ""           # the short form: "6,423 files · 4,425 vendored"
    run_at: str = ""              # when the analysis this set came from last ran -- the server's date, not the browser's
    #: True when this analysis produces no member-shaped rows at all — it
    #: measures, it does not enumerate. `total` is None (not 0) in this
    #: state: zero is a count, and there was nothing to count. See
    #: `members_for`'s reader-selection comment for the signal this is
    #: decided from.
    not_applicable: bool = False
    reason: str = ""
    #: True when the analysis's own store holds no member rows for this
    #: resource. `reason` then says which store was empty, so the pane never
    #: prints a bare "0 findings" for data that lives elsewhere.
    empty: bool = False

    def to_dict(self) -> dict:
        return {
            "analysis_id": self.analysis_id, "metric": self.metric, "title": self.title,
            "total": self.total, "scope": self.scope, "scope_honoured": self.scope_honoured,
            "groups": [{"name": g.name, "count": g.count, "members": g.members, "truncated": g.truncated}
                       for g in self.groups],
            "note": self.note, "source": self.source, "inventory": self.inventory,
            "run_at": self.run_at, "not_applicable": self.not_applicable, "reason": self.reason,
            "empty": self.empty,
        }


def _row(r):
    return dict(r) if not isinstance(r, dict) else r


def _detail(r) -> dict:
    d = r.get("detail_json")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = {}
    return d if isinstance(d, dict) else {}


# ── readers ───────────────────────────────────────────────────────────────

def _cve_members(registry, slug, scope, limit) -> MemberSet:
    rows = [_row(r) for r in registry.query_findings(slug, "cve_scan")]
    groups: list[Group] = []
    total = 0
    order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "MEDIUM": 2, "LOW": 3}
    rows.sort(key=lambda r: order.get(str(_detail(r).get("severity", "")).upper(), 9))
    for r in rows:
        d = _detail(r)
        ids = d.get("advisory_ids") or []
        total += len(ids)
        groups.append(Group(
            name=f"{d.get('package') or r['check_name']} {d.get('version') or ''}".strip(),
            count=len(ids),
            members=[{"name": a, "detail": str(d.get("severity") or r.get("label") or "").lower()} for a in ids][:limit],
            truncated=len(ids) > limit,
        ))
    return MemberSet("cve_scan", "advisories", "advisories, by package", total, scope, False, groups,
                     note="Declared dependencies only; severity is OSV's.", source="project_analysis_findings.detail_json")


def _dependency_members(registry, slug, scope, limit) -> MemberSet:
    deps = [_row(d) for d in registry.query_dependencies(slug)]
    by_eco: dict[str, list] = {}
    for d in deps:
        by_eco.setdefault(d.get("ecosystem") or "unknown", []).append(d)
    groups = []
    for eco, items in sorted(by_eco.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda d: (d.get("dep_type") or "", d.get("dep_name") or ""))
        groups.append(Group(eco, len(items), [
            {"name": d.get("dep_name"), "detail": " ".join(x for x in (d.get("dep_version"), d.get("dep_type")) if x)}
            for d in items[:limit]], len(items) > limit))
    return MemberSet("dependency_analysis", "total", "dependencies, by ecosystem", len(deps), scope, False, groups,
                     note="Declared in manifests at the latest indexing; transitive dependencies are not resolved here.",
                     source="project_dependencies")


def _data_file_members(registry, slug, scope, limit) -> MemberSet:
    rows = [_row(r) for r in registry.get_data_profiles(slug)]
    by_fmt: dict[str, list] = {}
    for r in rows:
        by_fmt.setdefault(r.get("format") or "unknown", []).append(r)
    groups = []
    for fmt, items in sorted(by_fmt.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda r: -(r.get("file_size_bytes") or 0))
        groups.append(Group(fmt, len(items), [
            {"name": r.get("file_path"),
             "detail": " × ".join(str(x) for x in (r.get("row_count"), r.get("col_count")) if x is not None) or ""}
            for r in items[:limit]], len(items) > limit))
    return MemberSet("data_file_profiling", "total", "data files, by format", len(rows), scope, False, groups,
                     source="project_data_profiles")


def _symbol_members(registry, slug, scope, limit) -> MemberSet:
    """Symbols nest file -> symbol, and this is the one set where scope is
    real: `public` drops rows marked private. The marker is inferred from
    naming, so the note says so."""
    where = "project_slug = ?" + ("" if scope == "all" else " AND COALESCE(is_private, 0) = 0")
    with registry._conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM project_code_symbols WHERE {where}", (slug,)).fetchone()
        total = _row(total).get("count", None) if isinstance(total, dict) else total[0]
        files = [_row(r) for r in conn.execute(
            f"SELECT file_path, language, COUNT(*) AS n FROM project_code_symbols WHERE {where} "
            "GROUP BY file_path, language ORDER BY n DESC LIMIT ?", (slug, limit)).fetchall()]
        nfiles = conn.execute(f"SELECT COUNT(DISTINCT file_path) FROM project_code_symbols WHERE {where}", (slug,)).fetchone()
        nfiles = _row(nfiles).get("count", None) if isinstance(nfiles, dict) else nfiles[0]
    by_lang: dict[str, list] = {}
    for f in files:
        by_lang.setdefault(f.get("language") or "unknown", []).append(f)
    groups = []
    for lang, items in sorted(by_lang.items(), key=lambda kv: -sum(i["n"] for i in kv[1])):
        groups.append(Group(lang, sum(i["n"] for i in items), [
            {"name": f["file_path"], "count": f["n"], "children_key": f"file:{f['file_path']}"} for f in items], False))
    # The honest number beside the misleading one: a project indexed before
    # 2026-09-11 still counts vendored code as its own until re-indexed, and
    # the inventory summary is how the rail says so rather than hiding it.
    inv = registry.file_inventory_summary(slug)
    provenance = inventory_sentence(inv, nfiles)
    sha = extraction_commit(registry, slug)
    extracted = f"Symbols extracted at {commit_label(sha)}." if sha else f"Extraction {commit_label(sha)}."
    return MemberSet("api_structure", "symbol_count",
                     "symbols, by language and file" + ("" if scope == "all" else " — public only"),
                     int(total or 0), scope, True, groups,
                     note=f"{provenance} {extracted} `public` is inferred from naming conventions, not declared; a maintainer's marking would be better.",
                     source="project_code_symbols", inventory=inv.get("short", ""))


def inventory_sentence(inv: dict, nfiles=None) -> str:
    """The rail's sentence about the inventory, saying WHICH kind of
    not-own. Vendored is a provenance claim (somebody else's code checked
    in); generated is the repository's own build output. "4,425 vendored"
    about dist/ was a wrong sentence with a defensible number."""
    kinds = []
    if inv.get("vendored"):
        kinds.append(f"{inv['vendored']:,} vendored")
    if inv.get("generated"):
        kinds.append(f"{inv['generated']:,} generated")
    if kinds:
        return (f"{inv['own']:,} of {inv['total']:,} files are this repository's own; "
                f"{' and '.join(kinds)}, not counted.")
    n = inv.get("total") or nfiles or 0
    return (f"{n:,} files. Indexed {(inv.get('indexed_at') or '')[:10] or 'unknown'}; an index before "
            "2026-09-11 counts vendored code as the repository's own until re-indexed.")


def _symbol_row(r: dict) -> dict:
    """One symbol as a member: name, a detail line that now carries the line
    and signature, and the same facts as keys (line, signature, doc) so a
    reader of the JSON does not parse the line. A class carries a
    children_key and opens to its methods."""
    from resource_explorer.code_symbols import TYPE_KINDS

    line = int(r.get("start_line") or 0)
    sig = (r.get("signature") or "").strip()
    doc = ""
    for ln in (r.get("docstring") or "").strip().splitlines():
        if ln.strip():
            doc = ln.strip()[:160]
            break
    bits = [(r.get("kind") or "") + (" · private" if r.get("is_private") else ""),
            role_label(r.get("file_role"))]
    if line:
        bits.append(f"line {line}")
    detail = " · ".join(bits) + (f" · {sig}" if sig else "") + (f" · {doc}" if doc else "")
    row = {"name": r["qualified_name"], "detail": detail, "line": line, "signature": sig, "doc": doc,
           "kind": r.get("kind") or ""}
    if (r.get("kind") or "") in TYPE_KINDS:
        row["children_key"] = f"class:{r['file_path']}::{r['qualified_name']}"
    return row


_SYMBOL_COLS = ("qualified_name, kind, name, file_path, start_line, signature, docstring, parent_class, "
                "COALESCE(is_private,0) AS is_private, file_role")


def _symbols_in_file(registry, slug, file_path, scope, limit) -> list[dict]:
    where = "project_slug = ? AND file_path = ?" + ("" if scope == "all" else " AND COALESCE(is_private, 0) = 0")
    with registry._conn() as conn:
        rows = [_row(r) for r in conn.execute(
            f"SELECT {_SYMBOL_COLS} FROM project_code_symbols "
            f"WHERE {where} ORDER BY start_line, qualified_name LIMIT ?", (slug, file_path, limit)).fetchall()]
    return [_symbol_row(r) for r in rows]


def _methods_of(registry, slug, key, scope, limit) -> list[dict]:
    """`class:<file>::<qualified_name>` -> the symbols whose parent_class is
    that class's simple name, in the same file, by line."""
    file_path, _, qn = key[len("class:"):].partition("::")
    simple = qn.rsplit(".", 1)[-1]
    where = ("project_slug = ? AND file_path = ? AND parent_class = ?"
             + ("" if scope == "all" else " AND COALESCE(is_private, 0) = 0"))
    with registry._conn() as conn:
        rows = [_row(r) for r in conn.execute(
            f"SELECT {_SYMBOL_COLS} FROM project_code_symbols WHERE {where} "
            "ORDER BY start_line, qualified_name LIMIT ?", (slug, file_path, simple, limit)).fetchall()]
    return [_symbol_row(r) for r in rows]


def _component_members(registry, slug, scope, limit) -> MemberSet:
    with registry._conn() as conn:
        rows = [_row(r) for r in conn.execute(
            "SELECT qualified_name, scope_locator FROM architecture_materialized_components "
            "WHERE entity_slug = ? ORDER BY scope_locator, qualified_name LIMIT ?", (slug, limit)).fetchall()]
        total = conn.execute("SELECT COUNT(*) FROM architecture_materialized_components WHERE entity_slug = ?", (slug,)).fetchone()
        total = _row(total).get("count", None) if isinstance(total, dict) else total[0]
    by_scope: dict[str, list] = {}
    for r in rows:
        by_scope.setdefault(r.get("scope_locator") or "(whole repository)", []).append(r)
    groups = [Group(k, len(v), [{"name": r["qualified_name"].split("::")[-1], "detail": ""} for r in v], False)
              for k, v in by_scope.items()]
    return MemberSet("architecture_recovery", "component_count", "components, by scope", int(total or 0), scope, False, groups,
                     note="Materialized components only — what was published, not every candidate the recovery found.",
                     source="architecture_materialized_components")


#: Analyses whose findings are stored under a different kind than their
#: id. The fallback reader looked up `sub_resource_survey` and found nothing
#: (found 2026-09-12 from the Curate screen's "review 31 ›").
_FINDINGS_KIND = {"sub_resource_survey": "repo_sub_resource_survey"}


def _findings_members(registry, slug, analysis_id, scope, limit) -> MemberSet:
    """The fallback: a finding row per member, grouped by verdict, with the
    detail the display readers drop."""
    rows = [_row(r) for r in registry.query_findings(slug, _FINDINGS_KIND.get(analysis_id, analysis_id))]
    by_label: dict[str, list] = {}
    for r in rows:
        by_label.setdefault(str(r.get("label") or ""), []).append(r)
    groups = []
    for label, items in sorted(by_label.items(), key=lambda kv: -len(kv[1])):
        groups.append(Group(label or "(no verdict)", len(items), [
            {"name": r.get("check_name"), "detail": (r.get("summary") or "")[:160], "extra": _detail(r) or None}
            for r in items[:limit]], len(items) > limit))
    return MemberSet(analysis_id, "findings", "findings, by verdict", len(rows), scope, False, groups,
                     source="project_analysis_findings")


def _spec_endpoint_members(registry, slug, scope, limit):
    """The endpoints from the best DESCRIBED source (committed spec, else registered running instance), grouped
    by tag -- the same source the chat compile quotes (api_reference/service.best_spec_interface). None when
    only code is known, so the by-file code list runs."""
    from resource_explorer.api_reference.service import best_spec_interface

    iface = best_spec_interface(registry, slug)
    if iface is None:
        return None
    by_tag: dict[str, list] = {}
    for op in iface.operations:
        by_tag.setdefault(op.group or "(untagged)", []).append(op)
    groups = []
    remaining = limit
    for tag in sorted(by_tag):
        items = by_tag[tag]
        take = items[:max(remaining, 0)]
        remaining -= len(take)
        groups.append(Group(tag, len(items), [
            {"name": o.name,
             "detail": " · ".join(x for x in (o.summary, f"auth {', '.join(o.security)}" if o.security else "",
                                              "deprecated" if o.deprecated else "") if x),
             "extra": {"method": o.method, "path": o.path, "operation_id": o.operation_id, "tags": o.tags}}
            for o in take], len(take) < len(items)))
    n = len(iface.operations)
    return MemberSet("interface_surface", "endpoints", "operations, by tag", n, scope, False, groups,
                     note=f"{iface.source_phrase.capitalize()}. The API reference pane has parameters, bodies, "
                          "responses and the comparison with the code.",
                     source=f"api_reference:{iface.source}",
                     inventory=f"{n:,} operations in {len(groups)} groups")


def _endpoint_members(registry, slug, scope, limit) -> MemberSet:
    """interface_surface's members are the endpoints, grouped by file, each
    with method, path, handler and line -- read from project_code_markers
    through the same reader the compile uses (resource_explorer/endpoints.py),
    so the list the model was shown and the list the pane opens are one list.
    The fallback this replaced opened the four findings and, inside the
    http_api one, a 1,869-item evidence blob that was mostly test mocks.

    Not measured (no markers ever captured) is `total=None`; measured and
    none is `total=0`. Different sentences, never one blank."""
    from resource_explorer.endpoints import read_endpoints

    spec = _spec_endpoint_members(registry, slug, scope, limit)
    if spec is not None:
        return spec
    es = read_endpoints(registry, slug)
    if es is None:
        return MemberSet("interface_surface", "endpoints", "endpoints, by file", None, scope, False,
                         empty=True, reason="the endpoint reader could not read the registry; this is not a count of zero",
                         source="project_code_markers")
    left_out = []
    if es.excluded_tests:
        left_out.append(f"{es.excluded_tests:,} registration(s) under test paths left out")
    if es.excluded_vendored:
        left_out.append(f"{es.excluded_vendored:,} under vendored code left out")
    note = ("Code registrations (route decorators and annotations) outside tests"
            + (f"; {'; '.join(left_out)}" if left_out else "") + ". Ordered by file, then line.")
    if not es.measured:
        return MemberSet("interface_surface", "endpoints", "endpoints, by file", None, scope, False,
                         empty=True, note=note,
                         reason="not measured: no code markers were captured for this repository, "
                                "which is not the same as having no endpoints",
                         source="project_code_markers")
    by_file: dict[str, list] = {}
    for e in es.rows:
        by_file.setdefault(e.file_path, []).append(e)
    groups = []
    remaining = limit
    for path in sorted(by_file):
        items = by_file[path]
        take = items[:max(remaining, 0)]
        remaining -= len(take)
        groups.append(Group(path, len(items), [
            {"name": e.label or e.path,
             "detail": " · ".join(x for x in (e.handler, f"line {e.start_line}" if e.start_line else "",
                                              e.framework, e.kind if e.kind != "http_api" else "") if x),
             "extra": {"handler": e.handler, "file": e.file_path, "line": e.start_line, "kind": e.kind,
                       "method": e.method, "path": e.path, "framework": e.framework,
                       "signature": e.signature, "doc": e.doc}}
            for e in take], len(take) < len(items)))
    ms = MemberSet("interface_surface", "endpoints", "endpoints, by file", es.total, scope, False, groups,
                   note=note, source="project_code_markers", inventory=f"{es.total:,} endpoints in {len(groups)} files")
    if es.total == 0:
        ms.empty = True
        ms.reason = f"measured: no endpoints found outside tests ({'; '.join(left_out) or 'nothing left out'})"
    return ms


_READERS = {
    ("interface_surface", None): _endpoint_members,
    ("cve_scan", None): _cve_members,
    ("dependency_analysis", None): _dependency_members,
    ("manifest_parse", "dependency_count"): _dependency_members,
    ("data_file_profiling", None): _data_file_members,
    ("api_structure", "symbol_count"): _symbol_members,
    # The answer page's link sends no metric. Without a bare key these fell
    # through to the findings fallback: "0 findings" for api_structure, whose
    # rows live in project_code_symbols (dataops_testgen, 2026-10-15).
    ("api_structure", None): _symbol_members,
    ("manifest_parse", None): _dependency_members,
    ("code_symbol_extraction", "symbol_count"): _symbol_members,
    ("code_symbol_extraction", None): _symbol_members,
    ("architecture_recovery", None): _component_members,
    ("architecture_summary", "components"): _component_members,
}


def last_run_at(registry: ProjectRegistry, slug: str, analysis_id: str) -> str:
    """When this analysis last ran, from the registry's own bookkeeping. The
    provenance line is composed on the server so it cannot be forged; a line
    that took its date from the browser was not (review, 2026-09-12).

    `get_analysis_last_run` credits DERIVED runs too (a survey run that
    executed this analysis's steps under a different analysis_id's
    analysis_run row) — using it here, rather than a narrower query, is what
    makes "run date not recorded" honest for an analysis that just ran."""
    try:
        runs = registry.get_analysis_last_run("repo", slug) or {}
        return (runs.get(analysis_id) or {}).get("last_run_at", "") or ""
    except Exception:
        return ""


def _is_metrics_only(analysis_id: str) -> bool:
    """True when the analysis-kind registry says this analysis writes only
    measurements — no finding rows, hence nothing member-shaped to list.

    The signal is `AnalysisKindResults.render == "metrics"` in
    `surveyors/repo_survey_definition_adapter.py`'s `ANALYSIS_KINDS`. That
    field is the registry's own, purpose-built claim about shape — it exists
    precisely so a generic surface (there, the Results dashboard; here, the
    members rail) can switch on it without new per-kind code, and unlike
    "does a query return rows today" it does not depend on what happened to
    run. It is corroborated by `analysis_catalog.yaml`'s `annotation_types`
    for the analyses it currently flags: repository_health carries only
    `QualityScoreAnnotation`, architecture_summary only
    `ResourceMeasureAnnotation`, rag_ingestion none at all — none of those
    is a per-item finding type. `annotation_types` was not used as the
    primary signal because it is not consistently binary across the catalog
    (several findings-producing kinds also carry `ClassificationAnnotation`-
    only lists), whereas `render` is exactly the frontend-shape tag this
    decision needs.

    render="findings_list" and render="custom" analyses with no registered
    reader keep the existing findings fallback (case b) — "custom" already
    covers every kind with member-shaped output today (dependency_analysis,
    data_file_profiling, api_structure, architecture_recovery, ...) via its
    own `_READERS` entry, and an unrecognised analysis_id (not in
    ANALYSIS_KINDS at all) is treated the same way, preserving prior
    behaviour for anything this registry doesn't know about."""
    kind = ANALYSIS_KINDS.get(analysis_id)
    if kind is None or kind.results is None:
        return False
    return kind.results.render == "metrics"


def members_for(registry: ProjectRegistry, slug: str, analysis_id: str, metric: str = "",
                scope: str = "public", limit: int = DEFAULT_LIMIT) -> MemberSet:
    scope = "all" if scope == "all" else "public"
    reader = _READERS.get((analysis_id, metric or None)) or _READERS.get((analysis_id, None))
    if reader is not None:
        ms = reader(registry, slug, scope, limit)
    elif _is_metrics_only(analysis_id):
        # Case (c): a registered analysis kind that writes only metrics.
        # The old fallback read project_analysis_findings here and reported a
        # measured, honest-looking zero for a table this analysis never
        # writes — "0 findings" for repository_health, which runs and writes
        # project_analysis_metrics every time. There is nothing to list, so
        # `total` is None (uncounted), not 0 (counted and empty).
        ms = MemberSet(analysis_id, metric or "members", "members", None, scope, False,
                       not_applicable=True,
                       reason=(f"{analysis_id.replace('_', ' ')} records measurements, not members — "
                               "there is nothing to list."),
                       source="project_analysis_metrics")
    else:
        # Case (b): no reader, but this kind's findings table is the right
        # place to look (or the analysis is unknown to this registry) —
        # unchanged behaviour.
        ms = _findings_members(registry, slug, analysis_id, scope, limit)
    if not ms.not_applicable and not ms.total and not ms.reason:
        ms.empty = True
        ms.reason = f"nothing stored by {analysis_id} for this repo"
    ms.run_at = last_run_at(registry, slug, analysis_id)
    return ms


def children_for(registry: ProjectRegistry, slug: str, analysis_id: str, key: str,
                 scope: str = "public", limit: int = DEFAULT_LIMIT) -> list[dict]:
    """One level down. Keys are opaque to the client: `file:<path>` today."""
    scope = "all" if scope == "all" else "public"
    if key.startswith("class:") and analysis_id in ("api_structure", "code_symbol_extraction"):
        return _methods_of(registry, slug, key, scope, limit)
    if key.startswith("file:") and analysis_id in ("api_structure", "code_symbol_extraction"):
        return _symbols_in_file(registry, slug, key[5:], scope, limit)
    return []


# ── Promotion: a selection becomes a thing someone acts on ──────────────────
#
# A member list is the first place in the product where a person looks at
# THINGS rather than NUMBERS, and things are what you act on. "18" is not a
# work item; "three of these advisories have no fix" is. Three acts, and
# they are different: ADD TO WORK LIST is "I will deal with this"; RAISE RFA
# is "someone must"; NOTE IN JOURNAL is "this is worth knowing about the
# resource" — the one with no obligation attached, and the likeliest used.
#
# A promoted selection keeps its PROVENANCE and does not keep its
# MEMBERSHIP: the analysis, the run date and the members as they were, so
# the item reads "3 of 18 advisories · high · from cve_scan, run 2026-09-03".
# Never a live query. A work item that silently changes what it refers to
# when the scan re-runs is unusable; five next time is a new observation
# against the same item, not a mutation of it.
#
# Deliberately absent: "ignore" / "accept risk". That is a disposition on
# the finding — a judgement with an author and a date — and belongs to the
# perishable-field machinery, not a list's toolbar.

def provenance_line(*, analysis_id: str, run_at: str, total: int, members: list[str],
                    facet: str = "", metric: str = "") -> str:
    """The sentence every promotion carries. Members are listed by name so the
    item stays legible after the scan moves on."""
    n = len(members)
    what = metric.replace("_", " ") if metric else "members"
    head = f"{n} of {total} {what}" if total and n != total else f"{n} {what}"
    if facet:
        head += f" · {facet}"
    when = f", run {run_at[:10]}" if run_at else ""
    names = ", ".join(members[:12]) + (f", and {n - 12} more" if n > 12 else "")
    return f"{head} · from {analysis_id}{when}: {names}"


def proposed_name(display_name: str, *, total: int, members: list[str], facet: str = "",
                  metric: str = "") -> str:
    """'egeria-workspaces — 3 advisories, high'. Editable before saving."""
    what = (metric.replace("_", " ") if metric else "members")
    n = len(members)
    core = f"{n} {what}" if n != 1 else f"1 {singular(what)}"
    return f"{display_name} — {core}" + (f", {facet}" if facet else "")


def singular(noun: str) -> str:
    """'advisories' -> 'advisory', 'dependencies' -> 'dependency', 'files' ->
    'file'. The first version was `rstrip('s')`, which strips a character SET
    and produced '1 advisorie' (review, 2026-09-12). Only the last word of a
    phrase is singularised: 'data files' -> 'data file'."""
    head, _, last = noun.rpartition(" ")
    if last.endswith("ies") and len(last) > 3:
        last = last[:-3] + "y"
    elif last.endswith("ses") or last.endswith("xes") or last.endswith("ches") or last.endswith("shes"):
        last = last[:-2]
    elif last.endswith("s") and not last.endswith("ss"):
        last = last[:-1]
    return f"{head} {last}".strip()
