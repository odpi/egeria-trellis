# Open data adapters (slice OD1)

Plugin point: entry-point group `trellis.open_data` (the shape of the Analytic Library's `pyegeria.analytics`). An adapter is a class with a
class-level `manifest` (`resource_explorer.open_data.adapters.AdapterManifest`) and the methods of its role:

| Role | Methods | Built-in |
|---|---|---|
| portal | `search(terms, filters, page)`, `describe(dataset_id)`, `distributions(dataset_id)` | `ckan` (CKAN 2.9+, API 3: `package_search`, `package_show`); `dcat_ap` (DCAT-AP JSON-LD `catalog.jsonld` feeds and Project Open Data `data.json`) |
| directory | `list_portals(page)` | `dataportals` (the dataportals.org JSON listing) |
| resolver | `resolve(doi)` | `datacite` (api.datacite.org/dois/{doi}, no key) |

A manifest names the `api_kind` it speaks (and aliases a directory may state), the registration fields, whether a key is
needed and the cost shape: `calls_per_page` and `page_size`. **A manifest without a cost shape is refused at load**: the
price line ("N pages × 1 call") is computed from the manifest before a search runs. Every row an adapter returns carries
the source's record verbatim under `record`; a field the source does not give is the typed absence `"not_reported"`
(`open_data/absence.py`), never `""` and never `None`.

The built-ins are always present (they live in this package), so a venv installed before this slice, whose entry-point
metadata has not been regenerated, still has them. An installed entry point of the same name replaces a built-in; one
that fails to load or whose manifest is refused is logged and skipped. Third-party packages register with:

```toml
[project.entry-points."trellis.open_data"]
socrata = "my_package.socrata:SocrataAdapter"
```

All outbound HTTP goes through `open_data/transport.py` (`Transport.get_json(url, params, headers)`); tests pass a fake that
binds that signature. Fixtures in `tests/fixtures/open_data/` were recorded 2026-10-28 from demo.ckan.org, dataportals.org
and api.datacite.org (read-only).

## The `dcat_ap` adapter

DCAT-AP is a vocabulary, not an API: it defines no search call. The adapter reads a catalog feed (a document, paged with
hydra `?page=N` when the feed says so, otherwise one document paged locally) and `search` is Resource Explorer keeping
the rows of the fetched pages that match (whole words, every term, over title, description and keywords; the format and
modified-after filters are local too). The manifest's `cost_note` puts that on the price line.

Registration probes the typed URL (`/catalog.jsonld?page=1`, then `/data.json`, then the URL itself: 1 to 3 calls) and
stores the feed URL it found as the portal's `base_url` and the feed's page size in the existing `page_size` column; no
other column. The reader accepts expanded, flattened JSON-LD (data.gov.ie), compact JSON-LD with a `@graph`
(ckanext-dcat dataset documents) and plain POD JSON through one lookup that tries the prefixed key, the expanded IRI and the
bare name. A record carries the source's entry verbatim; keys beginning `_` (`_language`, `_licence_note`,
`_filter_note`, `_access_only`) are RE's own notes on it. Fixtures and where each was recorded: `tests/fixtures/open_data/README`.
Not supported: RDF/XML, Turtle, SPARQL.
