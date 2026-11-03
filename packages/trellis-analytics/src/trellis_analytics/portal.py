"""Host 3 of curation_coverage: the Portal tile (brief 2b, 2d).

`curation_coverage_tile` is a thin adapter between the routine and pyegeria's `exec_report_spec`, which can only
hand an analytic function JSON parameters and only chart a flat {category: number} dict. It reshapes the routine's
`CoverageResult` and computes nothing of its own: every number, id, word, version and time below is read off the
result. `report_specs()` is the FormatSet for the tile. Installing the package registers it through the
`pyegeria.report_specs` entry point (pyegeria ISSUE-173); no `PYEGERIA_REPORT_SPEC_MODULES` setting is needed. The spec
names the tile's function by its registered name, `curation_coverage_tile` (Q2(b)).

When the state is `not_established` or `not_applicable` the dict carries no number at the top level, so a BAR
request cannot draw a chart of zeros. Since pyegeria 6.2.17 (#518) the executor returns kind "absent" for those
states: a sentence in `content`, no `data`, and this dict under `detail`.
"""
from __future__ import annotations

import os
import re

from trellis_analytics.readers import CATALOGUE_SCOPE, ODS, ReaderMissing, open_reader
from trellis_analytics.routines import curation_coverage
from trellis_analytics.routines.curation_coverage import no_reader

TILE_LABEL = "Trellis - Curation Coverage"
#: The tile function's registered name (manifests.CURATION_COVERAGE_TILE, installed through the pyegeria.analytics entry
#: point). A report spec names it by this, never by its Python path (Q2(b), project owner 2026-10-31).
TILE_FUNCTION = "curation_coverage_tile"
_CHARTED = ("measured", "nothing_found")     # the states that have counts to chart


def _family_slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", family.lower()).strip("_") or "unnamed"


def _kpis(result) -> list[dict]:
    """One KPI per line, keyed `<group>.<line>`. A line naming a family (in_other_family: one line per family) is keyed
    `<group>.<line>.<family slug>`, the same key whether one family or several hold schemas, so no two KPIs share a key."""
    out, seen = [], set()
    for g in result.groups:
        for l in g.lines:
            key = f"{g.key}.{l.key}" + (f".{_family_slug(l.family)}" if l.family else "")
            base, n = key, 2
            while key in seen:              # two family names that slug alike stay apart
                key, n = f"{base}_{n}", n + 1
            seen.add(key)
            out.append({"key": key, "group": g.key, "label": l.label, "value": l.count, "rows": l.rows,
                        "names": l.names})
    return out


def curation_coverage_tile(slug: str, ods_url: str = "", resource_type: str = "database") -> dict:
    """The tile for one resource: KPI rows, the two bar values, the envelope's state as subtitle, provenance."""
    url = ods_url or os.environ.get("REGISTRY_DATABASE_URL", "")
    try:
        # RE's catalogue first: without RE neither reader is here, and the catalogue is the absence worth naming.
        catalogue = open_reader(CATALOGUE_SCOPE, url)
        ods = open_reader(ODS, url)
    except ReaderMissing as exc:            # a reader is not installed here: said, never a chart of zeros
        result = no_reader(resource_type, slug, exc)
    else:
        result = curation_coverage(ods, resource_type, slug, catalogue)
    env = result.envelope
    state = env.result_state.value
    tile: dict = {
        "state": state,
        "state_reason": env.result_state_reason,
        "subtitle": env.result_state.words + (f" · {env.result_state_reason}" if env.result_state_reason else ""),
        "provenance": f"from trellis-analytics {env.producer_version} · curation coverage · as of {env.measured_at}",
        "manifest": {"function": TILE_FUNCTION, "routine": "trellis_analytics.routines.curation_coverage",
                     "version": env.producer_version, "as_of": env.measured_at, "read_back_at": result.as_of,
                     "scope": env.scope},
        "summary": result.summary,
        "groups": [{"key": g.key, "words": g.words} for g in result.groups],
        "kpis": _kpis(result),
    }
    if state in _CHARTED:
        published = result.count("published", "schemas")
        # every known schema is published, blocked or not yet published (the routine's own partition)
        tile["Published"] = published
        tile["Not yet published"] = result.known_schemas - published - result.count("blocked", "blocked")
    return tile


def report_specs():
    """The tile's FormatSet, keyed by its label: the loader the `pyegeria.report_specs` entry point names."""
    from pyegeria.view._output_format_models import ActionParameter, Format, FormatSet, FormatSetDict
    return FormatSetDict({TILE_LABEL: FormatSet(
        heading="Curation Coverage",
        description="Published against not yet published, from the curation record (trellis-analytics "
                    "curation_coverage). The subtitle is the result's state; counts carry the ids they count.",
        family="Curation",
        target_type=None,
        formats=[Format(types=["DICT", "JSON", "BAR"], attributes=[])],
        action=ActionParameter(analytic_function=TILE_FUNCTION,      # no action function (pyegeria 6.2.2, ISSUE-141)
                               analytic_spec_params={"resource_type": "database"}),
    )})
