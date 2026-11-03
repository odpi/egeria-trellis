# Open data adapters (slice OD1)

Plugin point: entry-point group `trellis.open_data` (the shape of the Analytic Library's `pyegeria.analytics`). An adapter is a class with a
class-level `manifest` (`resource_explorer.open_data.adapters.AdapterManifest`) and the methods of its role:

| Role | Methods | Built-in |
|---|---|---|
| portal | `search(terms, filters, page)`, `describe(dataset_id)`, `distributions(dataset_id)` | `ckan` (CKAN 2.9+, API 3: `package_search`, `package_show`); `dcat_ap` (DCAT-AP JSON-LD `catalog.jsonld` feeds and Project Open Data `data.json`); `zenodo` (Zenodo and InvenioRDM: `GET /records`, `GET /records/{id}`) |
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

## The `zenodo` adapter

Zenodo's records API (`https://zenodo.org/api`), which every InvenioRDM instance shares: a portal registered with another
base URL and api kind `Zenodo` or `InvenioRDM` works unchanged. **No key**: anonymous reads; a registered `secret_ref` is
sent as a bearer token for the higher rate limit only and is never required (the manifest says `needs_key=False`).

- `search` is `GET /records?q=<terms>&size=25&page=<n>` (plus `type=dataset` when the filter asks): one call per page, 25
  rows (Zenodo's default). `hits.total` gives the page count; the price line says "a record's files list costs one more call".
- A row's identifier is the **concept DOI** when the record states one (so versions are one dataset), else the record DOI
  (`identifier_kind` `doi`). Publisher is `metadata.publisher` or "Zenodo"; licence is `metadata.license.id` (SPDX-style
  ids map to the three flags through `licences.py`); modified is `updated`; formats (file-name extensions) and size
  (sum of `files[].size`) come from the record's `files`. A record with no `license` reads `not_reported`, never "open"; a
  record with no files has no formats and no size (not 0 bytes).
- `describe(id)` is `GET /records/{id}`; `distributions(id)` is its `files[]` (key, size, checksum, link);
  `resolve(doi)` is `GET /records?q=doi:"<doi>"`, so a DOI pasted anywhere finds the record.
- Registration probes `GET /records?size=1` (and `/api/records?size=1` when the URL was typed without `/api`) and requires
  `hits` in the answer; the answering root is stored as the portal's base URL.
- Fixtures: `zenodo_*` in `tests/fixtures/open_data/`, recorded 2026-11-03 (README there has the URLs).

## Open data from a registered repository

A repository can be the dataset. During ingestion (while the checkout is on disk) the `dataset_descriptor` step reads, and
keeps every one it finds, `datapackage.json` (Frictionless), `croissant.json` / `metadata.json` with `@type: Dataset`,
`CITATION.cff`, a DOI badge or link in the README and a Hugging Face dataset card (README front matter with `license:` and
`dataset_info:`), and stores them as `dataset_descriptor` findings. No network call is made then; a field a descriptor does
not state is `not_reported`.

- **Role.** `repo_classification` gains an eighth role, `dataset` (ranked after `samples`, before `middleware`; no score),
  supported by a descriptor that names a dataset (a README DOI alone does not, and a `CITATION.cff` only with
  `type: dataset`), a Hugging Face card, or data files at half or more of the tree's bytes with no build tooling. The
  repository page's kind row reads "a dataset repository · identity DOI 10.…/… · licence CC-BY-4.0 (from datapackage.json)",
  or, with data files and no such descriptor, "a repository with data files, not an open dataset by Resource Explorer's rule
  (no descriptor names it)".
- **Import.** `POST /api/open-data/import-from-repository {repo_slug, descriptor_path}` files one `open_data` row under the
  reserved `repository` pseudo-portal (a row, never listed, searched or re-read). The identifier is the DOI when any descriptor
  holds one, else the datapackage/Croissant name qualified by the repository (`<owner>/<repo>:<name>`); with neither it is
  refused. The row keeps the descriptor verbatim plus the DataCite record, formats and size from the declared files
  cross-checked against the tree ("3 of 4 declared files present in the tree"), and `repo_slug`. Cost: the descriptor already
  on disk, plus one DataCite call when there is a DOI; nothing is written to Egeria. DDL: `ddl-open-data-repo-slug.md`.

## Suggested portals

Resource Explorer ships a short list of open-data portals worth knowing, in `resource_explorer/configdata/open_data_portal_seeds.yaml`.
Admin › Data portals shows the rows not yet registered under a folded section, **Suggested portals · N**.

- **Offered, never applied.** Nothing in the list is registered until a person presses **Add** on one row. Add registers
  that portal for everyone, as the person who pressed it (origin `seed`), and checks reach first: a portal that does
  not answer stays unregistered and the row says why. There is no bulk Add.
- A row is matched to a registered portal by its feed address (host and path), never by name; a registered portal drops
  out of the list, and the section then says "all suggested portals are registered".
- Each row says what it is: the API (CKAN or DCAT-AP), the feed, the publisher, what the portal uses as a dataset
  identifier, the licence of the catalogue itself (`NOT_REPORTED` when unknown), the date a maintainer last got an HTTP
  200 from it, one honest size note, and why it is listed. A portal that did not answer stays listed, greyed, with
  "did not answer on <date>". A portal Resource Explorer cannot read yet (data.europa.eu: needs the SPARQL adapter) is
  listed as "not yet", with no Add.
- One portal can be listed through two adapters (opendata.swiss and Open Government Canada: CKAN and DCAT-AP rows).
- `catalog.data.gov/data.json` is deliberately absent: one document of hundreds of megabytes.

To add a row, open a PR that adds it to the yaml with a `checked` date (the day you last got a 200 from `feed_url`).
