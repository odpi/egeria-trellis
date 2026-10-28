"""Graphviz DOT generation for the relationship graph.

Implements `docs/design-notes/wireframes/RelationshipGraph.dc.html` (canvas
page 13, commit 1051e4d4 "Design: the relationship graph, drawn from the real
AdventureWorks edges") — a wireframe that specified the picture but was never
wired into the running app. This module produces the actual DOT source, from
the SAME data `db_derived.derive_relationship_graph()` and
`relationship_graph_by_container()` already compute — no second query path.

Three zoom levels, per the design note:

1. `schema_map_dot` — one box per schema, always legible regardless of
   database size. Box width/weight from table count; edges between schemas
   weighted by cross-schema key count.
2. `full_database_dot` — the whole database, schema clusters, hub nodes
   (sized/emphasised by in-degree), cross-schema edges drawn darker than
   intra-schema ones. `dot` layout (not `fdp` — the design note's own
   finding: "dot clearly beats fdp here"). Returns `None` above
   `FULL_GRAPH_TABLE_LIMIT` tables — the design note's own ~100-table
   threshold, "above ~100 tables fall back to map + hubs" — so a caller
   never asks Kroki to lay out something that will not read.
3. `schema_detail_dot` — one schema opened, with its OUTGOING keys also
   drawn (not just internal ones). This is what fixes the mislabelling the
   design note's own finding describes: `by_schema` used to call
   `sales.salestaxrate`/`sales.shoppingcartitem` "isolated" when both have
   keys LEAVING the schema — a table with only outgoing keys is genuinely
   isolated *within* the schema and must say so ("no keys inside sales"),
   never "isolated" outright. That underlying counting fix already landed
   in `relationship_graph_by_container` (crossing columns are withheld from
   the scoped graph and reported separately as
   `cross_container_references`) — this module's job is only to draw what
   that fix already computed, correctly.

Every function here is pure: DOT text in, DOT text out, no I/O. Rendering the
DOT to SVG happens server-side via the existing Kroki proxy
(`web/routes/diagrams.py`'s `/api/diagrams/graphviz`, mirroring the existing
`/api/diagrams/mermaid` route) — this module never calls Kroki itself.
"""
from __future__ import annotations

#: The design note's own threshold: "above ~100 tables fall back to map +
#: hubs". Stated as a name so a reader (and a test) can see the line rather
#: than a bare literal.
FULL_GRAPH_TABLE_LIMIT = 100

#: A node is drawn as a "hub" (bold label, in-degree badge, weight-scaled
#: penwidth/fontsize) once at least this many other tables reference it.
#: Matches the wireframe's own examples (`full_dot.dot`: businessentity=5,
#: employee=6, person=7, product=14 all carry the hub treatment; nothing
#: below 5 does).
HUB_MIN_IN_DEGREE = 5

#: Long single-word identifiers are WRAPPED across multiple lines within
#: their node, never cut. Root cause (live-verified against the real
#: `egeria-shared-kroki` container at localhost:6002, not assumed from
#: source): every `node [...]` default in this module ALREADY sets an
#: explicit `fontname="DejaVu Serif"` matching the edge declarations — a
#: missing/inconsistent `fontname` is not the bug. The actual mechanism is
#: that "DejaVu Serif" is never embedded in or shipped alongside the SVG
#: Kroki returns — it is emitted only as a `font-family` *name* on each
#: `<text>` element — so Graphviz's server-side box-width calculation (done
#: against whatever "DejaVu Serif" metrics its own fontconfig resolves
#: inside the Kroki container) and the box's ACTUAL rendered width (done by
#: whatever displays the SVG afterward — a browser, which silently
#: substitutes a different font if "DejaVu Serif" isn't genuinely installed
#: there) are computed against two different font metric tables. Confirmed
#: directly, not assumed: in the browser that renders this app's evidence
#: rail, `canvas.measureText()` returns the IDENTICAL width for
#: `font: "DejaVu Serif"` and for a font name that does not exist at all —
#: i.e. no real "DejaVu Serif" is present there and both fall back to the
#: same generic serif. Rendering `schema_detail_dot`'s own output for a
#: table named `salesorderheadersalesreason` (28 chars) through the real
#: Kroki container reproduces the reported overflow exactly (the label
#: crowds/exceeds its box edges); `specialofferproduct` (20 chars) and
#: `countryregioncurrency` (22 chars) render with a visible margin at the
#: same fontsize. No fixed margin tuned for one font's metrics can be
#: guaranteed to hold for whatever font a viewer's browser actually
#: substitutes — so rather than gamble on a margin, or drop characters,
#: this module wraps: a name over `WRAP_LINE_CHARS` breaks onto additional
#: lines (at `_`, or by hard character count for AdventureWorks-style
#: squashed names with no separator at all), and the node's box/height grow
#: to fit every line. The name is never shortened or cut — every character
#: the surveyor read is still on the node, just wrapped. (A shortened label
#: with the full name on `tooltip` remains available as a documented
#: fallback for a context that cannot wrap at all, e.g. a single-line
#: schema-map box — not needed by anything this module currently draws,
#: since no schema-map node carries a bare table name.)
WRAP_LINE_CHARS = 14

# Palette lifted directly from the merged wireframe's own `.dot` sources —
# not reinvented here, so the rendered picture matches what the design note
# actually approved.
_INK = "#201f1d"
_RULE = "#8e8a8a"
_RULE_LIGHT = "#c9c6c1"
_DARK_ACCENT = "#4a463e"
_WHITE = "#ffffff"


def _qname(schema: str, table: str) -> str:
    return f"{schema}.{table}"


def _dot_escape(text: str) -> str:
    """Escapes text for use inside a DOUBLE-QUOTED DOT string — a node ID
    (`"..."`) or a plain quoted label (`label="..."`). Backslash and quote
    only; this is NOT for HTML-like labels (`label=<...>`), which use
    `_html_escape` below instead — a quoted-identifier-safe string is not
    automatically a well-formed HTML fragment (`&`, `<`, `>` need entities
    there, not backslash-escaping).
    """
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _html_escape(text: str) -> str:
    """Escapes text for use inside a Graphviz HTML-like label (`label=<...>`).
    Every node label in this module that carries a `<b>`/`<br/>`/`<font>`
    wrapper is HTML-like, not a quoted string — a table or schema name that
    happens to contain `&`, `<`, `>` or `"` (a quoted Postgres identifier can
    contain any of them) would otherwise break the surrounding markup and
    Kroki would reject the WHOLE graph, not just mis-render that one label.
    Order matters: `&` must be escaped first, or the entities this function
    itself inserts would be double-escaped.
    """
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _id(text: str) -> str:
    """A value safe to place inside a double-quoted DOT node/edge ID —
    `_dot_escape` under a name that reads correctly at every call site that
    is escaping an ID rather than an HTML label's visible text."""
    return _dot_escape(text)


def _in_degrees(edges: list[dict], node_ids: set[str]) -> dict[str, int]:
    """In-degree per node, counting only edges whose target is IN `node_ids`
    (an edge to a table outside this drawing's scope contributes nothing —
    it is drawn, if at all, as a ghost node with no in-degree of its own)."""
    counts: dict[str, int] = {n: 0 for n in node_ids}
    for edge in edges:
        dst = _qname(edge["to_schema"], edge["to_table"])
        if dst in counts:
            counts[dst] += 1
    return counts


def _wrap_identifier_lines(name: str, max_line_chars: int = WRAP_LINE_CHARS) -> list[str]:
    """Splits a long identifier into display lines of at most
    `max_line_chars`, by slicing contiguous substrings of `name` —
    `"".join(_wrap_identifier_lines(name)) == name` always, by construction,
    so no character can ever go missing (see `WRAP_LINE_CHARS`'s docstring
    for why a fixed margin or a shortened label can't be trusted here
    instead). Each line greedily takes up to `max_line_chars`, preferring to
    break right AFTER the last `_` inside that window (common in snake_case
    table/column names, and more readable than a mid-word split) — the
    underscore itself stays on the line before the break, never dropped.
    An AdventureWorks-style name with no separator at all
    (`salesorderheadersalesreason`) has no `_` to break on and falls back
    to a hard character-count wrap.
    """
    if len(name) <= max_line_chars:
        return [name]
    lines: list[str] = []
    remaining = name
    while len(remaining) > max_line_chars:
        window = remaining[:max_line_chars]
        underscore_at = window.rfind("_")
        break_at = underscore_at + 1 if underscore_at > 0 else max_line_chars
        lines.append(remaining[:break_at])
        remaining = remaining[break_at:]
    if remaining:
        lines.append(remaining)
    return lines


def _wrapped_html_label_text(name: str, max_line_chars: int = WRAP_LINE_CHARS) -> str:
    """Multi-line HTML-like label fragment (`<br/>`-separated, entity-escaped
    per line) for a long identifier — for use inside an existing
    `label=<...>` block. Short names pass through as a single, unwrapped
    line."""
    return "<br/>".join(_html_escape(line) for line in _wrap_identifier_lines(name, max_line_chars))


def _wrapped_plain_label_text(name: str, max_line_chars: int = WRAP_LINE_CHARS) -> str:
    """Multi-line QUOTED-STRING label text (Graphviz's own `\\n` line-break
    escape — distinct from, and not usable inside, an HTML-like label) for
    a long identifier, for use inside `label="..."`. Short names pass
    through as a single, unwrapped, `_dot_escape`d line."""
    return "\\n".join(_dot_escape(line) for line in _wrap_identifier_lines(name, max_line_chars))


def _node_style(qname: str, bare_name: str, in_degree: int, note: str = "") -> str:
    """One node declaration line. Hub weighting is a straight ramp off
    in-degree, matching the wireframe's own values (penwidth = 0.8 +
    0.12×in-degree, fontsize = 9 + 0.25×in-degree) — see this module's
    docstring for how those constants were read back off `full_dot.dot`.
    """
    node_id = _id(qname)
    if in_degree >= HUB_MIN_IN_DEGREE:
        penwidth = round(0.8 + 0.12 * in_degree, 2)
        fontsize = round(9 + 0.25 * in_degree, 1)
        if note:
            label = (
                f'<<b>{_wrapped_html_label_text(bare_name)}</b>'
                f'<br/><font point-size="7.5" color="{_DARK_ACCENT}">{_html_escape(note)}</font>>'
            )
        else:
            label = (
                f'<<b>{_wrapped_html_label_text(bare_name)}</b> '
                f'<font color="{_DARK_ACCENT}">←{in_degree}</font>>'
            )
        return (
            f'"{node_id}" [label={label},color="{_INK}",'
            f'penwidth={penwidth},fontsize={fontsize}];'
        )
    if note:
        label = (
            f'<{_wrapped_html_label_text(bare_name)}'
            f'<br/><font point-size="7.5" color="{_DARK_ACCENT}">{_html_escape(note)}</font>>'
        )
        return f'"{node_id}" [label={label},color="{_INK}",penwidth=1.1];'
    return f'"{node_id}" [label="{_wrapped_plain_label_text(bare_name)}"];'


def _isolated_note(qname: str, has_in: bool, has_out: bool) -> str:
    if not has_in and not has_out:
        return "no keys in or out"
    return ""


def schema_map_dot(
    schema_table_counts: dict[str, int],
    schema_internal_key_counts: dict[str, int],
    cross_schema_pairs: dict[str, int],
) -> str:
    """Zoom level 1: one box per schema. Always legible — this never grows
    past the schema count, so it is the view every database gets regardless
    of size (the >100-table fallback lands here).

    `cross_schema_pairs` keys are `"from_schema → to_schema"` strings, the
    exact shape `relationship_graph_by_container`'s rollup already produces
    (`db_derived.py`'s `pairs` dict, `"{from} → {to}"`).
    """
    lines = [
        'digraph G { graph [rankdir=LR,bgcolor="transparent",pad=0.2,'
        'nodesep=0.5,ranksep=0.9,fontname="DejaVu Serif"];',
        f'node [shape=box,style="rounded,filled",fillcolor="{_WHITE}",'
        f'color="{_INK}",fontname="DejaVu Serif",fontcolor="{_INK}",'
        'fontsize=13,margin="0.18,0.10"];',
        f'edge [color="{_DARK_ACCENT}",fontname="DejaVu Serif",fontsize=11,'
        f'fontcolor="{_INK}",arrowsize=0.7];',
    ]
    for schema in sorted(schema_table_counts):
        table_count = schema_table_counts[schema]
        key_count = schema_internal_key_counts.get(schema, 0)
        width = round(0.8 + 0.048 * table_count, 2)
        penwidth = round(1.0 + 0.04 * key_count, 2)
        tables_word = "table" if table_count == 1 else "tables"
        keys_word = "key" if key_count == 1 else "keys"
        lines.append(
            f'"{_id(schema)}" [label=<<b>{_html_escape(schema)}</b>'
            f'<br/><font point-size="10" color="{_DARK_ACCENT}">{table_count} '
            f'{tables_word} · {key_count} {keys_word} inside</font>>,'
            f'width={width},penwidth={penwidth}];'
        )
    for pair, count in sorted(cross_schema_pairs.items(), key=lambda kv: -kv[1]):
        if " → " not in pair:
            continue
        src, dst = pair.split(" → ", 1)
        penwidth = round(0.8 + 0.45 * count, 2)
        lines.append(
            f'"{_id(src)}" -> "{_id(dst)}" '
            f'[label="{count}",penwidth={penwidth}];'
        )
    lines.append("}")
    return "\n".join(lines)


def _graph_title(credential_scope: dict | None) -> str:
    """The rollup title line, in the same words the 21b coverage headline
    already uses ('measured within this credential's visibility only',
    `survey_definition_adapter.py`'s `_note_for`) — no new vocabulary.

    `credential_scope` is `{"connected_as": str, "measured": int, "total":
    int}` or `None` for an unscoped (full-access) survey, in which case no
    title line is added at all — an unscoped graph says nothing extra, the
    same way `_note_for` only speaks up when `_status.fraction` is set.
    """
    if not credential_scope:
        return ""
    who = credential_scope.get("connected_as") or "this credential"
    measured = credential_scope.get("measured")
    total = credential_scope.get("total")
    if measured is None or total is None:
        return ""
    return (
        f'label=<within <b>{_html_escape(who)}</b>’s access, '
        f'{measured} of {total} tables>; labelloc=t; fontsize=11; '
        f'fontcolor="{_DARK_ACCENT}";'
    )


def full_database_dot(
    tables_by_schema: dict[str, list[str]],
    edges: list[dict],
    *,
    table_count: int | None = None,
    not_captured_by_schema: dict[str, list[str]] | None = None,
    credential_scope: dict | None = None,
    not_captured_reason_by_schema: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Zoom level 2: the whole database, schema clusters, hub weighting,
    darker cross-schema edges, `dot` layout.

    Returns `(dot_source, None)` normally, or `(None, reason)` above
    `FULL_GRAPH_TABLE_LIMIT` — the design note's own finding that a
    whole-database drawing stops reading past ~100 tables, so this refuses
    to produce one rather than handing Kroki something that will not lay
    out legibly. The caller falls back to `schema_map_dot` plus the hub list
    already in `db_relationship_graph`'s `most_referenced`.

    `not_captured_by_schema` — tables whose OWN keys were never captured
    (`DerivedInputs.keys_captured_for_table` is False for them; a native
    Egeria-survey read-back, or a catalog-only-fallback table). These are
    drawn dashed and labelled "keys not captured" by default — never as "no
    relationships", which would assert a measurement that was never taken.
    Same absence discipline `db_derived.py`'s own module docstring states:
    "not established is not a negative."

    `not_captured_reason_by_schema` — an optional, more specific caption for
    an ENTIRE schema's not-captured tables, keyed by schema name (e.g.
    `"structure only — no SELECT"`, `"not visible — no USAGE"`). This is a
    per-SCHEMA override, not per-table: `credential_capability`'s stored
    blob only records SELECT/USAGE grants aggregated per schema
    (`connection.py`'s `get_credential_capability`), not per individual
    table, so a schema whose grant is uniform (SCOPE_STRUCTURE_ONLY,
    SCOPE_NOT_VISIBLE — see `schema_scope.container_scope_states`) can be
    captioned precisely, while a SCOPE_PARTIALLY_READABLE schema (some
    tables selectable, others not, with no stored record of WHICH) keeps
    the generic "keys not captured" caption — that generic caption is still
    exact per table (it comes straight from whether THAT table's own
    columns carry key information), it just cannot say why for a partially
    granted schema. Never applied to a table that IS captured.
    """
    total = table_count if table_count is not None else sum(
        len(ts) for ts in tables_by_schema.values()
    )
    if total > FULL_GRAPH_TABLE_LIMIT:
        return None, (
            f"graph not drawn: {total} tables; per-schema views available "
            f"(exceeds the {FULL_GRAPH_TABLE_LIMIT}-table threshold above "
            "which a whole-database drawing stops reading clearly — the "
            "design note's own finding). The schema map and hub list are "
            "shown instead."
        )

    not_captured_by_schema = not_captured_by_schema or {}
    not_captured_reason_by_schema = not_captured_reason_by_schema or {}
    node_ids = {
        _qname(schema, table)
        for schema, tables in tables_by_schema.items()
        for table in tables
    }
    in_degree = _in_degrees(edges, node_ids)
    # Fully-isolated tables (no in AND no out edge among the edges actually
    # drawn here) get the "no keys in or out" caption from the wireframe.
    out_nodes = {_qname(e["from_schema"], e["from_table"]) for e in edges}
    in_nodes = {_qname(e["to_schema"], e["to_table"]) for e in edges if
                _qname(e["to_schema"], e["to_table"]) in node_ids}

    title = _graph_title(credential_scope)
    lines = [
        'digraph G { graph [bgcolor="transparent",pad=0.2,'
        'fontname="DejaVu Serif",compound=true,splines=true,overlap=false,'
        'nodesep=0.12,ranksep=0.45,rankdir=LR' + (f',{title}' if title else '') + '];',
        f'node [shape=box,style="rounded,filled",fillcolor="{_WHITE}",'
        f'color="{_RULE}",fontname="DejaVu Serif",fontcolor="{_INK}",'
        'fontsize=9,height=0.22,margin="0.06,0.02",penwidth=0.7];',
        f'edge [color="{_RULE_LIGHT}",arrowsize=0.45,penwidth=0.7];',
    ]
    for schema in sorted(tables_by_schema):
        lines.append(
            f'subgraph "cluster_{_id(schema)}" {{ '
            f'label=<<b>{_html_escape(schema)}</b>>; fontsize=12; '
            f'fontcolor="{_INK}"; color="{_RULE_LIGHT}"; style="rounded"; '
            "penwidth=1;"
        )
        not_captured = set(not_captured_by_schema.get(schema) or [])
        caption = not_captured_reason_by_schema.get(schema) or "keys not captured"
        for table in sorted(tables_by_schema[schema]):
            qname = _qname(schema, table)
            if table in not_captured:
                lines.append(
                    f'"{_id(qname)}" [label=<{_wrapped_html_label_text(table)}'
                    f'<br/><font point-size="7.5" color="{_DARK_ACCENT}">{_html_escape(caption)}'
                    f'</font>>,style="rounded,dashed",color="{_RULE}"];'
                )
                continue
            note = "" if (qname in out_nodes or qname in in_nodes) else \
                _isolated_note(qname, qname in in_nodes, qname in out_nodes)
            lines.append(_node_style(qname, table, in_degree.get(qname, 0), note))
        lines.append("}")

    for edge in edges:
        src = _qname(edge["from_schema"], edge["from_table"])
        dst = _qname(edge["to_schema"], edge["to_table"])
        if src not in node_ids or dst not in node_ids:
            continue
        cross = edge["from_schema"] != edge["to_schema"]
        style = f' [color="{_DARK_ACCENT}",penwidth=1.0]' if cross else ""
        lines.append(f'"{_id(src)}" -> "{_id(dst)}"{style};')

    lines.append("}")
    return "\n".join(lines), None


def schema_detail_dot(
    schema: str,
    internal_edges: list[dict],
    outgoing_edges: list[dict],
    all_schema_tables: list[str],
    not_captured: list[str] | None = None,
    not_captured_reason: str | None = None,
) -> str:
    """Zoom level 3: one schema opened, with its OUTGOING keys drawn too.

    `outgoing_edges` — the crossing columns `relationship_graph_by_container`
    already withholds from the internal graph and reports separately as
    `cross_container_references` — are drawn as edges to plaintext "ghost"
    nodes labelled `<other_schema>.<table>`, exactly like `sales.dot`. This
    is the drawing that makes the design note's finding visible: a table
    whose only keys leave the schema (`sales.salestaxrate`,
    `sales.shoppingcartitem`) is NOT a dead node here — it has a real
    outgoing edge, drawn — and it is labelled "no keys inside {schema}",
    never "isolated".

    `not_captured` — tables in this schema whose own keys were never
    captured. Drawn dashed and labelled "keys not captured", checked BEFORE
    the internal/outgoing/isolated classification below, so a table with no
    edges because nobody ever looked is never confused with one measured
    and found to have none.
    """
    internal_targets = {
        (e["from_table"], e["to_table"]) for e in internal_edges
    }
    has_internal_edge = {t for pair in internal_targets for t in pair}
    has_outgoing = {e["from_table"] for e in outgoing_edges}
    not_captured_set = set(not_captured or [])
    not_captured_caption = not_captured_reason or "keys not captured"

    lines = [
        'digraph G { graph [rankdir=LR,bgcolor="transparent",pad=0.2,'
        'nodesep=0.18,ranksep=0.55,fontname="DejaVu Serif"];',
        f'node [shape=box,style="rounded,filled",fillcolor="{_WHITE}",'
        f'color="{_RULE}",fontname="DejaVu Serif",fontcolor="{_INK}",'
        'fontsize=10,height=0.26,margin="0.07,0.03",penwidth=0.8];',
        f'edge [color="{_RULE}",arrowsize=0.5,penwidth=0.8];',
        f'subgraph cluster_s {{ label=<<b>{_html_escape(schema)}</b> '
        f'<font color="{_DARK_ACCENT}">· {len(all_schema_tables)} tables</font>>; '
        f'fontsize=13; color="{_INK}"; style="rounded"; penwidth=1.2;',
    ]
    for table in sorted(all_schema_tables):
        node_id = _id(_qname(schema, table))
        if table in not_captured_set:
            lines.append(
                f'"{node_id}" [label=<{_wrapped_html_label_text(table)}'
                f'<br/><font point-size="8" color="{_DARK_ACCENT}">'
                f'{_html_escape(not_captured_caption)}</font>>,'
                f'style="rounded,dashed",color="{_RULE}"];'
            )
        elif table in has_internal_edge:
            lines.append(f'"{node_id}" [label="{_wrapped_plain_label_text(table)}"];')
        elif table in has_outgoing:
            # The fix in picture form: a table with ONLY outgoing keys is
            # captioned "no keys inside <schema>", never "isolated" — it has
            # a real edge drawn below, to a table in another schema.
            lines.append(
                f'"{node_id}" [label=<{_wrapped_html_label_text(table)}'
                f'<br/><font point-size="8" color="{_DARK_ACCENT}">no keys '
                f'inside {_html_escape(schema)} — joined across</font>>,'
                f'color="{_INK}",penwidth=1.1];'
            )
        else:
            # Genuinely isolated even counting outgoing keys — this IS
            # "isolated", and only this case is allowed to say so.
            lines.append(
                f'"{node_id}" [label=<{_wrapped_html_label_text(table)}'
                f'<br/><font point-size="8" color="{_DARK_ACCENT}">isolated '
                f'— no keys in or out</font>>,color="{_INK}",penwidth=1.1];'
            )
    lines.append("}")

    ghost_nodes: dict[str, str] = {}
    for edge in outgoing_edges:
        target = _qname(edge["to_schema"], edge["to_table"])
        if target not in ghost_nodes:
            ghost_nodes[target] = (
                f'"{_id(target)}" [label=<'
                f'<font color="{_DARK_ACCENT}">{_html_escape(edge["to_schema"])}.'
                f'</font>{_wrapped_html_label_text(edge["to_table"])}>,shape=plaintext,'
                'style="",fontsize=9.5];'
            )
    for decl in ghost_nodes.values():
        lines.append(decl)

    for edge in internal_edges:
        src = _qname(schema, edge["from_table"])
        dst = _qname(schema, edge["to_table"])
        lines.append(f'"{_id(src)}" -> "{_id(dst)}";')
    for edge in outgoing_edges:
        src = _qname(schema, edge["from_table"])
        dst = _qname(edge["to_schema"], edge["to_table"])
        lines.append(
            f'"{_id(src)}" -> "{_id(dst)}" '
            f'[color="{_DARK_ACCENT}",style="solid",penwidth=0.9,arrowhead=vee];'
        )

    lines.append("}")
    return "\n".join(lines)


def build_relationship_diagrams(
    tables_by_schema: dict[str, list[str]],
    edges: list[dict],
    schema_summaries: dict[str, dict],
    by_schema_results: dict[str, dict],
    cross_schema_pairs: dict[str, int],
    *,
    table_count: int | None = None,
    not_captured_by_schema: dict[str, list[str]] | None = None,
    credential_scope: dict | None = None,
    not_captured_reason_by_schema: dict[str, str] | None = None,
) -> dict:
    """Assemble all three zoom levels from data `db_derived` already
    computed — the single entry point `db_derived.py` calls, so the DOT
    source lives in `db_relationship_graph`'s own result dict (under
    `graphviz`) and reaches the frontend through the exact same fact-reading
    path every other measurement on this analysis already uses.

    `schema_summaries` — `relationship_graph_by_container`'s own per-schema
    summary dict (`table_count`, `edge_count`, ...). `by_schema_results` —
    that same function's `by_<grain>` dict, each value carrying
    `cross_container_references` (the outgoing keys) alongside `edges` (the
    internal ones) for that schema.

    `not_captured_by_schema` / `credential_scope` carry the same absence
    and credential-scope information the 21b coverage headline already
    computes (`survey_definition_adapter.py`'s `_attach_container_credential_
    scope`/`_attach_coverage_status`) into the drawing itself — a table
    whose keys were never captured must never be drawn as "no
    relationships", and a scoped credential's title must say so, in the
    SAME words that headline uses (see `_graph_title`).
    """
    schema_table_counts = {s: len(ts) for s, ts in tables_by_schema.items()}
    schema_internal_key_counts = {
        s: (schema_summaries.get(s) or {}).get("edge_count") or 0
        for s in tables_by_schema
    }
    schema_map = schema_map_dot(
        schema_table_counts, schema_internal_key_counts, cross_schema_pairs,
    )

    full, fallback_reason = full_database_dot(
        tables_by_schema, edges, table_count=table_count,
        not_captured_by_schema=not_captured_by_schema,
        credential_scope=credential_scope,
        not_captured_reason_by_schema=not_captured_reason_by_schema,
    )

    by_schema_dot: dict[str, str] = {}
    for schema, tables in tables_by_schema.items():
        result = by_schema_results.get(schema) or {}
        internal_edges = result.get("edges") or []
        outgoing = result.get("cross_container_references") or []
        not_captured = (not_captured_by_schema or {}).get(schema) or []
        reason = (not_captured_reason_by_schema or {}).get(schema)
        by_schema_dot[schema] = schema_detail_dot(
            schema, internal_edges, outgoing, tables, not_captured, reason,
        )

    return {
        "schema_map": schema_map,
        "full": full,
        "full_fallback_reason": fallback_reason,
        "by_schema": by_schema_dot,
        "table_count": table_count if table_count is not None else sum(
            schema_table_counts.values()
        ),
    }
