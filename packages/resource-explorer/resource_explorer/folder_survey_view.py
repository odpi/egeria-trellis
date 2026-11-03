"""One read of a file system's folder survey, whoever led it (Files step 2).

The survey card and Understanding read ONE shape for both leads. This module picks the newest folder survey RE holds for
a file system and returns it as `read_folder_survey` does:

* **Egeria led:** the stored annotations of the newest COMPLETED native run of `FileSurvey::survey-folder` (the rows the
  native-survey port read back from Egeria's SurveyReport). "Complete" is `native_survey_run.derive_native_state`'s word,
  which needs the action's status, a stored report GUID and the stored annotation count to match.
* **Resource Explorer led:** the newest stored walk that carries `surveyed_by` (the walk's own `survey_data`), read through
  the SAME annotation specs it publishes (`folder_survey_annotations.folder_specs`), so what the screen shows is what
  Egeria is sent.

Words come from stored rows (the probe row, the run row, the walk's own limits), never from the branch a press took. A
file system with no survey is `state: "none"`: "no folder survey yet", not zeros.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from resource_explorer import file_reach
from resource_explorer.surveyors.filesystem.folder_survey_annotations import folder_specs, read_folder_survey

log = logging.getLogger(__name__)


def _reset_at(registry, slug: str) -> str:
    """The latest Egeria reset for this file system (its marker or the recorded reset), '' when none. A registry
    without the reader (a test double) claims none; a read that fails raises (it must not bring "published" back)."""
    fn = getattr(registry, "get_egeria_reset_at", None)
    return (fn(slug) if fn else "") or ""


def _pre_reset(publish: dict | None, reset_at: str, at: str) -> dict | None:
    """A publish proof read back before the latest Egeria reset is history (architect ruling (b), 2026-10-23; fix
    round 1): `published` False, `pre_reset` {at, words} with "published before the <date> reset · not in Egeria
    now". `at` is the proof's own time (its `at`, else the row's surveyed_at). Any other publish is unchanged."""
    from resource_explorer.catalogue_commit import pre_reset_claim

    if not publish or not publish.get("published"):
        return publish
    claim = pre_reset_claim(reset_at, at)
    return publish if claim is None else {**publish, "published": False, "pre_reset": claim}


def _when(text: str) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _egeria_candidate(registry, slug: str) -> dict | None:
    """The newest completed Egeria Folder Survey run with its stored annotations, plus the state of the newest run."""
    from resource_explorer import native_survey_run as nsr

    runs = registry.list_native_survey_runs("filesystem", slug, nsr.FOLDER_SURVEY_QN)
    newest_state = ""
    for i, run in enumerate(runs):
        report = run.get("survey_report_guid") or ""
        stored = registry.count_native_survey_annotations(report) if report else None
        state = nsr.derive_native_state(run, stored_annotation_count=stored).get("state", "")
        if i == 0:
            newest_state = state
        if state == nsr.COMPLETE and report:
            at = run.get("survey_report_at") or run.get("surveyed_at") or ""
            # Egeria's own report, read back before the latest reset: history, never "published" (fix round 1).
            from resource_explorer.catalogue_commit import pre_reset_claim

            pre = pre_reset_claim(_reset_at(registry, slug), at)
            return {"surveyor": file_reach.SURVEYOR_EGERIA, "at": at,
                    "report_guid": report, "annotations": registry.query_native_survey_annotations(report),
                    "run_state": newest_state, "limits": None, "published": pre is None,
                    "publish": None, "pre_reset": pre, "report_sentence": file_reach.EGERIA_REPORT_SENTENCE,
                    # Said on every Egeria-led report (design 2026-10-21 section 2, and the analysis level sent).
                    "detail_lines": [file_reach.analysis_level_line(), file_reach.EGERIA_CSV_LINE]}
    return {"none": True, "run_state": newest_state} if runs else None


def _re_candidate(registry, slug: str) -> dict | None:
    """The newest stored Resource Explorer walk (one that records who led it)."""
    rows = registry.list_filesystem_surveys(slug)
    walks = [r for r in rows if (r.get("survey_data") or {}).get("surveyed_by")]
    if not walks:
        return None
    newest = max(walks, key=lambda r: _when((r["survey_data"] or {}).get("surveyed_at") or r.get("surveyed_at")) or
                 datetime.min.replace(tzinfo=timezone.utc))
    sd = newest["survey_data"]
    at = sd.get("surveyed_at") or newest.get("surveyed_at") or ""
    # The published report is the row stored with the same run time and a report GUID (publish stores its own row, with
    # what landed: `published_to_egeria`, the count Egeria returned and the SurveyReport read-back).
    pub_row = next((r for r in rows
                    if r.get("egeria_report_guid") and r.get("surveyed_at") == newest.get("surveyed_at")), None)
    guid = (pub_row or {}).get("egeria_report_guid") or ""
    proof = ((pub_row or {}).get("survey_data") or {}).get("published_to_egeria") or {}
    publish = _publish_words(proof) if guid else None
    publish = _pre_reset(publish, _reset_at(registry, slug) if publish else "",
                         proof.get("at") or (pub_row or {}).get("surveyed_at") or "")
    reach = (sd.get("surveyed_by") or {}).get("reach") or {}
    return {"surveyor": file_reach.SURVEYOR_RE, "at": at, "report_guid": guid, "published": bool(publish and publish["published"]),
            "publish": publish,
            "annotations": folder_specs(sd, include_paths=True), "limits": sd.get("walk_limits"), "run_state": "",
            "reach_at_run": reach,
            "report_sentence": reach.get("report_sentence") or "surveyed by Resource Explorer",
            "detail_lines": [], "broken_symlinks": sd.get("broken_symlinks") or [],
            "broken_symlink_count": sd.get("broken_symlink_count")}


def _publish_words(proof: dict) -> dict:
    """What landed in Egeria, from the stored proof of the publish (never from the fact that a publish was pressed):
    "N of M annotations in Egeria" uses the GUIDs Egeria returned, and "published" needs the SurveyReport to have read
    back and at least one annotation to have landed. Anything else reads "not published · <why>"."""
    landed, built = proof.get("annotations_in_egeria"), proof.get("annotation_count")
    if not proof:
        why = "the publish left no proof of what landed"
    elif proof.get("report_read_back") is not True:
        why = proof.get("read_back_why") or "the SurveyReport could not be read back from Egeria"
    elif not landed:
        why = "no annotation landed in Egeria" if landed == 0 else "Egeria did not say how many annotations landed"
    else:
        why = ""
    return {"published": not why, "not_published_why": why, "landed": landed, "built": built,
            "landed_words": (f"{landed} of {built} annotations in Egeria" if landed is not None and built is not None else "")}


NARRATIVES_SHOWN = 50


def extensions_view(registry, slug: str) -> dict | None:
    """The newest walk's extensions (Files build step 1), whoever led the folder survey, or None when no walk measured them.

    What the screen says about the per-file rows comes from the registry's own count of the stored rows for THAT run (not from
    the fact that a walk ran): "kept" means rows are there. What it says about publishing comes from the stored publish proof
    ("N of M annotations in Egeria" from the GUIDs Egeria returned). Paths and narrative text are in this view because it is
    Resource Explorer's own screen; the publish carries counts only."""
    rows = registry.list_filesystem_surveys(slug)
    carriers = [r for r in rows if (r.get("survey_data") or {}).get("walk_extensions") and r.get("source") != "egeria-published"]
    if not carriers:
        return None
    newest = max(carriers, key=lambda r: _when(r["survey_data"].get("surveyed_at") or r.get("surveyed_at"))
                 or datetime.min.replace(tzinfo=timezone.utc))
    sd = newest["survey_data"]
    ext = sd["walk_extensions"]
    run_id = ext.get("run_id") or sd.get("surveyed_at") or newest.get("surveyed_at") or ""
    walk_runs = registry.list_filesystem_walk_runs(slug)
    stored = next((r for r in walk_runs if r["run_id"] == run_id), None)
    rows_kept = {
        "kept": stored is not None, "files": (stored or {}).get("files", 0), "hashed": (stored or {}).get("hashed", 0) or 0,
        "runs_kept": len(walk_runs),
        "egeria_report_guid": (stored or {}).get("egeria_report_guid", ""),
        "error": (ext.get("rows_kept") or {}).get("error", ""),
    }
    pub_row = next((r for r in rows if r.get("egeria_report_guid") and r.get("surveyed_at") == newest.get("surveyed_at")
                    and r.get("source") == "egeria-published"), None)
    proof = ((pub_row or {}).get("survey_data") or {}).get("published_to_egeria") or {}
    publish = _publish_words(proof) if pub_row else None
    publish = _pre_reset(publish, _reset_at(registry, slug) if publish else "",
                         proof.get("at") or (pub_row or {}).get("surveyed_at") or "")
    narratives = sd.get("folder_narratives") or []
    return {
        "run_id": run_id, "surveyed_at": sd.get("surveyed_at") or newest.get("surveyed_at") or "",
        "led_by": ext.get("led_by", ""), "capped_words": ext.get("capped_words") or (sd.get("walk_limits") or {}).get("capped_words", ""),
        "limits": sd.get("walk_limits"),
        "report_sentence": sd.get("report_sentence") or ((sd.get("surveyed_by") or {}).get("reach") or {}).get("report_sentence")
        or "surveyed by Resource Explorer",
        "folders": ext.get("folders"), "owners": ext.get("owners"), "modes": ext.get("modes"), "hashing": ext.get("hashing"),
        "narratives_summary": ext.get("narratives"), "narratives": narratives[:NARRATIVES_SHOWN],
        "narratives_not_shown": max(0, len(narratives) - NARRATIVES_SHOWN),
        "not_walked_by_name": (sd.get("walk_limits") or {}).get("not_walked_by_name") or {},
        "rows": rows_kept,
        "report_guid": (pub_row or {}).get("egeria_report_guid", ""), "published": bool(publish and publish["published"]),
        "publish": publish,
    }


def _file_profiles(registry, slug: str) -> dict:
    """The file_profiles step's cost line and newest run (Files build step 5); a failed read is said, never an empty run."""
    from resource_explorer.surveyors.filesystem.file_profiles import profiles_view

    try:
        return profiles_view(registry, slug)
    except Exception as exc:  # noqa: BLE001 -- the folder survey card still renders
        log.warning("%s: the file profiles could not be read", slug, exc_info=True)
        return {"error": f"could not read the data-file profiles: {type(exc).__name__}: {exc}"}


def _walk_derived(registry, slug: str, fork) -> tuple[dict, dict]:
    """(price, derived): the price line before the Survey press (F12) and the derived steps' stored results (Files build
    step 6). A failed read of either is said on its own line; the card still renders."""
    from resource_explorer.surveyors.filesystem import walk_derived

    try:
        price = walk_derived.price(registry, slug, egeria_leads=fork.surveyor == file_reach.SURVEYOR_EGERIA)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: the price line could not be read", slug, exc_info=True)
        price = {"priced": False, "words": f"not priced · could not read the last walk: {type(exc).__name__}: {exc}"}
    try:
        derived = walk_derived.derived_view(registry, slug)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: the derived steps could not be read", slug, exc_info=True)
        derived = {"error": f"could not read the derived steps: {type(exc).__name__}: {exc}"}
    return price, derived


def _narrative_docs(registry, slug: str) -> dict:
    """The narrative files read in full (Files build step 7, F11's long form): the price before the press and the newest
    run. A failed read is said on its own line; the card still renders."""
    from resource_explorer.surveyors.filesystem import narrative_docs

    try:
        return narrative_docs.view(registry, slug)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: the narrative files' run could not be read", slug, exc_info=True)
        return {"error": f"could not read the narrative files' run: {type(exc).__name__}: {exc}"}


def _class_match(registry, slug: str) -> dict:
    """data_class_match over the file profiles (Files build step 8, F7): the price before the press and the newest run.
    A failed read is said on its own line; the card still renders."""
    from resource_explorer.surveyors.filesystem import file_class_match

    try:
        return file_class_match.view(registry, slug)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: the data-class match could not be read", slug, exc_info=True)
        return {"error": f"could not read the data-class match: {type(exc).__name__}: {exc}"}


def folder_survey_view(registry, slug: str) -> dict:
    """The newest folder survey, whoever led it, with the probe state and who leads NOW."""
    fs = registry.get_filesystem(slug)
    fork = file_reach.fork_for(registry, fs)
    egeria = _egeria_candidate(registry, slug)
    re_walk = _re_candidate(registry, slug)
    candidates = [c for c in (egeria, re_walk) if c and not c.get("none")]
    base = {"slug": slug, "reach": fork.as_dict(), "leads": fork.surveyor,
            "egeria_run_state": (egeria or {}).get("run_state", ""),
            "egeria_asset": bool((getattr(fs, "egeria_asset_guid", "") or "").strip()), "extensions": extensions_view(registry, slug),
            "file_profiles": _file_profiles(registry, slug), "narrative_docs": _narrative_docs(registry, slug),
            "class_match": _class_match(registry, slug)}
    base["price"], base["derived"] = _walk_derived(registry, slug, fork)
    if not candidates:
        return {**base, "state": "none", "surveyor": "", "surveyed_at": "", "report_guid": "", "published": False,
                "publish": None, "pre_reset": None, "report_sentence": "", "results": None, "limits": None, "detail_lines": []}
    best = max(candidates, key=lambda c: _when(c["at"]) or datetime.min.replace(tzinfo=timezone.utc))
    return {**base, "state": "measured", "surveyor": best["surveyor"], "surveyed_at": best["at"],
            "report_guid": best["report_guid"], "published": best["published"],
            "publish": best.get("publish"), "pre_reset": best.get("pre_reset"),
            "report_sentence": best["report_sentence"],
            "detail_lines": best.get("detail_lines", []), "results": read_folder_survey(best["annotations"]),
            "limits": best["limits"], "broken_symlinks": best.get("broken_symlinks", []),
            "broken_symlink_count": best.get("broken_symlink_count")}
