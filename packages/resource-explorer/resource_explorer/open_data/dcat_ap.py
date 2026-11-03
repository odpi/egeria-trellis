"""DCAT-AP catalog feeds: JSON-LD (`catalog.jsonld`, ckanext-dcat and most EU portals) and Project Open Data `data.json`.

Read-only. Recorded 2026-10-31 (fixtures in tests/fixtures/open_data/, README beside them).

DCAT-AP defines no search call. A feed is a document you page through, so `search` here is Resource Explorer
filtering the pages it fetched, and the manifest's `cost_note` says so on the price line.

Two shapes, told apart by the feed URL and then by the document itself (never by the portal's name):
  jsonld  a `dcat:Catalog` as JSON-LD, expanded (full IRIs, arrays; data.gov.ie answers a flattened graph, a top-level
          array of nodes that point at each other by `@id`) or compact (prefixes, single values; a `@graph`).
          Paged with `?page=N` where it states hydra paging; a document that does not is the only page.
  pod     Project Open Data: a plain JSON catalog with a `dataset` array and bare key names (`title`, `license`).
Both go through the same reader: a property is looked up under its prefixed key, its expanded IRI and its bare name.

Rows carry the entry verbatim under `record`. RE's own annotations to a record are underscore-prefixed keys
(`_language`, `_licence_note`, `_filter_note`, `_access_only`); strip them to get the source's entry back.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from resource_explorer.open_data.absence import NOT_REPORTED, reported_or_absent
from resource_explorer.open_data.adapters import Adapter, AdapterManifest, page_of
from resource_explorer.open_data.ckan import normalise_doi
from resource_explorer.open_data.licences import licence_flags, licence_id_from_iri
from resource_explorer.open_data.transport import Transport, TransportError

LD_ACCEPT = {"Accept": "application/ld+json, application/json;q=0.9"}
FEED_PATH = "catalog.jsonld"
POD_PATH = "data.json"
DEFAULT_PAGE_SIZE = 100

_NS = {
    "dct": ("http://purl.org/dc/terms/", "dcterms:", "dc:"),
    "dcat": ("http://www.w3.org/ns/dcat#",),
    "foaf": ("http://xmlns.com/foaf/0.1/",),
    "adms": ("http://www.w3.org/ns/adms#",),
    "rdf": ("http://www.w3.org/1999/02/22-rdf-syntax-ns#",),
    "rdfs": ("http://www.w3.org/2000/01/rdf-schema#",),
    "skos": ("http://www.w3.org/2004/02/skos/core#",),
    "vcard": ("http://www.w3.org/2006/vcard/ns#",),
    "hydra": ("http://www.w3.org/ns/hydra/core#",),
}


class ProbeRefused(TransportError):
    """No URL tried answered as a DCAT catalog."""


# ── reading JSON-LD without a JSON-LD processor ──────────────────────────────

def _keys(ns: str, local: str) -> list[str]:
    spellings = _NS[ns]
    return [f"{ns}:{local}", *(f"{p}{local}" for p in spellings if p.endswith(":")),
            *(f"{p}{local}" for p in spellings if not p.endswith(":")), local]


def _vals(node: Any, ns: str, local: str) -> list:
    """The values of a property as a list, whichever spelling the document uses; [] when it has none."""
    if not isinstance(node, dict):
        return []
    for key in _keys(ns, local):
        if key in node:
            v = node[key]
            return list(v) if isinstance(v, list) else [v]
    return []


def _is_type(node: Any, *names: str) -> bool:
    if not isinstance(node, dict):
        return False
    types = node.get("@type", node.get("type", []))
    types = types if isinstance(types, list) else [types]
    for t in types:
        if isinstance(t, str) and re.split(r"[#:/]", t)[-1] in names:
            return True
    return False


def _walk(doc: Any, found: dict) -> None:
    if isinstance(doc, dict):
        ident = doc.get("@id") or doc.get("id")
        if isinstance(ident, str) and (len(doc) > 1):
            found.setdefault(ident, doc)
        for v in doc.values():
            if isinstance(v, (dict, list)):
                _walk(v, found)
    elif isinstance(doc, list):
        for v in doc:
            _walk(v, found)


class _Doc:
    """One fetched document: its nodes by `@id`, and the datasets and paging statement it holds."""

    def __init__(self, body: Any):
        self.body = body
        self.index: dict[str, dict] = {}
        _walk(body, self.index)
        self.nodes = self._top_nodes(body)
        self.pod_datasets = body.get("dataset") if isinstance(body, dict) and isinstance(body.get("dataset"), list) else None
        self.datasets: list[dict] = (
            [d for d in self.pod_datasets if isinstance(d, dict)] if self.pod_datasets is not None
            else [n for n in self._all_nodes() if _is_type(n, "Dataset")])
        self.paging = next((n for n in self._all_nodes() if _vals(n, "hydra", "totalItems")
                            or _is_type(n, "PagedCollection")), None)

    @staticmethod
    def _top_nodes(body: Any) -> list:
        if isinstance(body, list):
            return body
        if isinstance(body, dict) and isinstance(body.get("@graph"), list):
            return body["@graph"]
        return [body]

    def _all_nodes(self):
        seen = set()
        for n in [*self.nodes, *self.index.values()]:
            if isinstance(n, dict) and id(n) not in seen:
                seen.add(id(n))
                yield n

    def resolve(self, value: Any) -> Any:
        """A `{"@id": x}` reference replaced by the node it names when the document holds it."""
        if isinstance(value, dict) and set(value) <= {"@id", "id"}:
            ref = value.get("@id") or value.get("id")
            return self.index.get(ref, value)
        if isinstance(value, str) and value in self.index:       # compact JSON-LD with "@type": "@id" coercion
            return self.index[value]
        return value

    def stated_int(self, node: Any, ns: str, local: str) -> int | None:
        for v in _vals(node, ns, local):
            n = _scalar(v)
            try:
                return int(float(n))
            except (TypeError, ValueError, OverflowError):
                continue
        return None

    @property
    def total(self) -> Any:
        n = self.stated_int(self.paging, "hydra", "totalItems") if self.paging else None
        return n if n is not None else NOT_REPORTED

    @property
    def page_size(self) -> int | None:
        return self.stated_int(self.paging, "hydra", "itemsPerPage") if self.paging else None

    @property
    def has_next(self) -> bool:
        return bool(self.paging and (_vals(self.paging, "hydra", "nextPage") or _vals(self.paging, "hydra", "next")))

    @property
    def is_catalog(self) -> bool:
        if self.pod_datasets is not None:
            return bool(isinstance(self.body, dict) and (self.body.get("conformsTo") or _is_type(self.body, "Catalog")))
        return any(_is_type(n, "Catalog") for n in self._all_nodes())


def _scalar(value: Any) -> Any:
    """A literal's value: `{"@value": x}` -> x; a plain string/number as is; a node reference -> None."""
    if isinstance(value, dict):
        return value.get("@value")
    return value


def _text(value: Any) -> str | None:
    v = _scalar(value)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _iri(value: Any) -> str | None:
    """The IRI a value names: a reference's `@id`, or a plain http(s) string."""
    if isinstance(value, dict):
        v = value.get("@id") or value.get("id")
        return v if isinstance(v, str) and not v.startswith("_:") else None
    if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
        return value.strip()
    return None


def _candidates(values: list) -> list[tuple[str, str]]:
    """(text, language) for every string a property states, including a compact language map ({"en": "..", "de": ".."})."""
    out = []
    for v in values:
        if isinstance(v, dict) and "@value" not in v and "@id" not in v and "id" not in v and v:
            for lang, text in v.items():
                for t in (text if isinstance(text, list) else [text]):
                    if isinstance(t, str) and t.strip():
                        out.append((t.strip(), str(lang)))
            continue
        t = _text(v)
        if t:
            out.append((t, str(v.get("@language", "")) if isinstance(v, dict) else ""))
    return out


def _lang_text(values: list) -> tuple[str | None, str]:
    """The text of a language map: prefer English, else the first stated; with the language chosen ('' = untagged)."""
    cands = _candidates(values)
    for t, lang in cands:
        if lang.lower() == "en" or lang.lower().startswith("en-"):
            return t, lang
    return cands[0] if cands else (None, "")


def _short_format(raw: str) -> str:
    """'text/csv' -> 'CSV'; an IANA or EU file-type IRI -> its last segment; anything else upper-cased as stated."""
    text = raw.strip().split(";")[0]
    if text.lower().startswith(("http://", "https://")):
        text = text.rstrip("/").rsplit("/", 1)[-1]
    elif "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.upper()


def _bytes(values: list) -> int | None:
    total, seen = 0, False
    for v in values:
        try:
            n = int(float(_scalar(v)))
        except (TypeError, ValueError, OverflowError):
            continue
        if n >= 0:
            total, seen = total + n, True
    return total if seen else None


# ── the adapter ──────────────────────────────────────────────────────────────

class DcatApAdapter(Adapter):
    manifest = AdapterManifest(
        name="dcat_ap", api_kind="DCAT-AP",
        api_kind_aliases=("DCAT", "DCAT-AP 2", "DCAT-AP 3", "data.json", "Project Open Data"), role="portal",
        registration_fields=("name", "base_url"), calls_per_page=1, page_size=DEFAULT_PAGE_SIZE, needs_key=False,
        cost_note="this feed has no search; Resource Explorer reads {pages} and keeps the rows that match")

    def __init__(self, base_url: str, transport: Transport, api_key: str = ""):
        super().__init__(base_url, transport, api_key)
        self.calls = 0                                  # calls actually made by this adapter
        self._mode: str | None = None                   # "paged" (hydra) or "single" (one document), learned from the first read
        self._single: _Doc | None = None
        self._pages: dict[int, _Doc] = {}
        self._seen: dict[str, tuple[dict, _Doc]] = {}   # dataset_id -> (entry, the document it came from)

    # -- where things are ----------------------------------------------------

    @property
    def _path(self) -> str:
        return urlparse(self.base_url).path.rstrip("/").lower()

    @property
    def _paged_url(self) -> bool:
        """ckanext-dcat's `catalog.jsonld` is known to take `?page=N`; any other URL is read as given until its
        first document says (hydra) that it pages."""
        return self._path.endswith(FEED_PATH)

    @property
    def _site(self) -> str:
        """The portal's own base (the feed URL without its `catalog.jsonld` / `data.json` tail)."""
        for tail in (FEED_PATH, POD_PATH):
            if self._path.endswith(tail):
                return self.base_url[: self.base_url.lower().rindex(tail)].rstrip("/")
        return self.base_url

    def _get(self, url: str, params: dict | None = None) -> _Doc:
        self.calls += 1
        body = self.transport.get_json(url, params=params, headers=dict(LD_ACCEPT))
        if not isinstance(body, (dict, list)):
            raise TransportError(f"{url}: the answer was not a catalog document")
        return _Doc(body)

    def _page_doc(self, page: int) -> tuple[_Doc, bool]:
        """(the document for `page`, whether it is a single document paged locally)."""
        page = max(page, 1)
        if self._mode is None and page > 1:
            self._page_doc(1)                           # learn from the first document whether the feed pages
        if self._mode == "single":
            return self._single, True
        if page in self._pages:
            return self._pages[page], False
        use_param = self._paged_url or self._mode == "paged"
        doc = self._get(self.base_url, {"page": page} if use_param else None)
        if self._mode is None:
            if doc.paging is not None and (doc.has_next or doc.total != NOT_REPORTED):
                self._mode = "paged"
            else:
                self._mode, self._single = "single", doc
                return doc, True
        if doc.page_size:
            self.page_size = doc.page_size
        self._pages[page] = doc
        return doc, False

    # -- probe ---------------------------------------------------------------

    @classmethod
    def probe(cls, base_url: str, transport: Transport) -> dict:
        base = (base_url or "").strip().rstrip("/")
        tried = [f"{base}/{FEED_PATH}?page=1", f"{base}/{POD_PATH}", base]
        other: list[str] = []                            # content types named by answers that were not JSON
        for url in tried:
            try:
                doc = _Doc(transport.get_json(url, params=None, headers=dict(LD_ACCEPT)))
            except (TransportError, ValueError) as exc:
                m = re.search(r"content-type ([\w.+/-]+)", str(exc))
                if m and m.group(1) not in other:
                    other.append(m.group(1))
                continue
            if not doc.is_catalog:
                continue
            if url.endswith("?page=1"):
                feed, shape = f"{base}/{FEED_PATH}", "jsonld"
            elif url == f"{base}/{POD_PATH}":
                feed, shape = url, "pod"
            else:
                feed, shape = base, "pod" if doc.pod_datasets is not None else "jsonld"
            size = doc.page_size if shape == "jsonld" else DEFAULT_PAGE_SIZE
            total = doc.total if shape == "jsonld" else len(doc.pod_datasets or [])
            return {"feed_url": feed, "shape": shape, "page_size": size if size else NOT_REPORTED, "total": total}
        named = [c for c in other if any(k in c for k in ("xml", "turtle", "n3", "n-triples", "rdf"))]
        why = f"; it answered {named[0]}, which has no adapter yet" if named else ""
        raise ProbeRefused(f"Nothing at {tried[0]}, {tried[1]} or {tried[2]} answered as a DCAT catalog "
                           f"(JSON-LD or Project Open Data){why}.")

    # -- normalising ----------------------------------------------------------

    def _distribution_nodes(self, entry: dict, doc: _Doc) -> list[dict]:
        out = []
        for v in _vals(entry, "dcat", "distribution"):
            node = doc.resolve(v)
            if isinstance(node, dict):
                out.append(node)
        return out

    def _publisher(self, entry: dict, doc: _Doc) -> str:
        for v in _vals(entry, "dct", "publisher"):
            node = doc.resolve(v)
            if isinstance(node, dict) and not set(node) <= {"@id", "id"}:
                for ns, local in (("foaf", "name"), ("vcard", "fn"), ("rdfs", "label"), ("dct", "title"),
                                  ("skos", "prefLabel")):
                    name, _ = _lang_text(_vals(node, ns, local))
                    if name:
                        return name
            elif isinstance(node, str) and node.strip() and _iri(node) is None:
                return node.strip()
        return NOT_REPORTED            # a bare IRI is not a name

    def _licence(self, entry: dict, doc: _Doc, dists: list[dict]) -> tuple[Any, str]:
        def stated(node: dict) -> str | None:
            for v in _vals(node, "dct", "license"):
                got = _iri(doc.resolve(v)) or _iri(v) or _text(v)
                if got:
                    return got
            return None

        own = stated(entry)
        if own:
            return own, ""
        per_dist = [stated(d) for d in dists]
        distinct = {x for x in per_dist if x}
        if len(distinct) > 1:
            return NOT_REPORTED, "distributions disagree"
        if distinct and not all(per_dist):
            return NOT_REPORTED, "only some distributions state a license"
        if distinct:
            return distinct.pop(), ""
        return NOT_REPORTED, ""

    @staticmethod
    def _label(node: Any) -> str | None:
        """The text a format value names: a literal, a node's rdf:value / rdfs:label, or the IRI's last segment."""
        if not isinstance(node, dict) or "@value" in node:
            return _text(node)
        return _text(_first(_vals(node, "rdf", "value"))) or _text(_first(_vals(node, "rdfs", "label"))) or _iri(node)

    def _format_set(self, dist: dict, doc: _Doc) -> list[str]:
        """A distribution's format(s): `dct:format`, else `dcat:mediaType` (one statement of it, not both)."""
        raws = [self._label(doc.resolve(v)) for v in _vals(dist, "dct", "format")]
        if not any(raws):
            raws = [_text(v) or _iri(v) for v in _vals(dist, "dcat", "mediaType")]
        return sorted({_short_format(r) for r in raws if r})

    def _formats(self, dists: list[dict], doc: _Doc) -> Any:
        found = {f for d in dists for f in self._format_set(d, doc)}
        return sorted(found) if found else NOT_REPORTED

    def normalise(self, entry: dict, doc: _Doc | None = None) -> dict:
        doc = doc or _Doc([entry])
        record = dict(entry)
        ident = entry.get("@id") or entry.get("id")
        ident = ident if isinstance(ident, str) and not ident.startswith("_:") else None
        ids = [t for t in (_text(v) for v in _vals(entry, "dct", "identifier")) if t]
        dist_nodes = self._distribution_nodes(entry, doc)
        adms = []
        for v in _vals(entry, "adms", "identifier"):
            node = doc.resolve(v)
            adms += [t for t in ([_text(node)] if not isinstance(node, dict) or "@value" in node
                                 else [_text(x) for x in _vals(node, "skos", "notation")]) if t]
        doi = next((d for d in (normalise_doi(c) for c in [*ids, *adms, ident]) if d != NOT_REPORTED), NOT_REPORTED)
        dataset_id = ids[0] if ids else (ident or NOT_REPORTED)     # a blank node has no id to name it by
        if doi != NOT_REPORTED:
            identifier, kind = doi, "doi"
        elif ids:
            identifier, kind = ids[0], "dcat_identifier"
        else:
            identifier, kind = dataset_id, "portal_id"
        title, lang = _lang_text(_vals(entry, "dct", "title"))
        if title:
            record["_language"] = lang or "not stated"
        licence_raw, note = self._licence(entry, doc, dist_nodes)
        if note:
            record["_licence_note"] = note
        licence_id = licence_id_from_iri(licence_raw) if licence_raw != NOT_REPORTED else NOT_REPORTED
        modified = next((t for t in (_text(v) for ns_local in (("dct", "modified"), ("dct", "issued"))
                                     for v in _vals(entry, *ns_local)) if t), None)
        sizes = [_bytes(_vals(d, "dcat", "byteSize")) for d in dist_nodes]
        size = _bytes([v for d in dist_nodes for v in _vals(d, "dcat", "byteSize")])
        if size is not None and None in sizes:
            record["_size_note"] = "only some distributions state a size: this is a partial sum"
        landing = next((u for u in (_iri(doc.resolve(v)) or _iri(v) or _text(v)
                                    for v in _vals(entry, "dcat", "landingPage")) if u), None)
        return {
            "dataset_id": dataset_id,
            "identifier": identifier,
            "identifier_kind": kind,
            "doi": doi,
            "title": title or NOT_REPORTED,
            "publisher": self._publisher(entry, doc),
            "licence_id": licence_id,
            "licence": licence_flags(None if licence_id == NOT_REPORTED else licence_id),
            "modified": modified or NOT_REPORTED,
            "formats": self._formats(dist_nodes, doc),
            "size": size if size is not None else NOT_REPORTED,
            "url": landing or (ident if ident and ident.lower().startswith(("http://", "https://")) else NOT_REPORTED),
            "record": record,
        }

    # -- search ---------------------------------------------------------------

    @staticmethod
    def _words(text: str) -> set[str]:
        return set(re.findall(r"\w+", text.lower()))

    def _matches(self, entry: dict, terms: list[str]) -> bool:
        if not terms:
            return True
        parts = [v for ns_local in (("dct", "title"), ("dct", "description"), ("dcat", "keyword"))
                 for v in _vals(entry, *ns_local)]
        have = self._words(" ".join(t for t, _ in _candidates(parts)))      # every language of a language map
        return all(t in have for t in terms)

    def search(self, terms: str, filters: dict | None, page: int) -> dict:
        """One page of the feed, filtered here. `read` is how many entries the page held before the filters: a page
        that filters to nothing was still read, and the caller must go on to the next one."""
        filters = filters or {}
        page = max(page, 1)
        before = self.calls
        doc, local = self._page_doc(page)
        entries = doc.datasets
        if local:
            entries = entries[(page - 1) * self.page_size: page * self.page_size]
        wanted = self._words(terms or "")
        wanted_format = (filters.get("format") or "").strip().upper()
        after = (filters.get("modified_after") or "").strip()
        rows = []
        for entry in entries:
            if not self._matches(entry, sorted(wanted)):
                continue
            row = self.normalise(entry, doc)
            if row["dataset_id"] != NOT_REPORTED:
                self._seen.setdefault(row["dataset_id"], (entry, doc))
            if wanted_format:
                if row["formats"] == NOT_REPORTED:
                    row["record"]["_filter_note"] = "format not reported"
                elif wanted_format not in row["formats"]:
                    continue
            if after:
                mod = row["modified"]
                if mod == NOT_REPORTED:
                    row["record"]["_filter_note"] = "modified not reported"
                elif not re.match(r"\d{4}-\d{2}-\d{2}", mod):
                    row["record"]["_filter_note"] = "modified not a date"
                elif mod[:10] < after:
                    continue
            rows.append(row)
        # a POD document knows its own count; a hydra feed states one or it is not reported
        total = len(doc.datasets) if local and doc.pod_datasets is not None else doc.total
        got = page_of(rows, page, self.page_size, total)
        got["read"] = len(entries)
        got["calls"] = self.calls - before
        return got

    # -- one dataset ----------------------------------------------------------

    @staticmethod
    def _same(row: dict, wanted: str) -> bool:
        w = wanted.strip().lower()
        return w in {str(row["dataset_id"]).lower(), str(row["identifier"]).lower(), str(row["doi"]).lower()} \
            or (normalise_doi(wanted) != NOT_REPORTED and normalise_doi(wanted) == row["doi"])

    def _entry(self, dataset_id: str) -> tuple[dict, _Doc]:
        """The entry for a dataset id, a DOI, or the identifier a row was stored under."""
        if dataset_id in self._seen:
            return self._seen[dataset_id]
        if self._paged_url and dataset_id and "/" not in dataset_id and normalise_doi(dataset_id) == NOT_REPORTED:
            try:
                doc = self._get(f"{self._site}/dataset/{dataset_id}.jsonld")
                for entry in doc.datasets:
                    return entry, doc
            except (TransportError, ValueError):
                pass                                  # fall through to the feed, which is what the brief says
        # The document read so far (all of a POD / single document; the pages read for a hydra feed, or its first).
        docs = [self._single] if self._mode == "single" and self._single else list(self._pages.values())
        if not docs:
            docs = [self._page_doc(1)[0]]
        for doc in docs:
            for entry in doc.datasets:
                if self._same(self.normalise(entry, doc), dataset_id):
                    return entry, doc
        raise TransportError(f"{self.base_url}: no dataset {dataset_id!r} in the document read")

    def describe(self, dataset_id: str) -> dict:
        entry, doc = self._entry(dataset_id)
        return self.normalise(entry, doc)

    def distributions(self, dataset_id: str) -> list[dict]:
        entry, doc = self._entry(dataset_id)
        out = []
        for d in self._distribution_nodes(entry, doc):
            record = dict(d)
            download = next((u for u in (_iri(doc.resolve(v)) or _iri(v) or _text(v)
                                         for v in _vals(d, "dcat", "downloadURL")) if u), None)
            access = next((u for u in (_iri(doc.resolve(v)) or _iri(v) or _text(v)
                                       for v in _vals(d, "dcat", "accessURL")) if u), None)
            if download is None and access is not None:
                record["_access_only"] = True
            title, _ = _lang_text(_vals(d, "dct", "title"))
            ident = d.get("@id") or d.get("id")
            out.append({
                "id": ident if isinstance(ident, str) and not ident.startswith("_:") else NOT_REPORTED,
                "name": title or NOT_REPORTED,
                "format": ", ".join(self._format_set(d, doc)) or NOT_REPORTED,
                "url": download or access or NOT_REPORTED,
                "size": reported_or_absent(_bytes(_vals(d, "dcat", "byteSize"))),
                "record": record,
            })
        return out


def _first(values: list) -> Any:
    return values[0] if values else None
