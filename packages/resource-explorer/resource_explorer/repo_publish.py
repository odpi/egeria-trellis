"""Egeria on a repository: publish the survey report whole, read it back, forget the cached links,
catalog selected file types (parity slice G1, PI-001..PI-006).

**Publish publishes the survey the person decided on (brief section 1, owner 2026-10-07).** The report is
the latest completed survey already in the registry (`surveyors/survey_snapshot.py`), never the result of
a survey run by the press. Re-surveying is its own act (`resurvey`), never a side effect of a publish.
`report_published` records the `surveyed_at` of the survey it published and whether the report already
existed (`reused`), in the row's own `detail`: no schema change.

Every state word derives from a PROOF ROW, never from the branch the code took (PR #514 rules).
The rows live in `catalogue_commit_proofs` (no new table): `node_kind = "repo_report"` for the
survey report and the links, `node_kind = "file_type"` for a file type's DataSet. A success row is
written ONLY by GUID and ONLY after a read of that GUID; the full sentence Egeria gave is stored in
the row's `detail`, and the screen shows its first sentence with the rest one gesture away.

* `P_REPORT` (`report_published`)       the report GUID was READ BACK from the asset's reports.
* `P_SENT` (`report_sent`)              the create returned a GUID but the read did not show it yet.
* `P_READ_FAILED` (`read_failed`)       Egeria refused, or the read failed; the sentence is stored.
* `P_FORGOTTEN` (`links_forgotten`)     RE dropped its cached GUIDs and survey history. A LOCAL act:
                                        nothing is sent to Egeria, nothing is archived or deleted there.
* `P_FILE_TYPE` (`file_type_read_back`) a file type's DataSet GUID was read back by that GUID.

The ISSUE-117 block is untouched: nothing in this module archives or deletes in Egeria.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable

from resource_explorer.catalogue_commit import (
    P_EGERIA_RESET, P_READ_FAILED, P_REPORT, PROJECT_UNANSWERED, PROJECT_UNBOUND, PROJECT_UNBOUND_WORDS,
    RESET_WORDS, _ts, egeria_first_sentence)
from resource_explorer.surveyors import survey_snapshot
from resource_explorer.surveyors.survey_snapshot import NO_SURVEY_SENTENCE

NODE_REPORT = "repo_report"
NODE_FILE_TYPE = "file_type"
NODE_SUB_RESOURCE = "sub_resource"
P_SENT = "report_sent"
P_FORGOTTEN = "links_forgotten"
P_FILE_TYPE = "file_type_read_back"
P_SUB_RESOURCE = "sub_resource_read_back"

#: The sentence a first press shows when nothing answers "which Egeria project?" (brief PI-006).
NO_PROJECT_SENTENCE = ("no Egeria project context · bind this investigation to a project, "
                       "or publish without one")
#: What the confirmation says survives (brief PI-005), roll-forward language, never "reset Egeria".
FORGET_SENTENCE = ("Egeria is unchanged; Resource Explorer forgets its cached GUIDs and survey history "
                   "for this resource and re-reads them on the next publish.")

_running: set[str] = set()
_running_lock = threading.Lock()


class AlreadyRunning(RuntimeError):
    """A publish for this resource is already in flight (a second press is ignored, not queued)."""


def _now() -> str:
    return datetime.utcnow().isoformat()


def _repo_proofs(registry, slug: str, node_kind: str = NODE_REPORT) -> list[dict]:
    return [p for p in registry.list_catalogue_commit_proofs(slug) if p["node_kind"] == node_kind]


# ── project context (PI-006) ─────────────────────────────────────────────────

def resolve_project_context(registry, slug: str) -> dict | None:
    """The resource's Egeria project context, or None when nothing answers.

    An `unset` row (and no row) is not an answer. When the resource is in scope of an investigation
    bound to an Egeria project, that binding IS the answer and is WRITTEN, with the investigation that
    supplied it (the same rule `POST /api/egeria/{slug}/publish` has always applied)."""
    context = registry.get_project_context("repo", slug)
    if context and context.get("status") not in PROJECT_UNANSWERED:
        return context
    inherited = registry.inherited_egeria_project_context("repo", slug)
    if not inherited:
        return None
    registry.set_project_context(
        "repo", slug, status="linked",
        egeria_project_guid=inherited["egeria_project_guid"],
        egeria_project_qualified_name=inherited["egeria_project_qualified_name"],
        free_text_name=f"inherited from investigation '{inherited['_inherited_from_name']}'")
    return registry.get_project_context("repo", slug)


def project_words(context: dict | None) -> dict:
    """What the row says about the project: a short word and the name, from the stored context."""
    if not context or context.get("status") in (None, "", "unset"):
        return {"status": "unset", "word": "no project", "name": ""}
    status = context["status"]
    qn = context.get("egeria_project_qualified_name") or ""
    free = context.get("free_text_name") or ""
    if status == "linked":
        return {"status": status, "word": "project", "name": qn.split("::")[0] or qn or "linked",
                "detail": free}
    if status == PROJECT_UNBOUND:
        # The status is the value `unbound`; the words are display text. The name it had stays visible.
        return {"status": status, "word": PROJECT_UNBOUND_WORDS, "name": qn.split("::")[0] or qn}
    if status == "deferred":
        return {"status": status, "word": "project (named, not linked)", "name": free}
    if status == "personal":
        return {"status": status, "word": "personal exploration", "name": ""}
    return {"status": status, "word": "no project (chosen)", "name": ""}


# ── the state, from proof rows only (PI-002) ─────────────────────────────────

#: Brief F addendum (owner, 2026-10-11): "we just probably need to add a label that says confirmed once we read
#: back the same result after a publish". Line 1 of the Publish band carries ONE of these.
CONFIRMED = "confirmed"            # the read-back shows the same report with the same annotation count
UNCONFIRMED = "unconfirmed"        # published, but no read-back of the count yet (or the read failed)
DIFFERS = "differs"                # the read-back found the report but Egeria shows a different count


def confirmation(row: dict, unsettled: int = 0, run: dict | None = None) -> dict:
    """THE "same result" decision: whether what Egeria holds after a publish is what was published. The same
    report (a `report_published` proof is written only when the report's own GUID was READ BACK among the asset's
    reports) AND the same annotations. The band's label and every test use this one function.

    `run` (round 3) is that report's own outbox run as it stands now (`run_stats`: its annotation rows by the
    publish run id the proof records). It decides once it has rows:
      * any row not settled (pending, sending, backing off): "not yet confirmed · N sending";
      * settled, none dead, every published annotation done: "confirmed" (the rows are the proof; a count read
        once at publish time is NOT, since rows retried later land after it);
      * settled with dead rows: "Egeria shows N", N = the annotations that landed.
    With no run rows (an older proof, or a run that could not be read) the count the publish read back decides,
    as before. `unsettled` alone is the older form of `run["unsettled"]`.

    Returns {state, word, egeria_count, published_count, read_at, unsettled, basis}; state "" when nothing was
    published."""
    word = row.get("word")
    read_at = row.get("read_at") or ""
    day = read_at[5:10] if len(read_at) >= 10 else ""
    published = f"published {day}".strip()
    run = dict(run or {})
    pending = int(run.get("unsettled", unsettled) or 0)
    out = {"state": "", "word": "", "egeria_count": row.get("annotations_in_egeria"),
           "published_count": row.get("annotation_count"), "read_at": read_at, "unsettled": pending,
           "basis": "read_back"}
    waiting = f"{published} · not yet confirmed" + (f" · {pending} sending" if pending else "")
    if word == "sent":
        return {**out, "state": UNCONFIRMED, "word": waiting}
    if word != "published":
        return out
    sent = row.get("annotation_count")
    if pending:
        return {**out, "state": UNCONFIRMED, "word": waiting}
    queued = int(run.get("rows") or 0)
    if queued > 0:
        # Round 4: judged against what was QUEUED, one per annotation. Round 5: `published_count` stays the
        # survey's count; when the outbox capped the run (_MAX_ANNOTATIONS_PER_RUN) both counts are said.
        done, dead, unsent = int(run.get("done") or 0), int(run.get("dead") or 0), int(run.get("not_sent") or 0)
        capped = max(int(sent) - queued, 0) if sent is not None else 0
        counts = {"egeria_count": done, "queued_count": queued, "capped": capped, "basis": "outbox"}
        if dead or unsent or done < queued:
            return {**out, **counts, "state": DIFFERS, "word": f"{published} · Egeria shows {done}"
                    + (f" · {unsent} not sent" if unsent else "")
                    + (f" · {capped} not sent (capped)" if capped else "")}
        return {**out, **counts, "state": CONFIRMED,
                "word": f"{published} · confirmed" + (f" · {capped} not sent (capped)" if capped else "")}
    seen = row.get("annotations_in_egeria")
    if seen is None or sent is None:
        return {**out, "state": UNCONFIRMED, "word": waiting}
    if int(seen) == int(sent):
        return {**out, "state": CONFIRMED, "word": f"{published} · confirmed"}
    return {**out, "state": DIFFERS, "word": f"{published} · Egeria shows {int(seen)}"}


#: Outbox statuses that are not settled yet (the drain may still land them).
UNSETTLED = ("pending", "running", "failed")


def _per_annotation(rows: list[dict]) -> dict:
    """Round 4/5: one verdict per annotation (qualifiedName, the idempotency key). The run id
    (`Annotation::<slug>::<surveyed_at>`) is the SAME when one kept survey is published again, so a run's rows can be
    several presses' worth. Per qualifiedName: DONE if any row is done; else its newest row that was not retired
    decides (unsettled while pending/sending/backing off, dead when dead); a key whose only row was retired
    (cancelled/superseded, never sent) is NOT SENT; retired rows beside other rows of the same key are ignored."""
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r.get("qualified_name") or f"#{r.get('id')}", []).append(r)
    out = {"rows": 0, "done": 0, "dead": 0, "unsettled": 0, "not_sent": 0}
    for key_rows in by_key.values():
        if any(r.get("status") == "done" for r in key_rows):
            out["rows"] += 1
            out["done"] += 1
            continue
        live = [r for r in key_rows if r.get("status") not in ("cancelled", "superseded")]
        if not live:
            # Round 6: no live row and none done, however many retired rows: queued and never sent. ("capped" is
            # only the proof-vs-queued gap, never this.)
            out["rows"] += 1
            out["not_sent"] += 1
            continue
        newest = max(live, key=lambda r: int(r.get("id") or 0))
        out["rows"] += 1
        if newest.get("status") in UNSETTLED:
            out["unsettled"] += 1
        elif newest.get("status") == "dead":
            out["dead"] += 1
    return out


def run_stats(registry, slug: str, run_id: str) -> dict | None:
    """One publish run's outbox as it stands, one verdict per annotation (`_per_annotation`): {rows (what was
    queued), done, dead, not_sent} and `unsettled` across the annotations and the `::links` companion. None with
    no run id; {"unsettled": 1} when the rows cannot be read (unreadable is not settled, nothing is confirmed)."""
    if not run_id:
        return None
    try:
        ann = _per_annotation(registry.list_outbox_elements(run_id=run_id, entity_slug=slug, limit=100000))
        links = _per_annotation(registry.list_outbox_elements(run_id=f"{run_id}::links", entity_slug=slug,
                                                              limit=100000))
    except Exception:  # noqa: BLE001
        return {"rows": 0, "done": 0, "dead": 0, "not_sent": 0, "unsettled": 1}
    return {**ann, "unsettled": ann["unsettled"] + links["unsettled"]}


def unsettled_rows(registry, slug: str, run_id: str) -> int:
    """How many of one publish run's outbox rows (annotations, and the `::links` companion) are not settled."""
    return int((run_stats(registry, slug, run_id) or {}).get("unsettled") or 0)


def _report_row(last: dict | None, registry=None, slug: str = "") -> dict:
    row = _report_row_words(last)
    run_id = ((last or {}).get("detail") or {}).get("publish_run_id", "") if last else ""
    row["confirm"] = confirmation(row, run=run_stats(registry, slug, run_id) if registry is not None else None)
    return row


def _report_row_words(last: dict | None) -> dict:
    if last is None:
        return {"word": "none", "read_at": "", "sentence": "", "first": "", "report_guid": ""}
    kind, d = last["proof"], last.get("detail") or {}
    if kind == P_REPORT:
        return {"word": "published", "read_at": last["read_at"], "report_guid": last["element_guid"],
                "reused": bool(d.get("reused")), "annotation_count": d.get("annotation_count"),
                "annotations_in_egeria": d.get("annotations_in_egeria"),
                "sentence": "", "first": "", "surveyed_at": d.get("surveyed_at", "")}
    if kind == P_SENT:
        return {"word": "sent", "read_at": last["read_at"], "report_guid": last["element_guid"],
                "annotation_count": d.get("annotation_count"),
                "sentence": d.get("note", ""), "first": d.get("note", ""), "surveyed_at": d.get("surveyed_at", "")}
    if kind == P_READ_FAILED:
        full = d.get("error", "")
        first, rest = egeria_first_sentence(full)
        return {"word": "not published", "read_at": last["read_at"], "report_guid": last["element_guid"],
                "sentence": full, "first": first, "rest": rest}
    if kind == P_EGERIA_RESET:
        return {"word": "reset", "read_at": last["read_at"], "report_guid": "", "sentence": RESET_WORDS,
                "first": RESET_WORDS, "reset": d.get("text", "")}
    if kind == P_FORGOTTEN:
        return {"word": "forgotten", "read_at": last["read_at"], "report_guid": "", "sentence": FORGET_SENTENCE,
                "first": "links forgotten", "forgot": d}
    return {"word": "none", "read_at": "", "sentence": "", "first": "", "report_guid": ""}


def stale_step_keys(registry, slug: str, snapshot=None) -> list[str]:
    """The steps of the kept survey that are older than their refresh rule (the same freshness rule
    `workflows.analysis.assess_freshness` applies everywhere, via the commit's own stale-step
    computation). Named, not auto-run: a person chooses whether to re-survey. A repository with no
    run history has nothing the rule can judge, so nothing is called stale."""
    from resource_explorer.workflows.curate_commit import _resurvey_plan

    snap = snapshot or survey_snapshot.latest(registry, slug)
    if snap is None:
        return []
    steps, _note = _resurvey_plan(registry, slug)
    return sorted(k for k in (steps or []) if k in snap.steps)


def survey_state(registry, slug: str, now=None) -> dict:
    """What a publish would send: the kept survey, its age, its size, and how much of it is stale.
    RE's own records only; nothing here contacts Egeria and nothing here surveys."""
    snap = survey_snapshot.latest(registry, slug)
    if snap is None:
        before = survey_snapshot.surveyed_before_kept(registry, slug)
        return {"exists": False, "surveyed_before_kept": before,
                "sentence": survey_snapshot.SURVEYED_BEFORE_KEPT_SENTENCE if before else NO_SURVEY_SENTENCE}
    stale = stale_step_keys(registry, slug, snap)
    return {"exists": True, "surveyed_at": snap.surveyed_at, "age_seconds": snap.age_seconds(now),
            "annotations": snap.annotation_count, "steps": snap.step_count,
            "stale_steps": len(stale), "stale": stale}


def project_state(registry, slug: str) -> dict:
    """What the Egeria project answer is right now, WITHOUT writing it (a publish writes an inherited
    answer; a read must not). `status` "unset" means nothing answers and a press would be refused."""
    context = registry.get_project_context("repo", slug)
    inherited = None
    if not context or context.get("status") in PROJECT_UNANSWERED:
        inherited = registry.inherited_egeria_project_context("repo", slug)
    # An unbound context with nothing inheriting is shown as itself (its words), not as "no project".
    answered = context and (context.get("status") not in PROJECT_UNANSWERED or
                            (context.get("status") == PROJECT_UNBOUND and not inherited))
    return project_words(context) if answered else (
        {"status": "inherited", "word": "project", "name": (inherited["egeria_project_qualified_name"].split("::")[0]
                                                             or inherited["egeria_project_qualified_name"]),
         "detail": f"from investigation '{inherited['_inherited_from_name']}'"} if inherited
        else project_words(None))


def publish_state(registry, slug: str, viewer: str | None = None) -> dict:
    """One read of RE's own records. Nothing here contacts Egeria.

    With `viewer` (Brief F, the page's ONE Publish) it also says what that person's press sends besides the
    report (`goes_out`: tags and group, documentation sources, their own rating and notes), from the same
    functions the press acts on (`single_publish.goes_out`)."""
    asset_guid = registry.get_egeria_asset_guid(slug) or ""
    # The reset marker is keyed by slug, whatever node_kind the script gave it (a repo and a database can
    # share a slug and the script writes one marker per slug), so it is read across node kinds.
    proofs = [p for p in registry.list_catalogue_commit_proofs(slug)
              if (p["node_kind"] == NODE_REPORT and p["proof"] in (P_REPORT, P_SENT, P_READ_FAILED, P_FORGOTTEN))
              or p["proof"] == P_EGERIA_RESET]
    # The newest fact by time (read_at), not by row id: the reset marker is written after the fact, but it
    # carries the reset time, so a publish made after the reset still wins over it.
    # Same tie rule as catalogue_commit.derive_commit_state: a proof read AT the reset time is live, so at an
    # equal time the marker sorts BEFORE the proof (a marker is never newer than a publish in its own second).
    proofs.sort(key=lambda p: (_ts(p["read_at"]), p["proof"] != P_EGERIA_RESET, p["id"]))
    last = proofs[-1] if proofs else None
    proofs = [p for p in proofs if p["proof"] != P_EGERIA_RESET]
    project = project_state(registry, slug)
    out = {"slug": slug, "in_egeria": bool(asset_guid), "asset_guid": asset_guid,
           "row": _report_row(last, registry, slug), "project": project,
           "survey": survey_state(registry, slug),
           "can_publish_again": any(p["proof"] in (P_REPORT, P_SENT) for p in proofs)}
    if viewer is not None:
        from resource_explorer.single_publish import goes_out

        out["goes_out"] = goes_out(registry, "repo", slug, viewer)
    return out


# ── publish whole (PI-001) ───────────────────────────────────────────────────

def publish_report(registry, slug: str, author: str, *, without_project: bool = False) -> dict:
    """The page's ONE Publish (Brief F): the kept survey whole, read back by its GUID with a proof row, then the rest
    of the press (`single_publish.send_with_publish`: tags and group, documentation sources, the presser's own
    rating and notes), as the presser.

    **Never runs a survey** (brief section 1). With no kept survey it returns `{"gate": "no_survey"}` (the route maps
    it to 409) and no REPORT is sent; the first survey is never run implicitly. `{"gate": "context_required"}` (428)
    when no project answer exists and the press did not choose "without one". A gate stops the report only: a
    resource already in Egeria still sends the rest (`sent`), which the band listed as "will publish".

    One press per resource at a time, gated or not (round 3): a second is `AlreadyRunning` (409), since the outbox
    enqueue is not idempotent and two concurrent sends would queue every item twice.

    Zones are NEVER a per-press choice: the publisher's own zone rule applies. A 409 on the report's name reuses the
    report already in Egeria (the publisher does that), recorded as such."""
    from resource_explorer.single_publish import send_with_publish

    def rest(doc_sources: bool = True) -> dict:
        asset = registry.get_egeria_asset_guid(slug) or ""
        return send_with_publish(registry, "repo", slug, asset, author, doc_sources=doc_sources) if asset else {}

    with _running_lock:
        if slug in _running:
            raise AlreadyRunning(f"a publish for {slug} is already running")
        _running.add(slug)
    try:
        if without_project:
            registry.set_project_context("repo", slug, status="declined")
        context = resolve_project_context(registry, slug)
        if context is None:
            return {"gate": "context_required", "sentence": NO_PROJECT_SENTENCE, "sent": rest()}
        snapshot = survey_snapshot.latest(registry, slug)
        if snapshot is None:
            return {"gate": "no_survey", "sentence": survey_snapshot.no_kept_survey_sentence(registry, slug),
                    "sent": rest()}
        out = publish_snapshot(registry, slug, author, context, snapshot)
        # The rest goes whenever the asset exists (even when the report itself failed this time). The publisher
        # already sent the documentation sources when it got as far as the asset (EgeriaPublisher.publish).
        sent = rest(doc_sources=not out.get("doc_sources_sent"))
        return {"ok": out["ok"], "stage": out.get("stage", ""), "report_guid": out.get("report_guid", ""),
                "reused": out.get("reused", False), "sent": sent, **publish_state(registry, slug, viewer=author)}
    finally:
        with _running_lock:
            _running.discard(slug)


def _proof(registry, slug: str, proof: str, **kw) -> int:
    return registry.append_catalogue_commit_proof(slug, proof=proof, node_kind=kw.pop("node_kind", NODE_REPORT), **kw)


def resurvey(registry, slug: str, author: str, steps: list[str] | None = None) -> dict:
    """Run the survey and NOTHING else (brief section 1): no publish, no Egeria write. `steps` names
    the steps to run (the stale ones); `None` runs them all. The orchestrator keeps what completed as
    the repository's latest survey, and the returned state shows the new age."""
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    key = f"survey:{slug}"
    with _running_lock:
        if key in _running:
            raise AlreadyRunning(f"a survey for {slug} is already running")
        _running.add(key)
    try:
        result = SurveyOrchestrator(registry=registry).run(slug, steps=steps)
    finally:
        with _running_lock:
            _running.discard(key)
    errors = list(result.errors) + ([result.snapshot_error] if result.snapshot_error else [])
    return {"ok": not errors, "errors": errors, "survey": survey_state(registry, slug), "by": author}


def publish_snapshot(registry, slug: str, author: str, context: dict, snapshot) -> dict:
    """Send one kept survey to Egeria and record what happened. The one publish both the band's
    "Publish to Egeria" and the Curate commit use, so the two cannot disagree about the proof row.

    Returns `{ok, stage, report_guid, reused, surveyed_at, annotation_count, asset_guid}`."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    project = project_words(context)
    registered = registry.get(slug)
    result = survey_snapshot.to_result(registered, snapshot)
    # Runtime dependencies a person CONFIRMED travel as annotations only (brief section 3): Egeria's
    # planned "deployed by" relationship types are not available, and nothing here writes a relationship.
    from resource_explorer import dependency_table
    for annotation in dependency_table.runtime_annotations(registry, slug):
        result.add(annotation)
    surveyed_at = snapshot.surveyed_at
    publisher = EgeriaPublisher(registry=registry)           # zones: the publisher's own rule only
    # Brief F round 4: whether the publisher reached the asset and sent the documentation sources itself.
    docs_sent = lambda: getattr(publisher, "doc_sources_attempted", False) is True  # noqa: E731
    try:
        # The page's Publish waits for Egeria and then reads the report back (RUNS_PUBLISH_INLINE governs a run's
        # auto-publish only).
        report_guid = publisher.publish(result, defer_drain=False)
    except Exception as exc:
        _proof(registry, slug, P_READ_FAILED, recorded_by=author,
               detail={"error": str(exc), "stage": "publish", "surveyed_at": surveyed_at, "project": project})
        return {"ok": False, "stage": "publish", "error": str(exc), "surveyed_at": surveyed_at,
                "doc_sources_sent": docs_sent()}

    asset_guid = registry.get_egeria_asset_guid(slug) or ""
    reused = bool(getattr(publisher, "report_reused", False))
    common = {"surveyed_at": surveyed_at, "annotation_count": len(result.annotations),
              "steps": snapshot.step_count, "reused": reused, "project": project,
              # Brief F round 2: the outbox run of this report's annotations, so "confirmed" can tell rows still
              # being sent from a real difference (`confirmation`).
              "publish_run_id": run_id if isinstance(run_id := getattr(publisher, "publish_run_id", ""), str) else ""}
    # The proof is written AFTER a read of the report's own GUID: the asset's reports as Egeria answers them.
    read: list[dict] | None
    try:
        read = publisher.get_survey_reports_by_guid(asset_guid) if asset_guid else []
    except Exception as exc:
        read = None
        read_error = str(exc)
    if read is None:
        _proof(registry, slug, P_SENT, element_guid=report_guid or "", target_guid=asset_guid, recorded_by=author,
               detail={**common, "note": f"waiting for Egeria · the report could not be read back: {read_error}"[:400]})
    else:
        match = next((r for r in read if r.get("guid") == report_guid), None) if report_guid else None
        if match is not None:
            _proof(registry, slug, P_REPORT, element_guid=report_guid, target_guid=asset_guid,
                   qualified_name=match.get("qualified_name", ""), recorded_by=author,
                   detail={**common, "annotations_in_egeria": match.get("annotation_count")})
        else:
            _proof(registry, slug, P_SENT, element_guid=report_guid or "", target_guid=asset_guid, recorded_by=author,
                   detail={**common, "note": "sent · waiting for Egeria: the report is not in the asset's reports yet"})
    return {"ok": True, "stage": "", "report_guid": report_guid, "reused": reused, "surveyed_at": surveyed_at,
            "annotation_count": len(result.annotations), "asset_guid": asset_guid,
            "doc_sources_sent": docs_sent(),
            "read_back": read is not None and match_found(read, report_guid)}


def match_found(read: list[dict], report_guid: str) -> bool:
    """Whether the report's own GUID is among the asset's reports as Egeria answered them."""
    return bool(report_guid) and any(r.get("guid") == report_guid for r in read)


# ── the commit's sub-resources: a proof row per element, by GUID, after a read (brief section 2) ──

def record_sub_resource_proofs(registry, slug: str, curation_id: str, author: str, wanted: dict,
                               selected: list[str], guids: dict, reader, not_checked: dict | None = None) -> dict:
    """One proof row for every sub-resource the commit sent (the chosen ones AND the ancestor folders a
    NestedFile needs), written ONLY by GUID and ONLY after a read of that GUID.

    `wanted` is `{locator: kind}`; `selected` the locators the person chose (the rest are containers
    added so a file has its folder); `guids` what the publisher returned; `reader(guid)` reads one
    element back (a dict) or returns None when Egeria did not show it yet.

    * P_SUB_RESOURCE  the GUID was read back.
    * P_SENT          Egeria returned a GUID but the read did not show the element yet.
    * P_READ_FAILED   no GUID came back, or the read failed; the full sentence is stored. A locator in
                      `not_checked` (the publisher's `sub_resources_not_checked`: its look-up could not be
                      made, so nothing was sent) says "could not check, not created" with the reason, never
                      "Egeria returned no GUID", which would claim a create was tried.

    Returns the counts the table shows (`read_back`, `sent`, `failed`), which are these rows' counts,
    never the request's."""
    counts = {"read_back": 0, "sent": 0, "failed": 0}
    for locator in sorted(wanted):
        kind = wanted[locator]
        role = "chosen" if locator in selected else "container"
        base = {"kind": kind, "role": role, "curation_id": curation_id}
        guid = guids.get(locator, "")
        if not guid and locator in (not_checked or {}):
            _proof(registry, slug, P_READ_FAILED, node_kind=NODE_SUB_RESOURCE, table_name=locator,
                   curation_id=curation_id, recorded_by=author,
                   detail={**base, "could_not_check": True,
                           "error": f"could not check, not created: {not_checked[locator]}"})
            counts["failed"] += 1
            continue
        if not guid:
            _proof(registry, slug, P_READ_FAILED, node_kind=NODE_SUB_RESOURCE, table_name=locator,
                   curation_id=curation_id, recorded_by=author,
                   detail={**base, "error": "Egeria returned no GUID for this element: it was not created"})
            counts["failed"] += 1
            continue
        try:
            seen = reader(guid)
        except Exception as exc:
            _proof(registry, slug, P_READ_FAILED, node_kind=NODE_SUB_RESOURCE, table_name=locator,
                   element_guid=guid, curation_id=curation_id, recorded_by=author,
                   detail={**base, "error": str(exc)})
            counts["failed"] += 1
            continue
        if seen:
            _proof(registry, slug, P_SUB_RESOURCE, node_kind=NODE_SUB_RESOURCE, table_name=locator,
                   element_guid=guid, curation_id=curation_id, recorded_by=author, detail=base)
            counts["read_back"] += 1
        else:
            _proof(registry, slug, P_SENT, node_kind=NODE_SUB_RESOURCE, table_name=locator,
                   element_guid=guid, curation_id=curation_id, recorded_by=author,
                   detail={**base, "note": "sent · waiting for Egeria: the element was not shown yet"})
            counts["sent"] += 1
    return counts


def commit_proof_summary(registry, slug: str, curation: dict) -> dict:
    """The state column of the commit table, from PROOF ROWS only (brief section 2): what Egeria was
    shown to hold after the press, with the counts the rows have (never the request's counts).

    `report`        the newest report proof at or after the press: published / sent / not published;
    `sub_resources` counts by proof for this curation: read back / sent / failed, split chosen vs
                    container, with the first failure's first sentence;
    `file_types`    the file types cataloged so far (read back), from their own proof rows."""
    requested = curation.get("requested_at") or ""
    proofs = registry.list_catalogue_commit_proofs(slug)
    out: dict = {"report": {"state": "none"}, "sub_resources": {"read_back": 0, "sent": 0, "failed": 0,
                 "chosen_read_back": 0, "container_read_back": 0, "first_failure": "",
                 # the same counts per row of the table: the files you chose, the folders you chose, the containers
                 "by_row": {k: {"read_back": 0, "sent": 0, "failed": 0, "first_failure": ""}
                            for k in ("files", "folders", "containers")}},
                 "file_types": {"read_back": 0}}
    reports = [p for p in proofs if p["node_kind"] == NODE_REPORT and p["proof"] in (P_REPORT, P_SENT, P_READ_FAILED)
               and (p["read_at"] or "") >= requested[:19]]
    if reports:
        last, d = reports[-1], reports[-1].get("detail") or {}
        if last["proof"] == P_REPORT:
            # ONE decider for "same result" (the tab-1 band's): this report's own outbox run settles it.
            out["confirm"] = _report_row(last, registry, slug)["confirm"]
            out["report"] = {"state": "published", "read_at": last["read_at"], "surveyed_at": d.get("surveyed_at", ""),
                             "annotation_count": d.get("annotation_count"), "reused": bool(d.get("reused")),
                             "guid": last["element_guid"]}
        elif last["proof"] == P_SENT:
            out["report"] = {"state": "sent", "read_at": last["read_at"], "surveyed_at": d.get("surveyed_at", ""),
                             "guid": last["element_guid"]}
        else:
            first, rest = egeria_first_sentence(d.get("error", ""))
            out["report"] = {"state": "not published", "read_at": last["read_at"], "first": first, "rest": rest}
    for p in proofs:
        if p["node_kind"] == NODE_SUB_RESOURCE and p.get("curation_id") == curation.get("id"):
            d = p.get("detail") or {}
            row = out["sub_resources"]["by_row"]["containers" if d.get("role") == "container"
                                                  else "files" if d.get("kind") == "file" else "folders"]
            row[{P_SUB_RESOURCE: "read_back", P_SENT: "sent", P_READ_FAILED: "failed"}.get(p["proof"], "sent")] += 1
            if p["proof"] == P_READ_FAILED and not row["first_failure"]:
                row["first_failure"] = egeria_first_sentence(d.get("error", ""))[0]
            if p["proof"] == P_SUB_RESOURCE:
                out["sub_resources"]["read_back"] += 1
                out["sub_resources"]["chosen_read_back" if d.get("role") == "chosen" else "container_read_back"] += 1
            elif p["proof"] == P_SENT:
                out["sub_resources"]["sent"] += 1
            elif p["proof"] == P_READ_FAILED:
                out["sub_resources"]["failed"] += 1
                if not out["sub_resources"]["first_failure"]:
                    out["sub_resources"]["first_failure"] = egeria_first_sentence(d.get("error", ""))[0]
        elif p["node_kind"] == NODE_FILE_TYPE and p["proof"] == P_FILE_TYPE:
            out["file_types"]["read_back"] += 1
    return out


# ── forget the cached links (PI-005) ─────────────────────────────────────────

def forget_links(registry, slug: str, author: str) -> dict:
    """Clear RE's cached asset GUID and survey history for ONE resource, in RE's registry only.

    Never sends anything to Egeria, never archives or deletes there. The act is recorded as a row so
    the screen can say it happened and who did it; the next publish re-reads the GUID from Egeria."""
    before = registry.get_egeria_asset_guid(slug) or ""
    cleared = registry.clear_egeria_registration(slug)
    _proof(registry, slug, P_FORGOTTEN, element_guid=before, recorded_by=author,
           detail={"asset_guid_cleared": cleared["asset_guid_cleared"],
                   "surveys_deleted": cleared["surveys_deleted"],
                   "published_types_deleted": cleared.get("published_types_deleted", 0),
                   "egeria_unchanged": True})
    return {"ok": True, "sentence": FORGET_SENTENCE, "forgot_asset_guid": before, **cleared,
            **publish_state(registry, slug)}


# ── file types: a measurement, not a press (ruling 2026-10-07) ───────────────

#: The one sentence the retired routes answer (HTTP 410). From DESIGN-FILE-TYPES-AS-ANNOTATIONS.md.
FILE_TYPES_RETIRED_SENTENCE = ("file types are published as profile annotations in the survey report; "
                               "files are published by selection on Curate")

#: What an earlier DataSet reads as. They stay in Egeria; nothing here deletes or archives one.
RETIRED_DATASET_WORD = "retired mechanism · kept in Egeria"


def file_type_measurements(registry, project, slug: str) -> dict:
    """READ ONLY. What the survey report carries for file types, and the DataSets earlier presses made.

    `profiles`  the annotations a publish sends (Egeria's names verbatim), counted from the stored
                inventory: the same function the survey step uses, so screen and report agree.
    `retired`   one row per DataSet an earlier press created and read back, each marked
                "retired mechanism · kept in Egeria". Nothing here calls Egeria."""
    from resource_explorer.surveyors.file_type_profile import build_file_type_annotations

    summary = registry.file_inventory_summary(slug)
    anns = build_file_type_annotations(registry, slug, surveyed_at=summary.get("indexed_at") or "")
    profiles = [{"name": a.annotation_type_name, "type": a.annotation_type.value, "summary": a.summary,
                 "counts": dict(getattr(a, "value_count", None) or getattr(a, "resource_properties", {}))}
                for a in anns]
    retired = []
    for p in _repo_proofs(registry, slug, NODE_FILE_TYPE):
        if p["proof"] == P_FILE_TYPE:
            retired.append({"label": p["table_name"], "dataset_guid": p["element_guid"],
                            "qualified_name": p["qualified_name"], "word": RETIRED_DATASET_WORD,
                            "read_at": p["read_at"]})
    return {"slug": slug, "in_egeria": bool(registry.get_egeria_asset_guid(slug)),
            "inventoried": bool(summary.get("total")), "profiles": profiles, "retired": retired,
            "retired_word": RETIRED_DATASET_WORD}
