"""Sub-surveyor: what can you talk to, and is the contract written down?

Answers two of the questions in the catalog from data already collected — the
file inventory, the parsed dependencies, and (new, §6 of
`SPEC-ACTIONABLE-AND-HONEST.md`) the two other walks that already know facts
this one used to ignore: `distribution` findings (repo_manifest_parse's
DistributionParser — `[project.scripts]`/`package.json bin`) and
`deployment_evidence` findings (a `__main__.py` under the distribution's own
package). No fetch either way — everything here is a second READ of a table
another step already wrote, never a second parse of the manifest itself.

    "What kind of interfaces does it have?"  -> the interface kinds below
    "Is there a published API?"              -> the `published_spec` finding

One analysis rather than two, because the second is a finding ABOUT the
first: a library with 171 exported symbols has an interface surface and no
published contract, and answering those separately would mean re-deriving
the same evidence twice and letting the two disagree.

**Three rungs, not two — the vendored-rule defect one analytic over.** The
old two-rung ladder ("specified" / "implied") put almost everything on the
wrong rung: a `[project.scripts]` entry point IS the CLI, not a hint of one,
and the old code answered "cli — implied. Depends on click" for a repo whose
own manifest names the entry point — a fact `repo_role.py`,
`distribution_parser.py` ("the CLI entry point as a fact"), and
`deployment_evidence.py` (#94, a console entry point is the strongest signal)
already knew and this file never read. That is the same shape as the
vendored-walk defect: a fact known by one walk and not read by another,
producing a confident sentence the repository itself contradicts.

  declared     a contract committed in the repo (openapi.yaml, .proto, …),
               OR an entry point the packaging declares
               ([project.scripts]/package.json bin) — read from the SAME
               `distribution` finding rows deployment_evidence.py reads,
               never a second parse of pyproject.toml/package.json.
  implemented  the interface exists in the code whether or not anything
               documents it. From stored evidence when it exists — a
               `__main__.py` under the distribution's own package
               (deployment_evidence's `dunder_main` evidence, itself
               path-only, no content read) is implemented-CLI evidence with
               no declared entry point. For the five route-like kinds
               (http_api, grpc, graphql, messaging, soap), evidence is read
               from `project_code_markers` — decorator/annotation
               registrations `repo_symbol_extraction` records for Python and
               Java (DESIGN-INTERFACE-SURFACE-IMPLEMENTED-RUNG.md) — and,
               as a secondary source, `architecture_interfaces` port
               findings (`arch_recovery`'s FastAPI route count). Both are
               reads of rows another step already wrote — never a second
               fetch or a second parse — same shape as `distribution`/
               `deployment_evidence` above. Where neither source has a row
               for this repo (`repo_symbol_extraction` has not run, or its
               languages are outside marker coverage), the rung is reported
               `could_not_check` with the reason, and the finding SAYS SO
               rather than silently guessing `implied`. Where the capture
               HAS run and covered a marker-capable language but found no
               registration for a kind, that is a real measured zero, not a
               could_not_check.
  implied      a dependency suggests an interface and nothing confirms it —
               and only reached when neither higher rung applies for that
               interface kind.

Measured across the catalog on 2026-08-26 to ground the detection rather than
guess at it: 9 openapi/swagger files across 4 repos, 32 .proto across 4, 2
GraphQL schemas across 2 — while 17 repos depend on an HTTP framework and 22 on
a CLI framework. The two sources disagree far more often than they overlap,
which is exactly why they are not merged into one verdict.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from resource_explorer.ingestion.code_symbol_extractor import MARKER_CAPABLE_LANGUAGES
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation

log = logging.getLogger(__name__)

STEP = "InterfaceSurface"

#: Said once, so the annotation and the persisted finding cannot drift apart.
_NOTHING_TO_ASSESS = ("Neither a file inventory nor parsed dependencies are "
                             "recorded, so no interface could be detected — this is "
                             "not a finding that it exposes none. Run the file "
                             "inventory and dependency analyses first.")

#: The three-rung ladder (§6). `DECLARED` and `IMPLEMENTED` are both strong —
#: a committed contract or a packaging-declared entry point is exactly as
#: much a fact as code that provably runs; `IMPLIED` is a guess from a
#: dependency name. `SPECIFIED` is kept as the OLD label, still emitted
#: nowhere new but read by `curate_plan.py`, which has not been migrated —
#: see that module's comment. `detect()` itself never emits `SPECIFIED`
#: after this change; it emits `DECLARED`.
DECLARED = "declared"
IMPLEMENTED = "implemented"
IMPLIED = "implied"
#: Kept only as the historical name for DECLARED-via-spec-file, referenced by
#: a comment below; not used as a label value.
SPECIFIED = DECLARED

#: Confidence by rung — declared/implemented are both facts (packaging
#: manifest or running code), implied is a guess from a dependency name.
#: Kept as the same two numbers the old ladder used (100/60) so this change
#: does not also silently reweight every existing confidence-based reader;
#: IMPLEMENTED sits between them because it is a fact, but a weaker-attested
#: one than a committed contract or a declared entry point.
_CONFIDENCE = {DECLARED: 100, IMPLEMENTED: 90, IMPLIED: 60}

#: Spec files, checked against real paths in the catalog rather than invented.
#: Anchored to the basename or a directory segment so a stray "swagger" inside
#: a vendored bundle's filename does not count as this project's contract.
_SPEC_PATTERNS = {
    "openapi": re.compile(r"(^|/)(openapi|swagger)\.(ya?ml|json)$", re.I),
    "openapi_dir": re.compile(r"(^|/)(openapi|swagger)/[^/]+\.(ya?ml|json)$", re.I),
    "grpc": re.compile(r"\.proto$"),
    "graphql": re.compile(r"\.(graphql|gql)$", re.I),
    "asyncapi": re.compile(r"(^|/)asyncapi\.(ya?ml|json)$", re.I),
    "soap": re.compile(r"\.(wsdl)$", re.I),
}

#: Spec kind -> the interface kind it proves.
_SPEC_TO_INTERFACE = {
    "openapi": "http_api", "openapi_dir": "http_api", "grpc": "grpc",
    "graphql": "graphql", "asyncapi": "messaging", "soap": "soap",
}

#: Dependency names that IMPLY an interface. Drawn from what the catalog
#: actually contains — graphql frameworks are absent from every repo here, and
#: are listed anyway so a future one is not silently missed.
_DEPENDENCY_SIGNALS = {
    "http_api": {"fastapi", "flask", "django", "starlette", "express", "koa",
                 "axum", "actix-web", "gin", "spring-boot", "hapi", "tornado",
                 "sanic", "bottle", "falcon", "spring-boot-starter-web"},
    "grpc": {"grpcio", "grpc", "grpc-java", "google.golang.org/grpc", "tonic",
             "@grpc/grpc-js"},
    "graphql": {"graphql", "graphene", "strawberry-graphql", "apollo-server",
                "graphql-java"},
    "messaging": {"kafka-python", "confluent-kafka", "pika", "aio-pika",
                  "nats-py", "celery", "kombu"},
    "cli": {"typer", "click", "clap", "cobra", "commander", "picocli"},
}

#: Interface kinds for which "implemented" means route/handler/service-
#: method registrations in code (see module docstring). `cli` is
#: deliberately absent: a `__main__.py` under the distribution's own package
#: IS recorded, by deployment_evidence's `dunder_main` evidence, so cli's
#: implemented rung can actually be checked without project_code_markers.
#:
#: Renamed from `_ROUTE_LIKE_KINDS` (DESIGN-INTERFACE-SURFACE-IMPLEMENTED-
#: RUNG.md §3.2) — it was doing two jobs, naming the kinds that need
#: registration evidence AND standing in for "cannot be checked". Now that
#: project_code_markers can answer some of these, the two are split: this
#: set still names exactly the same kinds, and `_could_not_check_reason()`
#: below computes the second job per repo instead of being baked in here.
_REGISTRATION_KINDS = {"http_api", "grpc", "graphql", "messaging", "soap"}

#: `architecture_interfaces` port protocol -> the interface_surface kind it
#: proves (§1b/§3.2). Thrift has no dedicated interface_surface kind of its
#: own — mapped to "grpc" as the closest existing bucket (both are IDL-based
#: binary RPC frameworks); a judgement call, not a specified mapping.
_PORT_PROTOCOL_TO_INTERFACE_KIND = {
    "HTTP/REST": "http_api",
    "gRPC": "grpc",
    "GraphQL": "graphql",
    "Thrift": "grpc",
}


def _could_not_check_reason(capture_coverage: dict) -> str:
    """Per-repo reason a route-like kind's `implemented` rung could not be
    checked — replaces the old permanent, pipeline-wide strings (§3.2: this
    was "the part most at risk of being cleaned up during implementation").

    `capture_coverage` is {"has_run": bool, "languages_present": [...],
    "marker_capable_languages_present": [...]} — see
    InterfaceSurfaceSurveyor._capture_coverage()."""
    if not capture_coverage.get("has_run"):
        return "no code-marker extraction has run for this repository"
    uncovered = sorted(
        l for l in capture_coverage.get("languages_present", [])
        if l not in MARKER_CAPABLE_LANGUAGES
    )
    if uncovered:
        return f"route registrations are not captured for {', '.join(uncovered)}"
    # has_run but no languages at all recorded — an edge case (e.g. a repo
    # with a populated file inventory but no source files any extractor
    # recognized), stated rather than defaulting to a stale-sounding reason.
    return "route registrations are not captured for this repository's languages"


_NO_CAPTURE_COVERAGE = {"has_run": False, "languages_present": [],
                          "marker_capable_languages_present": []}


def _registrations(marker_rows: list[dict] | None,
                    arch_interface_details: list[dict] | None) -> dict[str, list[dict]]:
    """Registration evidence keyed by interface_kind, read in preference
    order (§3.2):

    1. `project_code_markers` rows for the slug — the new, general fact.
    2. `architecture_interfaces` findings with `detail.kind == "port"` and a
       protocol in `_PORT_PROTOCOL_TO_INTERFACE_KIND` — §1b, a real,
       already-computed fact from `arch_recovery` (FastAPI route counts via
       ast-grep), worth reading on its own even before markers exist for a
       given repo, but FastAPI-only and gated on `repo_arch_detect` having
       run.

    Both are reads of already-stored rows, never a fetch or a re-parse.
    Where a marker-derived finding already answers a kind, the arch-
    interfaces count for that SAME kind is skipped rather than duplicated —
    project_code_markers is the general-purpose, multi-framework source."""
    out: dict[str, list[dict]] = {}
    from resource_explorer.ingestion.test_paths import is_test_path
    for row in marker_rows or []:
        kind = row.get("interface_kind")
        if not kind:
            continue
        # Rows stored before extraction learned to skip test files still sit in
        # the table until the next survey: a test's mock patch is not a route.
        if is_test_path(row.get("file_path", "")):
            continue
        value = row.get("detail") or row.get("qualified_name") or row.get("file_path", "")
        out.setdefault(kind, []).append({
            "kind": "code_marker", "value": value,
            "source_analysis": "project_code_markers", "path": row.get("file_path", ""),
            "framework": row.get("framework", ""),
        })
    for detail in arch_interface_details or []:
        if detail.get("kind") != "port":
            continue
        kind = _PORT_PROTOCOL_TO_INTERFACE_KIND.get(detail.get("protocol") or "")
        if not kind or kind in out:
            continue
        out.setdefault(kind, []).append({
            "kind": "architecture_interfaces_port",
            "value": detail.get("component", ""),
            "source_analysis": "architecture_interfaces", "path": "",
            "operation_count": (detail.get("additionalProperties") or {}).get("operationCount"),
        })
    return out

#: Trees whose contents are not this project's published contract.
#:
#: Three kinds, and the third was found by reading real output rather than
#: reasoning about it: OpenLineage was reported as publishing a gRPC API on the
#: strength of `integration/flink/flink1/src/test/resources/InputEvent.proto`
#: and `.../src/test/proto/ProtobufTestEvent.proto` — Maven/Gradle test
#: fixtures whose filenames literally say Test. A spec under a test tree is
#: something the project parses, not something it offers.
#:
#:   vendored   — belongs to a dependency
#:   generated  — build output, not source
#:   test       — fixtures, including Java's src/test and src/it layouts
_VENDORED = re.compile(
    r"(^|/)(node_modules|vendor|third_party|thirdparty|\.venv|site-packages|"
    r"dist|build|target|out|generated|"
    r"testdata|fixtures?|examples?|samples?|"
    r"tests?|src/test|src/it)/", re.I)


def _finding(name: str, label: str, summary: str, detail: dict) -> dict:
    return {"check_name": name, "label": label, "summary": summary,
            "confidence": _CONFIDENCE.get(label, 60), "detail": detail}


def _cli_entry_points(distribution_details: list) -> list[dict]:
    """console_script evidence from stored `distribution` finding detail
    dicts — DistributionParser's shape (`scripts`, `script_targets`,
    `script_table`, `manifest`). Never re-parses pyproject.toml/package.json;
    reads exactly the fields repo_manifest_parse already wrote."""
    out: list[dict] = []
    for d in distribution_details or []:
        scripts = d.get("scripts") or []
        if not scripts:
            continue
        targets = d.get("script_targets") or {}
        table = d.get("script_table") or "the packaging manifest"
        manifest = d.get("manifest") or ""
        for name in scripts:
            target = targets.get(name)
            value = f'{name} = "{target}"' if target else name
            out.append({"kind": "console_script", "value": f"{value} in {table}",
                        "source_analysis": "distribution", "path": manifest})
    return out


def _cli_dunder_main(deployment_evidence_details: list) -> list[dict]:
    """implemented-CLI evidence from stored `deployment_evidence` finding
    detail dicts (check_name="distribution") — each carries an `evidence`
    list already computed by deployment_evidence.classify_distribution, which
    itself only reads project_file_inventory paths (path-only, no content).
    Never re-walks the file tree here."""
    out: list[dict] = []
    for d in deployment_evidence_details or []:
        for item in d.get("evidence") or []:
            if item.get("kind") == "dunder_main":
                out.append({"kind": "dunder_main", "value": item.get("path", ""),
                            "source_analysis": "deployment_evidence", "path": item.get("path", "")})
    return out


def detect(
    file_paths: list, dependency_names: list,
    distribution_details: list | None = None,
    deployment_evidence_details: list | None = None,
    marker_rows: list | None = None,
    arch_interface_details: list | None = None,
    capture_coverage: dict | None = None,
) -> list:
    """Interface findings from paths, dependencies, and the entry-point/
    deployment/registration facts other walks already recorded — evidence
    kept apart, and every finding's `detail` keeps `evidence` as a list of
    {kind, value, source_analysis} so a reader can see WHERE a rung came
    from, not just which rung it landed on.

    `distribution_details` / `deployment_evidence_details` are the `detail`
    dicts from `project_analysis_findings` kind="distribution" / kind=
    "deployment_evidence" rows. `marker_rows` are `project_code_markers`
    rows for the slug; `arch_interface_details` are the `detail` dicts from
    kind="architecture_interfaces" rows. `capture_coverage` is
    {"has_run": bool, "languages_present": [...],
    "marker_capable_languages_present": [...]} — see
    InterfaceSurfaceSurveyor._capture_coverage(). None/absence for any of
    these is treated as empty/never-run, never re-parsed manifests, re-
    walked trees or a second fetch. All default to `None` so every existing
    call site — and the pre-existing tests written before these parameters
    existed — keeps working unchanged.
    """
    out: list = []
    capture_coverage = capture_coverage or _NO_CAPTURE_COVERAGE

    # ── specs: declared via a committed contract ─────────────────────────
    specs: dict = {}
    for path in file_paths:
        if _VENDORED.search(path or ""):
            continue
        for kind, pattern in _SPEC_PATTERNS.items():
            if pattern.search(path):
                specs.setdefault(_SPEC_TO_INTERFACE[kind], []).append(path)

    for interface, paths in sorted(specs.items()):
        out.append(_finding(
            interface, DECLARED,
            f"{len(paths)} specification file(s): {', '.join(sorted(paths)[:3])}"
            + (" …" if len(paths) > 3 else ""),
            {"evidence": [{"kind": "specification_file", "value": p,
                          "source_analysis": "file_inventory"} for p in sorted(paths)[:20]],
             "spec_path": sorted(paths)[0], "file_count": len(paths),
             "routes": None},
        ))

    # ── cli: declared via a packaging-declared entry point ───────────────
    cli_declared = _cli_entry_points(distribution_details or [])
    if cli_declared and "cli" not in specs:
        names = ", ".join(e["value"] for e in cli_declared[:3])
        out.append(_finding(
            "cli", DECLARED, f"Entry point declared: {names}"
            + (" …" if len(cli_declared) > 3 else ""),
            {"evidence": cli_declared, "spec_path": cli_declared[0]["path"], "routes": None},
        ))

    # ── cli: implemented via a __main__.py, when not already declared ────
    cli_implemented = _cli_dunder_main(deployment_evidence_details or [])
    if cli_implemented and "cli" not in specs and not cli_declared:
        paths = ", ".join(e["value"] for e in cli_implemented[:3])
        out.append(_finding(
            "cli", IMPLEMENTED,
            f"No declared entry point, but the code runs as one: {paths}"
            + (" …" if len(cli_implemented) > 3 else ""),
            {"evidence": cli_implemented, "spec_path": "", "routes": None},
        ))

    # ── route-like kinds: implemented via code-marker/arch-interface
    # registrations, when not already declared via a spec ────────────────
    registrations = _registrations(marker_rows, arch_interface_details)
    implemented_registration_kinds: set = set()
    for interface in sorted(_REGISTRATION_KINDS):
        regs = registrations.get(interface)
        if not regs or interface in specs:
            continue
        implemented_registration_kinds.add(interface)
        modules = sorted({r["path"] for r in regs if r.get("path")})
        summary = (f"{len(regs)} registration(s) found in code"
                   + (f", across {', '.join(modules[:3])}"
                      + (" …" if len(modules) > 3 else "") if modules else ""))
        out.append(_finding(
            interface, IMPLEMENTED, summary,
            {"evidence": regs, "spec_path": "",
             "routes": {"count": len(regs), "modules": modules[:20],
                        "could_not_check_reason": None}},
        ))

    # ── dependencies: weaker evidence, only when no higher rung landed ────
    already_ranked = (specs.keys()
                       | ({"cli"} if (cli_declared or cli_implemented) else set())
                       | implemented_registration_kinds)
    names = {(n or "").lower().split("[")[0] for n in dependency_names}
    for interface, signals in sorted(_DEPENDENCY_SIGNALS.items()):
        matched = sorted(names & signals)
        if not matched or interface in already_ranked:
            # Already proven by a stronger rung — a weaker duplicate would
            # only muddy it.
            continue
        detail = {"evidence": [{"kind": "dependency", "value": m,
                                "source_analysis": "dependency"} for m in matched],
                   "spec_path": "", "dependencies": matched}
        summary = (f"Depends on {', '.join(matched[:3])} — suggests a "
                   f"{interface.replace('_', ' ')}, but nothing in the repo "
                   "declares or implements one.")
        if interface in _REGISTRATION_KINDS:
            # Three outcomes, not two (§3.2's table) — collapsing this back
            # to a permanent could_not_check string is exactly the
            # regression the design doc calls out as most likely.
            if (capture_coverage.get("has_run")
                    and capture_coverage.get("marker_capable_languages_present")):
                # The capture ran and covered a marker-capable language, and
                # genuinely found no registration for this kind — a real
                # finding about the repository, not about our coverage of it.
                detail["routes"] = {"count": 0, "modules": [], "could_not_check_reason": None}
                summary += (" Code-marker extraction has run and found no "
                            "route/handler registration for this dependency.")
            else:
                reason = _could_not_check_reason(capture_coverage)
                detail["routes"] = {"count": None, "modules": [], "could_not_check_reason": reason}
                summary += f" Whether it is implemented could not be checked: {reason}."
        else:
            detail["routes"] = None
        out.append(_finding(interface, IMPLIED, summary, detail))

    # ── the published-API answer ─────────────────────────────────────────────
    # Its own finding, and deliberately keyed on SPECS only. Neither a
    # declared entry point nor a dependency is a published CONTRACT; a
    # committed openapi.yaml is. Answering the question from a weaker signal
    # is the whole failure this separation exists to avoid.
    if specs:
        kinds = ", ".join(sorted(specs))
        out.append(_finding(
            "published_spec", "yes",
            f"A machine-readable contract is committed: {kinds}.",
            {"evidence": "specification file", "kinds": sorted(specs)},
        ))
    else:
        weaker = [f["check_name"] for f in out if f["label"] in (IMPLEMENTED, IMPLIED)]
        out.append(_finding(
            "published_spec", "no",
            ("No specification file found in the repository."
             + (f" Interfaces are declared or implemented without a published "
                f"contract ({', '.join(weaker)})." if weaker else "")),
            {"evidence": "absence of specification files",
             "implied_interfaces": weaker},
        ))
    return out


class InterfaceSurfaceSurveyor(BaseSurveyor):
    """Interface kinds and whether a contract is published."""

    def __init__(self, project, registry, surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or datetime.now(timezone.utc).replace(
            tzinfo=None).isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    @staticmethod
    def _detail(row: dict) -> dict:
        d = row.get("detail")
        if isinstance(d, dict):
            return d
        import json
        raw = row.get("detail_json")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _capture_coverage(metrics: dict) -> dict:
        """From `registry.query_metrics(slug, "symbol_extraction")` — the
        backfill-flag read (DESIGN-INTERFACE-SURFACE-IMPLEMENTED-RUNG.md
        Decisions §2). `metrics` is `{}` when repo_symbol_extraction has
        never run for this repo.

        `has_run` keys on the `markers_captured` flag the surveyor writes
        into its metric's `detail`, NOT on the metric row merely existing:
        a repo surveyed before project_code_markers shipped has a
        symbol_extraction metric row with no such flag, and reading that
        as "capture ran" would misreport a pre-existing repo's genuine
        absence-of-capture as a measured zero — the exact backfill trap the
        design doc names."""
        if not metrics:
            return dict(_NO_CAPTURE_COVERAGE)
        detail = metrics.get("detail") or {}
        has_run = bool(detail.get("markers_captured"))
        languages_present = sorted((detail.get("by_language") or {}).keys())
        capable_present = sorted(
            l for l in languages_present if l in MARKER_CAPABLE_LANGUAGES
        )
        return {"has_run": has_run, "languages_present": languages_present,
                "marker_capable_languages_present": capable_present}

    def run(self) -> list[Annotation]:
        out: list[Annotation] = []
        try:
            slug = self.project.slug
            paths = self.registry.get_file_inventory(slug)   # own files only; the registry holds the filter
            deps = [d.get("dep_name") for d in (self.registry.query_dependencies(slug) or [])]

            # Read, never re-derive: the SAME `distribution` / `deployment_
            # evidence` finding rows repo_manifest_parse's DistributionParser
            # and deployment_evidence.py already wrote. Absent when those
            # steps have not run for this repo — treated the same as "no
            # rows", not as an error, since a repo surveyed before this
            # change (or without ingestion at all — see manifest_parse.py's
            # own docstring on the org-import/discovery path) legitimately
            # has none yet.
            distribution_details = [self._detail(r) for r in
                                    (self.registry.query_findings(slug, "distribution") or [])]
            deployment_evidence_details = [self._detail(r) for r in
                                           (self.registry.query_findings(slug, "deployment_evidence") or [])
                                           if r.get("check_name") == "distribution"]

            # project_code_markers + architecture_interfaces — the two
            # sources §3.2 reads for the implemented rung on the five
            # route-like kinds, in preference order. Both are second READS
            # of tables another step already wrote (repo_symbol_extraction,
            # repo_arch_detect respectively) — no fetch here either way.
            # capture_coverage backs the three-outcome could_not_check vs.
            # measured-zero split; see _capture_coverage's own docstring for
            # why it keys on the metric's markers_captured flag rather than
            # the metric row's mere existence.
            marker_rows = self.registry.get_code_markers(slug)
            arch_interface_details = [self._detail(r) for r in
                                      (self.registry.query_findings(slug, "architecture_interfaces") or [])]
            capture_coverage = self._capture_coverage(
                self.registry.query_metrics(slug, "symbol_extraction"))

            if not paths and not deps:
                # Neither input exists. "No interfaces" would be a finding about
                # the repo; this is a finding about our coverage of it.
                # Persist the reason, not just annotate it. The annotation says
                # "not established"; the results card reads FINDINGS, so returning
                # without writing one made the card render an absence where a stated
                # reason exists — "we have nothing" instead of "we could not tell, and
                # here is why". Found 2026-08-27 via kedro_kubeflow, which has neither
                # commits nor stats and showed a blank card.
                self.registry.upsert_finding(
                    slug, "interface_surface",
                    [{"check_name": "interface_surface", "label": "not_established",
                      "summary": _NOTHING_TO_ASSESS, "confidence": 0,
                      "detail": {"known": False}}],
                    surveyed_at=self._surveyed_at,
                )
                out.append(ClassificationAnnotation(
check_name="interface_surface",
summary=_NOTHING_TO_ASSESS,
                    analysis_step=STEP,
                    candidate_classifications=["not_established"],
                    confidence=0,
                ))
                return out

            findings = detect(paths, deps, distribution_details, deployment_evidence_details,
                              marker_rows, arch_interface_details, capture_coverage)
            self.registry.upsert_finding(slug, "interface_surface", findings,
                                         surveyed_at=self._surveyed_at)

            declared = [f["check_name"] for f in findings if f["label"] == DECLARED]
            implemented = [f["check_name"] for f in findings if f["label"] == IMPLEMENTED]
            implied = [f["check_name"] for f in findings if f["label"] == IMPLIED]
            published = next((f for f in findings
                              if f["check_name"] == "published_spec"), None)
            summary = (
                (f"Declared: {', '.join(declared)}. " if declared else "")
                + (f"Implemented: {', '.join(implemented)}. " if implemented else "")
                + (f"Implied by dependencies: {', '.join(implied)}. " if implied else "")
                + (f"Published contract: {published['label']}."
                   if published else "")
            ) or "No interface signals found in the file inventory or dependencies."
            # Coverage travels with the answer: detection reads DECLARED
            # dependencies and the recorded inventory (plus, now, the
            # distribution/deployment_evidence tables), so a spec that is
            # generated at build time is invisible here.
            summary += (f" Read from {len(paths)} recorded file(s), "
                        f"{len(deps)} declared dependenc(ies), "
                        f"{len(distribution_details)} distribution(s), "
                        f"{len(deployment_evidence_details)} deployment-evidence row(s), "
                        f"{len(marker_rows)} code marker(s), and "
                        f"{len(arch_interface_details)} architecture-interface row(s).")

            # A zero here has two very different meanings and they were
            # reported identically. Detection reads the recorded file inventory
            # and the DECLARED dependencies, so "no interface signals" means
            # either this repo genuinely exposes nothing, or there was nothing
            # to read — an empty inventory and no parsed manifests produce the
            # same reassuring sentence as a thoroughly-examined library.
            #
            # The inputs are the known-positive: having read real files and
            # real dependencies and still found no interface is a provable
            # zero. Having read neither is not a finding about the repo.
            examined = len(paths) + len(deps)
            if declared or implemented or implied or published:
                outcome = StepOutcome("recovered", detail={
                    "files_read": len(paths), "dependencies_read": len(deps)})
            else:
                outcome = no_signal(
                    "no interface signals in the recorded inventory or declared dependencies",
                    known_positive=examined > 0,
                    files_read=len(paths), dependencies_read=len(deps),
                )
            out.append(ClassificationAnnotation(
                check_name="interface_surface",
                summary=summary, analysis_step=STEP,
                candidate_classifications=declared + implemented + implied,
                confidence=80,
                json_properties={"declared": declared, "implemented": implemented,
                                 "implied": implied,
                                 "published_spec": published["label"] if published else "",
                                 "files_read": len(paths),
                                 "dependencies_read": len(deps),
                                 **outcome.as_row()},
            ))
        except Exception as exc:
            log.exception("InterfaceSurfaceSurveyor failed for %s", self.project.slug)
            self._warn(out, str(exc))
        return out
