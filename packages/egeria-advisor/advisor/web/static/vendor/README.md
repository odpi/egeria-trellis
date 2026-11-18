# Vendored browser libraries (EA)

EA's pages load these from `/static/vendor/`, never from a CDN, so EA renders with no network and the
code it runs is pinned and reviewable (Phase 0, S2). Licences are in `licenses/`. Do not edit the files;
to upgrade, replace the file from the package named below, update the `?v=` query in `index.html`, and
update this table.

| File | Package and version | Taken from | Licence |
|---|---|---|---|
| `marked.min.js` | marked 18.0.9 | the copy RE already vendors (`resource_explorer/web/static/vendor/marked.min.js`, same bytes) | MIT |
| `purify.min.js` | dompurify 3.4.17 | npm `dompurify@3.4.17`, `dist/purify.min.js` | Apache-2.0 or MPL-2.0 |
| `mermaid.min.js` | mermaid 12.2.0 | npm `mermaid@12.2.0`, `dist/mermaid.min.js` | MIT |
| `vega.min.js` | vega 5.33.1 | npm `vega@5.33.1`, `build/vega.min.js` | BSD-3-Clause |
| `vega-lite.min.js` | vega-lite 5.23.0 | npm `vega-lite@5.23.0`, `build/vega-lite.min.js` | BSD-3-Clause |
| `vega-embed.min.js` | vega-embed 6.29.0 | npm `vega-embed@6.29.0`, `build/vega-embed.min.js` | BSD-3-Clause |
| `tailwindcss-play.min.js` | Tailwind CSS Play CDN script 3.4.17 | `https://cdn.tailwindcss.com/3.4.17` (the script the page loaded before) | MIT |

The vega majors (5 / 5 / 6) are the ones the page's CDN URLs pinned. mermaid and marked were unpinned on
the CDN (always latest); 12.2.0 is the newest mermaid release at least two weeks old on 2026-11-10.

Markdown is rendered only through `../render.js` (`renderMarkdown`), which runs `marked` and then
DOMPurify. `tests/unit/test_static_render_safety.py` fails if a page calls `marked.parse` anywhere else
or loads a script from a CDN.

Running the browser-side harness (`../test/render.test.mjs`, also run by that unit test):
- `EA_JSDOM_DIR`: a `node_modules` directory holding jsdom, e.g.
  `<trellis>/packages/resource-explorer/frontend-build/node_modules` (after `npm ci` there). Without
  it, that directory in this or the main trellis checkout is tried.
- `EA_RENDER_HARNESS_REQUIRED=1`: a missing node or jsdom, or any skipped harness test, is a failure
  instead of a skip. The gate sets it.

mermaid is initialised with `securityLevel: 'strict'` (index.html), so diagram labels can't carry HTML
or click handlers.
