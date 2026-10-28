"""Host 3 of curation_coverage: the Portal tile (brief 2b, 2d).

`curation_coverage_tile` is a thin adapter between the routine and pyegeria's `exec_report_spec`, which can only
hand an analytic function JSON parameters and only chart a flat {category: number} dict. It reshapes the routine's
`CoverageResult` and computes nothing of its own: every number, id, word, version and time below is read off the
result. `report_specs()` is the FormatSet for the tile, loadable through `PYEGERIA_REPORT_SPEC_MODULES`
("trellis_analytics.portal:report_specs").

When the state is `not_established` or `not_applicable` the dict carries no number at the top level, so a BAR
request cannot draw a chart of zeros: pyegeria's executor falls back to returning the dict, which says the state word.
"""
from __future__ import annotations

import os

from trellis_analytics.readers.ods import OdsReader
from trellis_analytics.routines import curation_coverage

TILE_LABEL = "Trellis - Curation Coverage"
TILE_FUNCTION = "trellis_analytics.portal.curation_coverage_tile"
_CHARTED = ("measured", "nothing_found")     # the states that have counts to chart


def curation_coverage_tile(slug: str, ods_url: str = "", resource_type: str = "database") -> dict:
    """The tile for one resource: KPI rows, the two bar values, the envelope's state as subtitle, provenance."""
    result = curation_coverage(OdsReader(ods_url or os.environ.get("REGISTRY_DATABASE_URL", "")), resource_type, slug)
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
        "kpis": [{"key": f"{g.key}.{l.key}", "group": g.key, "label": l.label, "value": l.count,
                  "rows": l.rows, "names": l.names} for g in result.groups for l in g.lines],
    }
    if state in _CHARTED:
        published = result.count("published", "schemas")
        # every known schema is published, blocked or not yet published (the routine's own partition)
        tile["Published"] = published
        tile["Not yet published"] = result.known_schemas - published - result.count("blocked", "blocked")
    return tile


def report_specs():
    """The tile's FormatSet, keyed by its label: the loader callable `PYEGERIA_REPORT_SPEC_MODULES` names."""
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
