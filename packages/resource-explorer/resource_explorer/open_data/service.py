"""Registration, search, directory run and import for open data (slice OD1).

Nothing here surveys, profiles or publishes, and nothing writes to Egeria.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from resource_explorer.open_data import match as match_mod
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.adapters import (
    Adapter, Price, adapter_for_api_kind, discover_adapters, price)
from resource_explorer.open_data.store import OpenDataStore, normalise_url, now, slugify

log = logging.getLogger(__name__)

DEFAULT_IMPORT_CAP = 25
IMPORT_CAP_SETTING = "open_data.import_cap"
DEFAULT_SEARCH_PAGES = 2
MAX_SEARCH_PAGES = 20


class OpenDataError(ValueError):
    """A request RE refuses, with the words to show. The web maps it to 422 (or 404 via `NotFound`)."""


class NotFound(OpenDataError):
    pass


def import_cap(registry) -> int:
    """25 by default; Admin changes it through `app_settings` (`open_data.import_cap`)."""
    try:
        raw = registry.get_setting(IMPORT_CAP_SETTING)
        return max(1, int(raw)) if raw else DEFAULT_IMPORT_CAP
    except Exception:
        return DEFAULT_IMPORT_CAP


# ── registration ─────────────────────────────────────────────────────────────

def _check_url(url: str) -> str:
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        raise OpenDataError("The base URL must start with http:// or https://")
    return u.rstrip("/")


def unique_slug(exists, base: str) -> str:
    slug, n = slugify(base), 2
    while exists(slug):
        slug, n = f"{slugify(base)}_{n}", n + 1
    return slug


def portal_secret_ref(slug: str) -> str:
    return f"{slug}::Open Data Portal Key"


def store_api_key(slug: str, api_key: str) -> str:
    """The key goes to the secrets store (`.omsecrets` projection), never a registry column. Returns the secret
    reference. Refuses when no store is configured: a key that is silently dropped is a portal that fails later."""
    from resource_explorer import omsecrets_store

    ref = portal_secret_ref(slug)
    if not omsecrets_store.write_credential(ref, "api_key", api_key):
        raise OpenDataError("No secrets store is configured on this server, so an API key cannot be held. "
                            "Register the portal without a key, or configure EGERIA_SECRETS_STORE_LOCAL_PATH.")
    return ref


def read_api_key(secret_ref: str) -> str:
    if not secret_ref:
        return ""
    from resource_explorer import omsecrets_store

    path = omsecrets_store.local_path()
    if not path:
        return ""
    entry = omsecrets_store._load(path)["secretsCollections"].get(secret_ref) or {}
    return str((entry.get("secrets") or {}).get("clearPassword") or "")


def register_portal(store: OpenDataStore, *, display_name: str, api_kind: str, base_url: str, api_key: str = "",
                    origin: str = "hand", registered_by: str = "", slug: str = "", transport=None) -> dict:
    cls = adapter_for_api_kind(api_kind, "portal")
    if cls is None:
        known = sorted(c.manifest.api_kind for c in discover_adapters().values() if c.manifest.role == "portal")
        raise OpenDataError(f"No adapter speaks {api_kind!r}; the adapters here speak {known}. "
                            f"A portal RE cannot search yet is kept through a directory import, not by hand.")
    typed, page_size = _check_url(base_url), None
    held = store.portal_by_url(typed)
    if held:
        raise OpenDataError(f"Already registered as '{held['display_name']}' ({held['slug']}).")
    base_url = typed
    probed = _probe(cls, typed, transport)
    if probed:
        base_url = _check_url(probed["feed_url"])
        page_size = probed["page_size"] if isinstance(probed.get("page_size"), int) else None
    portal = _insert_portal(store, cls, display_name=display_name, api_kind=cls.manifest.api_kind,
                            base_url=base_url, api_key=api_key, origin=origin,
                            registered_by=registered_by, slug=slug, page_size=page_size)
    _log_registration(store, portal, typed, probed)
    return portal


def _log_registration(store, portal: dict, typed: str, probed: dict | None) -> None:
    """The activity entry of a registration. The row's `base_url` is the feed the probe found, so what the person
    typed is kept here. Best effort: the portal is registered whether or not the entry could be written."""
    import uuid
    from datetime import datetime, timezone

    from resource_explorer.registry import ActivityEntry

    try:
        store.registry.write_activity(ActivityEntry(
            id=str(uuid.uuid4()), ts=datetime.now(timezone.utc).isoformat(), operation="register_portal",
            intent="discovery", entity_type="open_data_portal", entity_slug=portal["slug"],
            entity_name=portal["display_name"], entity_location=portal["base_url"],
            summary=f"registered {portal['display_name']} ({portal['api_kind']})"
                    + (f"; the feed is {portal['base_url']}" if typed != portal["base_url"] else ""),
            annotations=[{"typed_url": typed, "feed_url": portal["base_url"], "probe": probed or {},
                          "registered_by": portal.get("registered_by", "")}]))
    except Exception:  # noqa: BLE001 - registered; only the note of the typed URL is lost
        log.warning("Could not record the registration of portal %s", portal.get("slug"), exc_info=True)


def _probe(cls, base_url: str, transport) -> dict | None:
    """What the adapter learns from the URL at registration (1 to 3 read-only calls for DCAT-AP), or None when the
    adapter has nothing to probe. A URL it cannot read is refused here, in the adapter's one sentence."""
    if cls.probe.__func__ is Adapter.probe.__func__:
        return None
    from resource_explorer.open_data.transport import TransportError

    if transport is None:
        raise ValueError("registering a portal whose adapter probes needs a transport")
    try:
        return cls.probe(base_url, transport)
    except TransportError as exc:
        raise OpenDataError(str(exc)) from exc


def _insert_portal(store, cls, *, display_name, api_kind, base_url, api_key="", origin, registered_by, slug="",
                   page_size=None):
    if not display_name.strip():
        raise OpenDataError("A portal needs a name.")
    held = store.portal_by_url(base_url)
    if held:
        raise OpenDataError(f"Already registered as '{held['display_name']}' ({held['slug']}).")
    chosen = unique_slug(store.portal_exists, slug or display_name)
    ref = store_api_key(chosen, api_key) if api_key else ""
    m = cls.manifest if cls else None
    store.add_portal(slug=chosen, display_name=display_name.strip(), api_kind=api_kind, base_url=base_url,
                     secret_ref=ref, origin=origin, registered_by=registered_by, adapter=m.name if m else "",
                     calls_per_page=m.calls_per_page if m else None,
                     # a CKAN row keeps the manifest's size; a probed feed's own size wins
                     page_size=page_size if page_size else (m.page_size if m else None))
    return store.get_portal(chosen)


def register_directory(store: OpenDataStore, *, display_name: str, api_kind: str, base_url: str,
                       registered_by: str = "") -> dict:
    cls = adapter_for_api_kind(api_kind, "directory")
    if cls is None:
        known = sorted(c.manifest.api_kind for c in discover_adapters().values() if c.manifest.role == "directory")
        raise OpenDataError(f"No directory adapter speaks {api_kind!r}; the directory adapters here speak {known}.")
    if not display_name.strip():
        raise OpenDataError("A directory needs a name.")
    slug = unique_slug(store.get_directory, display_name)
    store.add_directory(slug=slug, display_name=display_name.strip(), api_kind=cls.manifest.api_kind,
                        base_url=_check_url(base_url), registered_by=registered_by)
    return store.get_directory(slug)


def portal_adapter(portal: dict, transport) -> Adapter:
    cls = adapter_for_api_kind(portal["api_kind"], "portal")
    if cls is None:
        raise OpenDataError(f"No adapter for {portal['api_kind']} yet.")
    adapter = cls(portal["base_url"], transport, read_api_key(portal.get("secret_ref", "")))
    if portal.get("page_size"):
        adapter.page_size = portal["page_size"]
    return adapter


def directory_adapter(directory: dict, transport) -> Adapter:
    cls = adapter_for_api_kind(directory["api_kind"], "directory")
    if cls is None:
        raise OpenDataError(f"No directory adapter for {directory['api_kind']} yet.")
    return cls(directory["base_url"], transport)


def portal_cost(portal: dict) -> dict:
    cls = adapter_for_api_kind(portal["api_kind"], "portal")
    if cls is None:
        return {}
    m = cls.manifest
    return {"calls_per_page": portal.get("calls_per_page") or m.calls_per_page,
            "page_size": portal.get("page_size") or m.page_size, "needs_key": m.needs_key,
            "cost_note": m.cost_note.format(pages="the pages you choose") if m.cost_note else ""}


# ── search ───────────────────────────────────────────────────────────────────

def search_price(portal: dict, pages: int) -> dict:
    cls = adapter_for_api_kind(portal["api_kind"], "portal")
    if cls is None:
        raise OpenDataError(f"No adapter for {portal['api_kind']} yet: this portal cannot be searched.")
    if not 1 <= pages <= MAX_SEARCH_PAGES:
        raise OpenDataError(f"A search is 1 to {MAX_SEARCH_PAGES} pages.")
    p = price(cls.manifest, pages)
    p = Price(pages=p.pages, calls_per_page=portal.get("calls_per_page") or p.calls_per_page,
              page_size=portal.get("page_size") or p.page_size)
    note = cls.manifest.cost_note.format(pages=f"{pages} page{'s' if pages != 1 else ''}") if cls.manifest.cost_note else ""
    return {"pages": p.pages, "calls_per_page": p.calls_per_page, "calls": p.calls, "page_size": p.page_size,
            "line": p.line + (f" · {note}" if note else ""), "rows_at_most": p.pages * p.page_size}


def _licence_verdict(row: dict, need: dict) -> str:
    """"keep" | "drop" | "unknown". `need` maps a flag to the value the request requires (e.g. share_alike False)."""
    unknown = False
    for flag, wanted in (need or {}).items():
        have = row["licence"].get(flag, NOT_REPORTED)
        if have == NOT_REPORTED:
            unknown = True
        elif have != wanted:
            return "drop"
    return "unknown" if unknown else "keep"


def run_search(store: OpenDataStore, portal_slug: str, *, terms: str, filters: dict | None = None,
               pages: int = DEFAULT_SEARCH_PAGES, transport) -> dict:
    """Runs the paged search. Candidate rows carry the portal; a row a licence need rules out is dropped and
    counted; a row whose licence flag is not reported is kept and marked `licence_check: could not check`."""
    portal = store.get_portal(portal_slug)
    if not portal:
        raise NotFound(f"No portal '{portal_slug}'.")
    priced = search_price(portal, pages)
    filters = dict(filters or {})
    need = filters.pop("licence", None) or {}
    adapter = portal_adapter(portal, transport)
    rows, dropped, total, calls = [], 0, NOT_REPORTED, 0
    for page in range(1, pages + 1):
        got = adapter.search(terms, filters, page)
        # an adapter that knows what it spent says so (a one-document feed costs one call over many pages)
        calls += got["calls"] if "calls" in got else adapter.manifest.calls_per_page
        total = got["total"]
        for row in got["rows"]:
            verdict = _licence_verdict(row, need)
            if verdict == "drop":
                dropped += 1
                continue
            row = dict(row, portal_slug=portal["slug"], portal_name=portal["display_name"],
                       licence_check="could not check" if verdict == "unknown" else "")
            if row["identifier_kind"] == "portal_id" and row["dataset_id"] != NOT_REPORTED:
                row["identifier"] = f"{portal['slug']}:{row['dataset_id']}"
            rows.append(row)
        # `read` (when the adapter filters locally) counts the entries the page held: a page that filters to nothing was read
        if not got.get("read", len(got["rows"])) or (isinstance(total, int) and page * got["page_size"] >= total):
            break
    return {"portal": portal["slug"], "price": priced, "calls_made": calls, "total": total, "rows": rows,
            "licence_dropped": dropped, "retrieved_at": now()}


# ── import ───────────────────────────────────────────────────────────────────

@dataclass
class Chosen:
    portal_slug: str
    dataset_id: str


def import_datasets(store: OpenDataStore, chosen: list[Chosen], *, work_list_name: str, transport,
                    egeria_lookup, imported_by: str = "", investigation: str = "",
                    work_list_slug: str = "", registry=None) -> dict:
    """Import the chosen rows as `open_data` registrations into a work list. Re-reads each dataset from its portal
    (`describe`, one call each) so the stored record is the publisher's, retrieved now, not whatever a client sent."""
    from resource_explorer.work_lists import WorkLists

    registry = registry or store.registry
    cap = import_cap(registry)
    if len(chosen) > cap:
        raise OpenDataError(f"{len(chosen)} rows chosen; one import takes at most {cap} (Admin can change the cap).")
    if not chosen:
        raise OpenDataError("Nothing chosen.")
    if not work_list_name.strip() and not work_list_slug:
        raise OpenDataError("The work list needs a name.")

    # 1. describe each, collapse equal identifiers to one dataset listing every portal
    datasets: dict[str, dict] = {}
    errors: list[dict] = []
    for c in dict.fromkeys((c.portal_slug, c.dataset_id) for c in chosen):
        portal_slug, dataset_id = c
        portal = store.get_portal(portal_slug)
        if not portal:
            errors.append({"portal": portal_slug, "dataset_id": dataset_id, "outcome": "refused",
                           "words": f"no portal '{portal_slug}'"})
            continue
        try:
            row = portal_adapter(portal, transport).describe(dataset_id)
        except Exception as exc:                       # the source did not answer: not imported, said so
            errors.append({"portal": portal_slug, "dataset_id": dataset_id, "outcome": "refused",
                           "words": f"could not read from {portal['display_name']}: {exc}"[:240]})
            continue
        retrieved = now()
        if row["identifier_kind"] == "portal_id":
            row["identifier"] = f"{portal_slug}:{row['dataset_id']}"
        entry = datasets.setdefault(row["identifier"], {"row": row, "portals": [], "retrieved_at": retrieved})
        if portal_slug not in entry["portals"]:
            entry["portals"].append(portal_slug)

    # 2. the read-first match, then DataCite for DOI rows
    from resource_explorer.open_data.datacite import DataCiteResolver

    resolver = DataCiteResolver(transport=transport).resolve
    outcomes, to_import = list(errors), []
    for identifier, entry in datasets.items():
        row = entry["row"]
        doi = row["doi"] if row["doi"] != NOT_REPORTED else None
        m = match_mod.match_identifier(store, identifier, egeria_lookup=egeria_lookup, doi=doi, resolver=resolver)
        base = {"identifier": identifier, "title": row["title"], "portals": entry["portals"],
                "check": m.as_dict(), "doi": row["doi"], "datacite": m.datacite_status or "no DOI reported"}
        if m.status == match_mod.ALREADY:
            outcomes.append({**base, "outcome": "already_in_catalog", "words": m.as_dict()["words"]})
            continue
        to_import.append((identifier, entry, m, base))

    # 3. the work list and the registrations
    wl_slug = work_list_slug
    if to_import:
        wls = WorkLists(registry)
        if wl_slug:
            if not wls.get(wl_slug):
                raise NotFound(f"No work list '{wl_slug}'.")
        else:
            wl_slug = wls.create(work_list_name.strip(), [], entity_type="open_data",
                                 investigation=investigation, created_by=imported_by)["slug"]
        for identifier, entry, m, base in to_import:
            row = entry["row"]
            slug = unique_slug(lambda s: store.get_dataset(s) is not None,
                               f"doi {identifier}" if row["identifier_kind"] == "doi" else identifier)
            store.add_dataset(slug=slug, row=row, portals=entry["portals"], retrieved_at=entry["retrieved_at"],
                              imported_by=imported_by, work_list_slug=wl_slug, datacite=m.datacite)
            wls.set_member(wl_slug, slug, entity_type="open_data", rationale=f"imported from {', '.join(entry['portals'])}")
            outcomes.append({**base, "outcome": "imported", "slug": slug,
                             "words": "imported" + (" · could not check Egeria" if m.status == match_mod.COULD_NOT_CHECK else "")})
    return {"work_list": wl_slug if to_import else "", "cap": cap,
            "imported": sum(1 for o in outcomes if o["outcome"] == "imported"),
            "already_in_catalog": sum(1 for o in outcomes if o["outcome"] == "already_in_catalog"),
            "refused": sum(1 for o in outcomes if o["outcome"] == "refused"), "rows": outcomes}


# ── directory ────────────────────────────────────────────────────────────────

def _directory_row_state(store: OpenDataStore, row: dict) -> dict:
    api_kind = row["api_kind"]
    held = store.portal_by_url(row["url"]) if row["url"] != NOT_REPORTED else None
    cls = adapter_for_api_kind(api_kind, "portal") if api_kind != NOT_REPORTED else None
    if held:
        status = f"already registered · {held['display_name']}"
    elif cls:
        status = f"adapter: {cls.manifest.name}"
    else:
        stated = "(api kind not reported)" if api_kind == NOT_REPORTED else api_kind
        status = f"no adapter for {stated} yet · registered for later"
    return {**row, "already_registered": bool(held), "adapter": cls.manifest.name if cls else "", "status": status}


def directory_price(store: OpenDataStore, directory_slug: str, pages: int) -> dict:
    """The price line of a directory run, from the adapter's manifest (the same `price` the run uses)."""
    d = store.get_directory(directory_slug)
    if not d:
        raise NotFound(f"No directory '{directory_slug}'.")
    cls = adapter_for_api_kind(d["api_kind"], "directory")
    if cls is None:
        raise OpenDataError(f"No directory adapter for {d['api_kind']} yet.")
    if not 1 <= pages <= MAX_SEARCH_PAGES:
        raise OpenDataError(f"A directory run is 1 to {MAX_SEARCH_PAGES} pages.")
    priced = price(cls.manifest, pages)
    return {"pages": pages, "calls": priced.calls, "line": priced.line}


def run_directory(store: OpenDataStore, directory_slug: str, *, pages: int = 1, terms: str = "", transport) -> dict:
    d = store.get_directory(directory_slug)
    if not d:
        raise NotFound(f"No directory '{directory_slug}'.")
    cls = adapter_for_api_kind(d["api_kind"], "directory")
    if cls is None:
        raise OpenDataError(f"No directory adapter for {d['api_kind']} yet.")
    if not 1 <= pages <= MAX_SEARCH_PAGES:
        raise OpenDataError(f"A directory run is 1 to {MAX_SEARCH_PAGES} pages.")
    priced = price(cls.manifest, pages)
    adapter = directory_adapter(d, transport)
    rows, total = [], NOT_REPORTED
    for page in range(1, pages + 1):
        got = adapter.list_portals(page)
        total = got["total"]
        rows.extend(got["rows"])
        if not got["rows"] or (isinstance(total, int) and page * got["page_size"] >= total):
            break
    needle = terms.strip().lower()
    if needle:
        rows = [r for r in rows if needle in " ".join(str(r[k]) for k in ("name", "country", "url", "api_kind")).lower()]
    store.touch_directory_run(d["slug"])
    return {"directory": d["slug"], "price": {"pages": pages, "calls": priced.calls, "line": priced.line},
            "total": total, "rows": [_directory_row_state(store, r) for r in rows], "retrieved_at": now()}


def import_portals(store: OpenDataStore, directory_slug: str, urls: list[str], *, transport,
                   registered_by: str = "", registry=None) -> dict:
    """Register chosen directory rows as portals (origin `directory:<slug>`). Re-reads the directory (one call)
    and takes the entries whose URL was chosen; an unknown api_kind is still registered, "for later"."""
    d = store.get_directory(directory_slug)
    if not d:
        raise NotFound(f"No directory '{directory_slug}'.")
    cap = import_cap(registry or store.registry)
    if len(urls) > cap:
        raise OpenDataError(f"{len(urls)} rows chosen; one import takes at most {cap} (Admin can change the cap).")
    adapter = directory_adapter(d, transport)
    wanted = {normalise_url(u) for u in urls}
    found, page = {}, 1
    while True:
        got = adapter.list_portals(page)
        for r in got["rows"]:
            if r["url"] != NOT_REPORTED and normalise_url(r["url"]) in wanted:
                found[normalise_url(r["url"])] = r
        if not got["rows"] or page * got["page_size"] >= got["total"]:
            break
        page += 1
    out = []
    for key in wanted:
        row = found.get(key)
        if not row:
            out.append({"url": key, "outcome": "refused", "words": "not in the directory's listing"})
            continue
        state = _directory_row_state(store, row)
        if state["already_registered"]:
            out.append({"url": row["url"], "name": row["name"], "outcome": "already_registered", "words": state["status"]})
            continue
        cls = adapter_for_api_kind(row["api_kind"], "portal") if row["api_kind"] != NOT_REPORTED else None
        name = row["name"] if row["name"] != NOT_REPORTED else row["url"]
        p = _insert_portal(store, cls, display_name=name,
                           api_kind=cls.manifest.api_kind if cls else row["api_kind"],
                           base_url=_check_url(row["url"]), origin=f"directory:{d['slug']}",
                           registered_by=registered_by)
        out.append({"url": row["url"], "name": name, "outcome": "registered", "slug": p["slug"],
                    "words": state["status"]})
    return {"directory": d["slug"], "rows": out,
            "registered": sum(1 for o in out if o["outcome"] == "registered")}


# ── the lens → terms ─────────────────────────────────────────────────────────

def terms_from_lens(lens: dict | None, fallback: str = "") -> dict:
    """Search terms and the modified-after filter from a data request's lens {subjects, period, places}. All
    editable; a lens that gives nothing falls back to `fallback` (the investigation's name) and says so."""
    lens = lens or {}
    words = [*(lens.get("subjects") or []), *(lens.get("places") or [])]
    terms = " ".join(w.strip() for w in words if isinstance(w, str) and w.strip())
    source = "lens"
    if not terms:
        terms, source = fallback.strip(), ("investigation name" if fallback.strip() else "")
    filters = {}
    period = str(lens.get("period") or "")
    if len(period) >= 4 and period[:4].isdigit():
        filters["modified_after"] = f"{period[:4]}-01-01"
    return {"terms": terms, "filters": filters, "source": source}


def default_work_list_name(investigation_request: str, terms: str) -> str:
    return (investigation_request or terms or "open data").strip()[:80]


# ── ExternalDataScout (OD2): re-read every dataset of a work list, one call each ──────────────────────────────

def scout_price(n: int) -> dict:
    """The scout's price line, before the press: N datasets, one `describe` call each."""
    return {"datasets": n, "calls": n, "line": f"{n} dataset{'s' if n != 1 else ''} × 1 call = {n} call{'s' if n != 1 else ''}"}


def scout_work_list(store: OpenDataStore, work_list_slug: str, *, transport) -> dict:
    """Re-read each dataset in the work list from its portal and refresh its stored columns (licence and flags,
    publisher, modified, formats, size, the publisher's record with its stated coverage). API only. A dataset whose
    portal did not answer is reported `could_not_scout` with the reason and keeps what it had: a failed read never
    blanks a column."""
    datasets = store.datasets_in_work_list(work_list_slug)
    if not datasets:
        raise NotFound(f"No open data in work list '{work_list_slug}'.")
    out = []
    adapters_by_portal: dict = {}        # one adapter per portal for the run: a one-document feed is read once
    for d in datasets:
        dataset_id = (d["record"] or {}).get("name") or (d["record"] or {}).get("id") or d["identifier"].split(":", 1)[-1]
        outcome = {"slug": d["slug"], "title": d["title"], "outcome": "could_not_scout", "words": ""}
        for portal_slug in d["portals"]:
            portal = store.get_portal(portal_slug)
            if not portal:
                outcome["words"] = f"portal '{portal_slug}' is no longer registered"
                continue
            try:
                if portal_slug not in adapters_by_portal:
                    adapters_by_portal[portal_slug] = portal_adapter(portal, transport)
                row = adapters_by_portal[portal_slug].describe(dataset_id)
            except Exception as exc:
                outcome["words"] = f"could not read from {portal['display_name']}: {exc}"[:240]
                continue
            changed = row["modified"] != d["modified"]
            store.update_scouted(d["slug"], row, now())
            outcome.update(outcome="scouted", words="scouted" + (" · changed on their side since the last look" if changed else " · no change on their side"),
                           changed=changed)
            break
        out.append(outcome)
    return {"work_list": work_list_slug, "price": scout_price(len(datasets)), "rows": out,
            "scouted": sum(1 for o in out if o["outcome"] == "scouted"),
            "could_not_scout": sum(1 for o in out if o["outcome"] != "scouted")}


def _states_nothing(request: dict | None) -> bool:
    return not any(v for k, v in (request or {}).items() if k != "licence") and not (request or {}).get("licence")


def fit_work_list(store: OpenDataStore, work_list_slug: str, request: dict | None, investigation: str = "") -> dict:
    """The fit row and winnow over the STORED columns (no network), with the Q-0011 overlap on each row. A request
    that states nothing, sent with an investigation, is that investigation's stored data request (when it has one)."""
    from resource_explorer.open_data import fit

    source = "the request as sent"
    if investigation and _states_nothing(request):
        stored = store.get_data_request(investigation)
        request, source = (stored, f"the data request on {investigation}") if stored else (request, "no data request stated")

    datasets = store.datasets_in_work_list(work_list_slug)
    if not datasets:
        raise NotFound(f"No open data in work list '{work_list_slug}'.")
    held = store.list_datasets()
    result = fit.winnow(datasets, fit.DataRequest.from_dict(request))
    by_slug = {d["slug"]: d for d in datasets}
    for r in result["rows"]:
        r["overlap"] = fit.overlap(by_slug[r["slug"]], held)
        r["retrieved_at"] = by_slug[r["slug"]]["retrieved_at"]
    return {"work_list": work_list_slug, "request_source": source, **result}
