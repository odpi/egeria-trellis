# Frontend build

Builds the bundled (non-CDN) frontend assets the UI loads:
`../resource_explorer/web/static/next/tailwind-next.css` and
`../resource_explorer/web/static/vendor/{marked,plotly,svg-pan-zoom}.min.js`.

Replaces `<script src="https://cdn....">`/`<link>` tags that previously
loaded these at runtime from external CDNs:

- **Tailwind** was the `cdn.tailwindcss.com` runtime JIT compiler — Tailwind
  itself documents this as unsuitable for production (it recompiles the
  stylesheet client-side on every page load).
- **marked** was pulled **unpinned** (`.../npm/marked/marked.min.js` — no
  version in the URL), meaning every page load got whatever "latest"
  happened to resolve to at that moment. A breaking upstream release could
  silently break this page with no warning and no way to pin or roll back.
- **plotly.js** and **svg-pan-zoom** were pinned by URL, but still a runtime
  dependency on an external CDN with no offline/degraded-network fallback.
- **mermaid** was pulled unpinned too, same risk as marked — but instead of
  vendoring it, diagrams now render **server-side** via the shared Kroki
  container (`POST /api/diagrams/mermaid`, a thin FastAPI proxy — see
  `resource_explorer/web/routes/diagrams.py`). The browser never loads
  mermaid.js at all now; `mermaid` was removed from this package's
  `devDependencies` and `build-vendor.js`'s copy list.

## Usage

```bash
cd frontend-build
npm install
npm run build          # builds both CSS and vendor JS
npm run build:css:next # Tailwind only
npm run build:vendor   # marked/plotly/svg-pan-zoom only
```

The build outputs are checked into the repo like any other static asset —
`npm`/`node_modules` are a build-time-only dependency, not a runtime one.
The UI just links/scripts against the built files.

## Running the /next render harness

```bash
cd frontend-build
npm install
npm run test:harness   # requires Node 20 or newer
```

The harness needs **Node 20+**. On Node 18 every test fails at load (12 of 12
in the first suite), which looks like a broken test but is a Node-version
mismatch: switch runtimes (`nvm use 20`) before debugging anything else.

## When to re-run

- `build:css:next`: any time a new Tailwind utility class is added under `static/next/` and doesn't already
  appear in the built stylesheet. `tests/test_tailwind_next_freshness.py` and
  `tests/test_tailwind_next_class_coverage.py` fail a stale build in CI.
  **Run `npm ci` first if `package.json`'s `devDependencies` changed**: the build runs against whatever is already
  in `node_modules`, so a stale install silently regenerates the stylesheet without the new plugin's rules.
- `build:vendor`: to pick up a version bump for marked/plotly.js/
  svg-pan-zoom — bump the version in `package.json`, `npm install`, then
  `npm run build:vendor`. Versions are pinned deliberately; don't `npm
  update` these casually without checking each library's changelog first.
