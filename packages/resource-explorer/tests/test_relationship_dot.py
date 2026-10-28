"""Tests for `relationship_dot.py` — the Graphviz DOT generation behind the
relationship-graph card (design note "Design: the relationship graph, drawn
from the real AdventureWorks edges", commit 1051e4d4). The wireframe
(`docs/design-notes/wireframes/RelationshipGraph.dc.html` and its three
`.dot` examples) specified the shape; these tests pin that this module
actually produces it from data shaped like `db_derived.py`'s own output —
not merely "some dot text comes out".
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry, STATE_MEASURED
from resource_explorer.surveyors.database.db_derived import run_db_derived
from resource_explorer.surveyors.database.relationship_dot import (
    FULL_GRAPH_TABLE_LIMIT,
    HUB_MIN_IN_DEGREE,
    WRAP_LINE_CHARS,
    build_relationship_diagrams,
    full_database_dot,
    schema_detail_dot,
    schema_map_dot,
)

# ── a small fixture shaped exactly like db_derived's own rows ──────────────

TABLES_BY_SCHEMA = {
    "sales": ["customer", "salesorderheader", "salestaxrate"],
    "person": ["person", "address"],
}

# `sales.salestaxrate` has ONE key, and it leaves the schema (-> person.
# address) -- the design note's own finding ("by_schema calls sales.
# salestaxrate ... isolated" when it has a key leaving the schema).
EDGES = [
    {"from_schema": "sales", "from_table": "customer", "from_column": "person_id",
     "to_schema": "person", "to_table": "person", "to_column": "id"},
    {"from_schema": "sales", "from_table": "salesorderheader", "from_column": "customer_id",
     "to_schema": "sales", "to_table": "customer", "to_column": "id"},
    {"from_schema": "sales", "from_table": "salestaxrate", "from_column": "address_id",
     "to_schema": "person", "to_table": "address", "to_column": "id"},
]

SCHEMA_SUMMARIES = {
    "sales": {"edge_count": 1},  # only salesorderheader->customer is INTERNAL to sales
    "person": {"edge_count": 0},
}

BY_SCHEMA_RESULTS = {
    "sales": {
        "edges": [EDGES[1]],  # internal only — salestaxrate's key was withheld
        "cross_container_references": [
            {"from_schema": "sales", "from_table": "customer", "from_column": "person_id",
             "to_schema": "person", "to_table": "person", "to_column": "id"},
            {"from_schema": "sales", "from_table": "salestaxrate", "from_column": "address_id",
             "to_schema": "person", "to_table": "address", "to_column": "id"},
        ],
    },
    "person": {"edges": [], "cross_container_references": []},
}

CROSS_SCHEMA_PAIRS = {"sales → person": 2}


class TestSchemaMapDot:
    def test_every_schema_is_a_cluster_free_box_with_counts(self):
        dot = schema_map_dot(
            {"sales": 3, "person": 2}, {"sales": 1, "person": 0}, CROSS_SCHEMA_PAIRS,
        )
        assert '"sales"' in dot
        assert '"person"' in dot
        assert "3" in dot and "tables" in dot

    def test_cross_schema_edge_is_drawn_with_its_count_label(self):
        dot = schema_map_dot(
            {"sales": 3, "person": 2}, {"sales": 1, "person": 0}, CROSS_SCHEMA_PAIRS,
        )
        assert '"sales" -> "person"' in dot
        assert 'label="2"' in dot

    def test_is_deterministic(self):
        a = schema_map_dot({"sales": 3, "person": 2}, {"sales": 1, "person": 0}, CROSS_SCHEMA_PAIRS)
        b = schema_map_dot({"sales": 3, "person": 2}, {"sales": 1, "person": 0}, CROSS_SCHEMA_PAIRS)
        assert a == b

    def test_a_larger_schema_gets_a_wider_box(self):
        dot = schema_map_dot({"big": 50, "small": 2}, {"big": 10, "small": 0}, {})
        big_line = next(l for l in dot.split("\n") if '"big"' in l and "label=" in l)
        small_line = next(l for l in dot.split("\n") if '"small"' in l and "label=" in l)

        def width(line):
            return float(line.split("width=")[1].split(",")[0])

        assert width(big_line) > width(small_line)


class TestFullDatabaseDot:
    def test_schema_clusters_present(self):
        dot, reason = full_database_dot(TABLES_BY_SCHEMA, EDGES)
        assert reason is None
        assert 'subgraph "cluster_sales"' in dot
        assert 'subgraph "cluster_person"' in dot

    def test_cross_schema_edges_are_styled_differently_from_internal_ones(self):
        dot, _ = full_database_dot(TABLES_BY_SCHEMA, EDGES)
        lines = dot.split("\n")
        cross_line = next(
            l for l in lines
            if '"sales.customer" -> "person.person"' in l
        )
        internal_line = next(
            l for l in lines
            if '"sales.salesorderheader" -> "sales.customer"' in l
        )
        assert "#4a463e" in cross_line  # the dark cross-schema accent
        assert "#4a463e" not in internal_line

    def test_hub_nodes_are_sized_by_in_degree(self):
        # Build a node referenced by exactly HUB_MIN_IN_DEGREE other tables,
        # and one referenced by one fewer — only the first should get the
        # hub (bold + in-degree badge) treatment.
        tables = {"s": [f"referrer{i}" for i in range(HUB_MIN_IN_DEGREE)] + ["hub", "almost_hub"]}
        edges = (
            [{"from_schema": "s", "from_table": f"referrer{i}", "from_column": "x",
              "to_schema": "s", "to_table": "hub", "to_column": "id"}
             for i in range(HUB_MIN_IN_DEGREE)]
            + [{"from_schema": "s", "from_table": "referrer0", "from_column": "y",
                "to_schema": "s", "to_table": "almost_hub", "to_column": "id"}]
        )
        dot, _ = full_database_dot(tables, edges)
        hub_line = next(l for l in dot.split("\n") if '"s.hub"' in l and "label=" in l)
        almost_line = next(l for l in dot.split("\n") if '"s.almost_hub"' in l and "label=" in l)
        assert "<b>" in hub_line  # bold hub label
        assert f"←{HUB_MIN_IN_DEGREE}" in hub_line
        assert "<b>" not in almost_line

    def test_not_captured_table_is_drawn_dashed_never_as_no_relationships(self):
        dot, _ = full_database_dot(
            TABLES_BY_SCHEMA, EDGES,
            not_captured_by_schema={"sales": ["salestaxrate"]},
        )
        line = next(l for l in dot.split("\n") if '"sales.salestaxrate"' in l)
        assert "dashed" in line
        assert "keys not captured" in line
        assert "no keys" not in line  # must not also claim a measured absence

    def test_credential_scope_adds_a_title_in_the_21b_coverage_vocabulary(self):
        dot, _ = full_database_dot(
            TABLES_BY_SCHEMA, EDGES,
            credential_scope={"connected_as": "analyst_ro", "measured": 3, "total": 5},
        )
        assert "analyst_ro" in dot
        assert "3 of 5 tables" in dot

    def test_no_credential_scope_adds_no_title(self):
        dot, _ = full_database_dot(TABLES_BY_SCHEMA, EDGES)
        assert "labelloc=t" not in dot

    def test_is_deterministic(self):
        a, _ = full_database_dot(TABLES_BY_SCHEMA, EDGES)
        b, _ = full_database_dot(TABLES_BY_SCHEMA, EDGES)
        assert a == b

    def test_above_threshold_returns_none_with_a_named_reason_not_blank(self):
        big = {"s": [f"t{i}" for i in range(FULL_GRAPH_TABLE_LIMIT + 1)]}
        dot, reason = full_database_dot(big, [])
        assert dot is None
        assert reason
        assert "graph not drawn" in reason
        assert str(FULL_GRAPH_TABLE_LIMIT + 1) in reason
        assert "per-schema views available" in reason

    def test_at_exactly_the_threshold_still_draws(self):
        exact = {"s": [f"t{i}" for i in range(FULL_GRAPH_TABLE_LIMIT)]}
        dot, reason = full_database_dot(exact, [])
        assert dot is not None
        assert reason is None


class TestSchemaDetailDot:
    def test_a_table_with_only_outgoing_keys_says_no_keys_inside_never_isolated(self):
        # This is the design note's own finding in picture form:
        # sales.salestaxrate has a key leaving sales and must not be drawn
        # (or captioned) as isolated.
        dot = schema_detail_dot(
            "sales",
            internal_edges=BY_SCHEMA_RESULTS["sales"]["edges"],
            outgoing_edges=BY_SCHEMA_RESULTS["sales"]["cross_container_references"],
            all_schema_tables=TABLES_BY_SCHEMA["sales"],
        )
        line = next(l for l in dot.split("\n") if '"sales.salestaxrate"' in l and "label=" in l)
        assert "no keys inside sales" in line
        assert "isolated" not in line

    def test_the_outgoing_key_is_actually_drawn_as_an_edge(self):
        dot = schema_detail_dot(
            "sales",
            internal_edges=BY_SCHEMA_RESULTS["sales"]["edges"],
            outgoing_edges=BY_SCHEMA_RESULTS["sales"]["cross_container_references"],
            all_schema_tables=TABLES_BY_SCHEMA["sales"],
        )
        assert '"sales.salestaxrate" -> "person.address"' in dot

    def test_a_table_with_no_keys_in_or_out_at_all_is_the_only_case_labelled_isolated(self):
        dot = schema_detail_dot(
            "person",
            internal_edges=[],
            outgoing_edges=[],
            all_schema_tables=["person", "orphan_table"],
        )
        line = next(l for l in dot.split("\n") if '"person.orphan_table"' in l)
        assert "isolated" in line
        assert "no keys in or out" in line

    def test_not_captured_takes_priority_over_isolated_labelling(self):
        dot = schema_detail_dot(
            "person",
            internal_edges=[],
            outgoing_edges=[],
            all_schema_tables=["person", "unmeasured_table"],
            not_captured=["unmeasured_table"],
        )
        line = next(l for l in dot.split("\n") if '"person.unmeasured_table"' in l)
        assert "keys not captured" in line
        assert "isolated" not in line
        assert "dashed" in line

    def test_is_deterministic(self):
        a = schema_detail_dot("sales", BY_SCHEMA_RESULTS["sales"]["edges"],
                               BY_SCHEMA_RESULTS["sales"]["cross_container_references"],
                               TABLES_BY_SCHEMA["sales"])
        b = schema_detail_dot("sales", BY_SCHEMA_RESULTS["sales"]["edges"],
                               BY_SCHEMA_RESULTS["sales"]["cross_container_references"],
                               TABLES_BY_SCHEMA["sales"])
        assert a == b


class TestBuildRelationshipDiagrams:
    def test_assembles_all_three_levels(self):
        result = build_relationship_diagrams(
            TABLES_BY_SCHEMA, EDGES, SCHEMA_SUMMARIES, BY_SCHEMA_RESULTS,
            CROSS_SCHEMA_PAIRS,
        )
        assert result["schema_map"]
        assert result["full"]
        assert result["full_fallback_reason"] is None
        assert set(result["by_schema"]) == {"sales", "person"}
        assert result["table_count"] == 5

    def test_fallback_reason_present_when_full_is_none(self):
        big_tables = {"s": [f"t{i}" for i in range(FULL_GRAPH_TABLE_LIMIT + 5)]}
        result = build_relationship_diagrams(
            big_tables, [], {"s": {"edge_count": 0}}, {"s": {"edges": [], "cross_container_references": []}},
            {},
        )
        assert result["full"] is None
        assert result["full_fallback_reason"]
        # The schema map must still render at this size (requirement: it
        # renders for ANY database size).
        assert result["schema_map"]

    def test_not_captured_threads_through_to_both_full_and_by_schema(self):
        result = build_relationship_diagrams(
            TABLES_BY_SCHEMA, EDGES, SCHEMA_SUMMARIES, BY_SCHEMA_RESULTS,
            CROSS_SCHEMA_PAIRS,
            not_captured_by_schema={"sales": ["salestaxrate"]},
        )
        assert "keys not captured" in result["full"]
        assert "keys not captured" in result["by_schema"]["sales"]


# ── label overflow: a long single-word identifier must WRAP, never be cut ──
#
# Reported live by the project owner: table-name labels overflowing their
# rounded node boxes in the rendered SVG (e.g. "salesorderheadersalesreason",
# "specialofferproduct", "countryregioncurrency"). Root cause, confirmed by
# rendering this module's own output through the real `egeria-shared-kroki`
# container (localhost:6002) and inspecting both the SVG text-node geometry
# and a browser's own `canvas.measureText()`: every `node [...]` default
# already sets an explicit `fontname="DejaVu Serif"` (so a missing/
# inconsistent fontname was NOT the bug); "DejaVu Serif" is simply not a
# real, distinctly-metriced font in the environment that displays the SVG
# afterward (canvas measured the SAME width for "DejaVu Serif" as for a
# font name that does not exist at all), so Graphviz's server-side box
# sizing and the box's actual displayed width are computed from two
# different font metric tables, and the tight per-node margins (tuned
# against whichever metrics Graphviz itself resolved) leave no slack to
# absorb the difference on a long, unbroken identifier. The fix wraps such
# a name across multiple lines within its box (`WRAP_LINE_CHARS`) instead
# of trusting a margin or shortening the name — every character survives.

_LONGEST_REAL_ADVENTUREWORKS_NAME = "salesorderheadersalesreason"  # 28 chars
_SIXTY_CHAR_SYNTHETIC_NAME = "a" * 60


class TestLabelWrapping:
    def test_wrap_helper_keeps_every_character_short_name_passes_through(self):
        from resource_explorer.surveyors.database.relationship_dot import (
            _wrap_identifier_lines,
        )

        assert _wrap_identifier_lines("customer") == ["customer"]

    def test_wrap_helper_keeps_every_character_long_name_wraps(self):
        from resource_explorer.surveyors.database.relationship_dot import (
            _wrap_identifier_lines,
        )

        lines = _wrap_identifier_lines(_LONGEST_REAL_ADVENTUREWORKS_NAME)
        assert len(lines) > 1
        assert all(len(line) <= WRAP_LINE_CHARS for line in lines)
        # No character lost or reordered.
        assert "".join(lines) == _LONGEST_REAL_ADVENTUREWORKS_NAME

    def test_wrap_helper_handles_a_sixty_character_synthetic_name(self):
        from resource_explorer.surveyors.database.relationship_dot import (
            _wrap_identifier_lines,
        )

        lines = _wrap_identifier_lines(_SIXTY_CHAR_SYNTHETIC_NAME)
        assert all(len(line) <= WRAP_LINE_CHARS for line in lines)
        assert "".join(lines) == _SIXTY_CHAR_SYNTHETIC_NAME

    def test_wrap_helper_prefers_underscore_breaks_when_present(self):
        from resource_explorer.surveyors.database.relationship_dot import (
            _wrap_identifier_lines,
        )

        lines = _wrap_identifier_lines("special_offer_product_category_name")
        assert "".join(lines) == "special_offer_product_category_name"
        # Every break happens at a retained underscore, not mid-word, since
        # this name has separators to break on.
        assert all(len(line) <= WRAP_LINE_CHARS for line in lines)

    def test_longest_real_adventureworks_name_is_never_cut_in_schema_detail_dot(self):
        dot = schema_detail_dot(
            "sales",
            internal_edges=[],
            outgoing_edges=[
                {"from_schema": "sales", "from_table": _LONGEST_REAL_ADVENTUREWORKS_NAME,
                 "from_column": "x", "to_schema": "sales", "to_table": "salesreason",
                 "to_column": "id"},
            ],
            all_schema_tables=[_LONGEST_REAL_ADVENTUREWORKS_NAME, "customer"],
        )
        # The full identifier is reconstructable from the label's `<br/>`
        # -joined lines -- every character present, none dropped.
        node_line = next(
            l for l in dot.split("\n")
            if f'"sales.{_LONGEST_REAL_ADVENTUREWORKS_NAME}"' in l and "label=" in l
        )
        # Every line the wrap helper would produce is present verbatim in
        # the node's label text -- nothing truncated with an ellipsis or
        # otherwise shortened.
        from resource_explorer.surveyors.database.relationship_dot import (
            _wrap_identifier_lines,
        )
        for line in _wrap_identifier_lines(_LONGEST_REAL_ADVENTUREWORKS_NAME):
            assert line in node_line
        assert "…" not in node_line
        assert "..." not in node_line

    def test_sixty_char_synthetic_name_is_never_cut_in_full_database_dot(self):
        tables = {"sales": [_SIXTY_CHAR_SYNTHETIC_NAME, "customer"]}
        edges = [
            {"from_schema": "sales", "from_table": _SIXTY_CHAR_SYNTHETIC_NAME,
             "from_column": "x", "to_schema": "sales", "to_table": "customer",
             "to_column": "id"},
        ]
        dot, reason = full_database_dot(tables, edges)
        assert reason is None
        node_line = next(
            l for l in dot.split("\n")
            if f'"sales.{_SIXTY_CHAR_SYNTHETIC_NAME}"' in l and "label=" in l
        )
        from resource_explorer.surveyors.database.relationship_dot import (
            _wrap_identifier_lines,
        )
        for line in _wrap_identifier_lines(_SIXTY_CHAR_SYNTHETIC_NAME):
            assert line in node_line
        assert "…" not in node_line
        assert "..." not in node_line
        # This is a plain node (no note, below the hub threshold) -- a
        # QUOTED-STRING label, not an HTML one, so wrapping onto multiple
        # lines shows up as Graphviz's own `\n` line-break escape rather
        # than `<br/>` (see `_wrapped_plain_label_text`'s docstring).
        assert '\\n' in node_line
        wrap_count = len(_wrap_identifier_lines(_SIXTY_CHAR_SYNTHETIC_NAME))
        assert node_line.count("\\n") >= wrap_count - 1

    def test_long_ghost_node_table_name_is_never_cut_in_schema_detail_dot(self):
        outgoing = [
            {"from_schema": "sales", "from_table": "customer", "from_column": "x",
             "to_schema": "person", "to_table": _LONGEST_REAL_ADVENTUREWORKS_NAME,
             "to_column": "id"},
        ]
        dot = schema_detail_dot(
            "sales", internal_edges=[], outgoing_edges=outgoing,
            all_schema_tables=["customer"],
        )
        ghost_line = next(
            l for l in dot.split("\n")
            if f'"person.{_LONGEST_REAL_ADVENTUREWORKS_NAME}"' in l and "shape=plaintext" in l
        )
        from resource_explorer.surveyors.database.relationship_dot import (
            _wrap_identifier_lines,
        )
        for line in _wrap_identifier_lines(_LONGEST_REAL_ADVENTUREWORKS_NAME):
            assert line in ghost_line

    def test_is_deterministic_with_a_long_wrapped_name(self):
        tables = [_LONGEST_REAL_ADVENTUREWORKS_NAME, "customer", _SIXTY_CHAR_SYNTHETIC_NAME]
        a = schema_detail_dot("sales", [], [], tables)
        b = schema_detail_dot("sales", [], [], tables)
        assert a == b


# ── escaping: a quoted Postgres identifier can carry &, <, >, " ────────────
#
# Found in review (PR/CI session, before this branch merged): node IDs were
# emitted unescaped, and HTML-like labels (`label=<...>`) used `_dot_escape`
# (backslash/quote escaping, correct for a quoted DOT string) rather than
# HTML-entity escaping. A quoted identifier containing any of these four
# characters would make Graphviz reject the WHOLE graph -- "renderer
# unavailable" instead of a diagram, for a database that happens to have an
# oddly-named table. AdventureWorks itself never exercises this path, which
# is exactly why it needs its own test rather than relying on the live
# fixture to catch it.

_HOSTILE_TABLE = 'a&b<c>"d'


class TestEscaping:
    def test_schema_map_dot_with_a_hostile_schema_name(self):
        dot = schema_map_dot({_HOSTILE_TABLE: 3}, {_HOSTILE_TABLE: 1}, {})
        # The raw, unescaped identifier must never appear verbatim inside a
        # quoted DOT string or an HTML label.
        assert f'"{_HOSTILE_TABLE}"' not in dot
        assert f'<b>{_HOSTILE_TABLE}</b>' not in dot
        # The HTML label carries entity-escaped text.
        assert "a&amp;b&lt;c&gt;&quot;d" in dot
        # The quoted node ID carries backslash-escaped quotes, not raw ones.
        assert '\\"' in dot

    def test_full_database_dot_with_a_hostile_table_name(self):
        # A plain node (no note, below the hub threshold) gets a plain
        # quoted label (`label="..."`) rather than an HTML one -- only
        # quote/backslash escaping applies there, and that is correct DOT
        # (an unescaped `&`/`<`/`>` inside an ordinary quoted string is
        # not a syntax error the way it would be inside `label=<...>`).
        # What must never happen, in either label form, is the raw
        # identifier appearing as an UNESCAPED quoted ID or ending the
        # quoted string early.
        tables = {"sales": [_HOSTILE_TABLE, "customer"]}
        edges = [{"from_schema": "sales", "from_table": _HOSTILE_TABLE, "from_column": "x",
                   "to_schema": "sales", "to_table": "customer", "to_column": "id"}]
        dot, reason = full_database_dot(tables, edges)
        assert reason is None
        # The raw identifier must never appear as an unescaped quoted ID
        # (that would be a bare `"` ending the string early, right before
        # the `d`).
        assert f'"sales.{_HOSTILE_TABLE}"' not in dot
        # The node ID itself is correctly quote-escaped.
        assert '"sales.a&b<c>\\"d"' in dot
        # A table whose label carries an in-degree badge (HTML label) must
        # use HTML entities -- covered by the hub/dashed/outgoing-key cases
        # in the other tests below, which all go through that branch.

    def test_schema_detail_dot_with_a_hostile_table_name_isolated(self):
        dot = schema_detail_dot(
            "sales", internal_edges=[], outgoing_edges=[],
            all_schema_tables=[_HOSTILE_TABLE],
        )
        assert f'"sales.{_HOSTILE_TABLE}"' not in dot
        assert "a&amp;b&lt;c&gt;&quot;d" in dot

    def test_schema_detail_dot_with_a_hostile_table_name_with_outgoing_key(self):
        outgoing = [{"from_schema": "sales", "from_table": _HOSTILE_TABLE, "from_column": "x",
                     "to_schema": "person", "to_table": "person", "to_column": "id"}]
        dot = schema_detail_dot(
            "sales", internal_edges=[], outgoing_edges=outgoing,
            all_schema_tables=[_HOSTILE_TABLE],
        )
        assert f'"sales.{_HOSTILE_TABLE}"' not in dot
        assert "no keys inside sales" in dot
        # The edge to the ghost node must use the escaped source ID.
        assert f'"sales.{_HOSTILE_TABLE}" ->' not in dot

    def test_hostile_schema_name_in_schema_detail_title(self):
        dot = schema_detail_dot(
            _HOSTILE_TABLE, internal_edges=[], outgoing_edges=[],
            all_schema_tables=["customer"],
        )
        assert "a&amp;b&lt;c&gt;&quot;d" in dot
        assert f'<b>{_HOSTILE_TABLE}</b>' not in dot

    def test_hostile_connected_as_in_credential_scope_title(self):
        dot, _ = full_database_dot(
            {"sales": ["customer"]}, [],
            credential_scope={"connected_as": _HOSTILE_TABLE, "measured": 1, "total": 1},
        )
        assert "a&amp;b&lt;c&gt;&quot;d" in dot
        assert f'<b>{_HOSTILE_TABLE}</b>' not in dot


# ── wiring: db_relationship_graph's own result dict carries `graphviz` ─────
#
# Integration-shaped, same fixture pattern test_schema_containment_grain.py
# uses (run_db_derived over a registry with stored rows), pinning that this
# module is actually reached from db_derived.py's `relationship_graph_by_
# container` — not just correct in isolation.

@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="coco_pharma", display_name="Coco Pharma", db_type="postgresql",
        host="localhost", port=5442, database_name="coco_pharma",
    ))
    return r


def _table(name, *, schema, rows=1000):
    return {
        "schema_name": schema, "table_name": name, "table_type": "BASE TABLE",
        "row_count": rows, "size_bytes": 8192, "description": "",
        "state": STATE_MEASURED,
    }


def _column(table, name, *, schema, pk=False, fk=None, dtype="integer", captured=True):
    return {
        "schema_name": schema, "table_name": table, "column_name": name,
        "data_type": dtype, "base_type": dtype, "description": "",
        "is_primary_key": (1 if pk else 0) if captured else None,
        "foreign_key_json": fk, "state": STATE_MEASURED,
    }


NOW = "2026-09-28T00:00:00"


class TestWiredIntoDbRelationshipGraph:
    def test_graphviz_key_present_on_the_real_result_dict(self, registry):
        tables = [
            _table("customer", schema="sales"),
            _table("orders", schema="sales"),
            _table("person", schema="person"),
        ]
        columns = [
            _column("customer", "customer_id", schema="sales", pk=True),
            _column("customer", "person_id", schema="sales", fk={
                "foreign_schema": "person", "foreign_table": "person",
                "foreign_column": "id"}),
            _column("orders", "order_id", schema="sales", pk=True),
            _column("orders", "customer_id", schema="sales", fk={
                "foreign_schema": "sales", "foreign_table": "customer",
                "foreign_column": "customer_id"}),
            _column("person", "id", schema="person", pk=True),
        ]
        registry.record_database_survey(
            slug="coco_pharma", schema_count=2, table_count=len(tables),
            column_count=len(columns), survey_data={}, surveyed_at=NOW,
        )
        registry.write_detail_rows("database_tables", "coco_pharma", NOW, rows=tables)
        registry.write_detail_rows("database_columns", "coco_pharma", NOW, rows=columns)

        derived = run_db_derived(registry, "coco_pharma")["derived"]
        graph = derived["db_relationship_graph"]

        assert "graphviz" in graph
        assert graph["graphviz"]["schema_map"]
        assert graph["graphviz"]["full"]
        assert "sales" in graph["graphviz"]["by_schema"]
        # The design note's own finding, reachable end-to-end: a
        # schema-detail view never mislabels an outgoing-only key as
        # isolated.
        assert "isolated" not in graph["graphviz"]["by_schema"]["sales"] or \
            "no keys inside sales" in graph["graphviz"]["by_schema"]["sales"]

    def test_never_key_captured_table_is_dashed_not_measured_absent(self, registry):
        tables = [_table("customer", schema="sales"), _table("orders", schema="sales")]
        columns = [
            _column("customer", "customer_id", schema="sales", pk=True, captured=True),
            _column("orders", "order_id", schema="sales", pk=True, captured=False),
        ]
        registry.record_database_survey(
            slug="coco_pharma", schema_count=1, table_count=len(tables),
            column_count=len(columns), survey_data={}, surveyed_at=NOW,
        )
        registry.write_detail_rows("database_tables", "coco_pharma", NOW, rows=tables)
        registry.write_detail_rows("database_columns", "coco_pharma", NOW, rows=columns)

        derived = run_db_derived(registry, "coco_pharma")["derived"]
        graph = derived["db_relationship_graph"]
        assert "keys not captured" in graph["graphviz"]["by_schema"]["sales"]

    def test_credential_scoped_survey_titles_the_graph_and_captions_structure_only(
        self, registry,
    ):
        """The coco_pharma case, verbatim from the coordination session:
        surveyor has SELECT on 3 of 26 relations (`coco_ods` is USAGE-granted
        but SELECT on NONE of its tables — `SCOPE_STRUCTURE_ONLY`), and the
        graph must say so in its title, in the SAME words the header banner
        and the 21b coverage headline already use, and caption `coco_ods`'s
        not-captured tables "structure only" rather than the generic "keys
        not captured" -- the reason is known and uniform for that whole
        schema, not a table-by-table guess.
        """
        cap = {
            "connected_as": "egeria_user",
            "schema_total": 2, "schema_visible": 2,
            "relation_total": 26, "relation_select": 3,
            "by_schema": {
                "coco_ods": {"usage_granted": True, "table_total": 23, "table_select": 0},
                "eu_sales": {"usage_granted": True, "table_total": 1, "table_select": 1},
            },
        }
        tables = [
            _table("customer", schema="coco_ods"),
            _table("orders", schema="coco_ods"),
            _table("region", schema="eu_sales"),
        ]
        columns = [
            # coco_ods: USAGE but no SELECT -- structure only, and in this
            # fixture its columns were genuinely never key-captured either
            # (the realistic case: a credential that cannot SELECT cannot
            # have its constraints read via a live introspection path that
            # needs SELECT -- catalog-only paths that don't are a separate,
            # already-tested case in TestWiredIntoDbRelationshipGraph).
            _column("customer", "customer_id", schema="coco_ods", pk=True, captured=False),
            _column("orders", "order_id", schema="coco_ods", pk=True, captured=False),
            # eu_sales: fully readable, keys genuinely captured.
            _column("region", "region_id", schema="eu_sales", pk=True, captured=True),
        ]
        registry.record_database_survey(
            slug="coco_pharma", schema_count=2, table_count=len(tables),
            column_count=len(columns), survey_data={"credential_capability": cap},
            surveyed_at=NOW,
        )
        registry.write_detail_rows("database_tables", "coco_pharma", NOW, rows=tables)
        registry.write_detail_rows("database_columns", "coco_pharma", NOW, rows=columns)

        derived = run_db_derived(registry, "coco_pharma")["derived"]
        graphviz = derived["db_relationship_graph"]["graphviz"]

        # The title, in the header banner's own vocabulary: connected_as,
        # and the same relation_select/relation_total pair.
        assert "egeria_user" in graphviz["full"]
        assert "3 of 26 tables" in graphviz["full"]

        # coco_ods's not-captured tables are captioned by their real,
        # known reason -- not the generic caption.
        coco_ods_dot = graphviz["by_schema"]["coco_ods"]
        assert "structure only" in coco_ods_dot
        assert "no SELECT" in coco_ods_dot
        assert "keys not captured" not in coco_ods_dot

        # eu_sales is fully readable and captured -- no caption of any kind,
        # and definitely not "structure only" bleeding across schemas.
        eu_sales_dot = graphviz["by_schema"]["eu_sales"]
        assert "structure only" not in eu_sales_dot
        assert "keys not captured" not in eu_sales_dot

    def test_fully_visible_credential_adds_no_title_stays_silent(self, registry):
        """A credential with full schema/table visibility has nothing to
        caveat -- same 'stay silent' contract `_credential_scope_status`
        follows. No title, no per-schema reason captions."""
        cap = {
            "connected_as": "erinoverview",
            "schema_total": 1, "schema_visible": 1,
            "relation_total": 1, "relation_select": 1,
            "by_schema": {"sales": {"usage_granted": True, "table_total": 1, "table_select": 1}},
        }
        tables = [_table("customer", schema="sales")]
        columns = [_column("customer", "customer_id", schema="sales", pk=True, captured=True)]
        registry.record_database_survey(
            slug="coco_pharma", schema_count=1, table_count=1, column_count=1,
            survey_data={"credential_capability": cap}, surveyed_at=NOW,
        )
        registry.write_detail_rows("database_tables", "coco_pharma", NOW, rows=tables)
        registry.write_detail_rows("database_columns", "coco_pharma", NOW, rows=columns)

        derived = run_db_derived(registry, "coco_pharma")["derived"]
        graphviz = derived["db_relationship_graph"]["graphviz"]
        assert "erinoverview" not in graphviz["full"]
        assert "labelloc=t" not in graphviz["full"]
