# `/next` render harness

A node+jsdom test harness that actually loads `/next`'s real ES module
graph (`app.js` and everything it imports) into a real DOM and renders it
-- as opposed to every other JS-related test in this package
(`tests/test_next_*.py`), which asserts only against app.js's *source
text*. See `NEXT-RENDER-HARNESS-IMPLEMENTED.md` for the
full write-up: why this exists, the two regression tests built on it, and
the red/green verification against the bugs they cover.

## Running it

```bash
cd frontend-build
npm ci               # installs jsdom (once)
npm run test:harness
```

Needs Node ≥ 20.6 (the `node:module` `register()` API used by
`static-loader.mjs` is stable from that version). Locally, if the default
`node` on your PATH is older:

```bash
export NVM_DIR="$HOME/.nvm" && source "$NVM_DIR/nvm.sh" && nvm use 20
```

CI (`.github/workflows/resource-explorer.yml`) pins Node 20 explicitly via
`actions/setup-node@v4`, alongside the same `npm ci` step that already
installs the pinned `tailwindcss` CLI for the freshness checks.

## How it works

- **`static-loader.mjs`** -- a Node ESM loader hook that resolves `/next`'s
  own root-relative import specifiers (`'/static/next/envelope.js'`,
  `'/static/re-api.js'`, etc.) to the real files under
  `resource_explorer/web/static/`. Without this, Node treats a specifier
  starting with `/` as an absolute filesystem path and looks for it at the
  OS root -- confirmed empirically, not assumed.
- **`dom-harness.mjs`** -- builds a fresh jsdom `window`/`document` per
  test, installs the small set of globals app.js's own module top level
  needs (`Auth`, a loudly-failing `fetch` stub, `sessionStorage`/
  `localStorage`), and imports the real `app.js` against that environment.
  `Auth.init`'s callback (app.js's full network-driven page bootstrap) is
  deliberately never invoked -- tests call specific exported render
  functions with fixture data instead.
- **`*.test.mjs`** -- plain `node --test` files, no extra test-runner
  dependency. Each calls `makeDomEnvironment()` + `loadAppModule()`, then
  exercises one or more of app.js's exported render functions against a
  real jsdom DOM.

## What changed in `app.js` itself

Four previously module-private functions gained an `export` keyword --
`surveyRowHtml`, `schemaTreeHtml`, `tableHtml`, `filterSchemaTree` -- no
logic changed. This is the same pattern app.js already uses for
`readEnvelope`/`esc`/`$` and its 30-plus other exports (see envelope.js's
own header comment on why some pieces of app.js were pulled out
specifically so a node-run test could import them without dragging in the
whole file's top-level DOM/auth side effects). These four stayed inline in
app.js rather than moving to their own module, so exporting them in place
was the minimal change.

## Adding a new render-harness test

1. Fixture data in, real exported function called, real jsdom DOM out.
2. Assert against the DOM (`element.style.display`, `.open`,
   `.textContent`, `querySelectorAll(...)`), not just the returned HTML
   string -- a string-only assertion is a test source-text tests could
   already do; the DOM is what this harness is for.
3. If the function you need isn't exported yet, add `export` to its
   declaration in app.js (no other change) and say so in your test's own
   comment, the same way the two existing tests do.
