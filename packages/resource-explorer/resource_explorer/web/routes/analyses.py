"""Analysis catalog API routes."""
from __future__ import annotations

from fastapi import APIRouter, Query

from resource_explorer.surveyors.analysis_catalog_reader import (
    get_analyses,
    get_egeria_merge_status,
    list_perspectives,
)

import logging
from concurrent.futures import as_completed

from resource_explorer import concurrency

log = logging.getLogger(__name__)
router = APIRouter()


from pydantic import BaseModel, Field
from fastapi import APIRouter, Query, HTTPException


class AnnotationTypeRegisterRequest(BaseModel):
    annotation_type: str = Field(..., alias="type")
    display_name: str
    description: str = ""
    properties: list[str] = []
    egeria_type: str = ""
    python_class: str = ""

    class Config:
        populate_by_name = True


class AnnotationTypeUpdateRequest(BaseModel):
    display_name: str
    description: str = ""
    properties: list[str] = []
    egeria_type: str = ""
    python_class: str = ""


@router.get("/annotation-types")
def list_annotation_types() -> list[dict]:
    """Return all known annotation types, their descriptions and properties."""
    from resource_explorer.registry import ProjectRegistry
    raw_list = ProjectRegistry().list_annotation_types()
    # Translate database column names to match the old frontend JSON interface:
    # "annotation_type" becomes "type"
    translated = []
    for item in raw_list:
        d = dict(item)
        d["type"] = d.pop("annotation_type")
        translated.append(d)
    return translated


@router.get("/annotation-types/{type_name}")
def get_annotation_type(type_name: str) -> dict:
    """Return details of a specific annotation type."""
    from resource_explorer.registry import ProjectRegistry
    item = ProjectRegistry().get_annotation_type(type_name)
    if not item:
        raise HTTPException(status_code=404, detail="Annotation type not found")
    d = dict(item)
    d["type"] = d.pop("annotation_type")
    return d


@router.post("/annotation-types")
def register_annotation_type(body: AnnotationTypeRegisterRequest) -> dict:
    """Register a new annotation type in the metadata catalog."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if registry.get_annotation_type(body.annotation_type):
        raise HTTPException(status_code=400, detail="Annotation type already registered")
    registry.register_annotation_type(
        annotation_type=body.annotation_type,
        display_name=body.display_name,
        description=body.description,
        properties=body.properties,
        egeria_type=body.egeria_type,
        python_class=body.python_class,
    )
    return {"status": "success"}


@router.put("/annotation-types/{type_name}")
def update_annotation_type(type_name: str, body: AnnotationTypeUpdateRequest) -> dict:
    """Update an existing annotation type details."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get_annotation_type(type_name):
        raise HTTPException(status_code=404, detail="Annotation type not found")
    registry.update_annotation_type(
        annotation_type=type_name,
        display_name=body.display_name,
        description=body.description,
        properties=body.properties,
        egeria_type=body.egeria_type,
        python_class=body.python_class,
    )
    return {"status": "success"}


@router.delete("/annotation-types/{type_name}")
def delete_annotation_type(type_name: str) -> dict:
    """Remove an annotation type from the catalog."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get_annotation_type(type_name):
        raise HTTPException(status_code=404, detail="Annotation type not found")
    registry.delete_annotation_type(type_name)
    return {"status": "success"}


@router.get("/annotation-types/{type_name}/usage")
def get_annotation_type_usage(type_name: str) -> dict:
    """Blast-radius number for Admin's delete/rename confirmation
    (SPEC-ADMIN-THE-FOUR-GAPS.md §4/§0): "an annotation type is referenced
    by recorded annotations — the confirmation must say how many, and say
    unknown rather than imply zero if that count is not cheap."

    `projects_published` is `ProjectRegistry.count_projects_published_annotation_type` —
    a real, cheap, indexed number, but a lower bound on distinct *projects*
    that have a local record of publishing this type, not a count of
    annotation *records* (RE keeps no durable local table of individual
    annotation instances — see that method's docstring). `exact` is always
    False here, on purpose: a caller (or a future test) that only checks
    `projects_published > 0` would otherwise be tempted to treat 0 as "safe
    to delete", which the docstring above explicitly says it is not."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get_annotation_type(type_name):
        raise HTTPException(status_code=404, detail="Annotation type not found")
    n = registry.count_projects_published_annotation_type(type_name)
    return {
        "type": type_name,
        "projects_published": n,
        "exact": False,
        "note": (
            f"Locally recorded as published for {n} project(s) — a lower bound, "
            "not a full annotation count. RE does not keep a durable per-annotation "
            "record of AnnotationType, so this cannot say the true number, and 0 "
            "here means 'no local publish record', not 'unused'."
        ),
    }


@router.get("/perspectives")
def list_perspectives_route(scope: str = "catalog") -> list[str]:
    """Distinct perspective values actually in the catalog — backs the UI's
    perspective selector so it's never a hardcoded, silently-stale list.
    `scope=all` returns the whole Egeria vocabulary instead: an AUDIENCE
    (the journal's "suggest to") is everyone who exists, not the subset
    something is tagged with today.
    NOTE: declared before /{resource_type} deliberately — Starlette matches
    routes in declaration order, so a literal path after a path-param catch-all
    at the same position would never be reached."""
    if scope == "all":
        from resource_explorer.surveyors.analysis_catalog_reader import EGERIA_PERSPECTIVES
        return list(EGERIA_PERSPECTIVES)
    return list_perspectives()


@router.get("/question-catalog")
def list_question_catalog(resource_type: str = "repo") -> list[dict]:
    """Full, unscoped Question catalog (docs/dr-egeria/resource_questions.csv,
    via question_catalog_reader.py) — every authored question with its
    funnel stage, perspectives, and answering info, no project/has_data
    computation. Backs the read-only Admin > Question Catalog browser.
    Contrast with GET /api/projects/{slug}/scouting-questions, which is
    project-scoped and adds a per-question has_data flag; this route is for
    browsing the catalog itself, not answering it for a specific resource.
    NOTE: declared before /{resource_type} deliberately — see
    list_perspectives_route's note above."""
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    return get_questions(resource_type)


class QuestionCatalogAddRequest(BaseModel):
    question: str
    stage: str = ""
    perspectives: list[str] = []
    purposes: list[str] = []
    why_important: str = Field("", alias="whyImportant")
    rationale: str = ""
    answering_mechanism: str = Field("", alias="answeringMechanism")

    class Config:
        populate_by_name = True


class QuestionCatalogRetireRequest(BaseModel):
    question: str


@router.post("/question-catalog/questions")
def add_question_catalog_entry(body: QuestionCatalogAddRequest) -> dict:
    """Append one new question to docs/dr-egeria/resource_questions.csv and
    regenerate configdata/question_catalog.yaml from it — the write half of
    the append-only decision recorded in question_catalog_writer.py and
    SPEC-ADMIN-THE-FOUR-GAPS.md §4.

    **Decision (project owner, 2026-09-20):** append-only — this route ADDS,
    it never edits. It 400s if `question` already exists in the CSV (active
    or retired), rather than silently updating that row, which is the
    backend half of "editing is not offered anywhere" — the UI not offering
    an edit form is not enough on its own; this route refuses one even if
    called directly."""
    from resource_explorer.surveyors.question_catalog_writer import (
        QuestionCatalogWriteError, add_question_with_id,
    )
    try:
        warnings, question_id = add_question_with_id(
            body.question,
            stage=body.stage,
            perspectives=body.perspectives,
            purposes=body.purposes,
            why_important=body.why_important,
            rationale=body.rationale,
            answering_mechanism=body.answering_mechanism,
        )
    except QuestionCatalogWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Purposes are suggestive (owner ruling 2026-10-13): an off-list one is added and named here.
    return {"status": "success", "warnings": warnings, "question_id": question_id}


@router.post("/question-catalog/questions/retire")
def retire_question_catalog_entry(body: QuestionCatalogRetireRequest) -> dict:
    """Flag an existing question retired — a status change, never a rewrite
    of its text, stage, or history. Retired questions stay in the catalog
    (and in the CSV) so a past survey answer's question is still readable
    exactly as it was asked; the UI shows them distinctly rather than
    hiding them (see next/admin/question_catalog.js's STATUS handling)."""
    from resource_explorer.surveyors.question_catalog_writer import (
        QuestionCatalogWriteError, QuestionNotFoundError, retire_question,
    )
    try:
        retire_question(body.question)
    except QuestionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except QuestionCatalogWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "success"}


@router.get("/{analysis_id}/cost")
def get_analysis_cost(analysis_id: str) -> dict:
    """The per-analysis `cost` object a DepthOffer entry carries
    (workflows/depth_offer.run_cost_as_dict), as its own route — the /next
    popover's per-analysis price-out. Resolved across every resource type
    the local catalog knows about (repo/database/filesystem), so an id is
    404 only when it's in none of them — known but never-run comes back 200
    with basis 'declared'/'unknown', which is not an error, just an
    unpriced answer.

    Two segments (`/{analysis_id}/cost`), so it can never collide with the
    single-segment `/{resource_type}` catch-all below regardless of
    declaration order — unlike /perspectives and /question-catalog above,
    which DO need to come first."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.depth_offer import find_cost_for_analysis

    cost = find_cost_for_analysis(ProjectRegistry(), analysis_id)
    if cost is None:
        raise HTTPException(status_code=404, detail=f"Analysis {analysis_id!r} not found")
    return cost


#: Most resources one bulk read will serve. Beyond this the request is
#: REFUSED with the cap named, rather than quietly truncated — a matrix that
#: silently drops rows past 200 is worse than one that says it cannot.
BULK_FACTS_MAX_SUBJECTS = 200


@router.get("/facts")
def bulk_resource_facts(
    slugs: str = Query(..., description="comma-separated resource slugs"),
    analysis_ids: str = Query("", description="comma-separated; omit for every analysis"),
    states_only: bool = Query(
        False,
        description="cheap projection: is there output and when was it measured, "
                    "without running the results readers",
    ),
    entity_type: str = Query(
        "repo",
        description="resource type of the slugs above ('repo' | 'database' | "
                     "'filesystem') — used to build FactLayer and to resolve the "
                     "default analysis_ids list for this resource type, same "
                     "convention as answer_question() above",
    ),
) -> dict:
    """What is known about SEVERAL resources, in one call.

    Exists for the comparison matrix, which is rows x questions and was making
    one request per row. Twelve rows is twelve round trips; four hundred is
    four hundred.

    **Scope `analysis_ids` if you can.** The cost here is dominated by a
    couple of analyses whose results readers are genuinely expensive —
    measured on `egeria_git`, `architecture_recovery` takes 47s and
    `architecture_diagram` 22s, while the other 32 together take under a
    second. Reading all 34 for one resource is 70s; reading the 5 that
    Scouting's questions actually use is 0.5s. A caller that knows which
    analyses it will display should say so.

    A GET rather than a POST because it is a read, and this app has a
    read-only mode that a POST would put it out of reach of. The cost of that
    choice is the URL length, hence the cap.
    """
    from resource_explorer.facts import FactLayer
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    subjects = [s.strip() for s in slugs.split(",") if s.strip()]
    # Deduped, order preserved: a repeated slug in the request should not mean
    # the work is done twice.
    subjects = list(dict.fromkeys(subjects))
    if not subjects:
        raise HTTPException(status_code=400, detail="slugs is required")
    if len(subjects) > BULK_FACTS_MAX_SUBJECTS:
        raise HTTPException(
            status_code=400,
            detail=f"{len(subjects)} resources requested; this endpoint serves at "
                   f"most {BULK_FACTS_MAX_SUBJECTS} at a time",
        )

    ids = [a.strip() for a in analysis_ids.split(",") if a.strip()]
    ids = ids or sorted(get_adapter(entity_type).analysis_results_map())

    if states_only:
        # THE CHEAP PATH. Two grouped queries for the whole matrix instead of
        # one results reader per (resource, analysis). The readers are what
        # cost — `architecture_recovery` at 47s on a large repo — and they
        # build a value a grid cell never displays.
        #
        # DELIBERATELY LESS INFORMATIVE, and it says so in the response. This
        # separates "there is output" from "there is none"; it does NOT
        # separate `measured` from `partial`, because that lives in the
        # results dict's own `_status` and only the reader produces it. A
        # caller must render the difference as not-yet-read. Claiming a state
        # nobody established is the exact failure this layer exists to stop,
        # and doing it for speed would be a poor trade.
        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry()
        summary = registry.analysis_result_summary(subjects, ids)
        layer = FactLayer(resource_type=entity_type)
        states: dict[str, dict] = {}
        for slug in subjects:
            runs = layer._last_run(slug)
            per: dict[str, dict] = {}
            for aid in ids:
                hit = summary.get((slug, aid)) or {}
                run = runs.get(aid) or {}
                per[aid] = {
                    "has_results": bool(hit.get("rows")),
                    "rows": hit.get("rows", 0),
                    # How current this cell is — the question a matrix of
                    # stored results has to answer before anyone trusts it.
                    "measured_at": hit.get("measured_at") or run.get("last_run_at", ""),
                    "last_run_at": run.get("last_run_at", ""),
                    # CERTAIN, not inferred: nothing stored and nothing run.
                    "certain_never_run": not hit.get("rows") and not run.get("last_run_at"),
                }
            states[slug] = per
        return {
            "states": {s: states[s] for s in subjects if s in states},
            "analysis_ids": ids,
            "requested": len(subjects),
            "returned": len(states),
            "projection": True,
            "projection_note": (
                "has_results and measured_at only — `measured` vs `partial` is "
                "not established here. Read the full facts for that."
            ),
        }

    out: dict[str, list] = {}
    failed: dict[str, str] = {}

    def read_one(slug: str):
        # A FactLayer per thread rather than one shared: it holds a registry
        # handle, and a DB connection is not something to share across
        # threads on the strength of it probably being fine.
        return slug, [f.as_dict() for f in FactLayer(resource_type=entity_type).facts(slug, ids)]

    # Fanned out across resources, because the cost here is dominated by a
    # couple of readers that are slow rather than by many that are quick —
    # `architecture_recovery` is 47s on a large repo and `architecture_diagram`
    # 22s, and Discovery's questions route to BOTH. Twelve resources serially
    # is a quarter of an hour; the work is DB- and IO-bound, so threads
    # actually buy something here.
    #
    # THE PROCESS'S SHARED POOL, not one of our own.
    #
    # `docs/process-model.md` §1.3 inventoried the ad-hoc pools this codebase
    # used to build and replaced them with one bounded, daemon-worker pool;
    # `test_no_module_builds_its_own_bridging_pool` keeps new ones out, by
    # AST rather than by substring, and caught this route the moment the
    # fan-out landed.
    #
    # The bound matters for the same reason it did when it was local: this
    # shares a Postgres with two apps and several sessions, and an unbounded
    # fan-out would trade one slow page for everyone else's connections. The
    # shared pool is already sized for that, so there is nothing to cap here.
    pool = concurrency.get_pool()
    futures = {pool.submit(read_one, slug): slug for slug in subjects}
    for fut in as_completed(futures):
        slug = futures[fut]
        try:
            slug, facts = fut.result()
            out[slug] = facts
        except Exception as exc:
            # One unreadable resource is one unreadable resource. Named, and
            # kept OUT of `subjects`, so a caller cannot mistake "we could not
            # read it" for "it has no results" — the distinction this whole
            # layer exists to preserve.
            log.warning("bulk facts failed for %s: %s", slug, exc)
            failed[slug] = f"{type(exc).__name__}: {exc}"

    return {
        # Requested order, not completion order — the grid renders rows in the
        # order it asked for them.
        "subjects": {s: out[s] for s in subjects if s in out},
        "analysis_ids": ids,
        "requested": len(subjects),
        "returned": len(out),
        "unreadable": failed,
    }


@router.get("/facts/{slug}")
def resource_facts(
    slug: str,
    analysis_ids: list[str] | None = Query(None),
    entity_type: str = Query(
        "repo",
        description="resource type of slug ('repo' | 'database' | 'filesystem') — "
                     "same convention as answer_question() above",
    ),
) -> dict:
    """What is known about this resource, and how well it is known.

    Facts arrive already judged: each carries a state from result_status's
    vocabulary rather than a bare value, so a caller cannot turn "never ran"
    into "none found". Omit analysis_ids for every analysis that has a results
    reader.
    """
    from resource_explorer.facts import FactLayer
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    ids = analysis_ids or sorted(get_adapter(entity_type).analysis_results_map())
    layer = FactLayer(resource_type=entity_type)
    return {"subject": slug, "facts": [f.as_dict() for f in layer.facts(slug, ids)]}


@router.get("/facts/{slug}/answer")
def answer_question(slug: str, question: str = Query(""),
                     entity_type: str = Query("repo"),
                     question_id: str = Query("", description="the question's stable id (Q-0001); "
                                              "accepted instead of the text, and wins when both are given")) -> dict:
    """An answer envelope for one catalogued question.

    The question is matched by its text, which is what the catalog keys on and
    what the Questions tab already renders. An envelope whose `answerable` is
    false carries `blocked_reason` and MUST NOT be rendered as a negative
    answer about the resource — for 30 of the 41 catalogued questions that is
    the correct outcome, and inventing an answer for them is precisely what
    this layer exists to prevent.

    `entity_type` used to be unaccepted here, so every lookup searched
    `get_questions()`'s "repo" default regardless of the resource actually
    asked about. A database/filesystem question with wording that happens to
    also exist under "repo" (several were authored that way — the wording
    pass reused repo's text) silently matched the REPO catalog's entry
    instead, answering from the repo's own analysis_ids/mechanism against a
    non-repo slug. A database/filesystem question with NO repo-side match at
    all 404'd outright — surfaced in `/next` as "This question is not in the
    catalog the answer layer reads." Both are the same bug: the entity type
    the checklist was built for never reached this lookup.

    That earlier fix (this docstring's paragraph above) covers only the
    CATALOG lookup. It shipped with `FactLayer()` still built with no
    `resource_type`, defaulting to "repo" — so even a correctly-matched
    database question was still answered out of the REPO's results map,
    which holds none of the database's analyses. Caught by the designer
    session reading this route end to end
    (`RULING-DB-QUESTION-CATALOG-CONSISTENCY.md` §0): every prior "fix" to
    the database question catalog (PR #226's entity-type threading, #229's
    eleven gap-to-analysis relabels) changed what the CATALOG claims without
    changing what this route actually READS, because nothing here ever told
    `FactLayer` which resource type's maps to use.
    """
    from resource_explorer.facts import FactLayer
    from resource_explorer.surveyors.question_catalog_reader import find_question_by_id, get_questions

    # The stable id is the durable key; the text still resolves, because URLs and saved state carry it.
    # Called in-process (no FastAPI), an omitted parameter is its `Query(...)` default object, not a string.
    question_id = question_id if isinstance(question_id, str) else ""
    question = question if isinstance(question, str) else ""
    if question_id.strip():
        match = find_question_by_id(question_id, entity_type)
        if not match:
            raise HTTPException(status_code=404, detail=f"Question id not in the catalog: {question_id!r}")
    else:
        if not question:
            raise HTTPException(status_code=422, detail="question or question_id is required")
        match = next((q for q in get_questions(entity_type) if q.get("question") == question), None)
        if not match:
            raise HTTPException(status_code=404, detail=f"Question not in the catalog: {question!r}")
    return FactLayer(resource_type=entity_type).answer(slug, match).as_dict()


@router.get("/{resource_type}")
def list_analyses(
    resource_type: str,
    intent: str | None = Query(None),
    perspective: str | None = Query(None),
) -> list[dict]:
    """Return available analyses for a resource type.

    resource_type: 'repo' | 'database' | 'filesystem'
    intent:       scouting | assessment | discovery | analysis | enrichment | understanding | curate
    perspective:  all | dba | data_scientist | steward | security
    """
    result = get_analyses(resource_type, intent=intent, perspective=perspective)
    _attach_trend_support(resource_type, result)
    return result


def _attach_trend_support(resource_type: str, entries: list[dict]) -> None:
    """Declare, per analysis, whether a series is kept for it.

    THE ANALYSIS KNOWS. `REPO_ANALYSIS_RESULTS_MAP` registers a trend reader,
    or registers `None` deliberately for a current-state classification whose
    history would be a flat, near-meaningless line. That is a property of the
    analysis, so it belongs in the analysis's descriptor — and then a client
    never has to ask a trend endpoint a question whose answer is "no", and
    never has to render an error to say "correctly, there is nothing".

    Same shape as putting the tier on a survey row instead of warning that the
    stage filter failed: the fact moves to where it is known, and the failure
    it used to produce stops existing.

    Three states, not two — `series`, `first measurement`, and `not tracked` —
    and only the first two involve a request at all.
    """
    if resource_type != "repo":
        # Only the repo adapter registers trend readers today. Unknown is not
        # the same as untracked, so these say nothing rather than guessing.
        return
    try:
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_RESULTS_MAP)
    except Exception:                                        # pragma: no cover
        return
    for entry in entries:
        record = REPO_ANALYSIS_RESULTS_MAP.get(entry.get("id"))
        if record is None:
            entry["trend"] = "unknown"          # not in the results map at all
            continue
        _, trend_reader = record
        entry["trend"] = "tracked" if trend_reader is not None else "not_tracked"


@router.get("/{resource_type}/egeria-status")
def get_analyses_egeria_status(resource_type: str) -> dict:
    """Outcome of the most recent live-Egeria merge attempt for this
    resource_type, so the UI can show a "live Egeria data unavailable"
    indicator only when a merge was actually attempted and failed — not for
    resource types that were never wired up to a Technology Type at all.
    Call GET /{resource_type} first; this reflects that call's outcome, it
    does not trigger a fresh one."""
    return {"resource_type": resource_type, "status": get_egeria_merge_status(resource_type)}
