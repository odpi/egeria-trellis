"""ExternalDataProfile (OD3): the profile of the shortlist's distributions, through the Files step 5 path.

For each dataset of a work list of open data: list its distributions (one call), download the first three tabular
ones (each capped), profile them with Files step 5's `profile_file`, match the sampled columns against Egeria's data
classes (`data_class_match`, as the file match does), read the documentation distributions (data dictionary, codebook,
CITATION, LICENSE, methodology) as evidence that is "stated by <file>" and only ever a proposal, collect the dataset's
provenance, and fingerprint the columns against open data already profiled.

What a number means here, and what it does not:
  * A distribution over the cap is NOT profiled: "not profiled · over the cap". It is never read in part and never 0.
  * Every column row carries `sample_ratio` and the rows read; a whole file read is ratio 1.0, said as "read whole".
  * A field the source does not state is `not reported by this source`; a file RE did not read says why.
  * The fingerprint is compared with other profiled open data only. Registered databases and file systems are not
    compared yet, and the words say so.

Results are kept as one `step_runs` row per dataset (entity type `open_data`): no table is added by this slice.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Callable

from resource_explorer.open_data.absence import NOT_REPORTED, NOT_REPORTED_WORDS
from resource_explorer.open_data.store import OpenDataStore, now

STEP_KEY = "external_data_profile"
ENTITY_TYPE = "open_data"
SOURCE = "resource-explorer"

MAX_DATA_DISTRIBUTIONS = 3
MAX_DOC_DISTRIBUTIONS = 2
DOC_CAP_BYTES = 1024 * 1024
PASSAGES_PER_DOC = 6
PASSAGE_CHARS = 280
MAX_MATCHES_KEPT = 60
CLAIM_STALE_SECONDS = 900
ALREADY_RUNNING_WORDS = "a profile of {slug} is already running"

OVER_CAP_WORDS = "not profiled · over the cap"
PROFILED, OVER_CAP, NOT_READ_FORMAT, NO_URL, DOWNLOAD_FAILED, UNREADABLE, NOT_CHOSEN = (
    "profiled", "over_cap", "format_not_read", "no_url", "download_failed", "unreadable", "not_chosen")

_DOC_WORDS = re.compile(r"data[\s_-]*dictionary|codebook|readme|citation|licen[cs]e|methodolog|metadata|schema", re.I)
_DOC_EXTENSIONS = {"txt", "md", "markdown", "rst", "text"}
_FOCUS_WORDS = ("dictionary", "codebook", "citation", "licence", "license", "methodology", "readme")


class NothingToProfile(RuntimeError):
    pass


class AlreadyRunning(RuntimeError):
    pass


def cap_bytes() -> int:
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    return fp.PROFILE_LIMIT_BYTES


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def profile_price(n_datasets: int) -> dict:
    """The price line before the press: per dataset one call to list the distributions, up to three data downloads
    and up to two documentation downloads, each data file capped. An upper bound, never a promise."""
    per = 1 + MAX_DATA_DISTRIBUTIONS + MAX_DOC_DISTRIBUTIONS
    n = max(0, int(n_datasets))
    plural = "" if n == 1 else "s"
    return {"datasets": n, "calls_at_most": n * per, "cap_bytes": cap_bytes(),
            "line": (f"{n} dataset{plural} × up to {per} calls (1 listing, {MAX_DATA_DISTRIBUTIONS} data files, "
                     f"{MAX_DOC_DISTRIBUTIONS} documents) = up to {n * per} calls · each data file capped at "
                     f"{_mb(cap_bytes())}, a larger one is not read")}


# ── distributions ───────────────────────────────────────────────────────────────────────────────────────────────

def _ext(dist: dict) -> str:
    fmt = dist.get("format")
    if isinstance(fmt, str) and fmt != NOT_REPORTED and fmt.strip():
        return fmt.strip().lstrip(".").lower()
    url = dist.get("url")
    if isinstance(url, str) and url != NOT_REPORTED:
        name = PurePosixPath(url.split("?")[0]).name
        return name.rpartition(".")[2].lower() if "." in name else ""
    return ""


def _label(dist: dict) -> str:
    for key in ("name", "id"):
        v = dist.get(key)
        if isinstance(v, str) and v != NOT_REPORTED and v.strip():
            return v.strip()
    url = dist.get("url")
    return PurePosixPath(url.split("?")[0]).name if isinstance(url, str) and url != NOT_REPORTED else "distribution"


def _is_document(dist: dict) -> bool:
    text = f"{_label(dist)} {dist.get('url') if isinstance(dist.get('url'), str) else ''}"
    return bool(_DOC_WORDS.search(text)) and (_ext(dist) in _DOC_EXTENSIONS | {"pdf", "html", "htm", "docx", ""})


def split_distributions(dists: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(data, documents, other). Data = a format Files step 5 reads; documents = named like one and not tabular."""
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    readable = fp.readable_extensions()
    data, docs, other = [], [], []
    for d in dists:
        if _is_document(d) and _ext(d) not in readable:
            docs.append(d)
        elif _ext(d) in readable:
            data.append(d)
        else:
            other.append(d)
    return data, docs, other


def _stated_size(dist: dict) -> int | None:
    v = dist.get("size")
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _safe_name(label: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._") or "distribution"
    return base if base.lower().endswith("." + ext) else f"{base}.{ext}"


# ── one data distribution ───────────────────────────────────────────────────────────────────────────────────────

def fingerprint_of(column_names: list[str]) -> str:
    """The structural fingerprint of a table: a hash of its normalised, sorted column names. Pure."""
    norm = sorted({re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_") for c in column_names if str(c).strip()})
    return hashlib.sha1("\n".join(norm).encode()).hexdigest()[:16] if norm else ""


def _normalised(names) -> set[str]:
    return {re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_") for c in names if str(c).strip()}


def profile_distribution(dist: dict, *, transport, catalog, workdir: Path, cap: int) -> dict:
    """One data distribution. Never raises: a failure is a row that says what happened."""
    from resource_explorer.surveyors.filesystem import file_class_match as fcm
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    ext, label, url = _ext(dist), _label(dist), dist.get("url")
    row = {"name": label, "format": ext or NOT_REPORTED, "url": url if isinstance(url, str) else NOT_REPORTED,
           "stated_size": _stated_size(dist) if _stated_size(dist) is not None else NOT_REPORTED,
           "retrieved_at": now()}
    if not isinstance(url, str) or url == NOT_REPORTED or not url.strip():
        return {**row, "state": NO_URL, "words": f"not profiled · url {NOT_REPORTED_WORDS}"}
    stated = _stated_size(dist)
    if stated is not None and stated > cap:
        return {**row, "state": OVER_CAP, "words": f"{OVER_CAP_WORDS} ({_mb(stated)} stated, cap {_mb(cap)})"}
    try:
        data, size, truncated = transport.get_bytes(url, cap)
    except Exception as exc:                                   # the source did not answer: said, never "found nothing"
        return {**row, "state": DOWNLOAD_FAILED, "words": f"not profiled · could not download: {exc}"[:240]}
    if truncated:
        return {**row, "state": OVER_CAP, "words": f"{OVER_CAP_WORDS} (longer than the {_mb(cap)} cap)"}
    path = workdir / _safe_name(label, ext)
    path.write_bytes(data)
    out = fp.profile_file(path, ext, limit=cap)
    state = out.get("state")
    if state not in fp.PROFILED_STATES:
        words = (f"not profiled · format {ext} is not read here" if state == fp.NOT_APPLICABLE
                 else f"not profiled · {state}" + (f": {out['error']}" if out.get("error") else ""))
        return {**row, "state": NOT_READ_FORMAT if state == fp.NOT_APPLICABLE else UNREADABLE, "words": words[:240],
                "bytes_read": len(data)}
    cols = out["columns"]
    ratio = out.get("sample_ratio", 1.0)
    rows_read = cols[0]["record_ct"] if cols else 0
    values, sampled_rows, why = fcm.sample_values(path, ext)
    matches = _class_matches(label, cols, values, sampled_rows, why, catalog)
    return {**row, "state": PROFILED, "bytes_read": len(data), "size_bytes": len(data), "sample_ratio": ratio,
            "rows": rows_read, "columns": [_column(c) for c in cols],
            "fingerprint": fingerprint_of([c["column_name"] for c in cols]),
            "matches": matches[:MAX_MATCHES_KEPT], "matches_not_kept": max(0, len(matches) - MAX_MATCHES_KEPT),
            "values_not_sampled": why if values is None else "",
            "words": (f"profiled · {rows_read:,} rows · sample ratio {ratio:g}"
                      + (" (read whole)" if ratio == 1.0 else ""))}


def _column(c: dict) -> dict:
    return {k: c.get(k) for k in ("column_name", "ordinal", "declared_type", "inferred_general_type", "record_ct",
                                  "value_ct", "null_ct", "distinct_ct", "min_text", "max_text", "sample_ratio", "state")}


def _class_matches(label: str, cols: list[dict], values, rows_read: int, why: str, catalog) -> list[dict]:
    from resource_explorer.surveyors.database.column_matching import (MATCH_NO_CANDIDATES, MATCH_NOT_APPLICABLE,
                                                                      MATCH_NOT_SAMPLED, data_class_match)
    from resource_explorer.surveyors.database.sampling import HEAD, SampleProvenance
    from resource_explorer.surveyors.filesystem import file_class_match as fcm

    sampled = values is not None
    prov = (SampleProvenance(strategy=HEAD, sample_rows=rows_read, total_rows=None, sampled_at=now(), truncated=False)
            if sampled else SampleProvenance(strategy="catalog_stats_only", reason_not_sampled=why))
    out = []
    for c in cols:
        m = data_class_match(
            schema_name="", table_name=label, column_name=c["column_name"],
            column_type=c.get("declared_type") or c.get("inferred_general_type") or "",
            known_classes=catalog.data_classes if catalog.available else [],
            sampled_values=(values or {}).get(c["column_name"]) if sampled else None, provenance=prov)
        if not catalog.available and m.verdict not in (MATCH_NOT_SAMPLED, MATCH_NOT_APPLICABLE):
            m.verdict, m.confidence, m.matched_guid = MATCH_NO_CANDIDATES, 0, ""
            m.matched_qualified_name = m.matched_display_name = ""
            m.not_established_reason = catalog.unavailable_reason or "Egeria's data classes could not be read"
        d = fcm._display(m, label)
        d["publisher_stated"] = False         # what the publisher says about a column is kept apart, in the documents
        out.append(d)
    out.sort(key=fcm._rank)
    return out


# ── documentation distributions ─────────────────────────────────────────────────────────────────────────────────

def read_document(dist: dict, *, transport) -> dict:
    """A documentation distribution as evidence: passages, each "stated by <file § section>", a proposal until a
    person confirms it. A format RE cannot read as text says so; it is not skipped silently."""
    from resource_explorer.surveyors.filesystem.narrative_docs import split_sections, stated_by_of

    label, ext, url = _label(dist), _ext(dist), dist.get("url")
    row = {"name": label, "format": ext or NOT_REPORTED, "retrieved_at": now()}
    if ext not in _DOC_EXTENSIONS and ext != "":
        return {**row, "state": NOT_READ_FORMAT, "words": f"not read · a .{ext} document is not read as text here"}
    if not isinstance(url, str) or url == NOT_REPORTED:
        return {**row, "state": NO_URL, "words": f"not read · url {NOT_REPORTED_WORDS}"}
    try:
        data, _size, truncated = transport.get_bytes(url, DOC_CAP_BYTES)
    except Exception as exc:
        return {**row, "state": DOWNLOAD_FAILED, "words": f"not read · could not download: {exc}"[:240]}
    if truncated:
        return {**row, "state": OVER_CAP, "words": f"not read · over the {_mb(DOC_CAP_BYTES)} document cap"}
    sections = split_sections(data.decode("utf-8", errors="replace"), label)
    passages = [{"stated_by": stated_by_of(label, heading), "text": " ".join(body.split())[:PASSAGE_CHARS],
                 "status": "proposal"}
                for heading, body in sections if body.strip()][:PASSAGES_PER_DOC]
    return {**row, "state": "read", "passages": passages,
            "words": (f"read · {len(passages)} passage{'s' if len(passages) != 1 else ''}, each stated by "
                      f"{label} · a proposal, never a fact") if passages else f"read · {label} has no text"}


# ── provenance ──────────────────────────────────────────────────────────────────────────────────────────────────

def _harvest(record: dict):
    wanted = {"harvest_source_title", "harvest_source_id", "harvest_object_id", "guid"}
    found = {str(e.get("key")): e.get("value") for e in (record.get("extras") or [])
             if isinstance(e, dict) and str(e.get("key", "")).lower() in wanted}
    return found or NOT_REPORTED


def provenance_of(store: OpenDataStore, ds: dict) -> dict:
    """What the registration already holds about where the dataset came from. No call is made."""
    title = (ds.get("title") or "").strip().lower()
    twins = [d["slug"] for d in store.list_datasets()
             if d["slug"] != ds["slug"] and title and title != NOT_REPORTED and (d.get("title") or "").strip().lower() == title]
    doi = ds.get("identifier") if ds.get("identifier_kind") == "doi" else NOT_REPORTED
    dc = ds.get("datacite")
    return {"identifier": ds.get("identifier"), "identifier_kind": ds.get("identifier_kind"), "doi": doi,
            "datacite": ("held" if dc else ("no DOI reported" if doi == NOT_REPORTED else "DOI reported · DataCite record not held")),
            "portals": ds.get("portals") or [], "harvested_from": _harvest(ds.get("record") or {}),
            "publisher": ds.get("publisher"), "licence_id": ds.get("licence_id"),
            "same_title_held_as": twins}


def fingerprint_overlap(mine: dict[str, set[str]], held: dict[str, dict[str, set[str]]]) -> list[dict]:
    """For each of this dataset's fingerprints, the held profiled distributions whose column names overlap it:
    {distribution, with, with_distribution, shared, jaccard}. Pure over column-name sets."""
    out = []
    for dist, names in mine.items():
        if not names:
            continue
        for other_ds, dists in held.items():
            for other_dist, other_names in dists.items():
                if not other_names:
                    continue
                union = names | other_names
                jac = len(names & other_names) / len(union)
                if jac >= 0.5:
                    out.append({"distribution": dist, "with": other_ds, "with_distribution": other_dist,
                                "shared": len(names & other_names), "jaccard": round(jac, 3),
                                "same": names == other_names})
    return sorted(out, key=lambda r: (-r["jaccard"], r["with"]))


FINGERPRINT_SCOPE_WORDS = ("compared with open data already profiled here · registered databases and file systems are "
                           "not compared yet")


def _held_fingerprints(registry, except_slug: str) -> dict[str, dict[str, set[str]]]:
    held: dict[str, dict[str, set[str]]] = {}
    for r in registry.query_step_runs(step_key=STEP_KEY, entity_type=ENTITY_TYPE, limit=2000):
        if r["slug"] == except_slug or r["slug"] in held:
            continue                                         # newest first: the first row of a slug is its latest
        m = r.get("metrics") or {}
        held[r["slug"]] = {d["name"]: _normalised(c["column_name"] for c in d.get("columns") or [])
                           for d in m.get("distributions") or [] if d.get("state") == PROFILED}
    return held


# ── the press ───────────────────────────────────────────────────────────────────────────────────────────────────

def profile_dataset(store: OpenDataStore, ds: dict, *, transport, catalog, registry=None) -> dict:
    from resource_explorer.open_data.service import portal_adapter

    registry = registry or store.registry
    cap = cap_bytes()
    portal = store.get_portal(ds["portal_slug"])
    base = {"slug": ds["slug"], "title": ds["title"], "ran_at": now(), "cap_bytes": cap}
    if not portal:
        return {**base, "outcome": "not_profiled", "words": f"no portal '{ds['portal_slug']}' is registered now",
                "distributions": [], "documents": [], "provenance": provenance_of(store, ds)}
    try:
        dists = portal_adapter(portal, transport).distributions(_dataset_id(ds))
    except Exception as exc:
        return {**base, "outcome": "not_profiled", "words": f"could not list the distributions: {exc}"[:240],
                "distributions": [], "documents": [], "provenance": provenance_of(store, ds)}
    data, docs, other = split_distributions(dists)
    chosen, rest = data[:MAX_DATA_DISTRIBUTIONS], data[MAX_DATA_DISTRIBUTIONS:]
    results, documents = [], []
    with tempfile.TemporaryDirectory(prefix="od-profile-") as tmp:
        work = Path(tmp)
        for d in chosen:
            results.append(profile_distribution(d, transport=transport, catalog=catalog, workdir=work, cap=cap))
    for d in rest:
        results.append({"name": _label(d), "format": _ext(d) or NOT_REPORTED, "state": NOT_CHOSEN,
                        "words": f"not profiled · only the first {MAX_DATA_DISTRIBUTIONS} distributions are profiled"})
    for d in other:
        results.append({"name": _label(d), "format": _ext(d) or NOT_REPORTED, "state": NOT_READ_FORMAT,
                        "words": f"not profiled · format {_ext(d) or NOT_REPORTED_WORDS} is not read here"})
    for d in docs[:MAX_DOC_DISTRIBUTIONS]:
        documents.append(read_document(d, transport=transport))
    mine = {r["name"]: _normalised(c["column_name"] for c in r.get("columns") or [])
            for r in results if r.get("state") == PROFILED}
    overlap = fingerprint_overlap(mine, _held_fingerprints(registry, ds["slug"])) if mine else []
    profiled = sum(1 for r in results if r.get("state") == PROFILED)
    return {**base, "outcome": "profiled" if profiled else "not_profiled",
            "words": (f"{profiled} of {len(dists)} distribution{'s' if len(dists) != 1 else ''} profiled" if dists
                      else f"the portal lists no distributions ({NOT_REPORTED_WORDS})"),
            "distributions": results, "documents": documents, "provenance": provenance_of(store, ds),
            "fingerprint_overlap": overlap, "fingerprint_scope": FINGERPRINT_SCOPE_WORDS,
            "distributions_listed": len(dists)}


def _dataset_id(ds: dict) -> str:
    """The id the portal knows the dataset by: the portal's own name for it, kept in the verbatim record."""
    rec = ds.get("record") or {}
    return rec.get("name") or rec.get("id") or str(ds["identifier"]).split(":", 1)[-1]


def profile_work_list(store: OpenDataStore, work_list_slug: str, *, transport,
                      catalog_loader: Callable[[], object] | None = None, registry=None) -> dict:
    """The press. Raises NothingToProfile (no dataset in the work list) and AlreadyRunning."""
    from resource_explorer.surveyors.filesystem import file_class_match as fcm

    registry = registry or store.registry
    datasets = store.datasets_in_work_list(work_list_slug)
    if not datasets:
        raise NothingToProfile("This work list holds no open data to profile.")
    key = f"{STEP_KEY}:{work_list_slug}"
    holder = f"{os.getpid()}:{now()}"
    if not registry.take_claim(key, holder, stale_after_seconds=CLAIM_STALE_SECONDS):
        raise AlreadyRunning(ALREADY_RUNNING_WORDS.format(slug=work_list_slug))
    try:
        catalog = (catalog_loader or fcm.default_catalog)()
        results = []
        for ds in datasets:
            r = profile_dataset(store, ds, transport=transport, catalog=catalog, registry=registry)
            registry.record_step_run(ds["slug"], STEP_KEY, r["ran_at"], entity_type=ENTITY_TYPE, source=SOURCE,
                                     executor="local", metrics=r)
            results.append(r)
    finally:
        registry.release_claim(key, holder)
    profiled = sum(1 for r in results if r["outcome"] == "profiled")
    return {"work_list": work_list_slug, "price": profile_price(len(datasets)), "profiled": profiled,
            "not_profiled": len(results) - profiled, "datasets": results, "ran_at": now()}


def view(store: OpenDataStore, work_list_slug: str, registry=None) -> dict:
    """The newest stored profile of each dataset in the work list, and the price of running it again."""
    registry = registry or store.registry
    datasets = store.datasets_in_work_list(work_list_slug)
    out = []
    for ds in datasets:
        rows = registry.query_step_runs(slug=ds["slug"], step_key=STEP_KEY, entity_type=ENTITY_TYPE, limit=1)
        out.append({"slug": ds["slug"], "title": ds["title"],
                    "profile": (rows[0].get("metrics") if rows else None),
                    "words": "" if rows else "Nothing has been measured for this yet · never run"})
    return {"price": profile_price(len(datasets)), "datasets": out}
