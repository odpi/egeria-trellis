/** Node ESM loader hook (registered via `node:module`'s `register()`) that
 *  makes `/next`'s own root-relative import specifiers -- e.g.
 *  `'/static/next/envelope.js'`, `'/static/re-api.js'` -- resolve to the
 *  real files under `resource_explorer/web/static/` instead of Node's
 *  default behaviour, which is to treat a specifier starting with `/` as an
 *  absolute FILESYSTEM path and look for it at the OS root (confirmed
 *  empirically: `import('/static/next/a.js')` fails with "Cannot find
 *  module '/static/next/a.js'", not a resolution against the project).
 *
 *  This is what makes it possible to import `app.js` (and its real
 *  dependency graph -- `envelope.js`, `worklist.js`, `re-api.js`, `glyphs.js`,
 *  `format.js`, the `stages/*.js` and `admin/*.js` modules it pulls in) as
 *  the ACTUAL production module graph, unmodified, rather than re-typing or
 *  string-slicing pieces of it into a test fixture -- see this directory's
 *  README and NEXT-RENDER-HARNESS-IMPLEMENTED.md for why
 *  that distinction is the whole point of this harness.
 *
 *  Every other specifier (bare package names like `jsdom`, relative `./`
 *  paths) passes straight through to Node's own resolver untouched.
 */
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const STATIC_ROOT = path.resolve(
  fileURLToPath(new URL('.', import.meta.url)),
  '..', '..', 'resource_explorer', 'web', 'static',
);

function isStaticSpecifier(specifier) {
  return specifier.startsWith('/static/');
}

export async function resolve(specifier, context, nextResolve) {
  if (isStaticSpecifier(specifier)) {
    // Strip a query string (used by the harness to cache-bust app.js
    // itself, giving each test its own top-level `state` object) before
    // mapping to a real file path -- `path.join` must never see the `?`.
    const queryIndex = specifier.indexOf('?');
    const rel = queryIndex === -1
      ? specifier.slice('/static/'.length)
      : specifier.slice('/static/'.length, queryIndex);
    const search = queryIndex === -1 ? '' : specifier.slice(queryIndex);
    const fileUrl = pathToFileURL(path.join(STATIC_ROOT, rel)).href + search;
    return { url: fileUrl, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.startsWith(pathToFileURL(STATIC_ROOT).href)) {
    // These files have no nearby package.json declaring "type": "module",
    // so Node's default format sniffing would treat them as CommonJS and
    // choke on `export`/`import`. They are ES modules -- say so explicitly.
    return nextLoad(url, { ...context, format: 'module' });
  }
  return nextLoad(url, context);
}
