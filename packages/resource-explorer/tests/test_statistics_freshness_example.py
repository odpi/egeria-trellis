"""F17: the example step's database summary must not read as all current when no table was ever analysed.

The headline measure counts the tables that need attention (never analyzed + stale); "Stale" alone was 0 for a
database whose 4 tables had never been analysed.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "trellis-analytics" / "examples" / "steps" / "statistics_freshness" / "src"


def _step():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    from statistics_freshness import step
    return step


def _table(name, **over):
    return {"schema_name": "public", "table_name": name, "live_tuples": 10, "last_analyze": None, "last_autoanalyze": None,
            "last_vacuum": None, "last_autovacuum": None, "pending_changes": 0, **over}


def test_four_never_analysed_tables_are_four_needing_attention_not_zero_stale():
    step = _step()
    snap = {"source": "ods", "surveyed_at": "2026-10-29T10:00:00", "tables": [_table(f"t{i}") for i in range(4)]}
    results = [step._table(t, snap["source"], snap["surveyed_at"]) for t in snap["tables"]]
    out = step._summary("db", results, snap["surveyed_at"])
    assert out["value"]["Stale"] == 0 and out["value"]["Never Analyzed"] == 4
    assert out["value"]["Needs Attention"] == 4
    assert out["summary"].startswith("of 4 tables: 4 need attention, 4 never analyzed, 0 stale")


def test_the_declared_summary_measure_reads_the_needs_attention_count():
    from statistics_freshness.manifest import MANIFEST

    m = next(x for x in MANIFEST.declaration.measures if x.name == "statistics_freshness_summary")
    assert m.value_field == "Needs Attention" and "never analyzed" in m.unit
    assert "Needs Attention" in next(e for e in MANIFEST.declaration.extents if e.name == "summary").fields
