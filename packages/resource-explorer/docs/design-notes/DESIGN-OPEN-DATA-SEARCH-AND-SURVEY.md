# DESIGN — Open data: the search and survey process, end to end (2026-10-28)

Owner's ask (2026-10-28, to the architect session): "design what an open
data search and survey process would look like". The kind, its thirteen
questions, four survey definitions, DCAT/CKAN facts and the Egeria publish
shape are in DESIGN-OPEN-DATA-RESOURCE-KIND-BRAINSTORM.md (2026-10-20, a
brainstorm); this note is the process a person walks, from a need to a
cataloged external dataset, and what each step reuses. It settles nothing
the brainstorm's §7 left to the owner; §8 restates those with a
recommendation each.

Inventory first (inventory-Egeria-connectors rule): Egeria's open-connectors
tree has no CKAN, DCAT, Socrata or open-data connector (the matches in
`apache-kafka-connectors` and `apache-atlas-connectors` are unrelated
strings). The file-survey connectors profile a CSV once it is a local file.
So search and metadata reading are RE's to build, as native adapters
(REQUIREMENTS-RE-DATA-CHANNELS: Egeria connectors cannot be the only channel);
profiling a fetched distribution reuses Egeria's CSV survey through the Files
step 5 path.

## 1. The process in six steps

| Step | Stage | Who | What happens | Reuses |
|---|---|---|---|---|
| 1 Need | Investigation | a person | states what data they are looking for as a data request: subjects, period, places, grain, licence needs, format needs | the investigation lens (fit row, preliminary_fit's input) |
| 2 Search | Scouting (discovery source) | RE, then the person | queries registered portals with the request's terms; returns candidate rows; the person previews and imports a shortlist as a work list | discovery sources (candidate row, preview-then-import, CSV in/out), work lists as the winnowing bench |
| 3 Scout | Scouting | RE | `ExternalDataScout` on each candidate: publisher metadata verbatim with retrieved-at, licence read against SPDX and the open-data licence list, distributions and sizes, cost line for the next step | the declaration (every measure with unit, as-of, absence sentence), the price-before-you-run rule |
| 4 Fit and winnow | Discovery | the person, with RE's columns | the fit row over the scouted metadata (period, place, subjects, grain, licence, format) narrows the list to kept / dropped / could-not-check; the survivors go to profiling | work-lists winnowing (§2 of that note), Q-0011 overlap against data already held |
| 5 Profile | Analysis | RE | `ExternalDataProfile`: one capped sample per distribution, columns and types, value profile, data-class match, narrative files read as evidence, provenance (DOI, harvested-from, duplicates across portals) | Files step 5 (file_profiles: Egeria CSV survey or RE pandas), data_class_match, folder_narratives, the fingerprint analysis |
| 6 Decide and publish | Curate, then Automate | the person | the resource page's headlines answer O1–O13; the person publishes the dataset as an external asset with its licence, publisher, coverage and sample-based measures; subscribes to `external_change` | Curate's single publish with the publish table, the Automate subscription |

A step never fetches more than the step before it priced. Step 2 reads
catalog APIs only; step 3 reads metadata only; step 5 is the first byte of
data, capped and stated.

## 2. Step 1, the need: the data request

The investigation lens already carries the dimensions preliminary_fit
compares (subjects, period, places, grain). For open data it gains two
declared dimensions that decide everything downstream before any data is
read: **licence needs** (may we redistribute; attribution acceptable;
share-alike acceptable; commercial use needed) and **format needs** (CSV or
API; a size ceiling). The request is stored on the investigation as today;
the fit row (§4) gets a cell per dimension. A request with no licence need
stated is not "any licence": the fit cell reads "licence need not stated"
and never passes a candidate on it.

## 3. Step 2, the search: portals as discovery sources

- A **portal** is a registered resource of its own kind (`open_data_portal`,
  one row per portal: name, API kind, base URL, optional API key reference),
  because a portal is a thing RE talks to repeatedly, with a credential and
  a cost, like a database server. Datasets found on it are the resources
  the person works with; the portal is their origin.
- **Portal adapters are a plugin point**, the same shape as remote-access
  mechanisms (DESIGN-FILES-REMOTE-REACH §3): a package discovered by an
  entry point with a manifest naming the API kind it speaks, the fields a
  registration needs, the cost shape (calls per page, page size) and one
  interface: `search(terms, filters, page)`, `describe(dataset_id)`,
  `distributions(dataset_id)`. Built-ins in order: **CKAN** first (the most
  common portal software: data.gov's catalog, most city and national
  portals, demo instances exist), **DCAT-AP** feeds second (the EU portal;
  data.gov also exposes DCAT), Socrata third. The adapter returns the
  publisher's record verbatim plus RE's normalised candidate fields, never
  only the normalised ones.
- **The search is the discovery source's Run** (REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS):
  terms from the data request (subjects as keywords, places and period as
  the portal's facets where it has them), filters for licence and format,
  paged with the page count priced before the press. Results are candidate
  rows with publisher, licence, modified, formats, size, identifier, each
  "not reported by this source" when absent, and the portal named on every
  row. Preview, then import the chosen rows; the import registers each
  dataset as a resource of kind `open_data` with its portal as origin and
  the publisher's identifier as its identity (a DOI or DCAT identifier,
  never the URL), and puts them in a work list named for the request.
- **Already held?** Before import, the same identifiers are matched
  against registered resources and against Egeria (external identifier
  read-first), so a dataset already cataloged shows "already in the
  catalog · <where>" on its candidate row rather than being imported twice.
- **Cross-portal duplicates** (the same identifier on two portals) collapse
  to one candidate with both portals listed; the publisher's own portal, if
  one of them says it harvested from the other, is the origin.

## 4. Step 3 and 4, scout then winnow

`ExternalDataScout` runs on the work list as a batch (definition × work
list × Automate, the quality-at-scale pattern), API only, one call per
dataset, priced as N calls. Its results fill the bench's columns: licence
(SPDX id and the three flags), publisher, modified and cadence, formats,
size, coverage as the portal states it, identifier, narrative distributions
found. The fit row then has something to compare: the person filters on
licence (a flag mismatch drops the row), on period and place (the portal's
stated coverage against the request; "could not check" when the portal
states none, never dropped silently), on format and size. The shortlist is
saved with its filter and as-of, as the work-lists note rules. Q-0011 runs
here too: a candidate whose coverage and subjects overlap data we already
hold is flagged "may duplicate <resource>" before anyone fetches it.

## 5. Step 5, the profile

`ExternalDataProfile` on the shortlist only. Per distribution: the fetch is
capped (first N MB, the cap on the card and the row; recommendation 50 MB,
the Files step 5 limit), the sample ratio recorded on every number; a CSV
goes through the Files step 5 path (Egeria's CSV survey where reachable,
RE's profile otherwise), other formats through RE's profile; an API-only
distribution is sampled through its API with the page count stated. Then
data_class_match over the sampled columns (personal data, O9, with the
publisher's statement kept apart), folder_narratives over the
documentation distributions (data dictionary, codebook, CITATION, LICENSE,
methodology: evidence, "stated by <file>", proposals never facts),
external_provenance (DOI metadata, harvested-from, the duplicate set), and
the fingerprint against held data (O11). Every number carries sample size
and retrieved-at; "not reported by this source" is the absence word for a
metadata field and "not sampled: over the cap" for a distribution RE did
not read.

## 6. Step 6, decide, publish, watch

The resource page answers O1–O13 as headlines from the declarations (the
same renderers and the same chat compile as every kind). Curate's single
Publish writes the external asset per the brainstorm §6: the nearest Egeria
type (DataSet, or DataFile set behind a DeployedAPI), the publisher's
identifier as the external identifier, the portal as the origin, licence as
Egeria's licence type with the SPDX id, terms as an external reference, the
publisher as an organisation actor, coverage as DataScope, measures as
survey annotations with the sample size, publisher statements as
annotations marked "stated by the publisher". The publish table lists every
declared measure with Publish | Keep in RE as for databases. Then the
person subscribes: `ExternalDataRefresh` on a cadence runs the scout again
and `external_change` differences the two (metadata, distributions, size,
modified), which is Q-0041 for this kind and the Automate tile's job.

## 7. What is new and what is reused

New: the `open_data` and `open_data_portal` kinds with their registration
screens; the portal adapter plugin point with the CKAN built-in; the five
analyses the brainstorm names (external_metadata, external_distributions,
external_profile, external_provenance, external_change), each a declared
analysis with its result kind, measures, absence sentences and confidence
(the extensibility walking skeleton makes a new kind's analyses declarable
without renderer or chat work); the licence and format dimensions on the
lens; the identifier match against the catalog. Reused, unchanged:
discovery sources' candidate row and import, work lists and the winnowing
bench, the fit row, Files step 5 profiling, data_class_match,
folder_narratives, the fingerprint, Curate's publish table, Automate's
subscriptions, the journal and chat.

## 8. The brainstorm's open points, with a recommendation each

1. **Name on screen:** "Open data" for the kind, "Data portal" for the
   portal kind; "external data" stays the design term. Open models are a
   sibling kind later (`open_model`, one survey, the card as its schema),
   not this kind.
2. **First portal API:** CKAN. A CKAN demo instance for the walking slice,
   then one real portal the owner names; DCAT-AP second.
3. **Egeria types** for distributions and models: the brainstorm §6 shape,
   confirmed with the Egeria leads before the publish slice, not before the
   search slice.
4. **First instance:** demo.ckan.org for the gate, since nothing is written
   there and its datasets are small; the owner's real portal for the second
   walk.
5. **Caps:** 50 MB per distribution (Files step 5's limit), 3 distributions
   per dataset in the first slice, 25 candidates per import, all four
   shown on the price line and changeable in Admin.

## 9. Slices and the gate

| Slice | What | Gate by use |
|---|---|---|
| OD1 | the two kinds, their registration, the CKAN adapter as the first plugin, search as a discovery source, import to a work list, the identifier match | on demo.ckan.org: a request with two subjects and a period, a search, a 10-row import with one "already in the catalog" row, the work list on the bench |
| OD2 | `ExternalDataScout` declared, the licence and format lens dimensions, the fit row and winnow on the scouted columns, Q-0011 overlap | the 10 candidates scouted as a batch with a price line; a licence filter drops the share-alike rows and says so; two "could not check" rows for missing coverage |
| OD3 | `ExternalDataProfile` on the shortlist through Files step 5, data_class_match, folder_narratives, external_provenance, the fingerprint | three profiled distributions with sample ratios; one over the cap "not sampled"; one data dictionary read as evidence |
| OD4 | publish and `ExternalDataRefresh`/`external_change` | one dataset published and read back by GUID with its licence, origin and DataScope; a second scout a day later shows "no change on their side" or the diff |

OD1 and OD2 are one walking slice if the owner wants speed; OD3 reuses
what Files 5 built; OD4 waits for the Egeria types confirmation.

## 10. For the owner

- The five recommendations in §8, each a yes or a different pick.
- Whether portals should be registered by any signed-in user (as file
  systems are today) or by an admin, since a portal registration carries a
  cost shape and possibly an API key.
- Whether the first real portal is one he already uses, so the gate's
  second walk has a dataset he cares about.

## 11. Addendum 1 (owner, 2026-10-28): portal directories and DataCite

Owner's words: "yes to the five recommendations, CKAN on demo.ckan.org
first" and, after the question of which open-data directories to connect
to, "add the portal directory kind and DataCite to the design".

### 11.1 The sources, in tiers

| Tier | Examples | Role in the process |
|---|---|---|
| Aggregators that harvest many portals | data.gov (CKAN), data.europa.eu (DCAT-AP), Socrata Discovery API, opendatasoft hub, HDX (CKAN) | search targets; one adapter reaches thousands of publishers |
| Directories of portals | dataportals.org, re3data | propose portals to register, never datasets to import |
| Identity and provenance registries | DataCite, OpenAIRE, Crossref | resolve an identifier to its metadata; settle "the same dataset on two portals"; citations (O12) |
| Research and machine-learning hubs | Zenodo, Dataverse, Figshare, Dryad, Hugging Face Hub, Kaggle, OpenML | later adapters, each on a real request; the Hub is where the open-model sibling starts |
| Standards rather than sites | schema.org/Dataset landing-page markup, SDMX, OGC CSW, STAC | one generic adapter each, later; the landing-page adapter is the fallback for a dataset with a page and no portal |

Adapter order after CKAN: DCAT-AP second (data.europa.eu, data.gov's
DCAT), Socrata Discovery third, schema.org landing pages fourth.

### 11.2 The portal directory kind

A third source kind, `portal_directory` (on screen "Portal directory"),
registered like a portal (name, API kind, base URL) with built-in adapters
for dataportals.org and re3data. Its Run does not search datasets: it
proposes **portals to register**, as candidate rows (portal name, country
or domain, API kind as the directory states it, URL, dataset count where
stated, licence of the directory entry), each "not reported by this
directory" when absent. Preview, then import: an imported row becomes a
`open_data_portal` registration with the directory named as its origin and
"adapter: CKAN" or "no adapter for <API kind> yet · register for later" on
the row, so a portal RE cannot yet search is kept, visible and honest,
rather than dropped. A directory entry whose URL is already a registered
portal shows "already registered" and is not imported twice. The directory
itself is re-run on a cadence like a scout, and `external_change` over two
runs lists new and vanished portals.

### 11.3 DataCite as the identity resolver

DataCite joins OD1 as a built-in resolver, not a search target: given a
DOI (from a candidate row, a landing page, or typed by a person), it
returns the dataset's metadata (title, publisher, dates, subjects,
related identifiers, versions) with no key and at negligible cost. Three
uses, all read-first:

1. **Identity.** A candidate whose record carries a DOI is keyed by that
   DOI; two candidates with one DOI are one dataset on two portals (§3's
   duplicate collapse), and the DOI's registered publisher names the origin.
2. **Already held.** The identifier match before import (§3) checks the
   DOI against registered resources and Egeria's external identifiers.
3. **Provenance (O10) and citations (O12).** `external_provenance` reads
   related identifiers (IsVersionOf, IsDerivedFrom, IsSupplementTo) and
   the citation count where DataCite exposes it; "not reported by
   DataCite" otherwise.

A dataset with no DOI keeps the portal's identifier as its identity and
the row says "no DOI reported". OpenAIRE and Crossref follow the same
resolver interface later.

### 11.4 Slice changes

OD1 gains the `portal_directory` kind with the dataportals.org adapter and
the DataCite resolver inside the identifier match; its gate adds: run the
directory, import demo.ckan.org from it (or see "already registered" if
registered by hand first), and import one candidate carrying a DOI whose
DataCite record names the publisher. re3data's adapter rides OD2.
