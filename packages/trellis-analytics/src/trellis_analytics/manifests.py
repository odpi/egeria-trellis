"""The manifests this package ships; each is an entry point in group `pyegeria.analytics`, so installing
the package registers it and a report spec names it by its `name`."""
from trellis_analytics import __version__
from trellis_analytics.manifest import RoutineManifest

CURATION_COVERAGE = RoutineManifest(
    name="curation_coverage",
    function="trellis_analytics.routines.curation_coverage.curation_coverage",
    description="Published, not yet published and blocked, counted from the curation record (analysis 1 of Curate's By analysis).",
    returns="CoverageResult (counts with the row ids they count, plus an Envelope)",
    source="ods",
    version=__version__,
    envelope=True,
    resource_types=["database"],
    family="Curation",
    tags=["curation", "coverage"],
)

CONTEXT_PACK = RoutineManifest(
    name="context_pack",
    function="trellis_analytics.routines.context_pack.context_pack",
    description="Package what Egeria holds about one database for one kind of consumer: a tabular shape, a document shape and a manifest, each fact with its state word and provenance.",
    returns="ContextPack (tabular rows, documents, manifest, plus an Envelope)",
    source="egeria",
    version=__version__,
    envelope=True,
    resource_types=["database"],
    family="Context",
    tags=["context", "pack"],
)

# The Portal tile's function (`portal.curation_coverage_tile`), registered so its report spec names it
# `curation_coverage_tile` rather than by a Python path (Q2(b), project owner 2026-10-31).
CURATION_COVERAGE_TILE = RoutineManifest(
    name="curation_coverage_tile",
    function="trellis_analytics.portal.curation_coverage_tile",
    description="The Portal tile for curation_coverage: its counts reshaped as KPI rows and two bar values, with the "
                "result's state as the subtitle. Computes nothing of its own.",
    returns="dict (tile: state, subtitle, provenance, kpis, and Published / Not yet published when there are counts)",
    source="ods",
    version=__version__,
    envelope=False,
    resource_types=["database"],
    family="Curation",
    tags=["curation", "coverage", "portal"],
)
