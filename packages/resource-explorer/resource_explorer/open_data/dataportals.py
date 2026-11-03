"""The dataportals.org directory: the JSON listing of open-data portals.

A directory proposes portals to register, never datasets. `base_url` of a registered directory is the listing's
URL (https://dataportals.org/api/data.json). The listing is one JSON object keyed by portal name; it is fetched
once per adapter instance and paged locally, so the real call count never exceeds the price shown.
"""
from __future__ import annotations

from resource_explorer.open_data.absence import NOT_REPORTED, reported_or_absent
from resource_explorer.open_data.adapters import Adapter, AdapterManifest, page_of

DEFAULT_LISTING_URL = "https://dataportals.org/api/data.json"


class DataPortalsDirectory(Adapter):
    manifest = AdapterManifest(
        name="dataportals", api_kind="dataportals.org", role="directory", registration_fields=("name", "base_url"),
        calls_per_page=1, page_size=100, needs_key=False)

    def __init__(self, base_url: str = DEFAULT_LISTING_URL, transport=None, api_key: str = ""):
        super().__init__(base_url or DEFAULT_LISTING_URL, transport, api_key)
        self._listing: dict | None = None

    def _all(self) -> list[dict]:
        if self._listing is None:
            body = self.transport.get_json(self.base_url)
            self._listing = body if isinstance(body, dict) else {}
        return [v for v in self._listing.values() if isinstance(v, dict)]

    @staticmethod
    def normalise(entry: dict) -> dict:
        url = (entry.get("url") or "").strip()
        return {
            "name": reported_or_absent(entry.get("title") or entry.get("name")),
            "country": reported_or_absent(entry.get("country")),
            "domain": reported_or_absent(entry.get("publisher_classification")),
            "api_kind": reported_or_absent(entry.get("api_type")),        # as the directory states it
            "api_endpoint": reported_or_absent(entry.get("api_endpoint")),
            "url": url or NOT_REPORTED,
            "dataset_count": NOT_REPORTED,                                # the listing does not give one
            "licence_id": reported_or_absent(entry.get("license_id")),
            "record": entry,
        }

    def list_portals(self, page: int) -> dict:
        entries = self._all()
        size = self.manifest.page_size
        start = (max(page, 1) - 1) * size
        return page_of([self.normalise(e) for e in entries[start:start + size]], page, size, len(entries))
