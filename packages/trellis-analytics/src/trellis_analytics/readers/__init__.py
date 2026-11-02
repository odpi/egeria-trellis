"""The reader seam (LS2, brief §2): what a routine or step may ask to read, owned here; who answers it, installed.

The library defines the reads (`OdsRead`, a Protocol: the typed reads of RE's registry a step may make) and finds
implementations through the entry-point group `trellis_analytics.readers`. Resource Explorer registers `ods`. A routine
asks for a reader by name with `open_reader`; when nothing installed provides it, `ReaderMissing` says so in words a
result can carry ("no reader installed for ods here"), and the routine answers `not_established` instead of importing
RE. That is what lets the Portal install the library without RE.

Nothing here imports an implementation: importing this module never pulls in RE, psycopg2 or a registry.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Protocol, runtime_checkable

GROUP = "trellis_analytics.readers"
#: The name Resource Explorer registers its registry reader under.
ODS = "ods"


class OdsUnreadable(Exception):
    """The registry could not be read; the cause is the message."""


class ResourceExplorerMissing(ImportError):
    """resource_explorer is not installed (install trellis-analytics[re])."""


class ReaderMissing(LookupError):
    """No installed package provides the named reader. The message is the sentence a result carries."""

    def __init__(self, name: str, detail: str = ""):
        self.name = name
        super().__init__(f"no reader installed for {name} here" + (f" ({detail})" if detail else ""))


@runtime_checkable
class OdsRead(Protocol):
    """The typed reads of RE's registry: each a fixed, parameterised SELECT the implementation owns. A step is handed
    these and nothing else (RE's host narrows `inputs.ods` to them); no SQL text crosses this seam. The shapes are the
    implementation's docstrings (RE's `OdsReader`)."""

    def commit_proofs(self, slug: str) -> list[dict]: ...
    def catalogue_scope_events(self, slug: str) -> list[dict]: ...
    def resource_scope_events(self, resource_type: str, slug: str) -> list[dict]: ...
    def outbox_status_counts(self, entity_type: str, slug: str) -> dict[str, list[int]]: ...
    def row_count_snapshots(self, slug: str, limit: int = 2) -> list[dict]: ...
    def table_activity_snapshots(self, slug: str, limit: int = 2) -> list[dict]: ...
    def table_keys(self, slug: str) -> dict | None: ...
    def schema_inventory(self, slug: str) -> dict: ...
    def filesystem_walk_runs(self, slug: str, limit: int = 2) -> list[dict]: ...
    def filesystem_walk_files(self, slug: str, run_id: str) -> list[dict]: ...
    def filesystem_walk_not_walked(self, slug: str, run_id: str) -> dict | None: ...
    def filesystem_hash_matches(self, slug: str, run_id: str) -> dict: ...


#: The typed reads, by name, in the Protocol's order.
ODS_READS = tuple(n for n in OdsRead.__dict__ if not n.startswith("_"))


def reader_factory(name: str):
    """The installed implementation registered as `name` (a class or callable that builds a reader). Raises
    ReaderMissing when no entry point names it, or when the one that does cannot be loaded (its sentence says which)."""
    for ep in entry_points(group=GROUP):
        if ep.name == name:
            try:
                return ep.load()
            except Exception as exc:  # noqa: BLE001 - said as the reason the reader is missing, never skipped silently
                raise ReaderMissing(name, f"{ep.value} failed to load: {type(exc).__name__}: {exc}") from exc
    raise ReaderMissing(name)


def open_reader(name: str, *args: Any, **kwargs: Any):
    """A reader built by the implementation registered as `name`. Raises ReaderMissing (see `reader_factory`)."""
    return reader_factory(name)(*args, **kwargs)


def installed_readers() -> dict[str, str]:
    """name -> entry point value, for every reader installed here."""
    return {ep.name: ep.value for ep in entry_points(group=GROUP)}
