"""Curate: what the catalogue should know about this resource, assembled
for review before the one near-irreversible act in the product.

The design (Enrichment and Curate Wireframes, Repo Handoff item 6): one
screen, three columns answering one question -- what it IS (library,
capability, endpoints), what it HOLDS (data files, sub-resources), what it
is MADE OF (components, ports, wires) -- plus how it relates, and a
manifest of what the commit writes. A database answers the first two; a
repository answers all three. The screen is a review-and-commit, not a
form: almost everything on it was decided earlier, and this is where it is
seen assembled.

Three rules from the design, held here:
- Candidates with evidence, never auto-applied. Every row names the
  analysis it came from and its state; a candidate is `□` until a person
  confirms it, and "3 Dockerfiles" carries a question mark, not
  "Infrastructure Asset (suggested)".
- Testimony is COPIED (enrichment -> classifications on the asset, with
  author and date, because it exists nowhere else); measurements are
  LINKED (survey reports are already Egeria objects; copying them makes a
  second truth). Unresolved things travel.
- Only worthy things get curated: the population is resources dispositioned
  `tracking` or `using`. The pane says so rather than hiding the resource.

Everything here is a local read: facts, findings, enrichment, verdicts, the
disposition. Nothing calls Egeria, so the plan renders when Egeria is down
and says which parts it could not know.

**Status note (2026-09-23):** despite the "a database answers the first two"
line above, `build_plan`/`execute_curation` are implemented against
`ProjectRegistry.get(slug)` (the repo-only `projects` table) only — no
database/filesystem path exists yet. Curate is reachable as a generic
`/next` nav item for any resource type, so clicking it for a
database/filesystem used to reach these routes and 404 with no explanation;
`web/static/next/stages/curate.js`'s `renderCurate` now detects the
resource type first and shows an honest "not available for this type yet"
message instead of calling here at all, rather than this module growing a
speculative database/filesystem implementation. Whether/how to generalize
this file for real is an open design question — not resolved by that fix.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from resource_explorer.registry import ProjectRegistry

#: What a press says when nothing the person chose would be sent (brief section 2): blocked, nothing written.
NOTHING_SELECTED_SENTENCE = "nothing selected · confirm a line under what it is, or choose folders and files"

#: Curate's population, by disposition (Repo Handoff, model statement 5).
CURATE_POPULATION = ("tracking", "using")

#: Enrichment judgements that become governance classifications on the asset,
#: and the Egeria level each value maps to. The levels are Egeria's default
#: GovernanceClassificationLevel sets; a value not in the map is copied as
#: notes on the classification rather than dropped.
CONFIDENTIALITY_LEVELS = {"public": 0, "internal": 1, "confidential": 2, "restricted": 4}
CRITICALITY_LEVELS = {"low": 1, "important": 2, "critical": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _GapsOncePerPlan:
    """A FactLayer view for ONE `build_plan` call: `facts()` records the gaps once, not once per
    analysis. Each fact read used to run `record_gaps_for` (about 111 statements); the plan reads
    several analyses, so one plan paid for it several times (90% of a small fixture's plan, measured).
    `gap_passes` is what the test counts."""

    def __init__(self, layer):
        self._layer = layer
        self.gap_passes = 0
        self.resource_type = getattr(layer, "resource_type", "repo")

    def facts(self, slug: str, analysis_ids: list, level: str = "resource") -> list:
        results = [self._layer.fact(slug, a, level) for a in analysis_ids]
        if results and not self.gap_passes:
            from resource_explorer.gaps import record_gaps_for

            self.gap_passes += 1
            try:
                record_gaps_for(self._layer._registry, slug, self._layer.resource_type)
            except ValueError:
                pass
        return results


def _fact(layer, slug: str, analysis_id: str) -> dict:
    try:
        facts = layer.facts(slug, [analysis_id])
    except Exception as exc:                        # a reader that raises is a state, not a crash
        return {"analysis_id": analysis_id, "state": "unreadable", "headline": str(exc), "value": {}}
    return facts[0].as_dict() if facts else {"analysis_id": analysis_id, "state": "never_run", "value": {}}


def _findings(fact: dict) -> list[dict]:
    return list(((fact.get("value") or {}).get("findings")) or [])


def _detail(finding: dict) -> dict:
    """A finding row carries its detail as `detail_json`; a fact's finding
    carries `detail`. Read either."""
    if isinstance(finding.get("detail"), dict):
        return finding["detail"]
    try:
        return json.loads(finding.get("detail_json") or "{}") or {}
    except ValueError:
        return {}


def _row(kind: str, label: str, *, evidence: str, source: str, state: str, count: int | None = None,
         members: dict | None = None, candidate: bool = True, detail: dict | None = None) -> dict:
    """One line on the screen. `state` is the analysis's own state; `candidate`
    False means the row is a count, not a thing to confirm."""
    return {"kind": kind, "label": label, "evidence": evidence, "source": source,
            "state": state, "count": count, "members": members, "candidate": candidate,
            "detail": detail or {}}


def awaiting_counts(comps: list, comp_verdicts: dict) -> dict:
    """What Structure still waits on a person for. ONE function: it marks the made-of row's `awaiting`
    figure AND is the plan's top-level `awaiting` (the Structure tab's count), so the two cannot drift.
    A component awaits a decision when the survey found it and no verdict row exists for its path. Absence
    of a verdict is "awaiting", never "reviewed"; the count says nothing about whether the caller may decide
    it (refusals stay per item). Blueprints and dependency rows are read lazily by their own sections and
    are not part of this figure yet."""
    unreviewed = sum(1 for c in comps
                     if (c.get("path") or c.get("scope_locator") or "") not in comp_verdicts)
    return {"components": unreviewed}


#: The Context facts a repository's Curate page shows and the commit carries, in the order they are shown.
CONTEXT_FACTS = (("owner", "Owner"), ("sensitivity", "Confidentiality"), ("criticality", "Criticality"),
                 ("retention", "Retention"), ("licence", "License"))


def context_facts(enrichment: dict, doc_sources: list[dict] | None = None) -> list[dict]:
    """The Context facts, one dict each, from the Enrichment record. The single source for both the
    "What we know" block (plan["know"]) and the plan's "what gets written" (build_plan derives its
    classifications, owner and licence from these dicts). `shown` is the words the plan has always shown for
    the value ("" = not declared); an interim or unsigned fact says so (`interim`, `unsigned`).
    Documentation sources come from the doc_sources table (declared by `added_by`), each with `published` (Brief F
    §10: the repository's single Publish sends them; they are not part of the Contents commit's writes)."""
    out = []
    for key, label in CONTEXT_FACTS:
        f = enrichment.get(key) or {}
        shown, skipped = f.get("value") or "", False
        if key == "retention":
            from resource_explorer import retention_basis
            rb = retention_basis.resolve(f)
            shown = retention_basis.LABELS.get(rb["basis"], "") + (" (from existing text)" if rb["carried"] else "")
            if not rb["basis"] and (f.get("value") or f.get("note")):
                # the plan previews what the commit will do: the same words as the step's skip
                shown, skipped = "skipped · no retention basis picked", True
        author = f.get("author", "") or ""
        out.append({"key": key, "label": label, "shown": shown or "", "declared": bool(shown),
                    "skipped": skipped, "author": author, "set_at": f.get("set_at", "") or "",
                    "interim": bool(f.get("interim")), "review": bool(f.get("review")),
                    "unsigned": bool(shown) and not author})
    docs = [{"label": d.get("label") or d.get("url") or "", "url": d.get("url", ""),
             "source_type": d.get("source_type", ""), "author": d.get("added_by", "") or "",
             "set_at": d.get("added_at", "") or "",
             # Brief F §10: a repository's single Publish sends each source as an ExternalReference; the row's own
             # Egeria reference says whether it is there (the same test `single_publish.unpublished_doc_sources` uses).
             "published": bool(d.get("egeria_external_ref_guid"))} for d in (doc_sources or [])]
    out.append({"key": "documentation", "label": "Documentation sources",
                "shown": ", ".join(d["label"] for d in docs), "declared": bool(docs), "skipped": False,
                "author": docs[0]["author"] if docs else "", "set_at": docs[0]["set_at"] if docs else "",
                "interim": False, "review": False,
                "unsigned": bool(docs) and not all(d["author"] for d in docs), "sources": docs})
    return out


#: The Context facts a commit copies onto the asset as governance classifications, in order.
CLASSIFIED_FACTS = ("sensitivity", "criticality", "retention")


def _classification_write(key: str, f: dict) -> tuple[str, str, dict]:
    """(pyegeria method, human name, body) for one judgement. A value outside the level map is not dropped -- it
    goes into `notes` with level 0, because testimony is copied, not normalised away."""
    from resource_explorer import retention_basis

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def common(notes=None):
        return {"steward": f.get("author", "") or "", "stewardTypeName": "UserIdentity",
                "stewardPropertyName": "userId", "source": "resource-explorer enrichment",
                "confidence": 100,
                "notes": notes if notes is not None else f"{f.get('value','')} — set by {f.get('author','?')} on {(f.get('set_at') or '')[:10]}"
                         f"{' · interim' if f.get('interim') else ''}; copied to Egeria {stamp}"}

    if key == "sensitivity":
        return ("set_confidentiality_classification", f"Confidentiality · {f['value']}", {
            "class": "NewClassificationRequestBody",
            "properties": {"class": "ConfidentialityProperties",
                           "confidentialityLevel": CONFIDENTIALITY_LEVELS.get(str(f["value"]).lower(), 0),
                           "statusIdentifier": 0, **common()}})
    if key == "criticality":
        return ("set_criticality_classification", f"Criticality · {f['value']}", {
            "class": "NewClassificationRequestBody",
            "properties": {"class": "CriticalityProperties",
                           "criticalityLevel": CRITICALITY_LEVELS.get(str(f["value"]).lower(), 0),
                           "statusIdentifier": 0, **common()}})
    rb = retention_basis.resolve(f)
    # `status` is not sent: pyegeria ISSUE-22 -- the server ignores `status` on these classifications
    # (the real key is `statusIdentifier`), and Dr.Egeria's live-confirmed Retention path sends neither.
    label = retention_basis.LABELS[rb["basis"]]
    notes = (f"{label}{' — ' + rb['note'] if rb['note'] else ''} — set by {f.get('author','?')} on "
             f"{(f.get('set_at') or '')[:10]}{' · interim' if f.get('interim') else ''}; copied to Egeria {stamp}")
    # common() first, then the two fields this classification owns, so nothing spread can override them
    return ("set_retention_classification", f"Retention · {label}", {
        "class": "NewClassificationRequestBody",
        "properties": {**common(notes), "class": "RetentionClassificationProperties",
                       "retentionBasis": retention_basis.ordinal(rb["basis"])}})


def classification_writes(enrichment: dict, facts: list[dict] | None = None) -> list[dict]:
    """THE classifications a repository commit writes, one dict each (Brief F §9: one source). What the plan shows
    ("what gets written", the Publish list) AND what `workflows/curate_commit` writes both come from here, and
    whether a classification is written at all is decided from `context_facts()`: a fact that is declared and not
    skipped is written; a skipped one (retention with no basis) is shown with its skip words and `write` None.
    `write` is (pyegeria method, human name, body)."""
    facts = facts if facts is not None else context_facts(enrichment)
    by_key = {f["key"]: f for f in facts}
    out = []
    for key in CLASSIFIED_FACTS:
        f = by_key[key]
        if not f["shown"]:
            continue
        out.append({"key": key, "classification": f["label"], "value": f["shown"], "skipped": f["skipped"],
                    "author": f["author"], "set_at": f["set_at"], "interim": f["interim"], "review": f["review"],
                    "write": None if f["skipped"] else _classification_write(key, enrichment.get(key) or {})})
    return out


def build_plan(registry: ProjectRegistry, slug: str) -> dict:
    from resource_explorer.blueprint_kinds import kind_word
    from resource_explorer.facts import FactLayer
    from resource_explorer.repo_publish import project_state, survey_state

    project = registry.get(slug)
    if not project:
        raise KeyError(slug)
    layer = _GapsOncePerPlan(FactLayer(registry))
    disp = registry.get_disposition(project.github_url) if project.github_url else None
    disposition = (disp or {}).get("disposition") or "undecided"

    # ── what it is ─────────────────────────────────────────────────────
    #
    # `SoftwareLibrary` per distribution was a type error (docs/Backlog.md,
    # "Catalogue in layers", 2026-09-14): in Egeria that classification names
    # a server managing distribution of software modules (PyPI, Nexus) — the
    # thing that manages libraries, not a library. Layer 1 is
    # `SoftwareCapability` classified `Application`, proposed only where
    # `deployment_evidence` found deployment evidence for the distribution;
    # an importable-only distribution is not a layer-1 capability at all —
    # at most a layer-2 component, offered unticked (`_row(candidate=False)`)
    # since a package with no deployment evidence is "probably not
    # catalogued" by default, not a claim this row can back.
    what_it_is: list[dict] = []
    dep_ev = registry.query_findings(slug, "deployment_evidence")
    dist_rows = [d for d in dep_ev if d.get("check_name") == "distribution"]
    if dist_rows:
        for d in dist_rows:
            det = _detail(d)
            name = det.get("name") or "?"
            ecosystem = det.get("ecosystem", "")
            evidence_kinds = ", ".join(sorted({e.get("kind", "").replace("_", " ")
                                                for e in (det.get("evidence") or []) if e.get("kind")}))
            # `kind` doubles as the row's pick identity (app.js's
            # `data-curate-pick`) -- a monorepo declares several
            # distributions, so it must carry the name, not just the type,
            # or two applications collapse onto one checkbox.
            if d.get("label") == "application":
                what_it_is.append(_row(
                    f"SoftwareCapability::{name}", f"Software Capability · {name}",
                    evidence=evidence_kinds or d.get("summary", ""), source="deployment_evidence",
                    state="measured", members={"analysis_id": "dependency_analysis"}, detail=det))
            else:  # "library" -- importable, no deployment evidence
                what_it_is.append(_row(
                    f"SoftwareComponentCandidate::{name}",
                    f"Component, not published by default · {name} — importable {ecosystem} "
                    "distribution, no deployment evidence",
                    evidence=d.get("summary", ""), source="deployment_evidence",
                    state="measured", candidate=False, detail=det))
    else:
        # Its own kind, not bare "SoftwareCapability" -- that string is the
        # intended-use row's identity a few lines down, and both would
        # otherwise collapse onto one checkbox in app.js's pick set.
        mp = _fact(layer, slug, "manifest_parse")
        manifests = ((mp.get("value") or {}).get("dependencies") or {}).get("manifests") or []
        if manifests:
            what_it_is.append(_row(
                "SoftwareCapabilityCandidate", "Software Capability · deployment evidence not yet measured",
                evidence=f"manifests present ({', '.join(manifests)}); run deployment_evidence to "
                         "classify as an application or a component",
                source="manifest_parse", state=mp.get("state", ""), detail={"manifests": manifests}))
        else:
            what_it_is.append(_row("SoftwareCapabilityCandidate", "Software Capability?",
                                   evidence="no dependency manifest found", source="manifest_parse",
                                   state=mp.get("state", "")))

    ctx = registry.get_context("repo", slug) or {}
    enrichment = ctx.get("enrichment") or {}
    use = (enrichment.get("intended_use") or {}).get("value") or ctx.get("purpose") or ""
    if use:
        who = (enrichment.get("intended_use") or {}).get("author", "")
        what_it_is.append(_row("SoftwareCapability", f"Software Capability · {use}",
                               evidence=f"your note{' · ' + who if who else ''}", source="enrichment",
                               state="measured"))
    else:
        what_it_is.append(_row("SoftwareCapability", "Software Capability · no intended use recorded",
                               evidence="set Intended use on the Enrichment pane", source="enrichment",
                               state="needs_human"))

    iface = _fact(layer, slug, "interface_surface")
    # interface_surface's vocabulary became `declared` / `implemented` /
    # `implied` / `no` on 2026-09-14 (SPEC-ACTIONABLE-AND-HONEST.md §6),
    # replacing the old two-rung `specified` / `implied`. `specified` is kept
    # here as an accepted alias for rows a survey run BEFORE that date left
    # in the table — interface_surface.py itself never emits it again, so
    # this is read-side compatibility only, not a live label.
    _STRONG = ("declared", "implemented", "specified")
    kinds = [f for f in _findings(iface) if f.get("check_name") != "published_spec"
             and f.get("label") in (*_STRONG, "implied")]
    spec = next((f for f in _findings(iface) if f.get("check_name") == "published_spec"), None)
    if kinds:
        names = ", ".join(f["check_name"] for f in kinds)
        contract = "no contract" if (spec and spec.get("label") == "no") else "contract published"
        implied = all(f.get("label") == "implied" for f in kinds)
        what_it_is.append(_row(
            "Endpoint", f"Endpoint × {len(kinds)} · {names} · {'implied' if implied else 'declared or implemented'}, {contract}",
            evidence="; ".join(f.get("summary", "") for f in kinds)[:400], source="interface_surface",
            state=iface.get("state", ""), count=len(kinds), members={"analysis_id": "interface_surface"},
            detail={"interfaces": [f["check_name"] for f in kinds], "implied": implied}))
    conv = _fact(layer, slug, "repo_conventions")
    docker = next((f for f in _findings(conv) if f.get("check_name") == "deployment_docker"), None)
    if docker and docker.get("label") == "pass":
        files = (_detail(docker).get("files") or [])
        what_it_is.append(_row(
            "InfrastructureAsset", f"Infrastructure Asset? {len(files) or ''} Dockerfile{'s' if len(files) != 1 else ''}".replace("?  ", "? "),
            evidence=docker.get("summary", ""), source="repo_conventions", state=conv.get("state", ""),
            detail={"files": files}))

    # ── what it holds ──────────────────────────────────────────────────
    holds: list[dict] = []
    dfp = _fact(layer, slug, "data_file_profiling")
    profiles = (dfp.get("value") or {}).get("profiles") or []
    with_schema = sum(1 for p in profiles if p.get("schema_json"))
    holds.append(_row("DataFile", f"{len(profiles):,} data files · {with_schema:,} with a profiled schema",
                      evidence="datasets with schemas — the same objects a database would give you",
                      source="data_file_profiling", state=dfp.get("state", ""), count=len(profiles),
                      members={"analysis_id": "data_file_profiling"}, candidate=len(profiles) > 0))
    srs = _fact(layer, slug, "sub_resource_survey")
    subs = _findings(srs)
    worthy = [f for f in subs if f.get("label") == "worthy"]
    rejected = [f for f in subs if f.get("label") == "not_worthy"]
    holds.append(_row("SubResource", f"{len(worthy)} of {len(subs)} files and folders worth including · {len(rejected)} not",
                      evidence="each becomes its own asset, related to this one",
                      source="sub_resource_survey", state=srs.get("state", ""), count=len(worthy),
                      members={"analysis_id": "sub_resource_survey"}, candidate=len(worthy) > 0,
                      detail={"worthy": [f["check_name"] for f in worthy],
                              # locator -> file|folder, so the commit table can say "N files · M folders"
                              "kinds": {f["check_name"]: (_detail(f).get("kind") or "folder") for f in worthy}}))

    # ── what it is made of ─────────────────────────────────────────────
    verdicts = registry.get_component_verdicts("repo", slug)
    comp_v = {k: v for k, v in verdicts.items() if v.get("verdict_target", "component") == "component"}
    bp_v = {k: v for k, v in verdicts.items() if v.get("verdict_target") == "blueprint"}
    arch = _fact(layer, slug, "architecture_recovery")
    comps = (arch.get("value") or {}).get("components") or []
    accepted = sum(1 for v in comp_v.values() if v.get("verdict") == "accepted")
    awaiting = awaiting_counts(comps, comp_v)
    made_of = [
        _row("Component", f"{accepted} of {len(comps):,} components accepted · {len(comp_v)} reviewed",
             # True today, and no more (designer, 2026-09-14): interfaces.propose()
             # reads deployment artifacts at survey time and consults no verdict;
             # acceptance changes which ports get DRAWN. "Derived from the
             # accepted components" claimed the stronger thing, in the sentence a
             # curator reads to decide whether to trust the column.
             evidence="ports and wires are read from the repository's deployment artifacts; the diagram shows those "
                      "belonging to accepted components; review is per component and lives on Architecture verdicts "
                      "(current UI) until it moves here",
             source="architecture_recovery", state=arch.get("state", ""), count=accepted,
             members={"analysis_id": "architecture_recovery"}, candidate=accepted > 0,
             detail={"reviewed": len(comp_v), "blueprints_reviewed": len(bp_v), "awaiting": awaiting,
                     "blueprints_accepted": sum(1 for v in bp_v.values() if v.get("verdict") == "accepted"),
                     # the kinds of the blueprints accepted ("Deployment Blueprint"), named in the table
                     "blueprint_kinds": sorted({kind_word(k.split("::", 1)[0]) + " Blueprint"
                                                for k, v in bp_v.items() if v.get("verdict") == "accepted"})}),
    ]

    # ── how it relates ─────────────────────────────────────────────────
    deps = _fact(layer, slug, "dependency_analysis")
    n_deps = sum(len(v) for v in ((deps.get("value") or {}).get("by_ecosystem") or {}).values())
    relates = [
        _row("Dependency", f"{n_deps} dependencies · already-published ones not looked up",
             evidence="which of these are already assets in Egeria is an Egeria search, not a local fact; not built yet",
             source="dependency_analysis", state=deps.get("state", ""), count=n_deps,
             members={"analysis_id": "dependency_analysis"}, candidate=False),
    ]

    # ── what gets written ──────────────────────────────────────────────
    # ONE source for the Context facts: "What we know" (plan["know"]) and "what gets written" (writes below)
    # both come from context_facts(), so the two never disagree.
    know = context_facts(enrichment, registry.list_doc_sources("repo", slug))
    by_key = {f["key"]: f for f in know}
    # Brief F §9: the commit writes exactly these (classification_writes is the one function both read).
    classifications = [{k: v for k, v in c.items() if k != "write"} for c in classification_writes(enrichment, know)]
    owner = by_key["owner"]
    licence = by_key["licence"]
    from resource_explorer.catalogue_commit import _ts, publish_fields
    published = registry.get_last_published_annotation_types(slug) or {}
    writes = {
        "entities": [r["kind"] for r in what_it_is if r["candidate"]],
        "contained": {"data_files": len(profiles), "sub_resources": len(worthy)},
        "classifications": classifications,
        "owner": {"value": owner["shown"], "interim": owner["interim"], "author": owner["author"]},
        "licence": licence["shown"],
        "survey_reports_linked": len(published),
        "last_published_at": max(published.values(), key=_ts, default=""),
        **publish_fields(registry, slug, max(published.values(), key=_ts, default="")),
        "catalogued": bool(getattr(project, "egeria_asset_guid", "")),
        "asset_guid": getattr(project, "egeria_asset_guid", "") or "",
    }

    return {
        "slug": slug,
        "display_name": project.display_name,
        "technology_type": "Git Repository",
        "disposition": disposition,
        "in_population": disposition in CURATE_POPULATION,
        "population": list(CURATE_POPULATION),
        "last_surveyed_at": getattr(project, "last_surveyed_at", "") or "",
        # The kept survey a commit would publish (brief sections 1 and 2): its date, age, size, stale steps.
        "survey": survey_state(registry, slug),
        # The Egeria project answer ("unset" blocks a press until it is answered), read without writing it.
        "project": project_state(registry, slug),
        "what_it_is": what_it_is,
        "what_it_holds": holds,
        "made_of": made_of,
        # Structure's tab count: the SAME figure the made-of row carries in its detail (awaiting_counts).
        "awaiting": awaiting,
        "relates": relates,
        "writes": writes,
        # Tab 1's read-only "What we know" block: the confirmed Context facts, each with who declared it and when.
        "know": know,
        "keeps_current": "Resource Explorer surveys, on the Automate schedule. No integration connector exists for this type.",
        "commits": Curations(registry).for_resource("repo", slug),
    }


# ── the record of the act ──────────────────────────────────────────────

# The resource_curation table is created by ProjectRegistry._init_schema (a migration),
# not here: DDL on a GET fails under a read-only session.


@dataclass
class CurationStep:
    name: str
    state: str = "pending"        # pending | running | done | failed | skipped
    detail: str = ""
    at: str = ""


class Curations:
    """Append-only record of each Catalogue → press: who, when, what was
    selected, what the manifest said it would write, and how each step went.
    The commit is asynchronous and can fail elsewhere (the design's point),
    so the record is what the screen shows while it runs and after."""

    def __init__(self, registry: ProjectRegistry):
        self.registry = registry

    def _conn(self):
        return self.registry._conn()

    def create(self, entity_type: str, slug: str, *, author: str, selection: dict, manifest: dict,
               steps: list[str], activity_id: str = "") -> dict:
        if not author:
            raise ValueError("a curation needs an author")
        cid = uuid.uuid4().hex
        now = _now()
        rows = [CurationStep(s).__dict__ for s in steps]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO resource_curation (id, entity_type, entity_slug, author, requested_at, selection, manifest, state, steps, activity_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                (cid, entity_type, slug, author, now, json.dumps(selection), json.dumps(manifest), json.dumps(rows), activity_id))
        return self.get(cid)

    def create_report(self, entity_type: str, slug: str, *, author: str, name: str, report: dict,
                      corrects: str = "") -> dict:
        """A report record: the act of writing a list down. No steps; done
        the moment it is written. `report` holds the question as asked, the
        analysis id and its run_at, the facet, total and shown, and the rows
        themselves -- a snapshot of names with their detail, never a stored
        filter. Append-only: a correction is a new record naming what it
        corrects (`corrects`), never an edit."""
        if not author:
            raise ValueError("a report needs an author")
        if not name.strip():
            raise ValueError("a report needs a name")
        cid = uuid.uuid4().hex
        now = _now()
        old = self.get(corrects) if corrects else None
        if corrects and (not old or old["entity_slug"] != slug or old.get("kind") != "report"):
            raise ValueError("a correction must name a report record on the same resource")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO resource_curation (id, entity_type, entity_slug, author, requested_at, selection, manifest, "
                "state, steps, activity_id, kind, name, report, finished_at, corrects) "
                "VALUES (?, ?, ?, ?, ?, '{}', '{}', 'done', '[]', '', 'report', ?, ?, ?, ?)",
                (cid, entity_type, slug, author, now, name.strip(), json.dumps(report), now, corrects))
            if old:
                # The superseded record learns who corrected it. Its content
                # is not touched; this sits where its uses appear.
                conn.execute("UPDATE resource_curation SET corrected_by = ? WHERE id = ?",
                             (json.dumps({"id": cid, "name": name.strip(), "at": now, "by": author}), corrects))
        return self.get(cid)

    def add_use(self, cid: str, *, act: str, target: str, target_name: str, by: str) -> dict:
        """Append one use -- 'added to work list "…"', 'raised RFA …', 'cited
        in the journal' -- to the record's uses. Append-only; the record's
        content is never touched."""
        rec = self.get(cid)
        if not rec:
            raise KeyError(cid)
        uses = list(rec.get("uses") or [])
        uses.append({"act": act, "target": target, "target_name": target_name, "at": _now(), "by": by})
        with self._conn() as conn:
            conn.execute("UPDATE resource_curation SET uses = ? WHERE id = ?", (json.dumps(uses), cid))
        return self.get(cid)

    def record_layer2_offer(self, cid: str, outcome: str, decided_by: str) -> dict:
        """Record the outcome of the layer-2 catalogue-depth offer on this
        catalogue record (owner's ruling, 2026-09-15) -- same
        accepted/declined/chose vocabulary DepthOffer uses for a repo's
        never-run analyses, mirrored here at once-per-CATALOGUE-RECORD
        rather than once-per-disposition-verdict, since the layer-2 act
        (component materialization) belongs to a curate commit, not to a
        disposition.

        Once per record: raises `ValueError` if this record already carries
        an offer outcome -- the pane only offers when it reads none, but
        that is a UI courtesy, not the enforcement; this is (same shape as
        `registry.record_depth_offer`)."""
        from resource_explorer.workflows.catalogue_depth_offer import LAYER2_OFFER_OUTCOMES

        if outcome not in LAYER2_OFFER_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(LAYER2_OFFER_OUTCOMES)}, got {outcome!r}")
        rec = self.get(cid)
        if not rec:
            raise LookupError(cid)
        if rec.get("layer2_offer"):
            raise ValueError(f"publish record {cid} already carries a layer-2 offer outcome")
        payload = {"outcome": outcome, "decided_by": decided_by, "at": _now()}
        with self._conn() as conn:
            conn.execute("UPDATE resource_curation SET layer2_offer = ? WHERE id = ?",
                         (json.dumps(payload), cid))
        return self.get(cid)

    def get(self, cid: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM resource_curation WHERE id = ?", (cid,)).fetchone()
        return self._decode(row) if row else None

    def latest(self, entity_type: str, slug: str) -> dict | None:
        """The newest record for a resource, one cheap read (the commit line survives a reload without the
        plan, which can take half a minute)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM resource_curation WHERE entity_type = ? AND entity_slug = ? "
                "ORDER BY requested_at DESC LIMIT 1", (entity_type, slug)).fetchone()
        return self._decode(row) if row else None

    def for_resource(self, entity_type: str, slug: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_curation WHERE entity_type = ? AND entity_slug = ? ORDER BY requested_at DESC",
                (entity_type, slug)).fetchall()
        return [self._decode(r) for r in rows]

    def set_step(self, cid: str, name: str, state: str, detail: str = "", more: str = "") -> None:
        """Record a step's state. `more` is the long tail of Egeria's own message, kept apart so
        the screen can show the first sentence and fold the rest under "details"."""
        cur = self.get(cid)
        if not cur:
            return
        steps = cur["steps"]
        for s in steps:
            if s["name"] == name:
                s.update({"state": state, "detail": detail[:2000], "at": _now()})
                if more:
                    s["more"] = more[:4000]
                else:
                    s.pop("more", None)
        with self._conn() as conn:
            conn.execute("UPDATE resource_curation SET steps = ?, state = 'running' WHERE id = ?",
                         (json.dumps(steps), cid))

    def settle_step(self, cid: str, name: str, state: str, detail: str = "", more: str = "") -> None:
        """Update one step of a record that may already be finished, WITHOUT reopening it:
        `set_step` marks the record running, which is wrong for a later read-back that only
        learns how an earlier step turned out."""
        cur = self.get(cid)
        if not cur:
            return
        steps = cur["steps"]
        for s in steps:
            if s["name"] == name:
                s.update({"state": state, "detail": detail[:2000], "at": _now()})
                if more:
                    s["more"] = more[:4000]
                else:
                    s.pop("more", None)
        with self._conn() as conn:
            conn.execute("UPDATE resource_curation SET steps = ? WHERE id = ?", (json.dumps(steps), cid))

    def finish(self, cid: str) -> dict:
        cur = self.get(cid)
        states = {s["state"] for s in cur["steps"]}
        final = "failed" if "failed" in states else "done"
        with self._conn() as conn:
            conn.execute("UPDATE resource_curation SET state = ?, finished_at = ? WHERE id = ?",
                         (final, _now(), cid))
        return self.get(cid)

    @staticmethod
    def _decode(row) -> dict:
        d = dict(row) if not isinstance(row, dict) else dict(row)
        for k in ("selection", "manifest", "steps", "report", "uses", "corrected_by", "layer2_offer"):
            empty = "[]" if k in ("steps", "uses") else "{}"
            try:
                d[k] = json.loads(d.get(k) or empty)
            except ValueError:
                d[k] = [] if k in ("steps", "uses") else {}
        d.setdefault("kind", "catalogue")
        d.setdefault("name", "")
        d.setdefault("corrects", "")
        return d
