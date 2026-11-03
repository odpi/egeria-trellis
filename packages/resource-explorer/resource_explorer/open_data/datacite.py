"""The DataCite resolver: `resolve(doi) -> record` over api.datacite.org/dois/{doi}, no key.

Not a search target. Given a DOI it returns the registered publisher, title, year, rights, the related identifiers
(stored on the record for OD3's provenance) and the citation count where DataCite exposes it.
"""
from __future__ import annotations

from resource_explorer.open_data.absence import NOT_REPORTED, reported_or_absent
from resource_explorer.open_data.adapters import Adapter, AdapterManifest
from resource_explorer.open_data.ckan import normalise_doi
from resource_explorer.open_data.transport import TransportError

DEFAULT_BASE_URL = "https://api.datacite.org"


class DataCiteResolver(Adapter):
    manifest = AdapterManifest(
        name="datacite", api_kind="DataCite", role="resolver", registration_fields=(),
        calls_per_page=1, page_size=1, needs_key=False)

    def __init__(self, base_url: str = DEFAULT_BASE_URL, transport=None, api_key: str = ""):
        super().__init__(base_url or DEFAULT_BASE_URL, transport, api_key)

    def resolve(self, identifier: str) -> dict:
        doi = normalise_doi(identifier)
        if doi == NOT_REPORTED:
            raise ValueError(f"{identifier!r} is not a DOI")
        body = self.transport.get_json(f"{self.base_url}/dois/{doi}")
        attrs = (body or {}).get("data", {}).get("attributes") if isinstance(body, dict) else None
        if not isinstance(attrs, dict):
            raise TransportError(f"DataCite: no record in the answer for {doi}")
        titles = attrs.get("titles") or []
        related = [
            {"relation": r.get("relationType"), "identifier": r.get("relatedIdentifier"),
             "identifier_type": r.get("relatedIdentifierType")}
            for r in attrs.get("relatedIdentifiers") or [] if isinstance(r, dict)]
        return {
            "doi": doi,
            "publisher": reported_or_absent(attrs.get("publisher")),
            "title": reported_or_absent(titles[0].get("title") if titles and isinstance(titles[0], dict) else None),
            "publication_year": reported_or_absent(attrs.get("publicationYear")),
            "rights": reported_or_absent(attrs.get("rightsList")),
            "related_identifiers": reported_or_absent(related),
            # citationCount is 0 for "none recorded" and for "not tracked" alike on DataCite; stated as DataCite gave it.
            "citation_count": reported_or_absent(attrs.get("citationCount")),
            "record": body,
        }
