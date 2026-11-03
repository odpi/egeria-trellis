#!/usr/bin/env node
// Copies pre-built dist files from node_modules into
// ../resource_explorer/web/static/vendor/, renaming each to match what
// the UI's <script src="/static/vendor/...​"> tags expect, and strips
// any `//# sourceMappingURL=...` comment left pointing at the source's
// original filename — since we rename on copy, that comment would 404 in
// the browser console for a .map file that was never fetched in the first
// place (harmless, but noisy and confusing to debug from a console error
// alone). See frontend-build/README.md for when to re-run this.
const fs = require('fs');
const path = require('path');

const OUT_DIR = path.join(__dirname, '..', 'resource_explorer', 'web', 'static', 'vendor');

// mermaid is NOT vendored — diagrams render server-side via the shared
// Kroki container (POST /api/diagrams/mermaid) instead of client-side
// mermaid.js. See diagrams.py.
const FILES = [
  ['node_modules/marked/lib/marked.umd.js', 'marked.min.js'],
  ['node_modules/plotly.js-dist-min/plotly.min.js', 'plotly.min.js'],
  ['node_modules/svg-pan-zoom/dist/svg-pan-zoom.min.js', 'svg-pan-zoom.min.js'],
];

fs.mkdirSync(OUT_DIR, { recursive: true });

for (const [src, destName] of FILES) {
  const srcPath = path.join(__dirname, src);
  const destPath = path.join(OUT_DIR, destName);
  let content = fs.readFileSync(srcPath, 'utf8');
  const stripped = content.replace(/\n?\/\/# sourceMappingURL=.*$/m, '');
  fs.writeFileSync(destPath, stripped);
  console.log(`${src} -> vendor/${destName}${stripped !== content ? ' (stripped sourceMappingURL)' : ''}`);
}
