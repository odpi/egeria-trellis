"""Sub-surveyor: the OpenSSF Best Practices badge (formerly the CII badge).

Unlike every other scorecard in this package, this one does not estimate
anything. The badge is a real, externally-held record, so the honest answer is
to go and read it: one GET against bestpractices.dev, the same shape of cheap
authoritative lookup cve_scan makes against OSV.

**Three answers, kept apart, because they are genuinely different.**

    registered      the project has a badge record — report its level
    not_registered  no record exists. A FACT about the project, not a gap in
                    our data, and the direct answer to "is it badged?"
    unreachable     we could not ask. Never rendered as "not registered".

The third is the one that needs saying. A network failure that reads as "this
project has no badge" would be a claim about someone's project derived from our
own connectivity, and it is the single most likely way this module could lie.

**A badge level is a self-assessment with a date on it, and the date is part of
the finding.** Reporting "silver" alone would carry the authority of the badge
and none of its staleness, so the age rides in the summary and drives its own
finding once past the threshold below.

The worked example this module was built on: on 2026-08-26, odpi/egeria held a
silver badge whose record had last been updated 2022-12-20 — a claim made about
a codebase nearly four years earlier, still displayed as current. **That is no
longer the state of that record.** It was refreshed 2026-08-28 and now reads as
current, which is the outcome the finding is for. The example is kept because
the reasoning does not depend on any project staying stale; a badge is a dated
claim whether or not today's example happens to be old.

**"Met" and "?" are not the same, in their record either.** Of egeria's 196
criteria fields, 115 are Met, 66 are unanswered, 13 N/A and 1 Unmet. The
unanswered ones are reported as unanswered rather than folded into either side.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation
from resource_explorer.survey_time import now_utc_iso

log = logging.getLogger(__name__)

STEP = "CiiBadge"

_API = "https://www.bestpractices.dev/projects.json"
_TIMEOUT = 20

#: Badge levels, weakest first. Order is used only for display, never summed.
_LEVELS = ("in_progress", "passing", "silver", "gold")

#: Past this, the badge is reported as stale in its own right. Two years is
#: deliberately generous: the finding should be unarguable, not a matter of
#: taste about cadence.
_STALE_DAYS = 730


#: `git@host:owner/repo` and `ssh://git@host/owner/repo` — the SSH remote forms.
_SSH_SCP_RE = re.compile(r"^(?:ssh://)?git@(?P<host>[^:/]+)[:/](?P<path>.+)$")


def _to_https(raw: str) -> str:
    """An SSH remote as the https URL naming the same repository, else unchanged.

    The badge API only knows https URLs. An SSH remote is not a different
    project, it is the same project written the way you clone it over SSH, so
    it should be *translated* rather than asked-and-missed.
    """
    m = _SSH_SCP_RE.match(raw)
    if not m:
        return raw
    return f"https://{m.group('host')}/{m.group('path')}"


def url_variants(repo_url: str) -> list:
    """The forms to try, canonical first.

    The badge API matches the repository URL literally, and the registry stores
    whatever the repo was added as. Measured: `https://github.com/odpi/egeria`
    returns a silver badge and `https://github.com/odpi/egeria.git` — the form
    actually stored for that project — returns nothing at all. Querying only
    the stored string would have reported "no badge" for a silver-badged
    project, which is the wrong-but-plausible answer in its purest form.

    SSH remotes are translated to https first (2026-08-31). Before that,
    `git@github.com:odpi/egeria.git` was sent to the API verbatim, matched
    nothing, and — because "matched nothing" is indistinguishable from "this
    project is not registered" — was reported as `not_registered`: a confident
    false negative about a silver-badged project, produced by the URL form
    alone. That is the exact lie this module's header says it exists to
    prevent, arriving through a variant it did not anticipate.

    Only forms that mean the same repository are tried, and only until one
    hits, so a genuinely unregistered project still costs a single request per
    distinct variant and lands on the honest answer.
    """
    raw = (repo_url or "").strip()
    if not raw:
        return []
    canonical = _to_https(raw)
    trimmed = canonical.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    out = [trimmed]
    for form in (canonical, raw):
        if form not in out:
            out.append(form)
    return [u for u in out if u.startswith(("http://", "https://"))]


def _query_one(url: str, timeout: int) -> tuple:
    query = f"{_API}?url={urllib.parse.quote(url, safe='')}"
    try:
        req = urllib.request.Request(query, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code} from bestpractices.dev"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"
    if not isinstance(payload, list) or not payload:
        return None, ""
    record = payload[0]
    return (record, "") if isinstance(record, dict) else (None, "")


def fetch_badge(repo_url: str, *, timeout: int = _TIMEOUT) -> tuple:
    """(record | None, error).

    Returns the error rather than raising it, so a caller can tell "no badge"
    from "could not ask" — the distinction this module exists to preserve.
    `(None, "")` means every variant was asked and none has a badge.

    An error on one variant is only returned if no later variant succeeds:
    a transient failure looking up a form that would have missed anyway must
    not mask a real answer, and a real failure must not be reported as "no
    badge" either.
    """
    variants = url_variants(repo_url)
    if not variants:
        raw = (repo_url or "").strip()
        # An unusable URL is a fact about US, never about the project. Falling
        # through to `(None, "")` here would render as `not_registered`.
        return None, ("no repository URL is recorded" if not raw else
                      f"the recorded repository URL is not one the badge API can be "
                      f"asked about ({raw[:60]!r})")
    first_error = ""
    for url in variants:
        record, error = _query_one(url, timeout)
        if record is not None:
            return record, ""
        first_error = first_error or error
    return None, first_error


def _age_days(updated_at: str) -> int | None:
    try:
        when = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).days


def _criteria(record: dict) -> dict:
    counts = {"met": 0, "unmet": 0, "unanswered": 0, "not_applicable": 0}
    unmet = []
    for key, value in record.items():
        if not key.endswith("_status"):
            continue
        v = str(value).strip()
        if v == "Met":
            counts["met"] += 1
        elif v == "Unmet":
            counts["unmet"] += 1
            unmet.append(key[: -len("_status")])
        elif v == "N/A":
            counts["not_applicable"] += 1
        elif v in ("?", "", "None"):
            counts["unanswered"] += 1
    return {**counts, "unmet_criteria": sorted(unmet)[:20]}


def _finding(name: str, label: str, summary: str, *, known: bool = True,
             detail: dict | None = None) -> dict:
    return {"check_name": name, "label": label if known else "not_established",
            "summary": summary, "confidence": 100 if known else 0,
            "detail": {"known": known, **(detail or {})}}


def assess(record, error: str) -> list:
    """Findings for the three outcomes, never collapsing any two of them."""
    if error:
        return [_finding(
            "badge_level", "",
            f"The OpenSSF Best Practices badge could not be looked up ({error}). "
            "This is not a finding that the project has no badge.",
            known=False, detail={"error": error})]

    if record is None:
        return [_finding(
            "badge_level", "not_registered",
            "The project has no OpenSSF Best Practices badge record. This was "
            "asked and answered — it is a fact about the project, not a gap in "
            "what we hold.",
            detail={"registered": False})]

    level = str(record.get("badge_level") or "").strip().lower() or "in_progress"
    age = _age_days(record.get("updated_at") or "")
    crit = _criteria(record)
    project_id = record.get("id")
    where = f"https://www.bestpractices.dev/projects/{project_id}" if project_id else ""

    age_note = (f" Self-assessed, last updated {age} day(s) ago." if age is not None
                else " The date of the assessment could not be read.")
    out = [_finding(
        "badge_level", level,
        f"Holds a '{level}' OpenSSF Best Practices badge "
        f"({record.get('tiered_percentage')}% tiered)." + age_note,
        detail={"registered": True, "level": level, "project_id": project_id,
                "url": where, "tiered_percentage": record.get("tiered_percentage"),
                "updated_at": record.get("updated_at"), "age_days": age,
                "known_levels": list(_LEVELS)})]

    # Staleness as its own finding, so it cannot be read past. A badge is a
    # claim someone made on a date; the level alone carries the authority of
    # the claim and none of its age.
    if age is not None and age > _STALE_DAYS:
        out.append(_finding(
            "badge_freshness", "stale",
            f"The badge record has not been updated in {age} day(s) — the "
            f"'{level}' level describes the project as it was then, not as it is.",
            detail={"age_days": age, "threshold_days": _STALE_DAYS}))
    elif age is not None:
        out.append(_finding(
            "badge_freshness", "current",
            f"The badge record was updated {age} day(s) ago.",
            detail={"age_days": age, "threshold_days": _STALE_DAYS}))
    else:
        out.append(_finding("badge_freshness", "",
                            "The badge record carries no readable update date.",
                            known=False))

    answered = crit["met"] + crit["unmet"] + crit["not_applicable"]
    out.append(_finding(
        "criteria_coverage",
        "complete" if not crit["unanswered"] else "partial",
        f"{crit['met']} criteria met, {crit['unmet']} unmet, "
        f"{crit['not_applicable']} not applicable, {crit['unanswered']} left "
        "unanswered by the maintainers — unanswered is neither met nor unmet, "
        "in their record or in this one.",
        detail={**crit, "answered": answered}))
    return out


def headline(findings: list) -> str:
    level = next((f for f in findings if f["check_name"] == "badge_level"), None)
    if not level or not level["detail"]["known"]:
        return "OpenSSF Best Practices badge could not be looked up"
    if level["label"] == "not_registered":
        return "No OpenSSF Best Practices badge"
    stale = any(f["check_name"] == "badge_freshness" and f["label"] == "stale"
                for f in findings)
    return f"OpenSSF Best Practices badge: {level['label']}" + (
        " (self-assessment is stale)" if stale else "")


def _badge_outcome(level):
    """recovered / no_signal / unverified for one badge lookup.

    Deliberately mirrors `headline()`'s own three branches rather than
    inventing a fourth reading: if these two ever disagree, the summary a human
    reads and the label a query reads would be describing different runs.
    """
    if not level or not level["detail"]["known"]:
        return StepOutcome("unverified", cause="badge lookup could not be completed")
    if level["label"] == "not_registered":
        return no_signal("the badge API answered and this project has no badge",
                         known_positive=True)
    return StepOutcome("recovered", detail={"badge_level": level["label"]})


class CiiBadgeSurveyor(BaseSurveyor):
    """The real OpenSSF Best Practices badge, read rather than estimated."""

    def __init__(self, project, registry, surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or now_utc_iso()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        out: list[Annotation] = []
        try:
            slug = self.project.slug
            record, error = fetch_badge(self.project.github_url or "")
            findings = assess(record, error)
            self.registry.upsert_finding(slug, "cii_badge", findings,
                                         surveyed_at=self._surveyed_at)
            level = next((f for f in findings if f["check_name"] == "badge_level"), None)
            out.append(ClassificationAnnotation(
                summary=headline(findings),
                analysis_step=STEP,
                check_name="cii_badge",
                candidate_classifications=[
                    f["label"] for f in findings if f["detail"]["known"] and f["label"]],
                confidence=100 if (level and level["detail"]["known"]) else 0,
                json_properties={
                    **{f["check_name"]: f["label"] for f in findings},
                    # Three states this step has always kept apart in prose —
                    # "not_registered  a FACT about the project" versus
                    # "unreachable  we could not ask. Never rendered as
                    # not_registered" — and which the shared vocabulary could
                    # not see. `known` is the lookup having succeeded, which is
                    # exactly the known-positive: the badge API answering about
                    # this project and saying "no badge" is a provable absence.
                    **_badge_outcome(level).as_row(),
                },
            ))
        except Exception as exc:
            log.exception("CiiBadgeSurveyor failed for %s", self.project.slug)
            self._warn(out, str(exc))
        return out
