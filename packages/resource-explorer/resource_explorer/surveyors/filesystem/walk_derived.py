"""What the folder survey card reads for Files build step 6: the derived steps' stored results, and the price line (F12).

**The derived steps** (`derived_view`). `fs_change_rates`, `fs_duplicates`, `fs_activity`, `fs_permissions` and (Files build
step 7) `fs_layout_signals` are
Analytic Library steps (packages under `trellis-analytics/examples/steps/`), attached beside filesystem_inventory by
`survey_step_host` from their generated catalog entries. Each derives from the walk's kept per-file rows only. This
reads, per catalogued step, what it last stored in `step_annotations` (each declared measure under its own key), with
the envelope's state and words, and says when the package is not installed. Nothing here derives anything: the words
on the card are the step's own stored summary and reason. A step never run is "not run yet", never a zero.

**The price line** (`price`, F12, the price-before-you-run rule X-06). Built from F1's bytes and file counts, before
the Survey press: the newest walk's files and bytes, its wall time on this machine (the walk records `walk_seconds` and
`machine` with its limits), whether it was capped (then the full size is NOT known and the line says so), the
directories it did not walk, and the heaviest folders' share of the bytes, which is the "which parts are not worth it"
half. Where Egeria's Folder Survey leads, the counts are Egeria's own and its time is Egeria's: the line says it is not
priced here. With no survey at all: "not priced · no walk has measured this file system yet", never a zero.

Paths appear only on Resource Explorer's own screen (the heaviest folders); nothing here is published.
"""
from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

#: How many of the heaviest folders the price line names.
PRICE_FOLDERS = 3


def _catalogued_steps() -> list:
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    return [e for e in _load().get("filesystem", []) if e.provided_by]


def derived_view(registry, slug: str) -> dict:
    """{"steps": [{analysis_id, step, name, description, intent, installed, missing_reason, run, results}]}. `results`
    is every stored row of the step's latest run, across its declared measures, each with its measure, state, words,
    summary, reason and values. `run` is '' when the step has never stored a run. `published` on a result is what the
    newest publish of RE's own file-system survey report proved for it (the report and the GUID its read found, for
    the same walk the result was derived from), else None: kept here only."""
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.annotation_readers import _envelope_state

    missing = survey_step_host.missing_library_steps("filesystem")
    published = published_derived(registry, slug)
    out = []
    for entry in _catalogued_steps():
        step = entry.provided_by.get("step") or entry.id
        measures = [m.name for m in (entry.declaration.measures if entry.declaration else [])] or [step]
        runs, results = [], []
        for measure in measures:
            key = survey_step_host.measure_step_key(step, measure)
            run, rows = registry.query_step_annotations("filesystem", slug, key)
            if run:
                runs.append(run)
            for a in rows:
                detail = a.get("detail") or {}
                state, words, reason = _envelope_state(detail)
                values = detail.get("resourceProperties") or {}
                results.append({"measure": measure, "scope": a.get("scope") or "", "state": state,
                                "state_words": words, "summary": a.get("summary") or "", "reason": reason,
                                "annotation_type": a.get("annotation_type") or "",
                                "values": values, "measured_at": a.get("measured_at"),
                                "published": published.get((measure, a.get("scope") or "", str(values.get("asOf") or "")))})
        out.append({"analysis_id": entry.id, "step": step, "name": entry.name, "description": entry.description,
                    "intent": entry.intent, "installed": step not in missing,
                    "missing_reason": survey_step_host.not_installed_sentence(step, missing[step]) if step in missing else "",
                    "run": max(runs) if runs else "", "results": results})
    return {"steps": out}


def published_derived(registry, slug: str) -> dict:
    """{(measure, scope, walk): {report_guid, guid, at}} from the newest publish of RE's own file-system survey report
    whose proof records derived results (`published_to_egeria.derived.sent`). Only a result with a GUID the report read
    found counts as published; one sent and not read back is not. {} when no publish carried any."""
    rows = [r for r in registry.list_filesystem_surveys(slug) if r.get("source") == "egeria-published"
            and isinstance(((r.get("survey_data") or {}).get("published_to_egeria") or {}).get("derived"), dict)]
    if not rows:
        return {}
    newest = max(rows, key=lambda r: ((r["survey_data"]["published_to_egeria"].get("at")) or ""))
    proof = newest["survey_data"]["published_to_egeria"]
    out = {}
    for r in proof["derived"].get("sent") or []:
        if r.get("guid"):
            out[(r.get("measure") or "", r.get("scope") or "", r.get("as_of") or "")] = {
                "report_guid": proof.get("report_guid") or "", "guid": r["guid"], "at": proof.get("at") or ""}
    return out


def _fmt_bytes(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1000 or unit == "GB":
            return f"{v:,.0f} {unit}" if unit == "B" else f"{v:,.1f} {unit}"
        v /= 1000
    return f"{v:,.1f} GB"


def _newest_walk(registry, slug: str) -> dict | None:
    """The newest stored walk blob that carries limits (an RE-led folder survey or the walk beside Egeria's)."""
    latest = registry.get_latest_filesystem_survey(slug)
    sd = (latest or {}).get("survey_data") or {}
    if isinstance(sd, dict) and sd.get("walk_limits"):
        return sd
    if latest is None:
        return None
    # The newest row is not a walk (rare: a row some other path stored); read back further, newest first.
    for row in registry.list_filesystem_surveys(slug):
        sd = row.get("survey_data") or {}
        if isinstance(sd, dict) and sd.get("walk_limits"):
            return sd
    return None


def price(registry, slug: str, *, egeria_leads: bool = False) -> dict:
    """The one cost line before the Survey press (F12). Every number comes from a stored walk; none is guessed."""
    sd = _newest_walk(registry, slug)
    if egeria_leads:
        tail = ("Egeria's Folder Survey leads here: its run time is Egeria's and is not priced by Resource Explorer"
                + (f"; Resource Explorer's last walk beside it saw {int(sd.get('total_files') or 0):,} files"
                   if sd and sd.get("total_files") is not None else ""))
        return {"priced": False, "words": tail}
    if not sd:
        return {"priced": False, "words": "not priced · no walk has measured this file system yet"}
    lim = sd.get("walk_limits") or {}
    files = sd.get("total_files")
    size = sd.get("total_size_bytes")
    capped = bool(lim.get("capped"))
    seconds = lim.get("walk_seconds")
    here = lim.get("machine") == socket.gethostname()
    parts = [f"last walk ({str(sd.get('surveyed_at') or '')[:10]}): {int(files or 0):,} files, "
             f"{_fmt_bytes(int(size or 0))}"]
    if capped:
        parts.append(f"{lim.get('capped_words') or 'capped'}: the full tree is larger and its size is not known")
    if seconds is None:
        parts.append("time not measured (walked before Resource Explorer timed its walks)")
    elif not here:
        parts.append(f"took {float(seconds):,.0f} s on another machine ({lim.get('machine') or 'unnamed'}); "
                     "not priced for this one")
    else:
        took = "under a second" if float(seconds) < 1 else f"about {float(seconds):,.0f} s"
        parts.append(f"{took} to walk again on this machine" + (" for the part walked" if capped else ""))
    skipped = int(lim.get("not_walked_directories") or 0)
    if skipped:
        names = ", ".join(lim.get("not_walked_names_found") or []) or "noise directories"
        parts.append(f"{skipped:,} directories not walked ({names})")
    ext = sd.get("walk_extensions") or {}
    heavy = (ext.get("folders") or {}).get("largest_folders") or []
    total = int((ext.get("folders") or {}).get("total_bytes") or size or 0)
    heaviest = []
    if heavy and total:
        # Only top-level-most folders: a folder inside another listed one is not counted twice.
        for f in sorted(heavy, key=lambda f: (int(f.get("depth") or 0), -int(f.get("bytes") or 0))):
            if any(f["path"].startswith(h["path"] + "/") for h in heaviest):
                continue
            heaviest.append(f)
            if len(heaviest) >= PRICE_FOLDERS:
                break
        heaviest.sort(key=lambda f: -int(f.get("bytes") or 0))
        share = ", ".join(f"{f['path']} {int(f['bytes']) * 100 // total}%" for f in heaviest)
        parts.append(f"the bulk sits in {share} of the bytes; leaving those out is the saving")
    return {"priced": seconds is not None and here, "capped": capped, "files": files, "bytes": size,
            "seconds": seconds, "machine_matches": here, "not_walked_directories": skipped,
            "heaviest_folders": [{"path": f["path"], "bytes": f["bytes"]} for f in heaviest],
            "words": " · ".join(parts)}


# ── Files build step 7: the derived steps' results in RE's own file-system survey report ─────────────────────────────

#: The annotation types the file-system survey report carries a derived step's result as: the three the derived steps
#: declare (Files brief §3 item 3: follow Egeria's lead). Wider than the engine host's PUBLISHABLE_ANNOTATION_TYPES,
#: which governs what Egeria may dispatch, not what RE's own report may carry.
REPORT_ANNOTATION_TYPES = ("ResourceMeasureAnnotation", "ResourceProfileAnnotation", "RequestForActionAnnotation")
#: The result states that are a derivation (a measured nothing included). A "not measured" result is not sent.
REPORT_STATES = ("measured", "nothing_found")


def _as_count(value) -> int | None:
    try:
        text = str(value).replace(",", "").strip()
        return int(text) if text.lstrip("-").isdigit() else None
    except (TypeError, ValueError):
        return None


def report_annotations(registry, slug: str, walk_run: str) -> dict:
    """The derived steps' stored results as the annotations RE's own file-system survey report carries, for the walk
    `walk_run` only: {state, annotations, sent, not_sent}. `sent[i]` describes `annotations[i]` (analysis, measure,
    type, the walk it was derived from). A result derived from another walk, a "not measured" one, a request for action
    with nothing to request, and a type the report cannot carry are each in `not_sent` with why, never silently dropped.

    COUNTS ONLY, as every derived result is (the steps name no file, folder, owner or hash). Each annotation is
    identified by step, measure and scope (`check_name`, `item_key`), so a re-publish of the same walk converges."""
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.survey_report import (RequestForActionAnnotation, ResourceMeasureAnnotation,
                                                           ResourceProfileAnnotation)

    try:
        entries = _catalogued_steps()
    except Exception as exc:  # noqa: BLE001 -- unknown, never "none stored"
        log.warning("%s: the derived steps could not be listed", slug, exc_info=True)
        return {"state": "not_read", "reason": str(exc)[:300], "annotations": [], "sent": [], "not_sent": []}
    annotations, sent, not_sent = [], [], []
    for entry in entries:
        step = entry.provided_by.get("step") or entry.id
        dec = entry.declaration
        for m in (dec.measures if dec else []):
            key = survey_step_host.measure_step_key(step, m.name)
            ann_type = (m.annotation.type if getattr(m, "annotation", None) else "") or "ResourceMeasureAnnotation"
            try:
                _, rows = registry.query_step_annotations("filesystem", slug, key)
            except Exception as exc:  # noqa: BLE001 -- unknown, never "none stored"; the report still publishes
                log.warning("%s: the stored results of %s could not be read", slug, key, exc_info=True)
                return {"state": "not_read", "reason": f"{type(exc).__name__}: {exc}"[:300], "annotations": [],
                        "sent": [], "not_sent": []}
            for r in rows:
                detail = r.get("detail") or {}
                props = dict(detail.get("resourceProperties") or {})
                extra = dict(detail.get("additionalProperties") or {})
                state = str(extra.get("resultState") or r.get("result_state") or "")
                row = {"analysis_id": entry.id, "step": step, "measure": m.name, "type": ann_type,
                       "scope": r.get("scope") or "", "as_of": str(props.get("asOf") or "")}
                why = ""
                if ann_type not in REPORT_ANNOTATION_TYPES:
                    why = f"{ann_type} is not a type the file-system survey report carries"
                elif state not in REPORT_STATES:
                    why = f"not measured ({state.replace('_', ' ') or 'no state recorded'}): nothing to publish"
                elif row["as_of"] != walk_run:
                    why = (f"derived from the walk of {row['as_of'] or 'an unrecorded walk'}, not this one "
                           f"({walk_run}): derive again, then publish")
                elif ann_type == "RequestForActionAnnotation" and state != "measured":
                    why = "nothing to request: the step found none"
                if why:
                    not_sent.append({**row, "why": why})
                    continue
                other = m.name != step
                common = dict(summary=str(r.get("summary") or ""), analysis_step=step,
                              annotation_type_name=survey_step_host.measure_display_name(m.name) if other else entry.name,
                              explanation=str(r.get("explanation") or ""))
                check = m.name if other else step
                envelope = {**extra, "surveyor": "Resource Explorer", "derivedFromWalk": walk_run,
                            "analysisId": entry.id}
                if ann_type == "ResourceProfileAnnotation":
                    counts = {k: c for k, v in props.items() if (c := _as_count(v)) is not None}
                    words = {k: v for k, v in props.items() if k not in counts}
                    annotations.append(ResourceProfileAnnotation(
                        value_count=counts, additional_properties={**envelope, **{k: str(v) for k, v in words.items()}},
                        check_name=check, item_key=row["scope"], **common))
                elif ann_type == "RequestForActionAnnotation":
                    annotations.append(RequestForActionAnnotation(
                        action_requested=str(r.get("summary") or ""), json_properties=props,
                        additional_properties=envelope, check_name=check, item_key=row["scope"], **common))
                else:
                    annotations.append(ResourceMeasureAnnotation(resource_properties=props, additional_properties=envelope,
                                                                 check_name=check, item_key=row["scope"], **common))
                sent.append(row)
    state = "sent" if annotations else ("none_stored" if not not_sent else "none_to_send")
    return {"state": state, "annotations": annotations, "sent": sent, "not_sent": not_sent}
