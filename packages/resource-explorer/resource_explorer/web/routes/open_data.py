"""Open data routes (slice OD1): Data portals, Portal directories, search, directory run, import.

Registration follows `filesystems.py`: POST /, a check call, a summary model. Any signed-in user may register, as
for file systems today (open question for the owner, noted in the PR: admin-only?). Nothing here surveys, profiles
or publishes, and nothing writes to Egeria; the one Egeria call is the read-first identifier match inside import.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from resource_explorer.open_data import service
from resource_explorer.open_data.adapters import discover_adapters
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.store import OpenDataStore
from resource_explorer.open_data.transport import HttpTransport
from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)
router = APIRouter()


# Seams a test replaces: outbound HTTP, the Egeria read, and who is signed in.
def _transport():
    return HttpTransport()


def _egeria_lookup():
    from resource_explorer.open_data.match import EgeriaIdentifierLookup

    return EgeriaIdentifierLookup()


def _missing_list_words(slug: str) -> str:
    """What a work-list route says for a list with no open data: also what it says for one the caller may not see (TR-147)."""
    return f"No open data in work list '{slug}'."


def _guard_list(request: Request, slug: str, words: str | None = None) -> None:
    """TR-147: a work list linked to an investigation the caller cannot see is a 404 with the words THIS route uses for a list that is not
    there (`words`, else the generic ones), so it never confirms the list exists (401 with nobody signed in)."""
    from resource_explorer.web.visibility import visible_work_list_or_404

    visible_work_list_or_404(request, slug, not_found=words or _missing_list_words(slug))


def _guard_investigation(request: Request, slug: str) -> None:
    """TR-147: the investigation as the signed-in caller sees it, else 401, else 404 in the words of a slug that does not exist."""
    from resource_explorer.web.visibility import visible_investigation_or_404

    visible_investigation_or_404(request, slug)


def _signed_in_user(request: Request) -> str:
    from resource_explorer.auth import get_current_user

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to register portals and import open data.")
    return str(user.get("sub") or user.get("user_id") or "")


def _store() -> OpenDataStore:
    return OpenDataStore(ProjectRegistry())


def _http(exc: service.OpenDataError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, service.NotFound) else 422, detail=str(exc))


class PortalRegistration(BaseModel):
    display_name: str
    api_kind: str = "CKAN"
    base_url: str
    api_key: str = ""
    slug: str = ""


class DirectoryRegistration(BaseModel):
    display_name: str
    api_kind: str = "dataportals.org"
    base_url: str = ""


class SearchRequest(BaseModel):
    terms: str = ""
    filters: dict = Field(default_factory=dict)
    pages: int = service.DEFAULT_SEARCH_PAGES


class ChosenRow(BaseModel):
    portal_slug: str
    dataset_id: str


class ImportRequest(BaseModel):
    rows: list[ChosenRow]
    work_list_name: str = ""
    work_list_slug: str = ""
    investigation: str = ""
    terms: str = ""


class RepositoryImportRequest(BaseModel):
    repo_slug: str
    descriptor_path: str = ""
    work_list_name: str = ""
    work_list_slug: str = ""
    investigation: str = ""


class DirectoryRunRequest(BaseModel):
    pages: int = 1
    terms: str = ""


class DirectoryImportRequest(BaseModel):
    urls: list[str]


def _portal_summary(p: dict) -> dict:
    cost = service.portal_cost(p)
    return {**{k: p[k] for k in ("slug", "display_name", "api_kind", "base_url", "origin", "registered_by",
                                 "registered_at", "adapter")},
            "has_key": bool(p["secret_ref"]), "cost": cost,
            "adapter_available": bool(cost)}


@router.get("/adapters")
def list_adapters() -> list[dict]:
    """What is installed (built-ins plus the `trellis.open_data` entry points), by manifest; an entry point that
    failed to load is listed too, with `error` and no cost shape."""
    from resource_explorer.open_data.adapters import adapter_load_failures

    rows = [{"name": c.manifest.name, "api_kind": c.manifest.api_kind, "role": c.manifest.role,
             "calls_per_page": c.manifest.calls_per_page, "page_size": c.manifest.page_size,
             "needs_key": c.manifest.needs_key, "error": ""} for c in discover_adapters().values()]
    rows += [{"name": n, "api_kind": "", "role": "", "calls_per_page": None, "page_size": None, "needs_key": False,
              "error": e} for n, e in adapter_load_failures().items()]
    return rows


# ── portals ──────────────────────────────────────────────────────────────────

@router.get("/portals")
def list_portals() -> list[dict]:
    return [_portal_summary(p) for p in _store().list_portals()]


@router.post("/portals")
def register_portal(reg: PortalRegistration, request: Request) -> dict:
    who = _signed_in_user(request)
    store = _store()
    try:
        portal = service.register_portal(store, display_name=reg.display_name, api_kind=reg.api_kind,
                                         base_url=reg.base_url, api_key=reg.api_key, registered_by=who, slug=reg.slug,
                                         transport=_transport())
    except service.OpenDataError as exc:
        raise _http(exc) from exc
    return _portal_summary(portal)


@router.post("/portals/check")
def check_portal(reg: PortalRegistration) -> dict:
    """Can RE reach this portal? Read-only: one call (page 1), or the adapter's probe (1 to 3 calls). A warning for the form, never a gate."""
    from resource_explorer.open_data.adapters import Adapter, adapter_for_api_kind

    cls = adapter_for_api_kind(reg.api_kind, "portal")
    if cls is None:
        return {"ok": False, "words": f"no adapter for {reg.api_kind} yet"}
    try:
        if cls.probe.__func__ is not Adapter.probe.__func__:       # an adapter that finds its feed says where
            found = cls.probe(reg.base_url.rstrip("/"), _transport())
            return {"ok": True, "words": f"reachable · the feed is {found['feed_url']}", "total": found["total"]}
        got = cls(reg.base_url.rstrip("/"), _transport(), "").search("", {}, 1)
    except Exception as exc:
        return {"ok": False, "words": f"could not reach it · {exc}"[:240]}
    return {"ok": True, "words": "reachable", "total": got["total"]}


class SeedAdd(BaseModel):
    feed_url: str


@router.get("/portal-seeds")
def list_portal_seeds() -> dict:
    """The shipped suggestions not yet registered (matched on feed host + path, not on name). Offered, never applied."""
    from resource_explorer.open_data import seeds

    rows = seeds.unregistered(_store().list_portals())
    return {"rows": rows, "count": len(rows),
            "all_registered_words": "all suggested portals are registered" if not rows else ""}


@router.post("/portal-seeds/add")
def add_portal_seed(body: SeedAdd, request: Request) -> dict:
    """Register ONE suggested portal as the person pressing it, origin 'seed'. Reach is checked first; a refusal
    registers nothing and says why in one sentence."""
    from resource_explorer.open_data import seeds

    who = _signed_in_user(request)
    seed = seeds.seed_by_feed(body.feed_url)
    if seed is None:
        raise HTTPException(status_code=404, detail="That address is not in the suggested portals.")
    if not seed["can_add"]:
        raise HTTPException(status_code=422, detail=f"{seed['display_name']} cannot be added: {seed['words']}.")
    reg = PortalRegistration(display_name=seed["display_name"], api_kind=seed["api_kind"], base_url=seed["feed_url"])
    reach = check_portal(reg)
    if not reach["ok"]:
        raise HTTPException(status_code=422, detail=f"Not added: {reach['words']}")
    try:
        portal = service.register_portal(_store(), display_name=seed["display_name"], api_kind=seed["api_kind"],
                                         base_url=seed["feed_url"], origin="seed", registered_by=who,
                                         slug=service.host_slug(seed["feed_url"]), transport=_transport())
    except service.OpenDataError as exc:
        raise _http(exc) from exc
    return {**_portal_summary(portal), "reach": reach["words"]}


@router.delete("/portals/{slug}")
def remove_portal(slug: str, request: Request) -> dict:
    _signed_in_user(request)
    return {"removed": _store().remove_portal(slug)}


@router.post("/portals/{slug}/search")
def search_portal(slug: str, req: SearchRequest) -> dict:
    try:
        return service.run_search(_store(), slug, terms=req.terms, filters=req.filters, pages=req.pages,
                                  transport=_transport())
    except service.OpenDataError as exc:
        raise _http(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The portal did not answer: {exc}") from exc


@router.get("/portals/{slug}/price")
def search_price(slug: str, pages: int = service.DEFAULT_SEARCH_PAGES) -> dict:
    """The price line, before the press."""
    portal = _store().get_portal(slug)
    if not portal:
        raise HTTPException(status_code=404, detail=f"No portal '{slug}'.")
    try:
        return service.search_price(portal, pages)
    except service.OpenDataError as exc:
        raise _http(exc) from exc


@router.post("/portals/{slug}/import")
def import_rows(slug: str, req: ImportRequest, request: Request) -> dict:
    who = _signed_in_user(request)
    if req.investigation:
        _guard_investigation(request, req.investigation)
    if req.work_list_slug:
        _guard_list(request, req.work_list_slug)
    chosen = [service.Chosen(c.portal_slug or slug, c.dataset_id) for c in req.rows]
    name = req.work_list_name or service.default_work_list_name("", req.terms)
    try:
        return service.import_datasets(
            _store(), chosen, work_list_name=name, transport=_transport(), egeria_lookup=_egeria_lookup(),
            imported_by=who, investigation=req.investigation, work_list_slug=req.work_list_slug)
    except service.OpenDataError as exc:
        raise _http(exc) from exc


# ── datasets that live in a registered repository ──────────────────────────────

@router.get("/repositories/{repo_slug}/dataset")
def repository_dataset_state(repo_slug: str) -> dict:
    """What an import from this repository would do, and what it costs: read before the press."""
    try:
        return service.repository_import_state(_store(), repo_slug)
    except service.OpenDataError as exc:
        raise _http(exc) from exc


@router.post("/import-from-repository")
def import_from_repository(req: RepositoryImportRequest, request: Request) -> dict:
    """One `open_data` row from a registered repository's stored dataset descriptor. Writes nothing to Egeria."""
    who = _signed_in_user(request)
    if req.investigation:
        _guard_investigation(request, req.investigation)
    if req.work_list_slug:
        _guard_list(request, req.work_list_slug)
    try:
        return service.import_from_repository(
            _store(), repo_slug=req.repo_slug, descriptor_path=req.descriptor_path, transport=_transport(),
            imported_by=who, work_list_name=req.work_list_name, work_list_slug=req.work_list_slug,
            investigation=req.investigation)
    except service.OpenDataError as exc:
        raise _http(exc) from exc


@router.post("/lens-terms")
def lens_terms(lens: dict, request: Request, investigation: str = "") -> dict:
    """Search terms from a data request's lens {subjects, period, places}; editable by the caller. A lens that states
    nothing, sent with an investigation, is that investigation's stored data request (when it has one); otherwise the
    investigation's name is the fallback and the answer says so."""
    fallback = ""
    if investigation:
        _guard_investigation(request, investigation)
        stored = _store().get_data_request(investigation)
        if stored and not any(lens.get(k) for k in ("subjects", "period", "places")):
            lens = stored
        try:
            inv = ProjectRegistry().get_investigation(investigation)
            fallback = (inv.get("display_name", "") if isinstance(inv, dict) else getattr(inv, "display_name", "")) or ""
        except Exception:
            fallback = ""
    out = service.terms_from_lens(lens, fallback)
    if out["source"] == "lens" and investigation and lens.get("updated_at"):
        out["source"] = "data request"
    return out


class DataRequestBody(BaseModel):
    subjects: list[str] = []
    period: str = ""
    places: list[str] = []
    grain: str = ""
    licence: dict = {}
    formats: list[str] = []


@router.get("/investigations/{slug}/data-request")
def get_data_request(slug: str, request: Request) -> dict:
    """The investigation's data request. `stated` is false when none was saved (every old investigation) or the table
    is not applied yet; `stored` says which: false = the table does not exist yet, so a save would be refused.
    TR-147: only for an investigation the signed-in caller can see (401, else 404 in the words of a slug that does not exist)."""
    _guard_investigation(request, slug)
    store = _store()
    request = store.get_data_request(slug)
    return {"investigation": slug, "stated": request is not None, "stored": store.data_request_table_present(),
            "request": request}


@router.put("/investigations/{slug}/data-request")
def put_data_request(slug: str, body: DataRequestBody, request: Request) -> dict:
    who = _signed_in_user(request)
    _guard_investigation(request, slug)          # TR-147: a request is replaced only by someone who can see its investigation
    from resource_explorer.open_data.store import DataRequestTableMissing

    try:
        saved = _store().save_data_request(slug, body.model_dump(), who)
    except DataRequestTableMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"investigation": slug, "stated": True, "stored": True, "request": saved}


# ── datasets ─────────────────────────────────────────────────────────────────

@router.get("/datasets")
def list_datasets() -> list[dict]:
    return [{k: d[k] for k in ("slug", "identifier", "title", "publisher", "licence_id", "portals", "work_list_slug",
                               "imported_at")} for d in _store().list_datasets()]


@router.get("/datasets/{slug}")
def get_dataset(slug: str) -> dict:
    """The resource page: the verbatim record, the normalised fields with who stated them and when, and the
    absence word for every question a survey will answer (OD2)."""
    store = _store()
    d = store.get_dataset(slug)
    if not d:
        raise HTTPException(status_code=404, detail=f"No open data '{slug}'.")
    from resource_explorer.open_data.licences import licence_statement

    d["stated_by"] = d["portals"]
    d["survey"] = "Not yet run"
    d["licence_statement"] = licence_statement(d)       # identified / stated in words, not identified / not stated
    from resource_explorer.open_data import licence_usable

    d["licence_check"] = licence_usable.view(store.registry, store, d)   # the shared facts + THIS reader's fit; None until it has run
    return d


@router.get("/datasets/{slug}/questions")
def get_dataset_questions(slug: str) -> dict:
    """The open data questions asked of one dataset, each with its state from the record alone.

    The lead analyses are declared and not built, so a row is `no_reader` (◌ "no reader yet") unless the publisher's
    record already states something that answers part of it: the licence and its three flags, the modified date and
    the stated cadence. Those rows are `partial` (◐) and say whose record and when it was retrieved. Nothing here
    runs an analysis; "none stated" is `no_reader`, never an answer."""
    from resource_explorer.open_data import dataset_questions

    d = _store().get_dataset(slug)
    if not d:
        raise HTTPException(status_code=404, detail=f"No open data '{slug}'.")
    return dataset_questions.build(d)


@router.get("/datasets/{slug}/analyses/{analysis_id}/price")
def dataset_analysis_price(slug: str, analysis_id: str) -> dict:
    """The price line of one analysis on one dataset, shown BEFORE the press: the portal calls it makes. Not priced (never 0)
    when the analysis has no reader yet. Registry-only: it calls nothing."""
    from resource_explorer.open_data import analyses

    dataset = _store().get_dataset(slug)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"No open data '{slug}'.")
    return analyses.price(analysis_id, dataset)


def _stale_cutoff() -> str:
    """A claimed or running row with no sign of life since this does not block a new run: a worker that died must not lock a dataset out."""
    from datetime import datetime, timedelta, timezone

    from resource_explorer.run_queue import HEARTBEAT_INTERVAL_SECONDS, STALE_AFTER_INTERVALS

    return (datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_INTERVAL_SECONDS * STALE_AFTER_INTERVALS)).isoformat()


def _enqueue_dataset_run(registry, dataset: dict, analysis_id: str, pressed_on: str = "") -> dict:
    """Queue ONE analysis run on ONE dataset, unless the same (dataset, analysis) is already queued or running (W-B4-1: a double click
    or two people pressing must not queue a second full set of runs, each with its own downloads). The check and the insert are one
    atomic step (`enqueue_run_once`). Its activity row opens first, so a poll always has something; a refused press opens none.
    Returns {slug, title, analysis_id, activity_id, run_id, already_queued}; for an already-queued one the ids are the EXISTING run's."""
    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.run_queue import requested_by

    slug = dataset["slug"]
    target = {"slug": slug, "analysis_id": analysis_id, **({"pressed_on": pressed_on[:40]} if pressed_on else {})}

    def _same(existing: dict) -> bool:
        return existing.get("slug") == slug and existing.get("analysis_id") == analysis_id

    def _open_activity() -> str:
        return log_analysis_run(registry, "open_data", slug, str(dataset.get("title") or slug), "running",
                                f"Running '{analysis_id}' on {slug}…", analysis_id, published=None)

    out = registry.enqueue_run_once("open_data_analysis_run", target, lock_key=f"open_data_analysis_run|{slug}|{analysis_id}",
                                    same=_same, stale_before=_stale_cutoff(), before_insert=_open_activity,
                                    requested_by=requested_by())
    for row in out.get("stale") or []:
        log.warning("open_data_analysis_run %s on %s/%s is %s with no sign of life: not blocking a new run", row.get("id"), slug,
                    analysis_id, row.get("state"))
    if not out["created"]:
        existing = out["existing"]
        return {"slug": slug, "title": dataset.get("title"), "analysis_id": analysis_id, "activity_id": existing.get("result_ref"),
                "run_id": existing["id"], "already_queued": True}
    log.info("enqueued open_data_analysis_run %s for %s/%s (activity %s)", out["run_id"], slug, analysis_id, out["result_ref"])
    return {"slug": slug, "title": dataset.get("title"), "analysis_id": analysis_id, "activity_id": out["result_ref"],
            "run_id": out["run_id"], "already_queued": False}


@router.post("/datasets/{slug}/analyses/{analysis_id}/run")
def run_dataset_analysis(slug: str, analysis_id: str, request: Request, pressed_on: str = "") -> dict:
    """Run ONE analysis on ONE dataset (TR-54 B1): validated here (an unknown analysis, or one with no reader yet, is a 400 in
    words), then queued, as every analysis is (never inline in a request). The answer is
    `{status: "started", activity_id, run_id, price}`: the activity row exists before the worker starts, so the checklist's
    pollActivity always has something to poll. The result is stored per (dataset, analysis); nothing is published, nothing
    is written to Egeria. The run executes as the person who pressed."""
    from resource_explorer.open_data import analyses

    _signed_in_user(request)
    registry = ProjectRegistry()
    dataset = OpenDataStore(registry).get_dataset(slug)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"No open data '{slug}'.")
    why = analyses.refusal(analysis_id)
    if why:
        raise HTTPException(status_code=400, detail=why)
    queued = _enqueue_dataset_run(registry, dataset, analysis_id, pressed_on)
    price = analyses.price(analysis_id, dataset)
    if queued["already_queued"]:      # nothing new was queued, so nothing new is priced
        price = {**price, "calls": 0, "line": f"already queued · no new portal calls ({price['line']} was the price of the run in flight)"}
    return {"status": "already queued" if queued["already_queued"] else "started", "slug": slug, "analysis_id": analysis_id,
            "activity_id": queued["activity_id"], "run_id": queued["run_id"], "price": price}


def _press_work_list(slug: str, analysis_id: str, request: Request) -> dict:
    """A work-list press (scout, profile, refresh), TR-54 B4: ONE queued run per dataset, none done in the request. The answer says
    what was queued and what it costs; each run's result (and which portal failed, and why) is `GET /work-lists/{slug}/runs`."""
    from resource_explorer.open_data import analyses

    _signed_in_user(request)
    _guard_list(request, slug)      # TR-147: a list linked to an investigation the caller cannot see is a 404 in a missing list's words
    registry = _store().registry
    datasets = _store().datasets_in_work_list(slug)
    if not datasets:
        raise HTTPException(status_code=404, detail=f"No open data in work list '{slug}'.")
    why = analyses.refusal(analysis_id)
    if why:
        raise HTTPException(status_code=400, detail=why)
    runs = [_enqueue_dataset_run(registry, d, analysis_id, "Work list") for d in datasets]
    fresh = [d for d, r in zip(datasets, runs) if not r["already_queued"]]       # priced: only what was newly queued
    already = len(runs) - len(fresh)
    calls = sum(int(analyses.runner_for(analysis_id).calls(d)) for d in fresh)
    upper = " (at most: each file is read only up to its cap)" if analysis_id == "external_data_profile" and fresh else ""
    n = len(fresh)
    say = (service.scout_price(n)["line"] if analysis_id == "external_data_scout" and n else
           f"{n} dataset{'s' if n != 1 else ''} · {calls} portal call{'s' if calls != 1 else ''}{upper}")
    return {"work_list": slug, "analysis_id": analysis_id, "queued": n, "already_queued": already, "runs": runs,
            "price": {"datasets": n, "calls": calls,
                      "line": say + (f" · {already} already queued, not priced again" if already else "")}}


# ── directories ──────────────────────────────────────────────────────────────

class FitRequest(BaseModel):
    request: dict = {}
    investigation: str = ""


@router.get("/work-lists/{slug}/scout-price")
def scout_price(slug: str, request: Request) -> dict:
    """The scout's price line for a work list of open data, before the press."""
    _guard_list(request, slug)
    n = len(_store().datasets_in_work_list(slug))
    if not n:
        raise HTTPException(status_code=404, detail=f"No open data in work list '{slug}'.")
    return service.scout_price(n)


@router.post("/work-lists/{slug}/scout")
def scout(slug: str, request: Request) -> dict:
    """ExternalDataScout: queue one run per dataset to re-read it from its portal (one call each, priced in the answer)."""
    return _press_work_list(slug, "external_data_scout", request)


@router.get("/work-lists/{slug}/profile")
def profile_view(slug: str, request: Request) -> dict:
    """The price line of a profile run and the newest stored profile of each dataset (None = never run)."""
    from resource_explorer.open_data import profile

    _guard_list(request, slug)
    if not _store().datasets_in_work_list(slug):
        raise HTTPException(status_code=404, detail=f"No open data in work list '{slug}'.")
    return profile.view(_store(), slug)


@router.post("/work-lists/{slug}/profile")
def profile_run(slug: str, request: Request) -> dict:
    """ExternalDataProfile: queue one run per dataset to download and profile its distributions, each capped (priced in the answer)."""
    return _press_work_list(slug, "external_data_profile", request)


@router.get("/work-lists/{slug}/refresh-price")
def refresh_price(slug: str, request: Request) -> dict:
    """The price line of an ExternalDataRefresh, before the press."""
    from resource_explorer.open_data import refresh

    _guard_list(request, slug)
    n = len(_store().datasets_in_work_list(slug))
    if not n:
        raise HTTPException(status_code=404, detail=f"No open data in work list '{slug}'.")
    return refresh.refresh_price(n)


@router.post("/work-lists/{slug}/refresh")
def refresh_run(slug: str, request: Request) -> dict:
    """ExternalDataRefresh: queue one run per dataset to look at it on its portal again and say what changed on their side."""
    return _press_work_list(slug, "external_data_refresh", request)


@router.get("/work-lists/{slug}/runs")
def work_list_runs(slug: str, analysis_id: str, request: Request) -> dict:
    """The newest run of `analysis_id` on each dataset of the work list: its status and words, so a press where one portal did not
    answer says which dataset failed and why (the others are done). TR-147: only for a list the caller can see."""
    from resource_explorer.open_data import analyses, queued

    _guard_list(request, slug)
    store = _store()
    if not store.datasets_in_work_list(slug):
        raise HTTPException(status_code=404, detail=f"No open data in work list '{slug}'.")
    if analysis_id not in analyses._declared():
        raise HTTPException(status_code=400, detail=analyses.refusal(analysis_id))
    return queued.work_list_runs(store.registry, store, slug, analysis_id)


@router.get("/datasets/{slug}/publish")
def publish_state(slug: str) -> dict:
    """The newest publish proof of one dataset (None = never published) and the price line."""
    from resource_explorer.open_data import publish

    if not _store().get_dataset(slug):
        raise HTTPException(status_code=404, detail=f"No open data '{slug}'.")
    return {"price": publish.publish_price(1), "proof": publish.latest_proof(_store().registry, slug),
            "words": "" if publish.latest_proof(_store().registry, slug) else "Not published to Egeria yet"}


@router.post("/datasets/{slug}/publish")
def publish_run(slug: str, request: Request) -> dict:
    """Publish one dataset to Egeria as the signed-in person, then read it back by GUID."""
    from resource_explorer.open_data import publish

    _signed_in_user(request)
    ds = _store().get_dataset(slug)
    if not ds:
        raise HTTPException(status_code=404, detail=f"No open data '{slug}'.")
    try:
        return publish.publish_dataset(_store(), ds, _egeria_port())
    except publish.PublishRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _egeria_port():
    from resource_explorer.open_data import publish

    return publish.PyegeriaPort()


@router.post("/work-lists/{slug}/fit")
def fit(slug: str, req: FitRequest, request: Request) -> dict:
    """The fit row and winnow over the stored columns. No outbound call."""
    _signed_in_user(request)
    _guard_list(request, slug)
    if req.investigation:
        _guard_investigation(request, req.investigation)         # TR-147: the stored request of an investigation the caller cannot see
    try:
        return service.fit_work_list(_store(), slug, req.request, req.investigation)
    except service.OpenDataError as exc:
        raise _http(exc) from exc


@router.get("/directories")
def list_directories() -> list[dict]:
    return _store().list_directories()


@router.post("/directories")
def register_directory(reg: DirectoryRegistration, request: Request) -> dict:
    who = _signed_in_user(request)
    try:
        return service.register_directory(
            _store(), display_name=reg.display_name, api_kind=reg.api_kind,
            base_url=reg.base_url or ("https://www.re3data.org/api/v1/repositories" if reg.api_kind.lower() == "re3data" else "https://dataportals.org/api/data.json"), registered_by=who)
    except service.OpenDataError as exc:
        raise _http(exc) from exc


@router.get("/directories/{slug}/price")
def directory_price(slug: str, pages: int = 8) -> dict:
    """A directory run's price line, before the press (one source for every price line)."""
    try:
        return service.directory_price(_store(), slug, pages)
    except service.OpenDataError as exc:
        raise _http(exc) from exc


@router.post("/directories/{slug}/run")
def run_directory(slug: str, req: DirectoryRunRequest) -> dict:
    try:
        return service.run_directory(_store(), slug, pages=req.pages, terms=req.terms, transport=_transport())
    except service.OpenDataError as exc:
        raise _http(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The directory did not answer: {exc}") from exc


@router.post("/directories/{slug}/import")
def import_directory_rows(slug: str, req: DirectoryImportRequest, request: Request) -> dict:
    who = _signed_in_user(request)
    try:
        return service.import_portals(_store(), slug, req.urls, transport=_transport(), registered_by=who)
    except service.OpenDataError as exc:
        raise _http(exc) from exc
