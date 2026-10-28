"""Catalogue →: the commit behind the Curate screen, run asynchronously.

The design's three consequences of Curate being a handoff rather than a
state change inside RE: the commit is asynchronous and can fail elsewhere;
"running · N targets" is read, not known; after Curate RE is not the
source of truth. So the commit is a queued run, each step writes its
outcome to the curation record as it lands, and the screen shows the
record. Nothing here raises past a step: a step that fails is a failed
step on the record, and the next step runs unless it depends on it.

Steps, in order:
  publish_asset     publish the survey already kept (never a survey run by
                    the press; an optional box re-surveys the stale steps
                    first) -- creates or finds the repository's own Asset and
                    links a SurveyReport (measurements LINKED, not copied)
  classifications   enrichment judgements COPIED onto the asset as
                    Confidentiality / Criticality / Retention, with the
                    author as steward and the date in the notes -- they
                    exist nowhere else
  sub_resources     the worthy sub-resources the person confirmed, as
                    FileFolder/DataFile assets under the repo's
  components        skipped here: accepted components materialize when
                    they are accepted (workflows/curate.py); the record
                    says so rather than pretending to have done it

**Decision (project owner, 2026-09-20):** pressing Catalogue used to run
`SurveyOrchestrator(...).run(slug, steps=None)` unconditionally -- every
sub-surveyor, every time, regardless of whether fresh data already sat in
Egeria. The project owner's framing: "it shouldn't have to execute all of
the surveys anyway -- it's likely that the surveys that the user is
interested in have already been published to Egeria? And why would we
execute and run surveys that the user isn't interested in?"

`publish_asset` now reuses the freshness gate `assess_freshness()`
(`workflows/analysis.py`) already applies elsewhere (Assessment/Analysis
cards, the scheduler) instead of introducing a second one: it reads
`registry.get_analysis_last_run("repo", slug)` for this repo's run
history and re-surveys only the analyses that are BOTH (a) something
someone has actually run before and (b) currently stale by
`RunsConfig.freshness_seconds`.

Two things follow from "someone has actually run before":

* An analysis with NO run history at all is excluded from the re-survey
  list even though it would trivially count as "stale" (there is no
  last-run timestamp for it to be fresh against). Catalogue must not be
  the thing that runs an analysis for the very first time -- that is
  running a survey nobody asked for, exactly the complaint above. A
  first-ever press of Catalogue, with no history for anything at all, is
  different (see below): there is nothing yet to be selective ABOUT, so
  that one case still runs a full survey once.
* A step whose last run was recorded as `error` still counts as "having
  run history" (it is a key in `get_analysis_last_run`'s result), so it
  stays eligible for re-running -- `assess_freshness` already treats an
  error run as not-fresh, so it gets re-run. Refusing to retry a step
  that failed last time is not a behaviour anyone wants.

Most of the time the re-survey list comes back short or empty:
`workflows/analysis.py`'s auto-publish already pushes an individual
analysis's results to Egeria the moment it runs, for any repo with
`has_assigned_egeria_project("repo", slug)` -- which is Catalogue's own
precondition a few lines below. So by the time a curator reaches
Catalogue, most analyses that ever ran have typically already been
published; freshness is what's left to decide. The one edge case that
assumption doesn't cover -- an analysis ran before the Egeria project was
ever assigned to this repo, so its data is fresh but was never actually
published -- is handled explicitly: if nothing is stale but this repo has
no cached Egeria asset guid yet, the survey still runs (with an empty
`steps=[]`, since nothing needs re-computing) purely so
`EgeriaPublisher.publish()` can find-or-create the asset; that call is
idempotent (`_find_or_create_asset`), so making it defensively is cheaper
than trying to prove up front that it is unnecessary.
"""
from __future__ import annotations

import json
import logging
import re

from resource_explorer.curate_plan import Curations, classification_writes
from resource_explorer.registry import ProjectRegistry
from resource_explorer.secret_redaction import scrub_text

log = logging.getLogger(__name__)

STEPS = ("publish_asset", "classifications", "sub_resources", "components")


def _classification_bodies(enrichment: dict) -> list[tuple[str, str, dict]]:
    """(step-method name, human name, body) for each judgement that maps to a governance classification.

    Brief F §9 (one source): these are read through `curate_plan.classification_writes`, the SAME function the
    plan's "what gets written" is drawn from, which decides from `context_facts()` whether each classification is
    written. A value outside the level map is not dropped -- it goes into `notes` with level 0, because testimony is
    copied, not normalised away."""
    return [c["write"] for c in classification_writes(enrichment) if c["write"]]


NO_RETENTION_BASIS = "skipped · no retention basis picked"


def _retention_skipped(enrichment: dict) -> bool:
    """A retention field exists (a note, say) but no basis resolves from it. Nothing stored at all is not
    this: that is 'not set', and the step says nothing about it. Read from the same `classification_writes`
    item the plan shows as "skipped · no retention basis picked" (Brief F §9)."""
    return any(c["key"] == "retention" and c["skipped"] for c in classification_writes(enrichment))


def _exc_text(exc: Exception) -> tuple[str, str, str]:
    """(short readable text, full text for the log, Egeria's reason). The reason is the response body
    pyegeria keeps in additional_info['reason']; its message field is preferred when it parses."""
    full = f"{type(exc).__name__}: {exc}"
    reason = ""
    info = getattr(exc, "additional_info", None)
    if isinstance(info, dict) and info.get("reason"):
        reason = str(info["reason"])
        try:
            parsed = json.loads(reason)
            if isinstance(parsed, dict):
                reason = str(parsed.get("exceptionErrorMessage") or parsed.get("userMessage") or reason)
        except ValueError:
            pass
    full, reason = scrub_text(full), scrub_text(reason)
    short = f"{reason[:200]}" if reason else full[:200]
    return short, full, reason


def _resurvey_plan(registry: ProjectRegistry, slug: str) -> tuple[list[str] | None, str]:
    """What `publish_asset` should re-survey for `slug`, and why.

    Returns (steps, note):

    steps
      * ``None``       — no run history at all for this repo; run a full
        survey (``steps=None``), same as this call always did before this
        freshness gate existed. There is nothing to be selective about on
        a genuinely first catalogue.
      * ``[]``         — every previously-run analysis is still fresh.
        Nothing needs re-computing; the caller decides separately whether
        the asset itself still needs to be created (see
        ``execute_curation`` below).
      * ``[key, ...]`` — the ``re_analysis_step`` keys (translated from
        stale ``analysis_id``s via ``REPO_ANALYSIS_SOURCE_STEPS``, the same
        vocabulary ``SurveyOrchestrator.run(steps=...)`` expects) to re-run.
        Deliberately ``REPO_ANALYSIS_SOURCE_STEPS`` and not
        ``REPO_ANALYSIS_STEP_MAP`` -- the latter is the ownership
        *partition* (used for run attribution) and resolves a
        derives-from analysis that owns no steps of its own, such as
        ``architecture_diagram``, to an empty list; that would silently
        never re-run the steps that actually refresh it. See
        ``test_run_publish_honesty.py``'s
        ``TestOwnershipMapIsOnlyUsedForAttribution``, which pins this
        distinction and would fail if this module read the ownership map
        directly.

    Only analyses with run history are ever candidates — an analysis
    nobody has ever run is excluded outright, never treated as "stale",
    so Catalogue can't be the thing that runs it for the first time. An
    analysis whose last run errored still has run history (it is a key
    in ``get_analysis_last_run``'s result) and is therefore still a
    candidate; ``assess_freshness`` itself already treats an error run as
    not-fresh, so it comes back stale and is re-run.
    """
    from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_SOURCE_STEPS
    from resource_explorer.workflows.analysis import assess_freshness

    history = registry.get_analysis_last_run("repo", slug)
    if not history:
        return None, "no run history for this repo yet — running a full survey for the first publish"

    stale_ids, fresh_ids = [], []
    for analysis_id in sorted(history):
        # A reserved bookkeeping key, not a real analysis_id -- see
        # get_analysis_last_run()'s own comment on "__unattributed_surveys__".
        if analysis_id.startswith("__") and analysis_id.endswith("__"):
            continue
        freshness = assess_freshness(registry, "repo", slug, analysis_id)
        (fresh_ids if freshness.fresh else stale_ids).append(analysis_id)

    step_keys = sorted({key for aid in stale_ids for key in REPO_ANALYSIS_SOURCE_STEPS.get(aid, [])})

    # Designer review, 2026-09-20: the never-run set was previously invisible
    # in this message -- reported as absence rather than as a state. They are
    # correctly never started (that is the ruling above), but a curator
    # reading "N of M already fresh" has no way to tell "a new analysis has
    # never reached this repo" from "everything relevant already ran". Named
    # explicitly so it stays a visible state, not a silent one -- the default
    # (don't start it for them) is unchanged.
    known_ids = set(REPO_ANALYSIS_SOURCE_STEPS)
    never_run = sorted(known_ids - set(history))
    never_run_clause = (
        f" · {len(never_run)} analys{'is' if len(never_run) == 1 else 'es'} "
        f"never run here and not started"
        if never_run else ""
    )

    plural = "is" if len(history) == 1 else "es"
    if stale_ids:
        note = (f"{len(fresh_ids)} of {len(history)} previously-run analys{plural} already fresh "
                f"and skipped; re-surveying {len(stale_ids)} stale one(s){never_run_clause}")
    else:
        note = f"all {len(history)} previously-run analys{plural} already fresh — nothing to re-survey{never_run_clause}"
    return step_keys, note


def execute_curation(registry: ProjectRegistry, curation_id: str) -> dict:
    cur = Curations(registry)
    rec = cur.get(curation_id)
    if not rec:
        raise KeyError(curation_id)
    slug = rec["entity_slug"]
    sel = rec.get("selection") or {}
    project = registry.get(slug)
    if not project:
        cur.set_step(curation_id, "publish_asset", "failed", "resource no longer registered")
        return cur.finish(curation_id)

    # ── 1. the asset and its survey report ─────────────────────────────
    #
    # Brief section 1 (owner, 2026-10-07): the commit PUBLISHES THE SURVEY THE PERSON DECIDED ON. It
    # does not refresh stale surveys on its own and then publish; re-surveying is a separate act that
    # the commit offers as a box, off by default. Unchecked, nothing is surveyed here. Checked, exactly
    # the stale steps run, and then the survey is published. With nothing surveyed yet the commit is
    # blocked (the route says so before the record exists; this is the same sentence if it is reached).
    asset_guid = ""
    cur.set_step(curation_id, "publish_asset", "running", "publishing the survey already kept")
    try:
        from resource_explorer import repo_publish
        from resource_explorer.surveyors import survey_snapshot

        context = repo_publish.resolve_project_context(registry, slug)
        if context is None:
            raise RuntimeError("no Egeria Project context — decide it on the Scouting pane (or bind the investigation) and press Publish again")
        snap = survey_snapshot.latest(registry, slug)
        if snap is None:
            raise RuntimeError(survey_snapshot.no_kept_survey_sentence(registry, slug))
        resurveyed = ""
        if sel.get("resurvey_stale"):
            stale = repo_publish.stale_step_keys(registry, slug, snap)
            if stale:
                from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

                cur.set_step(curation_id, "publish_asset", "running",
                             f"re-surveying {len(stale)} stale step(s) first: {', '.join(stale)}")
                res = SurveyOrchestrator(registry=registry).run(slug, steps=stale)
                problems = list(res.errors) + ([res.snapshot_error] if res.snapshot_error else [])
                snap = survey_snapshot.latest(registry, slug) or snap
                resurveyed = (f"re-surveyed {len(stale)} stale step(s) first"
                              + (f" ({len(problems)} problem(s): {scrub_text('; '.join(problems))[:300]})" if problems else "")
                              + " · ")
            else:
                resurveyed = "nothing was stale · "
        import asyncio
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        try:
            out = repo_publish.publish_snapshot(registry, slug, rec.get("author") or "", context, snap)
        finally:
            loop.close(); asyncio.set_event_loop(None)
        if not out["ok"]:
            raise RuntimeError(out["error"])
        asset_guid = out["asset_guid"]
        cur.set_step(curation_id, "publish_asset", "done",
                     f"{resurveyed}{'published' if out['read_back'] else 'sent, not yet read back'}"
                     f" · from the survey of {out['surveyed_at'][:10]}"
                     f"{' · reused the report already in Egeria' if out['reused'] else ''}"
                     f" · asset {asset_guid or '?'} · survey report {out['report_guid']} · "
                     f"{out['annotation_count']} annotations linked")
    except Exception as exc:
        short, full, _reason = _exc_text(exc)
        log.warning("curation %s: publish failed for %s: %s", curation_id, slug, full[:2000])
        cur.set_step(curation_id, "publish_asset", "failed", short, more=full[:2000] if len(full) > 200 else "")
        asset_guid = registry.get_egeria_asset_guid(slug) or ""

    # ── 2. testimony, copied ───────────────────────────────────────────
    ctx = registry.get_context("repo", slug) or {}
    bodies = _classification_bodies(ctx.get("enrichment") or {})
    if not bodies and _retention_skipped(ctx.get("enrichment") or {}):
        cur.set_step(curation_id, "classifications", "skipped", f"skipped: Retention · {NO_RETENTION_BASIS}")
    elif not bodies:
        cur.set_step(curation_id, "classifications", "skipped", "no sensitivity, criticality or retention set on the Enrichment pane")
    elif not asset_guid:
        cur.set_step(curation_id, "classifications", "failed", "no asset to classify — the publish step did not produce one")
    else:
        cur.set_step(curation_id, "classifications", "running")
        from resource_explorer.egeria_identity import classification_client
        done, failed, skipped, more = [], [], [], []
        if _retention_skipped(ctx.get("enrichment") or {}):
            skipped.append(f"Retention · {NO_RETENTION_BASIS}")
        try:
            client = classification_client()
            for method, name, body in bodies:
                try:
                    getattr(client, method)(asset_guid, body)
                    done.append(name)
                except Exception as exc:
                    short, full, reason = _exc_text(exc)
                    log.warning("curation %s: %s failed for %s: %s | egeria reason: %s",
                                curation_id, name, slug, full[:2000], reason[:2000] or "(none)")
                    failed.append(f"{name}: {short}" + (" (full text in the log)" if reason or len(full) > 200 else ""))
                    more.append(f"{name}\n{full[:1000]}" + (f"\nEgeria: {reason[:1000]}" if reason else ""))
        except Exception as exc:
            short, full, _r = _exc_text(exc)
            log.warning("curation %s: no classification client for %s: %s", curation_id, slug, full[:2000])
            failed.append(f"no classification client: {short}")
        parts = ([f"done: {', '.join(done)}"] if done else []) + \
                ([f"skipped: {', '.join(skipped)}"] if skipped else []) + \
                ([f"failed: {' | '.join(failed)}"] if failed else [])
        state = "failed" if failed else ("done" if done else "skipped")
        cur.set_step(curation_id, "classifications", state, " · ".join(parts), more="\n\n".join(more))

    # ── 3. what it holds ───────────────────────────────────────────────
    locators = list(sel.get("sub_resources") or [])
    if not locators:
        cur.set_step(curation_id, "sub_resources", "skipped", "none selected")
    elif not asset_guid:
        cur.set_step(curation_id, "sub_resources", "failed", "no parent asset — the publish step did not produce one")
    else:
        cur.set_step(curation_id, "sub_resources", "running")
        try:
            from resource_explorer.surveyors.egeria_publisher import rename_sentence
            # The list is the selection RECORD as it stood at the press (the route froze it into the
            # selection); this step never reads a request's list. Local cataloguing, the ancestor folders a
            # NestedFile needs, the publish and a proof row per element by GUID are one shared function.
            from resource_explorer import resource_scope
            out = resource_scope.publish_chosen(
                registry, slug, github_url=project.github_url, asset_guid=asset_guid, curation_id=curation_id,
                author=rec.get("author") or "", locators=locators)
            guids, proof_counts = out["guids"], out["counts"]
            missing = out["missing"]
            ancestors = out["ancestors"]
            # Count against what was SELECTED; the ancestor folders NestedFile
            # needs are named separately ("32 of 31" on the first live press).
            cur.set_step(curation_id, "sub_resources", "failed" if missing or proof_counts["failed"] else "done",
                         f"{len(locators) - len(missing)} of {len(locators)} published"
                         f" · {proof_counts['read_back']} read back"
                         + (f" · {proof_counts['sent']} sent, not yet read back" if proof_counts["sent"] else "")
                         + (f" · plus {ancestors} ancestor folder{'s' if ancestors != 1 else ''}" if ancestors else "")
                         + (f" · not published: {', '.join(missing[:8])}" if missing else "")
                         + rename_sentence(out.get("rename_counts")))
        except Exception as exc:
            short, full, _reason = _exc_text(exc)
            log.warning("curation %s: sub-resources failed for %s: %s", curation_id, slug, full[:2000])
            cur.set_step(curation_id, "sub_resources", "failed", short, more=full[:2000] if len(full) > 200 else "")

    # ── 4. what it is made of ──────────────────────────────────────────
    cur.set_step(curation_id, "components", "skipped",
                 "accepted components materialize at the moment they are accepted (Architecture verdicts); nothing to do at commit")
    return cur.finish(curation_id)
