/* Column specs for the Curate scope page's two tables (see colresize.js for the spec shape).
 * Kept free of imports so a plain page can load them: the real-browser layout checks do. */

/* The scope tree. Defaults in px; "Schema / table" is a fixed-width column like the rest. */
export const SCOPE_COLS = {
  id: 'curate-scope-tree', chrome: 90,
  columns: [
    { key: 'sel', def: 24, min: 24, resizable: false },
    { key: 'choice', def: 210, min: 190 },
    { key: 'name', def: 280, min: 120, align: 'left', breakLong: true },
    { key: 'rows', def: 110, min: 100 },
    { key: 'size', def: 110, min: 100 },
    { key: 'act', def: 170, min: 100 },
    { key: 'cls', def: 170, min: 120 },
    { key: 'egeria', def: 240, min: 110 },
  ],
};

/* "What this commit does": row label (left), what, how many, when. Fixed px, never ch: a ch is
 * the width of a zero in the cell's own font, so a header in a smaller type than its cells sat
 * at a different left edge from them. */
export const MANIFEST_COLS = {
  id: 'curate-commit-manifest', chrome: 40,
  columns: [
    { key: 'who', def: 130, min: 110, align: 'left' },
    { key: 'what', def: 200, min: 100 },
    { key: 'many', def: 230, min: 100 },
    { key: 'when', def: 200, min: 100 },
  ],
};
