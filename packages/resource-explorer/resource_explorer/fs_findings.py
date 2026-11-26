"""File-system findings a person can ask for action on (TR-87), and the ONE place that says which.

A finding is a stored result of an Analytic Library file-system step (`fs_permissions`, `fs_duplicates`,
`fs_activity`), run by `POST /api/filesystems/{slug}/analyses/{id}/run`. The press "Request action" files a
RequestForActionAnnotation for ONE finding of ONE step run, once. Nothing here is automatic.

`FS_RFA_FINDINGS` is the allowlist. A finding is offered, and accepted, only when the stored result of that run is
**measured** and its count is **> 0**; `not_established`, `not_measured`, `nothing_found` and `unreadable` are never
offered or accepted. The server re-reads the stored result (`reread`); a browser's counts or file lists are never
trusted. Not in the first set: fs_duplicates_elsewhere, fs_access_activity, fs_change_rates, fs_layout_signals.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Named files go in an annotation capped at this many, then "and N more".
FILE_CAP = 20


@dataclass(frozen=True)
class FsFinding:
    step: str
    finding: str
    measure: str          # the step measure whose stored row holds the count
    field: str            # the field of that row's values that is the count
    unit: str             # what the count counts (words)
    action: str           # actionRequested
    step_name: str        # the step's display name
    why_listed: str       # one line for the 400 message
    #: Whether this finding's request may carry file names to Egeria. False for every finding (TR-109 W1): fs_permissions
    #: promises "the names are in Resource Explorer, not published", and no step stores a file list today. The reader and
    #: the sender both check it, so a list that appears later in a stored result still goes nowhere.
    publishes_names: bool = False
    #: What the request says about the file list, so a reader of the annotation is not left to guess whether it was dropped.
    list_words: str = ""


FS_RFA_FINDINGS: dict[tuple[str, str], FsFinding] = {(f.step, f.finding): f for f in (
    FsFinding("fs_permissions", "world_readable_sensitive", "world_readable_sensitive", "World Readable Secret Named Files",
              "secret-looking files readable by others",
              "Review file permissions: secret-looking files are world-readable", "File Permissions",
              "secret-looking files readable by others",
              list_words="the names are in Resource Explorer, not published"),
    FsFinding("fs_permissions", "world_writable", "fs_permissions", "World Writable Files",
              "world-writable files", "Review file permissions: files are world-writable", "File Permissions",
              "world-writable files"),
    FsFinding("fs_duplicates", "fs_duplicates", "fs_duplicates", "Duplicate Sets",
              "duplicate content sets", "Review duplicate content", "Duplicate Files",
              "duplicate content sets within this file system"),
    FsFinding("fs_activity", "fs_activity", "fs_activity", "Cold Files",
              "cold files", "Review cold data: keep, archive or delete", "File Activity",
              "cold files (not modified for longer than cold_days)",
              list_words="no file list"),
)}


class FindingRefused(Exception):
    """`status` is the HTTP status; the message is the words."""

    def __init__(self, status: int, words: str):
        super().__init__(words)
        self.status = status
        self.words = words


def allowed_words() -> str:
    return "; ".join(f"{s}::{f} ({v.why_listed})" for (s, f), v in FS_RFA_FINDINGS.items())


def finding_key(step: str, finding: str, run_id: str) -> str:
    """The idempotency unit: one request per key. A re-run is a new finding."""
    return f"{step}::{finding}::{run_id}"


def spec_for(step: str, finding: str) -> FsFinding:
    spec = FS_RFA_FINDINGS.get((step, finding))
    if spec is None:
        raise FindingRefused(400, f"'{step}::{finding}' is not a finding Request action covers. Allowed: {allowed_words()}.")
    return spec


def _count(value) -> int | None:
    try:
        text = str(value).replace(",", "").strip()
        return int(text) if text.lstrip("-").isdigit() else None
    except (TypeError, ValueError):
        return None


def reread(registry, slug: str, step: str, finding: str, run_id: str) -> dict:
    """The finding as the STORED result of `run_id` holds it: {spec, key, count, as_of, files, more, run_id}.
    Raises FindingRefused(409, "this finding is not in that run's result") when the run is not the stored run, or the
    result is absent, not measured, or 0."""
    from resource_explorer.surveyors import survey_step_host

    spec = spec_for(step, finding)
    absent = FindingRefused(409, "this finding is not in that run's result")
    try:
        run, rows = registry.query_step_annotations("filesystem", slug, survey_step_host.measure_step_key(step, spec.measure))
    except Exception:  # noqa: BLE001 -- an unreadable store is never "the finding is there"
        raise absent
    if not run or str(run) != str(run_id):
        raise absent
    for r in rows:
        detail = r.get("detail") or {}
        extra = detail.get("additionalProperties") or {}
        state = str(extra.get("resultState") or r.get("result_state") or "")
        values = detail.get("resourceProperties") or {}
        count = _count(values.get(spec.field))
        if state != "measured" or not count or count <= 0:
            continue
        named = values.get("files") if spec.publishes_names and isinstance(values.get("files"), list) else []
        files = [str(x) for x in named[:FILE_CAP]]
        return {"spec": spec, "key": finding_key(step, finding, str(run)), "count": count, "run_id": str(run),
                "as_of": str(values.get("asOf") or ""), "files": files, "more": max(0, len(named) - FILE_CAP)}
    raise absent


def offer_for(registry, slug: str, step: str, measure: str) -> dict | None:
    """What the publish-table row offers: {step, finding, run_id, count, unit, key} when the stored result of the step's
    latest run is a measured count > 0 on an allowlisted finding, else None (no button). Registry-only. A row that
    cannot publish (its type is not natively writable) still gets the offer: it is a different write."""
    from resource_explorer.surveyors import survey_step_host

    for (s_key, finding), spec in FS_RFA_FINDINGS.items():
        if s_key != step or spec.measure != measure:
            continue
        try:
            run, _ = registry.query_step_annotations("filesystem", slug, survey_step_host.measure_step_key(step, spec.measure))
            found = reread(registry, slug, step, finding, str(run)) if run else None
        except Exception:  # noqa: BLE001 -- FindingRefused or an unreadable store: no offer is the safe answer
            found = None
        if found:
            return {"step": step, "finding": finding, "run_id": found["run_id"], "count": found["count"],
                    "unit": spec.unit, "key": found["key"]}
    return None
