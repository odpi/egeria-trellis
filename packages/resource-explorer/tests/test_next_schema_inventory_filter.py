"""Schema Inventory tab's filter box and node-kind labels in /next
(Slice 22 follow-up, Dan's usability gate on `laz_local_adventureworks`,
2026-09-27).

Task 1 of the gate FAILED on the served page: with a filter typed (e.g.
"salesorderheader"), clicking a matched table's triangle opened nothing --
no column rows appeared, even though the same triangle showed columns with
no filter active. `filterSchemaTree()` used to test every `[data-tree-node]`
independently against `data-tree-text`, which for a schema/table used to be
the schema/table's own name PLUS every descendant name concatenated
together -- so a table "matched" via its concatenated text (which includes
the query as its own name), but its column rows (each carrying only their
own name) did not, and were hidden independently even though the table's
own `<details>` was open.

`schemaTreeHtml`/`tableHtml` now write `data-tree-text` as a node's OWN
name only (matching what column rows already did), and `filterSchemaTree`
was rewritten as a proper recursion (`filterTreeNode`/`directTreeChildren`)
implementing the gate's own rule: a node whose own name matches shows every
descendant unconditionally (but still collapsed, unless individually
opened); a node whose own name does not match but some descendant's does
stays visible with its own `<details>` forced open, and unmatched siblings
are hidden; a node with no match anywhere under it is hidden.

Also from the same gate: a clear (x) control for the filter box (task 2),
and quiet node-kind labels -- "schema"/"table"/"view"/"matview" -- so a
reader can tell a view from a base table without opening it (task 3).

No browser verification with a signed-in session is asserted here -- see
this branch's own report for what was verified live instead (a standalone
DOM harness in the built-in browser, not the authenticated app) -- same
static-source-assertion pattern test_next_db_server_discovery.py
established.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _fn(name: str, terminator: str = "\n}\n") -> str:
    src = _app()
    start = src.index(name)
    end = src.index(terminator, start)
    return src[start:end]


class TestOwnNameOnlySearchText:
    """The root cause: data-tree-text must be a node's OWN name, not a
    concatenation of itself and its descendants -- the concatenation is
    what let a table "match" while its own columns silently didn't."""

    def test_schema_search_text_is_the_schema_name_alone(self):
        body = _fn("function schemaTreeHtml(schemas)", "\n}\n\nexport function tableHtml")
        assert "data-tree-text=\"${esc(s.schema.toLowerCase())}\"" in body
        # The old concatenation join must be gone, not just unused.
        assert "...(s.tables || [])" not in body

    def test_table_search_text_is_the_table_name_alone(self):
        body = _fn("function tableHtml(t)")
        assert "data-tree-text=\"${esc(t.name.toLowerCase())}\"" in body
        assert "...(t.columns || [])" not in body

    def test_column_rows_already_carried_their_own_name_and_still_do(self):
        body = _fn("function tableHtml(t)")
        assert "data-tree-text=\"${esc(c.name.toLowerCase())}\"" in body


class TestFilterIsARealRecursionNotIndependentPerNodeMatching:
    def test_filter_tree_node_helper_exists(self):
        app = _app()
        assert "function filterTreeNode(el, q)" in app

    def test_direct_tree_children_helper_exists(self):
        app = _app()
        assert "function directTreeChildren(el)" in app

    def test_a_self_match_shows_every_descendant_unconditionally(self):
        body = _fn("function filterTreeNode(el, q)")
        assert "el.querySelectorAll('[data-tree-node]').forEach((n) => { n.style.display = ''; });" in body

    def test_a_self_match_opens_its_own_details(self):
        body = _fn("function filterTreeNode(el, q)")
        assert "if (el.tagName === 'DETAILS') el.open = true;" in body

    def test_a_descendant_only_match_still_opens_the_ancestor(self):
        body = _fn("function filterTreeNode(el, q)")
        assert "if (anyChildMatched && el.tagName === 'DETAILS') el.open = true;" in body

    def test_no_match_anywhere_hides_the_node(self):
        body = _fn("function filterTreeNode(el, q)")
        assert "el.style.display = anyChildMatched ? '' : 'none';" in body

    def test_direct_tree_children_stops_descent_at_the_first_tree_node(self):
        # Otherwise a schema's traversal would reach straight through its
        # tables into their columns, treating columns as the schema's own
        # direct children -- exactly the flattening bug this rewrite fixes.
        body = _fn("function directTreeChildren(el)")
        assert "if (child.matches('[data-tree-node]')) out.push(child);" in body
        assert "else walk(child);" in body

    def test_filter_schema_tree_uses_the_recursion_not_a_flat_query_select_all(self):
        body = _fn("function filterSchemaTree(raw)")
        assert "directTreeChildren(root).forEach((n) => filterTreeNode(n, q));" in body
        assert "document.querySelectorAll('#schema-tree [data-tree-node]')" not in body


class TestClearControl:
    def test_a_clear_button_exists_in_the_filter_box(self):
        body = _fn("async function loadSchemaInventoryPane()")
        assert 'id="schema-tree-filter-clear"' in body
        assert "Clear filter" in body

    def test_the_clear_button_is_wired_to_empty_and_refilter(self):
        body = _fn("function bindSchemaTreeFilter()")
        assert "input.value = '';" in body
        assert "filterSchemaTree('');" in body

    def test_escape_also_clears(self):
        body = _fn("function bindSchemaTreeFilter()")
        assert "e.key === 'Escape'" in body

    def test_the_clear_button_is_hidden_until_there_is_something_to_clear(self):
        body = _fn("function bindSchemaTreeFilter()")
        assert "clearBtn.classList.toggle('hidden', !input.value);" in body


class TestTableKindLabels:
    """Dan asked for these "without being didactic" -- quiet, muted words,
    not a loud badge."""

    def test_kind_label_map_covers_the_four_relation_kinds(self):
        app = _app()
        start = app.index("const _TABLE_KIND_LABELS")
        end = app.index("};", start)
        block = app[start:end]
        assert "'BASE TABLE': 'table'" in block
        assert "'VIEW': 'view'" in block
        assert "'MATERIALIZED VIEW': 'matview'" in block

    def test_table_html_renders_the_kind_label_muted(self):
        body = _fn("function tableHtml(t)")
        assert "_TABLE_KIND_LABELS[t.table_type]" in body
        assert "text-caveat text-ink-muted" in body

    def test_schema_row_also_names_its_own_kind(self):
        body = _fn("function schemaTreeHtml(schemas)", "\n}\n\nexport function tableHtml")
        assert "<span class=\"text-caveat text-ink-muted\"> schema</span>" in body
