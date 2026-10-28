/** A real, node+jsdom render harness for `/next`'s vanilla-JS frontend.
 *
 * WHY THIS EXISTS: every JS-related test in this package before it existed
 * (grep `tests/test_next_*.py`) asserts against app.js's SOURCE TEXT --
 * "does this string appear near that string" -- never against actual
 * rendered DOM. That catches a function calling the wrong thing; it cannot
 * catch a function producing a DOM tree that, after a SECOND render pass
 * (a pane switch, a reload, a filter-then-expand), has lost state a first
 * render pass had. Two real bugs shipped this class of defect in one week
 * (2026-09-27/28) -- the engine-note-persistence bug (a transient DOM
 * append wiped by the next full-pane re-render) and the Schema Inventory
 * filter-then-expand bug (a column row's own visibility flag not surviving
 * its ancestor table's re-filter) -- and no source-text test could have
 * caught either, because both are about what the DOM looks like AFTER a
 * second render, not about what the source says once. See
 * NEXT-RENDER-HARNESS-IMPLEMENTED.md.
 *
 * WHAT THIS LOADS: the REAL `app.js`, unmodified except that three of its
 * previously-module-private render functions (`surveyRowHtml`, `tableHtml`,
 * `schemaTreeHtml`, `filterSchemaTree`) now carry an `export` keyword so a
 * test can call them directly -- no logic changed, only visibility, the
 * same pattern app.js already uses for `readEnvelope`/`esc`/`$` and the
 * rest of its 30-plus other exports. app.js is imported as its REAL ES
 * module graph (worklist.js, format.js, glyphs.js, envelope.js, re-api.js,
 * the stages/* and admin/* modules it pulls in) via the loader hook in
 * `static-loader.mjs`, which resolves its root-relative `/static/...`
 * specifiers to the real files under `resource_explorer/web/static/` --
 * NOT a copy, NOT a re-typed fixture.
 *
 * WHAT IS STUBBED, and why it's safe to stub: app.js's own module top level
 * runs exactly two side-effecting statements (the tail of the file) --
 * `document.getElementById('login-dismiss-btn')?.addEventListener(...)`
 * and `Auth.init(start)`. `Auth` is a global (loaded via a classic
 * `<script>` tag in index.html, not an ES import -- see app.js's own
 * comments), so importing app.js under node throws `ReferenceError: Auth
 * is not defined` unless something defines it first. The stub's `init`
 * intentionally does NOT invoke the callback it's given: `start()` is
 * app.js's full page bootstrap (a dozen parallel network fetches against
 * the real API) and this harness has no server to fetch from, nor any
 * need for one -- the two regression tests below call specific exported
 * render functions directly with fixture data, never the bootstrap path.
 * `fetch` is stubbed to reject loudly (`_UNSTUBBED_FETCH`) rather than
 * silently returning nothing, so a test that accidentally exercises a
 * network path fails with a clear message instead of hanging or reading
 * `undefined`.
 */
import { register } from 'node:module';
import { JSDOM } from 'jsdom';

let registered = false;

// Exported (2026-09-28, doc-sources-enrichment.test.mjs) so a test that
// needs to import a `/next` module by its PLAIN specifier -- to land on the
// exact same cached module instance a real `/next` file's own static
// `import ... from '/static/...'` resolves to, rather than a cache-busted
// copy `loadAppModule()` would produce -- can register the loader hook
// without going through `loadAppModule()` first. See that test file's
// `setUpEnrichmentDom` for why the distinction matters: a cache-busted
// `app.js` instance and enrichment.js's own plain-specifier import of
// `app.js` are TWO DIFFERENT module instances with two different `state`
// objects, so mutating one is invisible to code driven through the other.
export function ensureLoaderRegistered() {
  if (registered) return;
  register('./static-loader.mjs', import.meta.url);
  registered = true;
}

/** Builds a fresh jsdom `window`/`document`, installs the stubs app.js's
 *  own module top level needs, and returns them alongside a `loadApp()`
 *  that imports the real app.js against this environment. Each call gets
 *  its own jsdom instance -- tests must not share one, so a leftover DOM
 *  node from one test can never leak into another's assertions. */
export function makeDomEnvironment() {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'https://example.invalid/next/',
  });
  const { window } = dom;

  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.navigator = window.navigator;
  globalThis.sessionStorage = window.sessionStorage;
  globalThis.localStorage = window.localStorage;
  globalThis.HTMLElement = window.HTMLElement;
  globalThis.CustomEvent = window.CustomEvent;

  globalThis.fetch = async (...args) => {
    throw new Error(
      `_UNSTUBBED_FETCH: app.js called the real fetch() with ${JSON.stringify(args[0])}. ` +
      'This harness stubs fetch to fail loudly rather than hang or return nothing -- ' +
      'if a test needs a network response, stub globalThis.fetch before calling into app.js.',
    );
  };

  // See this module's own header comment: `init`'s callback (app.js's full
  // page bootstrap) is deliberately never invoked.
  globalThis.Auth = {
    init() {},
    hideLogin() {},
    getUser() { return null; },
    getHeaders() { return {}; },
  };

  return { dom, window, document: window.document };
}

/** Imports the real app.js module graph against whatever DOM environment
 *  is currently installed on `globalThis` (call `makeDomEnvironment()`
 *  first). Cache-busted per call (`?t=` query) so successive tests in the
 *  same `node --test` process each get a fresh module instance with its
 *  own top-level `state` object, rather than silently sharing app.js's
 *  singleton `state` across tests. */
export async function loadAppModule() {
  ensureLoaderRegistered();
  const cacheBust = `?t=${Date.now()}_${Math.random().toString(36).slice(2)}`;
  return import(`/static/next/app.js${cacheBust}`);
}
