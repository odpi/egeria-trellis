"""The re3data directory: the registry of research-data repositories (https://www.re3data.org).

A directory proposes repositories to register, never datasets. `base_url` of a registered directory is the listing's
URL (https://www.re3data.org/api/v1/repositories). The listing is ONE XML document, fetched once per adapter
instance and paged locally, so the call count never exceeds the price shown.

What the listing states per repository is only its re3data id, its DOI and its name (recorded 2026-10-30,
tests/fixtures/open_data/re3data_repositories.xml). Everything else a candidate row carries (country, domain, API,
the repository's own URL, dataset count) is NOT in the listing: it is `NOT_REPORTED` ("not reported by this
directory"), and a row with no URL cannot be registered. The per-repository record (`/api/v1/repository/<id>`) holds
those fields; reading it is one call per candidate and is a separate, priced step that is not built yet.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from resource_explorer.open_data.absence import NOT_REPORTED, reported_or_absent
from resource_explorer.open_data.adapters import Adapter, AdapterManifest, page_of
from resource_explorer.open_data.transport import TransportError

DEFAULT_LISTING_URL = "https://www.re3data.org/api/v1/repositories"
SITE = "https://www.re3data.org"


class Re3dataDirectory(Adapter):
    manifest = AdapterManifest(
        name="re3data", api_kind="re3data", role="directory", registration_fields=("name", "base_url"),
        calls_per_page=1, page_size=100, needs_key=False)

    def __init__(self, base_url: str = DEFAULT_LISTING_URL, transport=None, api_key: str = ""):
        super().__init__(base_url or DEFAULT_LISTING_URL, transport, api_key)
        self._entries: list[dict] | None = None

    def _all(self) -> list[dict]:
        if self._entries is None:
            text = self.transport.get_text(self.base_url)
            # A listing never carries a DTD; refusing one closes entity-expansion attacks without a new dependency.
            if "<!DOCTYPE" in text or "<!ENTITY" in text:
                raise TransportError(f"{self.base_url}: the listing carried a DTD, which re3data's does not")
            try:
                root = ET.fromstring(text)
            except ET.ParseError as exc:
                raise TransportError(f"{self.base_url}: the answer was not the XML listing ({exc})") from exc
            self._entries = [{
                "id": (r.findtext("id") or "").strip(), "doi": (r.findtext("doi") or "").strip(),
                "name": (r.findtext("name") or "").strip(),
                "record_url": (SITE + link.get("href")) if (link := r.find("link")) is not None and link.get("href") else "",
            } for r in root.findall("repository")]
        return self._entries

    @staticmethod
    def normalise(entry: dict) -> dict:
        return {
            "name": reported_or_absent(entry.get("name")),
            "country": NOT_REPORTED, "domain": NOT_REPORTED, "api_kind": NOT_REPORTED, "api_endpoint": NOT_REPORTED,
            "url": NOT_REPORTED,                                          # in the per-repository record, not the listing
            "dataset_count": NOT_REPORTED,
            "licence_id": NOT_REPORTED,
            "record": entry,
        }

    def list_portals(self, page: int) -> dict:
        entries = self._all()
        size = self.manifest.page_size
        start = (max(page, 1) - 1) * size
        return page_of([self.normalise(e) for e in entries[start:start + size]], page, size, len(entries))
