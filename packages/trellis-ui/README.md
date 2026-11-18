# trellis-ui

The browser pieces Resource Explorer and Egeria Advisor share, served by each app at `/static/trellis-ui/`:
the three looks (`themes.css`, `look.js`), resizable table columns (`colresize.js`),
markdown rendering (`markdown.js`), the run-envelope renderer with its formatting helpers
(`envelope.js`, `format.js`), and pyegeria's structured TABLE renderer (`egeria-table.js`).

`egeria-table.js`'s home is **pyegeria's package data** (owner decision D3, 2026-11-14: `pyegeria.view.renderers`),
so the renderer ships with the executor whose shape it draws. `mount()` serves the installed pyegeria's copy at
`/static/trellis-ui/egeria-table.js` and falls back to the copy **vendored unchanged** here from the Egeria Portal
only when that pyegeria predates the renderer. It says which: `trellis_ui.egeria_table_source()`, the
`X-Egeria-Table-Source` response header, and a log line at mount. `egeria-table.js.source.json` names the vendored
copy's source commit and sha256, and EA's `tests/unit/test_dashboard_structured_table.py`
(`test_vendored_egeria_table_is_the_portals_file_unchanged`) fails if it drifts. Never edit it here: an app's own
needs go through its options (`linkBuilder`). The vendored copy goes once the trellis pyegeria floor ships the
renderer.

The files ship as package data; an app serves them with `trellis_ui.mount(app)`, called **before** its own
`/static` mount. See `trellis_ui/__init__.py`.

`themes.css` is the one place the looks' colours live. Each app maps its own Tailwind names onto its
variables: RE in `frontend-build/tailwind-next.config.js`, EA in the `tailwind.config` block in each page's
`<head>`. Contrast is checked by RE's `tests/test_themes_contrast.py` and, for EA's pages, by
`packages/egeria-advisor/tests/unit/test_looks_contrast.py`.
