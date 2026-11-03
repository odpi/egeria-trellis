"""A table result, read through its declaration (B1, DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT-2026-11-02 §4-5).

A step that declares a table result stores EVERY row in `analysis_table_rows` (`result_materializer.write_table_rows`);
the declaration's `max_rows` caps what is SHOWN. This module is the one typed read: column types applied, an empty cell
replaced by its column's declared absence word (never a blank), the shown and total counts, and the truncation
sentence filled in. The renderer, the Value profile tab, the publish table and Chat all read rows through it.

The rule it keeps: the summary is the step's own measures, computed over all rows and declared beside the table. Nothing
here adds up the shown slice into a number a person would read as the whole.

It lives in Resource Explorer, not in the library's ODS reader, so the library move (LS2) carries none of it; it can join
the library's reader protocol once that seam exists.
"""
from __future__ import annotations

import json
from typing import Any

#: The publish band's words for a table result: only the summary leaves Resource Explorer (owner's ruling 2026-11-02,
#: "Summary only").
ROWS_STAY = "rows stay in Resource Explorer · summary published"


def row_key(table, row: dict) -> str:
    """The row's key fields as a JSON array: one member, found without parsing the row."""
    return json.dumps([row.get(k) for k in table.key_fields], default=str)


def _number_words(n: int) -> str:
    return f"{n:,}"


def truncation_words(table, shown: int, total: int) -> str:
    """The table's declared truncation sentence with its counts filled in ("the first 1,000 of 1,916 columns; ...")."""
    return table.truncation.replace("{shown}", _number_words(shown)).replace("{total}", _number_words(total))


def typed(column, value: Any) -> Any:
    """A stored cell as its column's type; None stays None (the caller words it)."""
    if value is None:
        return None
    t = column.type
    try:
        if t == "integer":
            return int(value)
        if t in ("number", "bytes", "duration"):
            f = float(value)
            return int(f) if f == int(f) and t != "number" else f
        if t == "boolean":
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "t", "yes")
    except (TypeError, ValueError):
        return str(value)
    # text, state, date and datetime read as the words they were stored as (a date stays its ISO form).
    return str(value)


def apply(table, raw: dict) -> dict:
    """One stored row as a reader shows it: every declared field, typed, and an empty cell as its absence word.
    `absent` names the fields that were empty, so a renderer can mute them without guessing from the text."""
    out: dict = {}
    absent = []
    for c in table.columns:
        v = typed(c, raw.get(c.name))
        if v is None or v == "":
            absent.append(c.name)
            v = c.absence or ""
        out[c.name] = v
    out["absent"] = absent
    return out


def stored_rows(registry, resource_kind: str, slug: str, analysis_id: str, declaration, *,
                run: str | None = None) -> tuple[str, list[dict]]:
    """(run_id, every stored row) with column types applied and NULLs KEPT: for a reader that words each cell itself
    or recounts (the Value profile screen), never for drawing as is. ("", []) when no run stored rows."""
    run_id, _total, rows = registry.query_table_rows(resource_kind, slug, analysis_id, run=run)
    cols = declaration.table.columns
    return run_id, [{c.name: typed(c, r.get(c.name)) for c in cols} for r in rows]


def table_rows(registry, resource_kind: str, slug: str, analysis_id: str, declaration, *,
               limit: int | None = -1, run: str | None = None) -> dict:
    """The typed read. `limit` defaults to the declaration's `max_rows` (what a table SHOWS); None reads every stored
    row (what a reader that recounts needs, never what a screen draws). Returns
    {"stored": bool, "run_id", "total", "shown", "truncation", "fields", "columns", "rows"}; `stored` False (with
    total 0) when no run stored rows: never read as "measured, no rows"."""
    table = declaration.table
    cap = table.max_rows if limit == -1 else limit
    run_id, total, rows = registry.query_table_rows(resource_kind, slug, analysis_id, run=run, limit=cap)
    typed_rows = [apply(table, r) for r in rows]
    return {
        "stored": bool(run_id),
        "run_id": run_id,
        "total": total,
        "shown": len(typed_rows),
        "truncation": truncation_words(table, len(typed_rows), total) if run_id else "",
        "fields": table.fields(),
        "key_fields": list(table.key_fields),
        "order": table.order,
        "columns": [{"name": c.name, "type": c.type, "unit": c.unit, "absence": c.absence, "states": list(c.states),
                     "key": c.name in table.key_fields} for c in table.columns],
        "rows": typed_rows,
    }


#: How a Questions row or a chat answer cites a table result after its summary.
CITES_TABLE = "the table lists them"


def summary_measures(declaration, source: dict, run: dict | None = None) -> list[dict]:
    """The declared summary measures in words ({"name", "words"}), from the step's result: "1,846 of 1,916 columns".
    A summary measure the result does not hold is left out, never shown as 0."""
    from resource_explorer.declared_words import measure_words

    out = []
    for name in declaration.table.summary:
        m = declaration.measure(name)
        w = measure_words(m, source or {}, run or {}) if m is not None else None
        if w is not None:
            out.append({"name": name, "words": w["text"]})
    return out


def question_line(declaration, source: dict, run: dict | None = None) -> str:
    """A Questions row's answer for a table result: the summary in words, citing the table; "" when the result holds
    none of the summary (the row then says its state, as for any analysis)."""
    words = [m["words"] for m in summary_measures(declaration, source, run)]
    return f"{' · '.join(words)} · {CITES_TABLE}" if words else ""


def payload(registry, resource_kind: str, slug: str, analysis_id: str, declaration, source: dict,
            run: dict | None = None, *, headline: str = "") -> dict:
    """What the renderer's `table` block draws (envelope.js `tableResultHtml`): the headline sentence (the analysis's
    own when it has one, else the summary), the summary measures behind it, the shown rows, and the publish band."""
    measures = summary_measures(declaration, source, run)
    return {
        "headline": headline or " · ".join(m["words"] for m in measures),
        "measures": measures,
        "band": ROWS_STAY,
        "table": table_rows(registry, resource_kind, slug, analysis_id, declaration),
        "not_stored": declaration.absence.sentence("not_measured"),
    }


class RegistryTableRows:
    """RE's reader behind the library's table-rows seam (`trellis_analytics.readers.table_rows.TableRowsReader`,
    Architect ruling 2026-11-02: the protocol is the library's, this read is RE's implementation), installed as the
    `table_rows` entry point of `trellis_analytics.readers` (pyproject.toml). The registry is a constructor argument;
    with none it opens RE's own on each call, never at import."""

    def __init__(self, registry=None):
        self._registry = registry

    def table_rows(self, run: dict, declaration, limit: int | None = -1) -> dict:
        from resource_explorer.registry import ProjectRegistry

        return table_rows(self._registry or ProjectRegistry(), run["resource_kind"], run["slug"], run["analysis_id"],
                          declaration, limit=limit, run=run.get("run_id"))

