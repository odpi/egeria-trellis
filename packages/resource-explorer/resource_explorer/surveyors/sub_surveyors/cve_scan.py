"""Sub-surveyor: dependency advisories from OSV.dev, over dependencies already parsed.

Answers "are there CVEs?" without collecting anything new about the repo — the
input is `project_dependencies`, written by the manifest parser at ingestion
time. The only new thing is one batched call to a public advisory database.

**Coverage is reported as prominently as the count, and the reason is not
pedantry.** A clean result here means "none of the dependencies we could check
had an advisory", which is a much weaker claim than "this repo has no
vulnerable dependencies", and three things routinely separate them:

* **Unparsed ecosystems.** Measured 2026-08-26 across the whole catalog: 5959
  dependencies in javascript, python, java and go — and zero from Cargo, even
  though Rust repos are registered. A Rust project scanned here would come back
  clean having had nothing scanned at all. That is the single most dangerous
  answer this analysis could give.
* **Missing versions.** OSV matches on version; 5% of recorded dependencies
  have none, and those cannot be queried.
* **Transitive dependencies.** The manifest parser records what a manifest
  declares. Advisories in the tree below that are invisible here.

So a run reports `checked`, `unqueryable` and `ecosystems_seen` alongside the
findings, and NEVER emits a clean bill of health without them. A network
failure produces no finding at all rather than a clean one — "we could not ask"
and "we asked and nothing came back" are opposite answers.
"""
from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager
from typing import NamedTuple

import httpx

from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import (
    Annotation,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
)
from resource_explorer.survey_time import now_utc_iso

log = logging.getLogger(__name__)

STEP = "CveScan"

_OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
_BATCH_SIZE = 500          # OSV's documented batch ceiling is 1000; half that
                           # keeps a single failure from losing a large repo.
_TIMEOUT_SECONDS = 45

#: Our ecosystem labels -> OSV's. An ecosystem NOT in this map is not silently
#: skipped: it is counted in `unqueryable` and named in the summary, because a
#: dependency nobody checked must not be indistinguishable from one that came
#: back clean.
ECOSYSTEM_MAP = {
    "javascript": "npm",
    "python": "PyPI",
    "go": "Go",
    "java": "Maven",
    "rust": "crates.io",
    "ruby": "RubyGems",
    "dotnet": "NuGet",
    "php": "Packagist",
}

#: A pinned, OSV-queryable version. Anything else is NOT sent.
#:
#: This validation is load-bearing, not defensive tidiness. Measured
#: 2026-08-26: OSV returns EVERY advisory for a package when it cannot parse
#: the version, so a malformed one silently becomes a maximal false positive.
#: numpy 1.23.5 is clean; the version as stored by the manifest parser
#: ("==1.23.5 \\") returned 13 advisories, as did the literal string
#: "garbage-not-a-version".
_VERSION_RE = __import__("re").compile(r"^v?\d+(\.\d+)*([-.+~]?[A-Za-z0-9.\-+]*)?$")

#: Requirement operators a manifest may carry. A version pinned with `==` can
#: be queried once the operator is removed; a RANGE cannot, because there is no
#: single installed version to ask about.
_PIN_PREFIXES = ("==", "===")
_RANGE_MARKERS = (">", "<", "!=", "~=", "^", ",", "*", "||", " - ")


def normalise_version(raw: str) -> str:
    """A concrete version OSV can match, or "" when there is not one.

    Returning "" routes the dependency to `unqueryable`, where it is counted
    and explained -- which is the only safe outcome, since sending it anyway
    produces a confident wrong answer rather than an error.
    """
    v = (raw or "").strip()
    # Requirements files wrap lines with a trailing backslash; the parser keeps
    # it, and it is enough on its own to make the version unparseable.
    v = v.rstrip("\\").strip()
    v = v.split(";")[0].strip()          # environment markers
    v = v.split("#")[0].strip()          # trailing comments
    if not v:
        return ""
    if any(m in v for m in _RANGE_MARKERS):
        return ""                        # a range, not an installed version
    for prefix in _PIN_PREFIXES:
        if v.startswith(prefix):
            v = v[len(prefix):].strip()
            break
    else:
        v = v.lstrip("=").strip()
    v = v.lstrip("v").strip()
    return v if v and _VERSION_RE.match(v) else ""


#: Advisory severity, highest first, for ordering findings. OSV reports several
#: schemes; this reads the one most commonly present and falls back to unknown
#: rather than assuming low.
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "MEDIUM": 2, "LOW": 3}


_OSV_VULN_URL = "https://api.osv.dev/v1/vulns/{}"

#: Detail lookups are per-advisory, so this caps them. Matches are rare in
#: practice (2 across 53 dependencies on the repo this was built against), but
#: a badly-outdated project could match hundreds and should not spend the
#: survey doing it.
_MAX_DETAIL_LOOKUPS = 40


class _Request(NamedTuple):
    """What a sender is handed for the batch POST (`opener` in tests receives this, or a URL string for a GET)."""
    method: str
    url: str
    data: bytes
    headers: dict


#: What a failed request raises. `httpx.HTTPError` covers transport errors and (via raise_for_status) an error
#: status, which the old urlopen raised as HTTPError; the rest are as before.
_SEND_ERRORS = (httpx.HTTPError, TimeoutError, ValueError, OSError)


@contextmanager
def _send(req):
    """One request to OSV through httpx (TR-15, 2026-11-15): verified against certifi's CA bundle, not the platform's
    (the old urlopen used the system bundle, which on some hosts still trusts the emSign ECC Root CA - G3 that certifi
    2026.10.18 dropped). An error status raises, as urlopen did. Yields an object with `read()`, the shape `json.load`
    and the tests' openers use."""
    if isinstance(req, str):
        req = _Request("GET", req, b"", {})
    resp = httpx.request(req.method, req.url, content=req.data or None, headers=req.headers,
                         timeout=_TIMEOUT_SECONDS, follow_redirects=True)
    resp.raise_for_status()
    yield io.BytesIO(resp.content)


def fetch_severities(advisory_ids: list, *, opener=None) -> dict:
    """{advisory_id: severity} for as many as the cap allows.

    The BATCH endpoint returns only `id` and `modified` per advisory — no
    severity at all — so without this every finding would be labelled
    "unknown", which reads as "severity could not be determined" when the
    truth is "we did not ask". Anything not looked up stays explicitly
    unrated rather than being guessed at.
    """
    out: dict = {}
    send = opener or _send
    for advisory_id in advisory_ids[:_MAX_DETAIL_LOOKUPS]:
        try:
            with send(_OSV_VULN_URL.format(advisory_id)) as resp:
                out[advisory_id] = _severity_of(json.load(resp))
        except _SEND_ERRORS as exc:
            log.debug("severity lookup failed for %s: %s", advisory_id, exc)
    return out


def _severity_of(vuln: dict) -> str:
    """A named severity, or the CVSS vector, or "".

    OSV's `severity` field carries a CVSS VECTOR ("CVSS:3.1/AV:N/AC:L/..."),
    not a word — measured 2026-08-26, when matching on HIGH/CRITICAL inside it
    found nothing and left every advisory "unknown". A named rating appears in
    `database_specific.severity` when the source database supplies one, and
    plenty of advisories (GO-2026-5024 among them) carry no severity at all.

    Deriving a rating from the vector would mean computing a CVSS base score
    here. That is a defined algorithm, not a guess, but getting it subtly wrong
    produces confidently wrong severities on security findings — so the vector
    is carried verbatim for a human to read instead.
    """
    db = (vuln.get("database_specific") or {})
    named = str(db.get("severity") or "").upper()
    if named in _SEVERITY_ORDER:
        return named
    for entry in vuln.get("severity") or []:
        score = str(entry.get("score") or "").strip()
        if score:
            return score      # a CVSS vector — not a rating, and not treated as one
    return ""


def query_osv(queries: list, *, url: str = _OSV_BATCH_URL,
              opener=None) -> tuple:
    """Batch-query OSV. Returns (results, error).

    The error is RETURNED, not raised: a caller has to be able to tell a failed
    lookup from an empty one, and an exception here would either abort the
    survey or -- worse -- be caught somewhere that treats it as "no vulns".
    """
    if not queries:
        return [], ""
    results: list = []
    send = opener or _send
    for start in range(0, len(queries), _BATCH_SIZE):
        chunk = queries[start:start + _BATCH_SIZE]
        body = json.dumps({"queries": chunk}).encode()
        req = _Request("POST", url, body, {"Content-Type": "application/json"})
        try:
            with send(req) as resp:
                payload = json.load(resp)
        except _SEND_ERRORS as exc:
            return results, f"{type(exc).__name__}: {str(exc)[:120]}"
        batch = payload.get("results") or []
        if len(batch) != len(chunk):
            # OSV returns results positionally. A length mismatch means we
            # cannot tell which advisory belongs to which package, and pairing
            # them anyway would attribute vulnerabilities to the wrong
            # dependency -- worse than reporting nothing.
            return results, (
                f"OSV returned {len(batch)} result(s) for {len(chunk)} "
                "quer(ies); positional pairing would be unsafe"
            )
        results.extend(batch)
    return results, ""


def build_queries(dependencies: list) -> tuple:
    """(queries, pairs, unqueryable) from recorded dependencies.

    `unqueryable` carries a reason per dependency so the summary can say WHY
    coverage is short rather than only that it is.
    """
    queries, pairs, unqueryable = [], [], []
    for dep in dependencies:
        name = (dep.get("dep_name") or "").strip()
        raw_version = (dep.get("dep_version") or "").strip()
        version = normalise_version(raw_version)
        eco = (dep.get("ecosystem") or "").strip().lower()
        osv_eco = ECOSYSTEM_MAP.get(eco)
        if not name:
            unqueryable.append({**dep, "reason": "no package name recorded"})
        elif not osv_eco:
            unqueryable.append({**dep, "reason": f"ecosystem {eco or '(none)'!r} is not queryable against OSV"})
        elif not version:
            unqueryable.append({
                **dep,
                "reason": ("no pinned version to query" if not raw_version else
                           f"version {raw_version!r} is a range or unparseable — "
                           "sending it would return every advisory for the package"),
            })
        else:
            queries.append({"package": {"name": name, "ecosystem": osv_eco},
                            "version": version})
            pairs.append(dep)
    return queries, pairs, unqueryable


class CveScanSurveyor(BaseSurveyor):
    """Dependency advisories, from dependencies already parsed."""

    def __init__(self, project, registry, surveyed_at: str | None = None,
                 osv_url: str = _OSV_BATCH_URL, opener=None,
                 detail_opener=None) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or now_utc_iso()
        self._osv_url = osv_url
        self._opener = opener
        self._detail_opener = detail_opener

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        out: list[Annotation] = []
        try:
            slug = self.project.slug
            deps = self.registry.query_dependencies(slug) or []
            if not deps:
                # No dependencies RECORDED is not no dependencies. Emitting a
                # clean result here would let a repo whose manifests were never
                # parsed read as one with nothing vulnerable in it.
                out.append(ResourceMeasureAnnotation(
                    check_name="cve_scan",
                    summary=("No dependencies are recorded for this resource, so nothing "
                             "could be checked — this is not a finding that it has none. "
                             "Run the dependency analysis first."),
                    analysis_step=STEP,
                    json_properties={"checked": 0, "unqueryable": 0, "advisories": 0,
                                     "scanned": False,
                                     # The prose already says this is not a
                                     # finding of "no vulnerabilities"; this
                                     # makes the same statement machine-
                                     # readable, so it reaches the tool-fit
                                     # query rather than only a human reader.
                                     **StepOutcome(
                                         "unverified", cause="no dependencies recorded",
                                         detail={"recorded": 0}).as_row()},
                ))
                return out

            queries, pairs, unqueryable = build_queries(deps)
            results, error = query_osv(queries, url=self._osv_url, opener=self._opener)

            if error:
                out.append(ResourceMeasureAnnotation(
                    check_name="cve_scan",
                    summary=f"Advisory lookup failed ({error}). Nothing was concluded.",
                    analysis_step=STEP,
                    json_properties={"error": error, "scanned": False,
                                     "checked": 0, "unqueryable": len(unqueryable),
                                     **StepOutcome(
                                         "unverified", cause="advisory lookup failed",
                                         detail={"error": error}).as_row()},
                ))
                return out

            # Severities come from a second, per-advisory call: the batch
            # endpoint does not carry them.
            matched_ids = [v.get("id", "") for r in results
                           for v in (r.get("vulns") or []) if v.get("id")]
            severities = fetch_severities(matched_ids, opener=self._detail_opener)

            findings, advisory_count = [], 0
            for dep, result in zip(pairs, results):
                vulns = result.get("vulns") or []
                if not vulns:
                    continue
                advisory_count += len(vulns)
                ids = [v.get("id", "") for v in vulns if v.get("id")]
                rated = [severities.get(i) for i in ids if severities.get(i)]
                known = [r for r in rated if r in _SEVERITY_ORDER]
                # A CVSS vector is evidence, not a rating: it is kept in detail
                # for a human, and the label stays `unrated` so nothing sorts
                # or filters on a severity nobody assigned.
                vectors = [r for r in rated if r not in _SEVERITY_ORDER]
                worst = (min(known, key=lambda s: _SEVERITY_ORDER[s])
                         if known else "unrated")
                findings.append({
                    "check_name": f"{dep.get('ecosystem')}:{dep.get('dep_name')}",
                    "label": worst.lower(),
                    "summary": (f"{len(vulns)} advisor{'y' if len(vulns) == 1 else 'ies'} "
                                f"for {dep.get('dep_name')} {dep.get('dep_version')}: "
                                f"{', '.join(ids[:4])}"),
                    "confidence": 100,
                    "detail": {"package": dep.get("dep_name"),
                               "version": dep.get("dep_version"),
                               # Where the version came from — "declared" (the
                               # manifest said so at this dependency's own
                               # declaration site) vs. resolved from elsewhere
                               # ("variable_interpolation", "version_catalog").
                               # A CVE finding is a claim about a specific
                               # version; carrying this through means a reader
                               # can tell "the manifest pinned this" from "we
                               # substituted this from a BOM variable" without
                               # having to go back to the parser.
                               "version_source": dep.get("dep_version_source") or "unknown",
                               "ecosystem": dep.get("ecosystem"),
                               "advisory_ids": ids,
                               "severity": worst,
                               "severity_source": ("osv-detail" if known else
                                                   "cvss vector only" if vectors else
                                                   "none published"),
                               "cvss_vectors": vectors},
                })

            ecosystems = sorted({(d.get("ecosystem") or "?") for d in deps})
            coverage = {
                "checked": len(pairs),
                "unqueryable": len(unqueryable),
                "recorded": len(deps),
                "ecosystems_seen": ecosystems,
                "advisories": advisory_count,
                "packages_affected": len(findings),
                "scanned": True,
                # Stated on every run, clean or not: a clean result covers only
                # what was checked, and transitive dependencies are never in
                # `recorded` because the manifest parser reads declarations.
                "excludes_transitive": True,
            }
            # supersedes_previous=True: this call IS the whole run's answer
            # for (slug, "cve_scan", "") — the motivating case for the
            # opt-in (docs/Backlog.md, 2026-09-13 defect: a clean run wrote
            # zero rows and a 12-day-old advisory kept answering as current).
            self.registry.upsert_finding(slug, "cve_scan", findings,
                                         surveyed_at=self._surveyed_at,
                                         supersedes_previous=True)
            self.registry.upsert_metric(
                slug, "cve_scan",
                {"advisories": float(advisory_count),
                 "packages_affected": float(len(findings)),
                 "checked": float(len(pairs)),
                 "unqueryable": float(len(unqueryable))},
                detail=coverage, surveyed_at=self._surveyed_at,
            )

            shortfall = ""
            if unqueryable:
                reasons = {u["reason"] for u in unqueryable}
                shortfall = (f"; {len(unqueryable)} dependenc(ies) could not be checked "
                             f"({'; '.join(sorted(reasons)[:2])})")
            summary = (
                f"{advisory_count} advisor{'y' if advisory_count == 1 else 'ies'} across "
                f"{len(findings)} package(s), from {len(pairs)} of {len(deps)} recorded "
                f"dependencies ({', '.join(ecosystems)}){shortfall}. "
                "Declared dependencies only — transitive ones are not covered."
            )
            # A clean scan and an unrun scan were already distinguished in the
            # prose — three separate paths, each careful. What they had in
            # common was that none of it was expressible in the shared
            # vocabulary, so the distinction stopped at the human reader and
            # never reached "which tools work for which repos".
            #
            # `len(pairs)` is the known-positive: dependencies that were
            # actually queryable and actually queried. Zero advisories across
            # real queried packages is a provable clean result; zero across
            # nothing queryable is not a result at all.
            if advisory_count:
                outcome = StepOutcome("recovered", detail={"checked": len(pairs),
                                                           "advisories": advisory_count})
            else:
                outcome = no_signal(
                    "no advisories for the dependencies that could be queried",
                    known_positive=len(pairs) > 0,
                    checked=len(pairs), unqueryable=len(unqueryable),
                )
            annotation = (RequestForActionAnnotation if findings else ResourceMeasureAnnotation)
            out.append(annotation(
                summary=summary, analysis_step=STEP,
                check_name="cve_scan",
                json_properties={**coverage, **outcome.as_row()},
            ))
        except Exception as exc:
            log.exception("CveScanSurveyor failed for %s", self.project.slug)
            self._warn(out, str(exc))
        return out
