/** Real-DOM regression test for the Schema Inventory filter-then-expand
 *  bug (Slice 22 follow-up, fixed 2026-09-27, commit 93e50e27,
 *  SLICE-22-SCHEMA-INVENTORY-VIEW-IMPLEMENTED.md,
 *  tests/test_next_schema_inventory_filter.py).
 *
 *  RESEARCH (git log / design-notes, since the task brief said the harness
 *  should catch this class of defect without needing to fully reconstruct
 *  it first): the bug is documented in commit 93e50e27's own message and
 *  in `filterTreeNode`'s doc comment in app.js. Found live by the project
 *  owner gating `laz_local_adventureworks`: typing a table name (e.g.
 *  "salesorderheader") into the Schema Inventory filter box opened the
 *  matching table's `<details>` but showed NO columns underneath it, even
 *  though the same triangle showed columns fine with no filter active.
 *
 *  ROOT CAUSE: `data-tree-text` on a schema/table node used to be that
 *  node's own name PLUS every descendant name concatenated together, and
 *  `filterSchemaTree()` tested every `[data-tree-node]` independently
 *  against that attribute. A table's concatenated text contained the
 *  query (via its own name), so the table itself "matched" and its
 *  `<details>` was force-opened -- but each COLUMN row carries only its
 *  own name, never the table's, so a column row was matched (or not)
 *  independently of its own parent table's match, and stayed `display:
 *  none` under an open `<details>`. This is exactly the render-state class
 *  this harness exists for: a static read of the source shows the filter
 *  logic runs and opens SOMETHING, which is all `test_next_schema_
 *  inventory_filter.py`'s pre-existing source-text tests can check --
 *  only building the actual tree in a real DOM, filtering it, and reading
 *  back each row's actual `style.display`/`.open` catches that the
 *  COLUMN rows stayed hidden.
 *
 *  THE FIX: `data-tree-text` is now always a node's OWN name only, and
 *  `filterSchemaTree` was rewritten as a real recursion
 *  (`filterTreeNode`/`directTreeChildren`, both still module-private --
 *  exercised here only through the exported `filterSchemaTree` entry
 *  point, the same surface a real filter keystroke drives): a node whose
 *  own name matches shows every descendant unconditionally; a node whose
 *  own name doesn't match but some descendant's does stays visible with
 *  its own `<details>` forced open, hiding non-matching siblings; no match
 *  anywhere hides the node.
 *
 *  GAP FLAGGED (per the task brief -- report this, don't guess silently):
 *  a related but DIFFERENT bug was found in the SAME gate and explicitly
 *  deferred (commit b033a6f9): `filterTreeNode` force-opens every matched
 *  table's `<details>` unconditionally, so two simultaneous table matches
 *  both dump their full column lists at once. That follow-up is NOT
 *  fixed on main as of this branch and is deliberately NOT tested here --
 *  this file covers only the original, fixed filter-then-expand bug.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

function fixtureSchemas() {
  return [
    {
      schema: 'public',
      classification: 'data',
      table_count: 2,
      row_total: 1500,
      is_estimate: false,
      reason: '',
      tables: [
        {
          name: 'salesorderheader',
          table_type: 'BASE TABLE',
          row_count: 1000,
          row_count_state: 'measured',
          size_bytes: 65536,
          column_count: 2,
          columns: [
            { name: 'salesorderid', type: 'integer', nullable: false, key_role: 'PK', comment: '' },
            { name: 'orderdate', type: 'timestamp', nullable: true, key_role: '', comment: '' },
          ],
        },
        {
          name: 'customer',
          table_type: 'BASE TABLE',
          row_count: 500,
          row_count_state: 'measured',
          size_bytes: 32768,
          column_count: 2,
          columns: [
            { name: 'customerid', type: 'integer', nullable: false, key_role: 'PK', comment: '' },
            { name: 'name', type: 'text', nullable: true, key_role: '', comment: '' },
          ],
        },
      ],
    },
  ];
}

/** Renders the tree into a real `#schema-tree` container the way
 *  `loadSchemaInventoryPane()` does (`$('schema-tree').innerHTML =
 *  schemaTreeHtml(tree.schemas)`), and returns it. */
function renderSchemaTree(document, schemaTreeHtml, schemas) {
  const container = document.createElement('div');
  container.id = 'schema-tree';
  document.body.appendChild(container);
  container.innerHTML = schemaTreeHtml(schemas);
  return container;
}

function isVisible(el) {
  return el.style.display !== 'none';
}

test('filtering by a TABLE name opens it and shows all of its columns (not just the table itself)', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = renderSchemaTree(document, app.schemaTreeHtml, fixtureSchemas());

  app.filterSchemaTree('salesorderheader');

  const salesTable = [...container.querySelectorAll('[data-tree-node]')]
    .find((n) => n.dataset.treeText === 'salesorderheader');
  assert.ok(salesTable, 'fixture must render a data-tree-node for the salesorderheader table');
  assert.ok(isVisible(salesTable), 'the matched table itself must be visible');
  assert.equal(salesTable.tagName, 'DETAILS');
  assert.equal(salesTable.open, true, 'the matched table must be force-opened by the filter');

  // THE BUG, exactly: pre-fix, these column rows stayed `display: none`
  // because they were matched independently against their OWN name
  // ("salesorderid"/"orderdate"), neither of which contains the query
  // "salesorderheader" -- even though their parent <details> was open.
  const salesColumnRows = [...salesTable.querySelectorAll('[data-tree-node]')];
  assert.equal(salesColumnRows.length, 2, 'the matched table fixture must carry 2 column rows');
  for (const row of salesColumnRows) {
    assert.ok(
      isVisible(row),
      `column row "${row.dataset.treeText}" under a matched table must be visible ` +
      '-- this is the exact filter-then-expand bug (Slice 22) if it fails',
    );
  }
});

test('a sibling table with no match anywhere under it is hidden entirely', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = renderSchemaTree(document, app.schemaTreeHtml, fixtureSchemas());
  app.filterSchemaTree('salesorderheader');

  const customerTable = [...container.querySelectorAll('[data-tree-node]')]
    .find((n) => n.dataset.treeText === 'customer');
  assert.ok(customerTable);
  assert.equal(isVisible(customerTable), false, 'a non-matching sibling table must be hidden');
});

test('filtering by a COLUMN name opens the table that owns it, straight to that column', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = renderSchemaTree(document, app.schemaTreeHtml, fixtureSchemas());
  app.filterSchemaTree('customerid');

  const customerTable = [...container.querySelectorAll('[data-tree-node]')]
    .find((n) => n.dataset.treeText === 'customer');
  assert.ok(customerTable);
  assert.equal(customerTable.open, true, 'the owning table must be force-opened for a column match');
  assert.ok(isVisible(customerTable));

  const matchedColumn = [...customerTable.querySelectorAll('[data-tree-node]')]
    .find((n) => n.dataset.treeText === 'customerid');
  assert.ok(matchedColumn && isVisible(matchedColumn), 'the matched column row itself must be visible');

  const salesTable = [...container.querySelectorAll('[data-tree-node]')]
    .find((n) => n.dataset.treeText === 'salesorderheader');
  assert.equal(isVisible(salesTable), false, 'the table that does not own the matched column must be hidden');
});

test('clearing the filter (empty query) restores every node to visible', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();

  const container = renderSchemaTree(document, app.schemaTreeHtml, fixtureSchemas());
  app.filterSchemaTree('salesorderheader');
  app.filterSchemaTree('');

  const allNodes = [...container.querySelectorAll('[data-tree-node]')];
  assert.ok(allNodes.length > 0);
  for (const n of allNodes) {
    assert.ok(isVisible(n), `node "${n.dataset.treeText}" should be visible again once the filter is cleared`);
  }
});
