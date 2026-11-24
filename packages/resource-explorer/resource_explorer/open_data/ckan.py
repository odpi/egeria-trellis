"""CKAN (2.9+, action API 3): package_search, package_show.

Read-only. Verified against demo.ckan.org on 2026-10-28 (fixtures in tests/fixtures/open_data/).
"""
from __future__ import annotations

import re
from typing import Any

from resource_explorer.open_data.absence import NOT_REPORTED, reported_or_absent
from resource_explorer.open_data.adapters import Adapter, AdapterManifest, page_of
from resource_explorer.open_data.licences import licence_flags
from resource_explorer.open_data.transport import TransportError


def normalise_doi(raw: Any) -> str:
    """A bare lower-case DOI ("10.x/y") from a DOI or doi.org URL, else NOT_REPORTED."""
    if not isinstance(raw, str):
        return NOT_REPORTED
    text = raw.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip().lower()
    return text if text.startswith("10.") and "/" in text else NOT_REPORTED


def _extra(record: dict, *keys: str) -> Any:
    wanted = {k.lower() for k in keys}
    for item in record.get("extras") or []:
        if isinstance(item, dict) and str(item.get("key", "")).lower() in wanted:
            return item.get("value")
    return None


def _size(resources: list) -> Any:
    """Total bytes when at least one resource states a numeric size, else NOT_REPORTED (a sum over nothing is not 0)."""
    total, seen = 0, False
    for r in resources:
        try:
            n = int(float(r.get("size")))
        except (TypeError, ValueError):
            continue
        if n >= 0:
            total, seen = total + n, True
    return total if seen else NOT_REPORTED


class NotCkan(TransportError):
    """At PROBE time: nothing at /api/3/action answered as CKAN (HTTP 404/410, or not JSON, or no CKAN envelope). Registration
    moves on to DCAT. A portal already registered as CKAN never raises this: its 404 stays a plain TransportError, in its words."""


class CkanAdapter(Adapter):
    #: Registering a CKAN portal needs no probe call when no transport is given (as before TR-54 E1); with one, it probes.
    PROBE_OPTIONAL = True

    manifest = AdapterManifest(
        name="ckan", api_kind="CKAN", api_kind_aliases=("CKAN API", "ckan api 3"), role="portal",
        registration_fields=("name", "base_url"), calls_per_page=1, page_size=20, needs_key=False)

    @classmethod
    def probe(cls, base_url: str, transport) -> dict:
        """One read-only call at registration: does /api/3/action answer as CKAN? A 404/410 or a non-JSON answer is `NotCkan`
        (the caller may try DCAT); any other failure (a 503, a timeout) is raised as it is, never read as "not CKAN"."""
        base = (base_url or "").strip().rstrip("/")
        url = f"{base}/api/3/action/package_search"
        try:
            body = transport.get_json(url, params={"rows": 0}, headers=None)
        except TransportError as exc:
            text = str(exc)
            if re.search(r"HTTP (404|410)\b", text) or "was not JSON" in text:
                raise NotCkan(f"{base}: not a CKAN portal (no /api/3/action here): {text}") from exc
            raise
        if not isinstance(body, dict) or "success" not in body:
            raise NotCkan(f"{base}: not a CKAN portal (no /api/3/action here): it answered without CKAN's envelope")
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        return {"feed_url": base, "shape": "ckan", "page_size": None, "total": result.get("count", NOT_REPORTED)}

    def _action(self, name: str, params: dict) -> dict:
        body = self.transport.get_json(
            f"{self.base_url}/api/3/action/{name}", params=params,
            headers={"Authorization": self.api_key} if self.api_key else None)
        if not isinstance(body, dict) or not body.get("success"):
            raise TransportError(f"{self.base_url}: {name} was not successful: {str(body)[:160]}")
        return body["result"]

    def normalise(self, record: dict) -> dict:
        org = record.get("organization") if isinstance(record.get("organization"), dict) else {}
        resources = [r for r in (record.get("resources") or []) if isinstance(r, dict)]
        formats = sorted({str(r["format"]).strip() for r in resources if str(r.get("format") or "").strip()})
        doi = normalise_doi(record.get("doi") or _extra(record, "doi"))
        dataset_id = record.get("name") or record.get("id") or ""
        licence_id = reported_or_absent(record.get("license_id"))
        return {
            "dataset_id": dataset_id,
            "identifier": doi if doi != NOT_REPORTED else dataset_id,
            "identifier_kind": "doi" if doi != NOT_REPORTED else "portal_id",
            "doi": doi,
            "title": reported_or_absent(record.get("title")),
            "publisher": reported_or_absent(org.get("title") or record.get("author") or record.get("maintainer")),
            "licence_id": licence_id,
            "licence": licence_flags(None if licence_id == NOT_REPORTED else licence_id),
            "modified": reported_or_absent(record.get("metadata_modified")),
            "formats": reported_or_absent(formats),
            "size": _size(resources),
            "url": f"{self.base_url}/dataset/{dataset_id}" if dataset_id else NOT_REPORTED,
            "record": record,
        }

    def search(self, terms: str, filters: dict | None, page: int) -> dict:
        filters = filters or {}
        size = self.manifest.page_size
        fq = []
        if filters.get("format"):
            fq.append(f'res_format:"{filters["format"]}"')
        if filters.get("modified_after"):
            fq.append(f'metadata_modified:[{filters["modified_after"]}T00:00:00Z TO *]')
        params = {"q": terms or "", "rows": size, "start": (max(page, 1) - 1) * size}
        if fq:
            params["fq"] = " AND ".join(fq)
        result = self._action("package_search", params)
        rows = [self.normalise(r) for r in result.get("results", []) if isinstance(r, dict)]
        return page_of(rows, page, size, result.get("count", NOT_REPORTED))

    def describe(self, dataset_id: str) -> dict:
        return self.normalise(self._action("package_show", {"id": dataset_id}))

    def distributions(self, dataset_id: str) -> list[dict]:
        record = self._action("package_show", {"id": dataset_id})
        return [{
            "id": reported_or_absent(r.get("id")), "name": reported_or_absent(r.get("name")),
            "format": reported_or_absent(r.get("format")), "url": reported_or_absent(r.get("url")),
            "size": reported_or_absent(r.get("size")), "record": r,
        } for r in record.get("resources") or [] if isinstance(r, dict)]
