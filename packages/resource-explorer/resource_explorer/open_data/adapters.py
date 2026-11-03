"""The adapter plugin point for open data (entry point group `trellis.open_data`).

Same shape as the Analytic Library's (`pyegeria.analytics`) and DESIGN-FILES-REMOTE-REACH §3's: a package ships an
adapter class carrying a `manifest`, and installing it registers it; RE changes nothing. The built-ins in this
tree (`ckan`, `dcat_ap`, `zenodo`, `dataportals`, `datacite`) are always present, and an entry point of the same name replaces one, so
a venv installed before this slice (whose entry-point metadata has not been regenerated) still has them.

A manifest says what the adapter speaks and what it costs, because the price line is shown BEFORE the press:
`calls_per_page` and `page_size` are required for anything that pages, and a manifest without them is refused at
load, not defaulted.

The interface, by role:
  portal     search(terms, filters, page) -> Page;  describe(dataset_id) -> row;  distributions(dataset_id) -> rows
  directory  list_portals(page) -> Page of portal rows
  resolver   resolve(identifier) -> record
Every row carries the source's own record VERBATIM under `record`, beside the normalised fields, and a field the
source does not give is `NOT_REPORTED` (absence.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, ClassVar

from resource_explorer.open_data.transport import Transport

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "trellis.open_data"
ROLES = ("portal", "directory", "resolver")

#: Entry points the last discovery skipped, with the reason (read through `adapter_load_failures`).
LOAD_FAILURES: dict[str, str] = {}


class ManifestError(ValueError):
    """An adapter manifest that cannot be priced or registered."""


@dataclass(frozen=True)
class AdapterManifest:
    name: str                              # the adapter's id, e.g. "ckan"
    api_kind: str                          # what it speaks, as a portal record states it, e.g. "CKAN"
    role: str = "portal"
    #: The fields a registration of a portal/directory needs beyond name and base URL.
    registration_fields: tuple[str, ...] = ()
    #: Cost shape. Required for every role: a search is priced as pages x calls_per_page before it runs.
    calls_per_page: int | None = None
    page_size: int | None = None
    needs_key: bool = False
    #: Other spellings of the api_kind a directory may state ("CKAN API"); matched case-insensitively.
    api_kind_aliases: tuple[str, ...] = ()
    #: One sentence the price line and the portal's cost words carry when the source cannot do what the person
    #: might assume (a DCAT feed has no search call). `{pages}` is replaced by "N page(s)". Empty = nothing to add.
    cost_note: str = ""

    def validate(self) -> "AdapterManifest":
        if not self.name or not self.api_kind:
            raise ManifestError("an adapter manifest needs a name and the api_kind it speaks")
        if self.role not in ROLES:
            raise ManifestError(f"adapter {self.name!r}: role must be one of {ROLES}, not {self.role!r}")
        for field_name in ("calls_per_page", "page_size"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ManifestError(
                    f"adapter {self.name!r} declares no cost shape ({field_name} is {value!r}): "
                    f"the price is shown before a search runs, so calls_per_page and page_size are required")
        return self

    def speaks(self, api_kind: str) -> bool:
        wanted = (api_kind or "").strip().lower()
        return bool(wanted) and wanted in {self.api_kind.lower(), *(a.lower() for a in self.api_kind_aliases)}


class Adapter:
    """Base for adapters. `manifest` is the class-level declaration; an instance is bound to one source."""

    manifest: ClassVar[AdapterManifest]

    def __init__(self, base_url: str, transport: Transport, api_key: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.transport = transport
        self.api_key = api_key
        #: The page size this source is read at: the registered row's value when the caller sets it, else the manifest's.
        self.page_size = self.manifest.page_size

    @classmethod
    def probe(cls, base_url: str, transport: Transport) -> dict | None:
        """At registration: look at what `base_url` answers and return what to store (`feed_url`, `page_size`).
        None (the default) = nothing to probe; the URL as typed is the base URL."""
        return None

    # portal
    def search(self, terms: str, filters: dict | None, page: int) -> dict:
        raise NotImplementedError

    def describe(self, dataset_id: str) -> dict:
        raise NotImplementedError

    def distributions(self, dataset_id: str) -> list[dict]:
        raise NotImplementedError

    # directory
    def list_portals(self, page: int) -> dict:
        raise NotImplementedError

    # resolver
    def resolve(self, identifier: str) -> dict:
        raise NotImplementedError


def _builtins() -> list[type[Adapter]]:
    from resource_explorer.open_data.ckan import CkanAdapter
    from resource_explorer.open_data.datacite import DataCiteResolver
    from resource_explorer.open_data.dataportals import DataPortalsDirectory
    from resource_explorer.open_data.re3data import Re3dataDirectory

    from resource_explorer.open_data.dcat_ap import DcatApAdapter
    from resource_explorer.open_data.zenodo import ZenodoAdapter

    return [CkanAdapter, DcatApAdapter, ZenodoAdapter, DataPortalsDirectory, DataCiteResolver, Re3dataDirectory]


def _entry_points():
    return metadata.entry_points(group=ENTRY_POINT_GROUP)


def discover_adapters() -> dict[str, type[Adapter]]:
    """name -> adapter class: the built-ins, then every installed entry point (which wins on a name clash).
    An entry point that fails to load, or whose manifest is refused, is skipped: one bad package must not take the
    others down. It is not silent: the failure is kept in `adapter_load_failures()` (listed by GET /api/open-data/adapters) and
    logged."""
    found: dict[str, type[Adapter]] = {}
    LOAD_FAILURES.clear()
    for cls in _builtins():
        found[cls.manifest.validate().name] = cls
    for ep in _entry_points():
        try:
            cls = ep.load()
            manifest = getattr(cls, "manifest", None)
            if not isinstance(manifest, AdapterManifest):
                raise ManifestError("no AdapterManifest on the adapter")
            found[manifest.validate().name] = cls
        except (ImportError, AttributeError, TypeError, ManifestError) as exc:
            LOAD_FAILURES[ep.name] = f"{type(exc).__name__}: {exc}"[:240]
            log.warning("open data: adapter entry point %r skipped: %s", ep.name, exc)
    return found


def adapter_load_failures() -> dict[str, str]:
    """entry point name -> why it was skipped, as of the last `discover_adapters()`."""
    return dict(LOAD_FAILURES)


def adapter_for_api_kind(api_kind: str, role: str | None = None) -> type[Adapter] | None:
    """The adapter that speaks `api_kind` (optionally of one role), else None: "no adapter yet"."""
    for cls in discover_adapters().values():
        if (role is None or cls.manifest.role == role) and cls.manifest.speaks(api_kind):
            return cls
    return None


@dataclass
class Price:
    pages: int
    calls_per_page: int
    page_size: int
    calls: int = field(init=False)

    def __post_init__(self):
        self.calls = self.pages * self.calls_per_page

    @property
    def line(self) -> str:
        return f"{self.pages} page{'s' if self.pages != 1 else ''} × {self.calls_per_page} call" \
               f"{'s' if self.calls_per_page != 1 else ''} = {self.calls} call{'s' if self.calls != 1 else ''}"


def price(manifest: AdapterManifest, pages: int) -> Price:
    """The call count of `pages` pages, from the manifest's cost shape alone."""
    manifest.validate()
    if pages < 1:
        raise ValueError("a search is at least one page")
    return Price(pages=pages, calls_per_page=manifest.calls_per_page, page_size=manifest.page_size)


def page_of(rows: list[dict], page: int, page_size: int, total: Any) -> dict:
    return {"rows": rows, "page": page, "page_size": page_size, "total": total}
