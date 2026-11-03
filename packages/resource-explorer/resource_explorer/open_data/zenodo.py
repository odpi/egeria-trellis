"""Zenodo (and any InvenioRDM instance): the records API, `GET /records`, `GET /records/{id}`.

Read-only and keyless: anonymous reads work. A registered `secret_ref` is sent as a bearer token for the higher rate
limit only; nothing here needs it. InvenioRDM instances share the API, so a registered row with another base URL works
unchanged. Verified against zenodo.org on 2026-11-03 (fixtures in tests/fixtures/open_data/).

A Zenodo record is a version; its concept DOI names the whole record across versions. The identifier RE keeps is the
concept DOI when the record states one (so two versions are one dataset), else the record's own DOI.
"""
from __future__ import annotations

import re
from typing import Any

from resource_explorer.open_data.absence import NOT_REPORTED, reported_or_absent
from resource_explorer.open_data.adapters import Adapter, AdapterManifest, page_of
from resource_explorer.open_data.ckan import normalise_doi
from resource_explorer.open_data.dcat_ap import ProbeRefused
from resource_explorer.open_data.licences import licence_flags
from resource_explorer.open_data.transport import Transport, TransportError

DEFAULT_BASE_URL = "https://zenodo.org/api"
PAGE_SIZE = 25


def _files(record: dict) -> list[dict]:
    files = record.get("files")
    if isinstance(files, dict):                     # InvenioRDM's own shape: {"entries": {key: {...}}}
        files = list((files.get("entries") or {}).values())
    return [f for f in (files or []) if isinstance(f, dict)]


def _formats(files: list[dict]) -> Any:
    found = set()
    for f in files:
        m = re.search(r"\.([A-Za-z0-9]{1,8})$", str(f.get("key") or ""))
        if m:
            found.add(m.group(1).upper())
    return sorted(found) if found else NOT_REPORTED


def _size(files: list[dict]) -> Any:
    """Total bytes when at least one file states a numeric size, else NOT_REPORTED (a sum over nothing is not 0)."""
    total, seen = 0, False
    for f in files:
        size = f.get("size")
        if isinstance(size, bool) or not isinstance(size, (int, float)) or size < 0:
            continue
        total, seen = total + int(size), True
    return total if seen else NOT_REPORTED


def _licence_id(meta: dict) -> Any:
    lic = meta.get("license")
    if isinstance(lic, dict):
        return reported_or_absent(lic.get("id"))
    return reported_or_absent(lic)


class ZenodoAdapter(Adapter):
    manifest = AdapterManifest(
        name="zenodo", api_kind="Zenodo", api_kind_aliases=("InvenioRDM",), role="portal",
        registration_fields=("name", "base_url"), calls_per_page=1, page_size=PAGE_SIZE, needs_key=False,
        cost_note="one call per page; a record's files list costs one more call")

    def __init__(self, base_url: str = DEFAULT_BASE_URL, transport: Transport | None = None, api_key: str = ""):
        super().__init__(base_url or DEFAULT_BASE_URL, transport, api_key)

    # -- reads -----------------------------------------------------------------

    def _headers(self) -> dict | None:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None

    def _get(self, path: str, params: dict | None = None) -> dict:
        body = self.transport.get_json(f"{self.base_url}{path}", params=params, headers=self._headers())
        if not isinstance(body, dict):
            raise TransportError(f"{self.base_url}{path}: the answer was not a record or a hit list")
        return body

    @classmethod
    def probe(cls, base_url: str, transport: Transport) -> dict:
        """Recognise an InvenioRDM base: `GET /records?size=1` answers `hits`. A base typed without `/api` is tried
        with it (zenodo.org -> zenodo.org/api)."""
        base = (base_url or "").strip().rstrip("/")
        tried = [base] + ([] if base.endswith("/api") else [f"{base}/api"])
        for root in tried:
            try:
                body = transport.get_json(f"{root}/records", params={"size": 1}, headers=None)
            except (TransportError, ValueError):
                continue
            hits = body.get("hits") if isinstance(body, dict) else None
            if isinstance(hits, dict):
                total = hits.get("total")
                return {"feed_url": root, "page_size": PAGE_SIZE, "total": total if isinstance(total, int) else NOT_REPORTED}
        raise ProbeRefused(f"Nothing at {' or '.join(t + '/records?size=1' for t in tried)} answered with `hits`: "
                           f"that is not an InvenioRDM records API.")

    # -- normalising -------------------------------------------------------------

    def normalise(self, record: dict) -> dict:
        meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        files = _files(record)
        doi = normalise_doi(record.get("conceptdoi"))
        if doi == NOT_REPORTED:
            doi = normalise_doi(record.get("doi") or meta.get("doi"))
        rec_id = record.get("id")
        dataset_id = str(rec_id) if rec_id not in (None, "") else NOT_REPORTED
        licence_id = _licence_id(meta)
        links = record.get("links") if isinstance(record.get("links"), dict) else {}
        html = links.get("self_html") or links.get("html")
        if not html and dataset_id != NOT_REPORTED:
            html = f"{self.base_url.removesuffix('/api')}/records/{dataset_id}"
        return {
            "dataset_id": dataset_id,
            "identifier": doi if doi != NOT_REPORTED else (dataset_id if dataset_id != NOT_REPORTED else NOT_REPORTED),
            "identifier_kind": "doi" if doi != NOT_REPORTED else "portal_id",
            "doi": doi,
            "title": reported_or_absent(meta.get("title") or record.get("title")),
            "publisher": reported_or_absent(meta.get("publisher")) if meta.get("publisher") else "Zenodo",
            "licence_id": licence_id,
            "licence": licence_flags(None if licence_id == NOT_REPORTED else licence_id),
            "modified": reported_or_absent(record.get("updated") or record.get("modified")),
            "formats": _formats(files),
            "size": _size(files),
            "url": reported_or_absent(html),
            "record": record,
        }

    # -- portal ---------------------------------------------------------------------

    def search(self, terms: str, filters: dict | None, page: int) -> dict:
        filters = filters or {}
        size = self.page_size
        params = {"q": terms or "", "size": size, "page": max(page, 1)}
        if filters.get("type"):
            params["type"] = filters["type"]
        body = self._get("/records", params)
        hits = body.get("hits") if isinstance(body.get("hits"), dict) else {}
        rows = [self.normalise(r) for r in hits.get("hits") or [] if isinstance(r, dict)]
        total = hits.get("total")
        return page_of(rows, page, size, total if isinstance(total, int) else NOT_REPORTED)

    def describe(self, dataset_id: str) -> dict:
        return self.normalise(self._get(f"/records/{dataset_id}"))

    def distributions(self, dataset_id: str) -> list[dict]:
        record = self._get(f"/records/{dataset_id}")
        return [{
            "id": reported_or_absent(f.get("id")), "name": reported_or_absent(f.get("key")),
            "format": _formats([f]), "url": reported_or_absent((f.get("links") or {}).get("self")),
            "size": reported_or_absent(f.get("size")), "checksum": reported_or_absent(f.get("checksum")),
            "record": f,
        } for f in _files(record)]

    def resolve(self, identifier: str) -> dict:
        """A DOI pasted anywhere finds the record: `GET /records?q=doi:"<doi>"`. Raises when Zenodo holds none."""
        doi = normalise_doi(identifier)
        if doi == NOT_REPORTED:
            raise ValueError(f"{identifier!r} is not a DOI")
        body = self._get("/records", {"q": f'doi:"{doi}"', "size": 1})
        hits = (body.get("hits") or {}).get("hits") or []
        if not hits:
            raise TransportError(f"{self.base_url}: no record holds the DOI {doi}")
        return self.normalise(hits[0])
