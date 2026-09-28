/* re-api.js — the client half of Resource Explorer's API, as an ES module.
 *
 * Placed at /static/re-api.js rather than under /static/next/ deliberately:
 * it is meant to be imported by BOTH shells, and a module that lives inside
 * one UI's directory is a module the other will never adopt. Today only
 * /next imports it; rewiring index.html's inlined equivalents to import it
 * is a separate change with its own verification, because index.html's
 * copies carry behaviour (toasts, view routing) this module deliberately
 * does not.
 *
 * Every function here returns data or throws. Nothing in this file touches
 * the DOM, shows a toast, or knows what a tab is — that is what makes it
 * shareable.
 *
 * AUTH: /static/auth.js monkey-patches window.fetch to attach the bearer
 * token to same-origin requests, and it must be loaded before this module
 * issues its first call. This module does not re-implement that.
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** A failed API call, carrying the status so a caller can tell 401 from 500. */
export class ApiError extends Error {
  constructor(status, message, path) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.path = path;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (err) {
    // A network-level failure (the fetch() call itself threw — a plain
    // TypeError, "Failed to fetch"/"NetworkError", never an ApiError) means
    // window.location.origin never answered at all: the RE server is
    // stopped, or this tab points at a port nothing serves any more.
    // Left as the raw TypeError, every caller's own catch block reads
    // `err.message` and shows the literal string "Failed to fetch" —
    // found live, 2026-09-27: the "Register Database Server" dialog showed
    // it three times over ("Could not load registered servers: Failed to
    // fetch", "Failed to fetch", "✗ Request failed: Failed to fetch"),
    // which reads as three different failures, and as a DATABASE or Egeria
    // problem rather than what it actually is — this tab's own server is
    // unreachable. One clear message here fixes every caller at once.
    throw new ApiError(
      0,
      `Resource Explorer at ${window.location.origin} is not responding — `
        + 'the server may be stopped, or this tab may point at a port that '
        + 'is no longer served.',
      path,
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch (_) { /* a non-JSON error body is still an error */ }
    throw new ApiError(res.status, detail, path);
  }
  if (res.status === 204) return null;
  return res.json();
}

const get = (path) => request(path);
const post = (path, body) =>
  request(path, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const patch = (path, body) =>
  request(path, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const put = (path, body) =>
  request(path, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const del = (path) => request(path, { method: 'DELETE' });

/* ────────────────────────────────────────────────────────────────────────
 * A small TTL cache.
 *
 * The catalog and perspective vocabularies change when someone edits a YAML
 * file, not between two clicks, so re-fetching them per render is waste.
 * Answers are NOT cached: an answer's whole job is to say what is true now,
 * and a stale one is the failure this app spends most of its design effort
 * avoiding.
 * ──────────────────────────────────────────────────────────────────────── */
const CACHE_TTL_MS = 60_000;
const _cache = new Map();

async function cached(key, loader, ttl = CACHE_TTL_MS) {
  const hit = _cache.get(key);
  if (hit && Date.now() - hit.at < ttl) return hit.value;
  const value = await loader();
  _cache.set(key, { at: Date.now(), value });
  return value;
}

/** Drop every cached vocabulary. Call after anything that edits a catalog. */
export function clearCache() {
  _cache.clear();
}

/* ── Session ─────────────────────────────────────────────────────────── */

export const getPolicy = () => cached('policy', () => get('/api/auth/policy'));
export const getMe = () => get('/api/auth/me');

/* ── Resources ───────────────────────────────────────────────────────── */

/**
 * Every registered repo.
 *
 * NOTE the default filters, which are stronger than their names suggest:
 * `include_ignored=false` drops BOTH `ignored` and `abandoned` repos, and
 * `include_working_set_hidden=false` drops anything hidden from the personal
 * view. A sidebar that offers disposition facets has to ask for the full
 * list and filter client-side, or the facets for those two dispositions can
 * never have anything in them.
 */
export const listProjects = ({ includeIgnored = false, includeHidden = false } = {}) =>
  get(`/api/projects/?include_ignored=${includeIgnored}`
      + `&include_working_set_hidden=${includeHidden}`);

/** The Scouting-tier facts for one repo — this is where `homepage`,
 *  `last_published_at` and `egeria_link_stale` live; they are NOT on the
 *  summary row that the list endpoint returns. */
export const getScoutingOverview = (slug) =>
  get(`/api/projects/${encodeURIComponent(slug)}/scouting-overview`);
export const getProject = (slug) => get(`/api/projects/${encodeURIComponent(slug)}`);
export const listDatabases = () => get('/api/databases/');

/** Slice 22 — the whole Schemas → Tables → Columns tree for one database's
 *  Schema Inventory view, one call. See `schema_inventory_tree()`'s own
 *  docstring (survey_definition_adapter.py) for the shape. */
export const getSchemaInventoryTree = (slug) =>
  get(`/api/databases/${encodeURIComponent(slug)}/schema-inventory-tree`);
export const listFilesystems = () => get('/api/filesystems/');

/* ── Database servers (web/routes/db_servers.py) ────────────────────────
 *
 * Classic's (index.html) real mechanism for finding databases: a server is
 * registered once with stored credentials, then `discoverDatabases` connects
 * and lists what is actually on it (`DiscoveredDatabase` rows, each flagging
 * `is_registered` so an already-added database can be shown but disabled),
 * and `addDiscoveredDatabase` turns a chosen candidate into a real
 * `DatabaseEntity` row (what `listDatabases` above returns). Ported for
 * /next by db-server-discovery.js, reached from the sidebar's `find-repos`
 * action for `state.resourceType === 'db'`.
 */
export const listDbServers = () => get('/api/db-servers/');

export const registerDbServer = (payload) => post('/api/db-servers/register', payload);

export const deleteDbServer = (slug) =>
  request(`/api/db-servers/${encodeURIComponent(slug)}`, { method: 'DELETE' });

/** Test a REGISTERED server's stored credentials -- `overrides` lets a
 *  caller test host/port/credentials that haven't been saved yet, same as
 *  classic's `TestConnectionRequest` (all fields optional, falling back to
 *  the stored values server-side). */
export const testDbServer = (slug, overrides = {}) =>
  post(`/api/db-servers/${encodeURIComponent(slug)}/test`, overrides);

/** Test connection details BEFORE a server is registered at all -- classic's
 *  `_test-inline` route, used by the registration form's own "Test" button. */
export const testDbServerInline = (payload) => post('/api/db-servers/_test-inline', payload);

/** Connects to the server right now and returns what's actually there --
 *  read-only, like `searchDiscoveryRepos`: nothing is added to `databases`
 *  until `addDiscoveredDatabase` is called for a chosen row. */
export const discoverDatabases = (slug) =>
  post(`/api/db-servers/${encodeURIComponent(slug)}/discover`);

/** Registers one discovered database as a real `DatabaseEntity`. The route
 *  takes `database_name`/`display_name` as query params, not a JSON body
 *  (see db_servers.py's `add_database_from_server` signature) -- hence the
 *  query string here rather than `post()`'s JSON body. */
export const addDiscoveredDatabase = (slug, databaseName, displayName = '') =>
  post(`/api/db-servers/${encodeURIComponent(slug)}/add-database`
    + `?database_name=${encodeURIComponent(databaseName)}`
    + (displayName ? `&display_name=${encodeURIComponent(displayName)}` : ''));

/* ── Catalog vocabularies ────────────────────────────────────────────── */

/** The perspectives that can actually narrow something — never a hardcoded
 *  list. The vocabulary is a union of the analysis and question catalogs and
 *  has changed size more than once. */
export const listPerspectives = () =>
  cached('perspectives', () => get('/api/analyses/perspectives'));

/** The whole vocabulary -- an audience, not a filter. */
export const listAllPerspectives = () =>
  cached('perspectives-all', () => get('/api/analyses/perspectives?scope=all'));

export const listAnalyses = (resourceType, { intent, perspective } = {}) => {
  const qs = new URLSearchParams();
  if (intent) qs.set('intent', intent);
  if (perspective) qs.set('perspective', perspective);
  const suffix = qs.toString() ? `?${qs}` : '';
  return cached(`analyses:${resourceType}${suffix}`, () =>
    get(`/api/analyses/${encodeURIComponent(resourceType)}${suffix}`));
};

/* ── Questions and answers ───────────────────────────────────────────── */

/** repo -> /api/projects/{slug}/scouting-questions (the original, and the
 *  only route that keeps that name — database/filesystem's equivalent
 *  routes are just called `.../questions`, added later). Not a general-
 *  purpose entity-type-to-path-prefix mapper: kept private and narrow to
 *  these two routes' actual shapes rather than invented as a shared utility
 *  nothing else needs yet. */
function _questionsPath(entityType, slug) {
  const enc = encodeURIComponent(slug);
  if (entityType === 'database') return `/api/databases/${enc}/questions`;
  if (entityType === 'filesystem') return `/api/filesystems/${enc}/questions`;
  return `/api/projects/${enc}/scouting-questions`;
}

/**
 * The question checklist for one resource and one funnel stage.
 *
 * `perspectives` is a Set or array; empty means all, which is the API's own
 * default — do not send an empty `perspectives=` param, it is not the same
 * thing as omitting it.
 *
 * `entityType` defaults to 'repo' for every existing caller — pass the
 * `apiEntityType(state.resourceType)`-translated value at every /next
 * boundary crossing, same as `getSurveyCandidates`/`runSurveyDefinition`
 * already do, so a database's 'db' never reaches here untranslated.
 */
export function getQuestions(slug, { phase = 'scouting', perspectives = [], purposes = [], entityType = 'repo' } = {}) {
  const qs = new URLSearchParams({ phase });
  const persp = [...perspectives];
  const purp = [...purposes];
  if (persp.length) qs.set('perspectives', persp.join(','));
  if (purp.length) qs.set('purposes', purp.join(','));
  return get(`${_questionsPath(entityType, slug)}?${qs}`);
}

/**
 * The answer envelope for one catalogued question, from the FactLayer.
 *
 * This is the source of the answer, the caveat and the state in a question
 * row. Facts arrive ALREADY JUDGED — each carries a state from
 * `surveyors/result_status.py`'s vocabulary (`measured`, `nothing_found`,
 * `partial`, `never_run`, `not_established`) rather than a bare value.
 *
 * An envelope whose `answerable` is false carries `blocked_reason` and MUST
 * NOT be rendered as a negative answer about the resource. For most of the
 * 41 catalogued questions that is the correct outcome, and inventing an
 * answer for them is exactly what this layer exists to prevent.
 *
 * `entityType` defaults to 'repo' for existing callers, same as
 * `getQuestions()` above — pass `apiEntityType(state.resourceType)` at
 * every /next boundary crossing. Omitting it used to mean every lookup
 * silently searched the repo catalog regardless of the resource's real
 * type: a database/filesystem question worded identically to a repo one
 * matched the repo's entry (wrong analysis/mechanism, same slug); one
 * worded differently 404'd outright ("not in the catalog the answer layer
 * reads").
 */
export const getAnswer = (slug, question, entityType = 'repo') =>
  get(`/api/analyses/facts/${encodeURIComponent(slug)}/answer`
      + `?question=${encodeURIComponent(question)}&entity_type=${encodeURIComponent(entityType)}`);

/* ── Write paths ─────────────────────────────────────────────────────── */

/**
 * A repo's disposition.
 *
 * Keyed on `github_url`, NOT the slug — the endpoint is reachable for a repo
 * that has not been imported yet, and it resolves the slug server-side.
 * Passing a slug here silently fails to match anything.
 */
export const VALID_DISPOSITIONS = [
  'undecided', 'tracking', 'investigating', 'recommended',
  'using', 'abandoned', 'ignored',
];

export const setDisposition = (githubUrl, disposition, reason = '') =>
  post('/api/discovery/disposition', { github_url: githubUrl, disposition, reason });

/**
 * A database/filesystem's disposition — the entity-generic sibling of
 * `setDisposition`/`getDispositionHistory` above, added when
 * `repo_dispositions`' PK generalized from github_url alone to
 * (entity_type, entity_slug) (Backlog.md, "Disposition is NOT fixed here",
 * 2026-09-22). Keyed on `entitySlug` directly — no pre-import ambiguity to
 * resolve server-side the way a repo's github_url has, since a database/
 * filesystem's slug IS its stable identity from registration. Pass the
 * `apiEntityType()`-translated value ('database'/'filesystem'), never the
 * UI's own 'db' shorthand.
 */
export const getEntityDisposition = (entityType, entitySlug) =>
  get(`/api/discovery/disposition/${encodeURIComponent(entityType)}/${encodeURIComponent(entitySlug)}`);
export const setEntityDisposition = (entityType, entitySlug, disposition, reason = '') =>
  post(`/api/discovery/disposition/${encodeURIComponent(entityType)}/${encodeURIComponent(entitySlug)}`,
       { disposition, reason });
export const getEntityDispositionHistory = (entityType, entitySlug) =>
  get(`/api/discovery/disposition-history/${encodeURIComponent(entityType)}/${encodeURIComponent(entitySlug)}`);

/* ── Enrichment context ───────────────────────────────────────────────────
 *
 * Human-provided metadata for one resource. `question_answers` holds answers
 * to the catalog's Human-Supplied questions, keyed by a slug of the question
 * text — the catalog has no stable question id, so each stored answer also
 * carries the wording it was given for.
 *
 * POST replaces the whole document, so a caller changing one answer must
 * send back everything it read. That is why saveQuestionAnswer below does a
 * read-modify-write rather than posting a single field: posting one answer
 * alone would silently blank environment, sensitivity and the rest.
 */
/** Save ONE enrichment field. The server stamps author and date from the
 *  signed-in identity and does the read-modify-write, so two people setting
 *  two fields do not clobber each other. 401 when anonymous: a judgement
 *  needs an author.
 *
 *  `entityType` defaults to 'repo' for existing callers — pass the
 *  `apiEntityType(state.resourceType)`-translated value for a database/
 *  filesystem, same as `getContext`/`saveContext` above. The backend route
 *  (`context.py`) is already generic (`PATCH /{entity_type}/{slug}/field`);
 *  this wrapper used to hardcode 'repo' regardless of the resource actually
 *  being enriched, so a database/filesystem Enrichment save silently landed
 *  in the repo context bucket under that slug instead of its own bucket —
 *  a real write to the wrong place, not just a wrong read. */
export const saveEnrichmentField = (slug, key, { value = '', kind = 'judgement', source = '', evidence = {}, interim = false } = {}, entityType = 'repo') =>
  patch(`/api/context/${encodeURIComponent(entityType)}/${encodeURIComponent(slug)}/field`, { key, value, kind, source, evidence, interim });

/* ── The journal ──────────────────────────────────────────────────────────
 * Append-only prose on a resource, with a server-stamped author. A
 * suggestion is routed by perspective or person and arrives as a work-list
 * entry for them — never a notification. */
// `entityType` defaults to 'repo' for every existing caller — pass the
// `apiEntityType(state.resourceType)`-translated value for a database/
// filesystem, same as `getQuestions` above. The backend route was already
// entity-generic (`/api/journal/{entity_type}/{slug}`); only this wrapper
// was hardcoded to 'repo' (Backlog.md, "Disposition is NOT fixed here",
// 2026-09-22).
export const getJournal = (slug, entityType = 'repo') =>
  get(`/api/journal/${encodeURIComponent(entityType)}/${encodeURIComponent(slug)}`);
export const writeJournal = (slug, body, suggestTo = [], entityType = 'repo') =>
  post(`/api/journal/${encodeURIComponent(entityType)}/${encodeURIComponent(slug)}`, { body, suggest_to: suggestTo });

export const getContext = (entityType, slug) =>
  get(`/api/context/${entityType}/${encodeURIComponent(slug)}`);

export const saveContext = (entityType, slug, data) =>
  post(`/api/context/${entityType}/${encodeURIComponent(slug)}`, data);

/** Slug for one catalog question. Lowercased words, non-alphanumerics
 *  collapsed to '-'. Must match nothing else in the system: it is only ever
 *  compared against itself. */
export function questionKey(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, 80);
}

/** Read-modify-write one answer. See the caution above: the POST replaces
 *  the document, so everything read must be sent back. */
export async function saveQuestionAnswer(entityType, slug, question, answer) {
  const current = await getContext(entityType, slug).catch(() => ({}));
  const answers = { ...(current.question_answers || {}) };
  const key = questionKey(question);
  answers[key] = {
    question,
    answer,
    answered_at: new Date().toISOString(),
  };
  const next = { ...current, question_answers: answers };
  delete next.updated_at;   // server-stamped; sending it back is meaningless
  return saveContext(entityType, slug, next);
}

export const getDispositionHistory = (githubUrl) =>
  get(`/api/discovery/disposition-history?github_url=${encodeURIComponent(githubUrl)}`);

/**
 * Hide or unhide a resource in the personal working set.
 *
 * A view preference, not a judgement about the resource, and a different
 * axis from disposition — the endpoint's own docstring is explicit about
 * that. Nothing is deleted.
 */
export const setWorkingSetHidden = (entityType, entitySlug, hidden) =>
  post('/api/discovery/working-set', {
    entity_type: entityType, entity_slug: entitySlug, hidden,
  });

/**
 * Unregister a repo and delete its local survey data.
 *
 * DESTRUCTIVE and irreversible: it drops the repo's pgvector collections and
 * removes the registry row. The endpoint takes no confirmation flag of any
 * kind, so the only confirmation that will ever exist is the caller's.
 */
export const removeProject = (slug) =>
  request(`/api/projects/${encodeURIComponent(slug)}`, { method: 'DELETE' });

/**
 * Unregister a database / filesystem and delete its local survey data —
 * the database.py `DELETE /{slug}` and filesystems.py `DELETE /{slug}/`
 * siblings of `removeProject` above. Same caution applies: no confirmation
 * flag, irreversible, caller must confirm before calling.
 *
 * The trailing slash on the filesystem route is real (filesystems.py's
 * `delete_filesystem` is registered at `/{slug}/`, not `/{slug}`) — dropped,
 * this 404s.
 */
export const removeDatabase = (slug) =>
  request(`/api/databases/${encodeURIComponent(slug)}`, { method: 'DELETE' });
export const removeFilesystem = (slug) =>
  request(`/api/filesystems/${encodeURIComponent(slug)}/`, { method: 'DELETE' });

/** Dispatch to whichever of the three deletes above matches an
 *  `apiEntityType()`-translated entity type — the frontend's equivalent of
 *  `POST /{slug}/group`'s server-side dispatch by resource type
 *  (projects.py), since repo/database/filesystem deletion are three
 *  genuinely different registry operations with no single shared route. */
export const removeEntity = (entityType, slug) =>
  entityType === 'database' ? removeDatabase(slug)
    : entityType === 'filesystem' ? removeFilesystem(slug)
    : removeProject(slug);

/* ── Groups ──────────────────────────────────────────────────────────── */

export const listGroups = () => cached('groups', () => get('/api/projects/groups'));

/** Ungrouped repos sharing a GitHub org, suggested (never auto-applied) as
 * candidate groupings — see projects.py's `suggest_groups()` docstring. */
export const groupSuggestions = () => get('/api/projects/groups/suggestions');

export const createGroup = (slug, displayName, description = '') =>
  post('/api/projects/groups', { slug, display_name: displayName, description });

/**
 * Delete a group. Does NOT delete its member resources — they return to
 * Ungrouped (`resources_unassigned` in the response is the count that did).
 * The route takes no confirmation flag of any kind, so the only
 * confirmation that will ever exist is the caller's — same rule as
 * `removeProject` above.
 */
export const deleteGroup = (slug) =>
  request(`/api/projects/groups/${encodeURIComponent(slug)}`, { method: 'DELETE' });

export const assignGroup = (slug, groupSlug, resourceType = 'repo') =>
  post(`/api/projects/${encodeURIComponent(slug)}/group`,
       { resource_type: resourceType, group_slug: groupSlug });

/* ── Discovery sources ───────────────────────────────────────────────────
 *
 * Named, reusable "where do we scout" configs (routes/discovery.py). Two
 * shapes share one create/list/delete surface: `source_type: 'search'`
 * (saved GitHub search filters) and `'list'` (a curated URL list, optionally
 * bound to a `fetch_kind` auto-fetcher like a foundation's landscape.yml).
 *
 * `run` is READ-ONLY — it returns candidate repos for review, exactly like
 * a live search, and imports NOTHING on its own (checked against the route:
 * web/routes/discovery.py's run_discovery_source calls the same search/
 * list-enrichment helpers as /search and returns DiscoveredRepo rows).
 * Turning those candidates into registered projects is the separate
 * `importDiscoveredRepos` call below — the admin UI must show the run's
 * results and let a person choose what (and where) to import rather than
 * treating "run" as if it already wrote anything.
 *
 * `previewSourceRefresh`/`applySourceRefresh` split the same way, but the
 * server enforces it: refresh (GET-shaped POST) never persists, and apply
 * re-fetches rather than trusting a client-held diff, so the applied state
 * always matches a fetch that just happened.
 */
export const listDiscoverySources = () => get('/api/discovery/sources');

export const createDiscoverySource = (slug, displayName, sourceType, config) =>
  post('/api/discovery/sources', { slug, display_name: displayName, source_type: sourceType, config });

export const deleteDiscoverySource = (slug) =>
  request(`/api/discovery/sources/${encodeURIComponent(slug)}`, { method: 'DELETE' });

export const runDiscoverySource = (slug) =>
  post(`/api/discovery/sources/${encodeURIComponent(slug)}/run`);

export const previewSourceRefresh = (slug) =>
  post(`/api/discovery/sources/${encodeURIComponent(slug)}/refresh`);

export const applySourceRefresh = (slug) =>
  post(`/api/discovery/sources/${encodeURIComponent(slug)}/refresh-apply`);

/** A live, unsaved GitHub search — used both by a plain preview and by the
 *  "save this search as a source" flow, so what you saved is provably what
 *  you saw. */
export const searchDiscoveryRepos = (filters) => post('/api/discovery/search', filters);

export const listQuickListSources = () => cached('discovery-quick-list-sources', () => get('/api/discovery/quick-list-sources'));

/** Queues a background import of the given DiscoveredRepo-shaped candidates.
 *  Returns immediately (`{queued, skipped}`); progress lands in the activity
 *  log, not in this response. */
export const importDiscoveredRepos = (repos, { groupSlug = '', sourceLabel = 'search' } = {}) =>
  post('/api/discovery/import', {
    repos: repos.map((r) => ({
      github_url: r.html_url, display_name: r.full_name, description: r.description || '',
    })),
    group_slug: groupSlug,
    source_label: sourceLabel,
  });

/** A pasted/uploaded CSV or newline list of GitHub URLs, turned into the
 *  same DiscoveredRepo rows a search would — read-only, same as search:
 *  nothing is registered until the caller selects rows and calls
 *  `importDiscoveredRepos`. An account URL in the list (e.g.
 *  `github.com/apache`) is expanded into its member repos server-side and
 *  reported back in `expanded_orgs`, since a bare account isn't itself a
 *  repo — see `discovery.py`'s `discover_from_list` docstring. */
export const discoverFromList = (text) => post('/api/discovery/from-list', { text });

/** The inventory CSV export — raw text + filename, not `get()`'s JSON path,
 *  since the response is `text/csv` with a `Content-Disposition` header.
 *  Deliberately does not touch the DOM (no Blob, no anchor click): classic's
 *  `_downloadInventory` (index.html) found that a plain `<a download>`
 *  quietly saved a 404's `{"detail":"Not Found"}` body as a `.csv`-shaped
 *  file that looked like a real export — checking `res.ok` here, before any
 *  caller touches the response as a file, is what a blob-download helper in
 *  the DOM layer cannot do on its own. */
export async function fetchInventoryCsv() {
  const res = await fetch('/api/discovery/inventory.csv');
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* non-JSON error body */ }
    throw new ApiError(res.status, detail, '/api/discovery/inventory.csv');
  }
  const text = await res.text();
  const cd = res.headers.get('content-disposition') || '';
  const m = cd.match(/filename="?([^";]+)"?/);
  const filename = m ? m[1] : `re-inventory-${new Date().toISOString().slice(0, 10)}.csv`;
  return { text, filename };
}

/* ── Investigations ──────────────────────────────────────────────────── */
/* The full route surface lives here, in one place (a second, partial
 * `Investigations` section used to sit further down in this file with just
 * `listInvestigations` -- merged in here). `web/routes/investigations.py`
 * is the source of truth for every path and body shape below. */

export const listInvestigations = ({ includeClosed = false } = {}) =>
  get(`/api/investigations/?include_closed=${includeClosed}`);

export const getInvestigationPurposes = () => get('/api/investigations/purposes');

export const getInvestigationClassifications = () => get('/api/investigations/classifications');

export const getInvestigation = (slug) => get(`/api/investigations/${encodeURIComponent(slug)}`);

export const createInvestigation = ({
  displayName, description = '', purposes = [],
  projectClassification = 'StudyProject', egeriaBinding = 'egeria', hypothesis = '',
  egeriaProjectGuid = '', egeriaProjectQualifiedName = '',
} = {}) =>
  post('/api/investigations/', {
    display_name: displayName, description, purposes,
    project_classification: projectClassification, egeria_binding: egeriaBinding, hypothesis,
    egeria_project_guid: egeriaProjectGuid, egeria_project_qualified_name: egeriaProjectQualifiedName,
  });

export const updateInvestigation = (slug, fields) =>
  patch(`/api/investigations/${encodeURIComponent(slug)}`, fields);

export const listInvestigationMembers = (slug) =>
  get(`/api/investigations/${encodeURIComponent(slug)}/members`);

export const addInvestigationMember = (slug, entityType, entitySlug, rationale = '') =>
  post(`/api/investigations/${encodeURIComponent(slug)}/members`, {
    entity_type: entityType, entity_slug: entitySlug,
    membership_rationale: rationale, state: 'in-scope',
  });

export const removeInvestigationMember = (slug, entityType, entitySlug) =>
  request(`/api/investigations/${encodeURIComponent(slug)}/members/`
          + `${encodeURIComponent(entityType)}/${encodeURIComponent(entitySlug)}`,
          { method: 'DELETE' });

export const closeInvestigation = (slug) => post(`/api/investigations/${encodeURIComponent(slug)}/close`);

export const suspendInvestigation = (slug) => post(`/api/investigations/${encodeURIComponent(slug)}/suspend`);

export const reopenInvestigation = (slug) => post(`/api/investigations/${encodeURIComponent(slug)}/reopen`);

export const bindInvestigationEgeriaProject = (slug, {
  status = 'unset', egeriaProjectGuid = '', egeriaProjectQualifiedName = '', freeTextName = '',
} = {}) =>
  put(`/api/investigations/${encodeURIComponent(slug)}/egeria-project`, {
    status, egeria_project_guid: egeriaProjectGuid,
    egeria_project_qualified_name: egeriaProjectQualifiedName, free_text_name: freeTextName,
  });

export const promoteInvestigation = (slug) => post(`/api/investigations/${encodeURIComponent(slug)}/promote`);

export const reclassifyInvestigation = (slug, projectClassification, hypothesis = '') =>
  post(`/api/investigations/${encodeURIComponent(slug)}/reclassify`, {
    project_classification: projectClassification, hypothesis,
  });

export const relinkInvestigationMembers = (slug) =>
  post(`/api/investigations/${encodeURIComponent(slug)}/relink-members`);

export const syncInvestigationEgeria = (slug) =>
  post(`/api/investigations/${encodeURIComponent(slug)}/sync-egeria`);

export const getInvestigationDispositions = (slug) =>
  get(`/api/investigations/${encodeURIComponent(slug)}/dispositions`);

export const setInvestigationDisposition = (slug, entityType, entitySlug, disposition = '', rationale = '') =>
  post(`/api/investigations/${encodeURIComponent(slug)}/dispositions/`
       + `${encodeURIComponent(entityType)}/${encodeURIComponent(entitySlug)}`
       + `?disposition=${encodeURIComponent(disposition)}&rationale=${encodeURIComponent(rationale)}`);

export const getInvestigationNextSteps = (slug) =>
  get(`/api/investigations/${encodeURIComponent(slug)}/next-steps`);

/* ── Query ───────────────────────────────────────────────────────────── */

/**
 * `entityType` defaults to 'repo' for existing callers, same convention as
 * `getQuestions`/`getAnswer` above — pass the `apiEntityType(state.resourceType)`-
 * translated value at every /next boundary crossing. Omitting it used to mean
 * every chat/Ask turn silently compiled evidence from the repo catalog
 * regardless of what resource was actually selected: a database or
 * filesystem question always got repo-shaped sections (`foss_scorecard`,
 * `chaoss_metrics`, ...) and reported its real answer as a gap.
 */
export const ask = (query, { resourceSlug, entityType = 'repo', perspectives = [], sessionId } = {}) =>
  post('/api/query/', {
    query,
    project_slug: resourceSlug || null,   // the wire key is still the old name
    entity_type: entityType,
    perspectives: [...perspectives],
    session_id: sessionId || null,
  });

/**
 * Rate an answer.
 *
 * `vote` is THREE states, not a thumb: +1 helpful, 0 partially correct,
 * -1 not helpful. Recording a "partly" as either of the others is the kind
 * of quiet flattening that makes the feedback corpus useless.
 *
 * `queryHash` always comes from a prior server response — the client never
 * computes it — so that a vote on the same question text lands under one key
 * whether the answer came from retrieval or from measurements.
 */
export const sendFeedback = (queryHash, vote, compileId = null) =>
  post('/api/query/feedback',
       compileId ? { query_hash: queryHash, vote, compile_id: compileId }
                 : { query_hash: queryHash, vote });

/**
 * Join a chat vote to the gaps loop (item 8 — ITEM-8-FEEDBACK-IMPLEMENTED.md).
 *
 * `sendFeedback` above records the vote for MetricsCollector's own tracing —
 * a different consumer than the gaps collection, and not the mechanism
 * `record_disagreement` (gaps.py) reads. This is the SAME endpoint the
 * Questions-checklist "Was this right?" bar already posts to
 * (`/api/feedback/answer`, feedback.js:313) — a chat vote is the same kind of
 * claim about the same kind of answer, so it goes through the same door
 * rather than a parallel one. Only `disagree` raises a gap; `agree`/`partly`
 * still land in the feedback store (feedback.py's module note).
 *
 * Requires a resource in scope: the endpoint 404s on an unknown slug and a
 * chat turn asked with nothing selected has no slug to attribute a gap to —
 * that turn's vote still reaches `sendFeedback` above, it just cannot join
 * the per-resource gaps collection. Callers should skip this call rather
 * than let it throw when `slug` is empty.
 */
export const submitAnswerFeedback = ({ slug, question, verdict, comment = '', sessionId = '', page = '', entityType = 'repo' }) =>
  post('/api/feedback/answer', { slug, question, verdict, comment, session_id: sessionId, page, entity_type: entityType });

/**
 * SSE variant of `ask()` — POST /api/query/stream, yielding one event per
 * server line rather than one Promise for the whole answer.
 *
 * Async generator, not a callback pair: the caller drives it with `for await`
 * and can stop consuming (e.g. the resource selection changed underneath it)
 * without this module needing to know why. Events come back exactly as the
 * server names them — `{t:'chunk', v}` while text is arriving, one
 * `{t:'done', ...}` carrying intent/hash/chart/compiled/compile_id and
 * whichever structured payload (symbol_table, compare_symbols,
 * alias_suggestion) the done event carried.
 *
 * Falls back to nothing: a caller that cannot get a readable stream (an
 * old browser, a proxy that buffers SSE) should catch and retry with the
 * plain `ask()` above rather than this function pretending to stream.
 */
export async function* askStream(query, { resourceSlug, entityType = 'repo', perspectives = [], sessionId } = {}) {
  const res = await fetch('/api/query/stream', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      query,
      project_slug: resourceSlug || null,
      entity_type: entityType,
      perspectives: [...perspectives],
      session_id: sessionId || null,
    }),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, detail, '/api/query/stream');
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line; a frame may arrive split
    // across chunks, so only a complete "...\n\n" is safe to parse.
    let sep;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const line = frame.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      yield JSON.parse(line.slice(6));
    }
  }
}

/* ── Charts ──────────────────────────────────────────────────────────── */

/** The chart kinds `/api/stats/{slug}/charts/{kind}` serves for a repo. */
/** What each chart measures, and on what scale.
 *
 * The range belongs in the TITLE. It is what stops the radar-chart problem
 * recurring: two charts of the same word on different scales become obviously
 * different rather than quietly so.
 */
export const CHART_MEASURE = {
  stars: ['stars', ''],
  commits: ['commits', ''],
  weekly_commits: ['commits per week', ''],
  languages: ['share of code', '0–100%'],
  file_types: ['files', ''],
  top_committers: ['commits', ''],
  health: ['health signals', '0–10, five axes'],
  survey_history: ['total files', ''],
};

export const REPO_CHARTS = [
  ['stars', 'Stars over time'],
  ['commits', 'Commits over time'],
  ['weekly_commits', 'Commits by week'],
  ['languages', 'Languages'],
  ['file_types', 'File types'],
  ['top_committers', 'Top committers'],
  ['health', 'Health'],
  ['survey_history', 'Survey history'],
];

/**
 * One Plotly figure.
 *
 * A 200 with an empty `data` array is a real and different answer from a
 * failure: it means the series exists and has nothing in it yet. Callers
 * must not render the two the same way.
 */
export const getChart = (slug, kind) =>
  get(`/api/stats/${encodeURIComponent(slug)}/charts/${encodeURIComponent(kind)}`);

/* ── Surveys and dashboards ──────────────────────────────────────────── */

/**
 * The Survey Definitions that can be run against one resource.
 *
 * `entity_type` is the ADAPTER's vocabulary, not the sidebar's: repositories
 * are `repo` here, and passing `project` returns a 404 naming the known types.
 *
 * PASS THE STAGE. The route's own contract says each intent's UI should send
 * its stage as the primary filter, and omitting it falls back to every
 * cataloged Question regardless of stage — which is a list of every survey,
 * rendered under a stage heading.
 *
 * The response carries `scoping`: `questions` when the stage really narrowed
 * it, `full-scan` when nothing resolved and this is everything. Callers must
 * render those differently — a full scan under a stage heading is the same
 * lie, one layer deeper.
 */
export const getSurveyCandidates = (slug, { entityType = 'repo', phase = '' } = {}) =>
  get(`/api/survey-definitions/${encodeURIComponent(entityType)}/${encodeURIComponent(slug)}/candidates${
    phase ? `?phase=${encodeURIComponent(phase)}` : ''}`);

/** Every authored Survey Definition, catalog-wide -- step_count/fetch_steps
 *  live here, not on a candidates row: the candidates route asks "which
 *  suit THIS resource" (Egeria-backed, per repo); this asks "what surveys
 *  exist" (local, cacheable). Joined onto candidate rows by qualified_name
 *  (stage-page round, point 2). */
export const listSurveyDefinitions = () => cached('survey-definitions', () => get('/api/survey-definitions/definitions'));

/** Launch one Survey Definition. `ref` is its qualified_name or guid. */
export const runSurveyDefinition = (slug, ref, { entityType = 'repo' } = {}) =>
  post(`/api/survey-definitions/${encodeURIComponent(entityType)}/${encodeURIComponent(slug)}/run`,
       { survey_definition_ref: ref });

/**
 * The dashboards registered for a resource, optionally scoped to a stage.
 *
 * `includeEmpty` keeps dashboards that have no stored results. The route
 * drops them by default, which renders "registered, never run" as "no such
 * dashboard" — an absence reported as a non-existence, which is the one thing
 * this UI is most careful not to do.
 */
/** repo -> /api/projects/, database -> /api/databases/, filesystem ->
 *  /api/filesystems/ — all three now expose the identically-shaped
 *  `/{slug}/survey-results` route (see `workflows.analysis.
 *  build_survey_results`'s docstring for what a database/filesystem
 *  dashboard actually contains, vs. repo's curated groupings). */
function _surveyResultsPath(entityType, slug) {
  const enc = encodeURIComponent(slug);
  if (entityType === 'database') return `/api/databases/${enc}/survey-results`;
  if (entityType === 'filesystem') return `/api/filesystems/${enc}/survey-results`;
  return `/api/projects/${enc}/survey-results`;
}

/** `entityType` defaults to 'repo' for every existing caller — pass
 *  `apiEntityType(state.resourceType)`-translated value at every /next
 *  boundary crossing, same as `getQuestions` above. */
export const getSurveyDashboards = (slug, stage = '', { includeEmpty = false, entityType = 'repo' } = {}) => {
  const qs = new URLSearchParams();
  if (stage) qs.set('stage', stage);
  if (includeEmpty) qs.set('include_empty', 'true');
  const q = qs.toString();
  return get(`${_surveyResultsPath(entityType, slug)}${q ? `?${q}` : ''}`);
};

/** One-line headline per analysis that has results — the summary tiles. */
export const getSurveySummary = (slug, stage = '') =>
  get(`/api/projects/${encodeURIComponent(slug)}/survey-results/summary${
    stage ? `?phase=${encodeURIComponent(stage)}` : ''}`);

/**
 * One analysis's recorded series for a resource.
 *
 * THE HISTORY WAS ALWAYS THERE. Results are keyed by `surveyed_at` and the
 * reader returns the rows at MAX(surveyed_at) — earlier rows are retained,
 * which the `'probe'` incident proved by hiding real runs underneath one.
 * Every measurement therefore already has a series; nothing displayed it.
 */
/** FOUND, NOT FIXED (Tier 1 audit, 2026-09-23): `/api/projects/{slug}/analyses/
 *  {analysis_id}/trend` has no database/filesystem equivalent today —
 *  confirmed by grepping `databases.py`/`filesystems.py` for `/trend`, no
 *  match. `entityType` is accepted here so callers are ready the moment a
 *  generic route exists, but it is NOT sent — there is nowhere to send it —
 *  and this still always hits the repo-only path. Needs its own scoping
 *  pass: a per-type results-history reader, not just a routing fix. */
export const getAnalysisTrend = (slug, analysisId, metric = '', entityType = 'repo') => // eslint-disable-line no-unused-vars
  get(`/api/projects/${encodeURIComponent(slug)}/analyses/${
    encodeURIComponent(analysisId)}/trend${metric ? `?metric=${encodeURIComponent(metric)}` : ''}`);

/**
 * The fact behind a number (stage-page round, point 10): not the members a
 * count counted, but the measurements an analysis recorded — `965 source
 * files` alongside `175,716 lines_of_code`, each with a name, a value, and
 * where a value opens (a members list, or nothing to open). One call fills
 * both the in-pane table and the "the numbers behind this N" link's count.
 */
// `entityType` defaults to 'repo' for existing callers — pass
// `apiEntityType(state.resourceType)`-translated value at every /next
// boundary crossing, same as `getAnswer`/`ask`/`askStream` above. Omitting
// it used to mean this always 404'd for a database/filesystem slug (the
// backend always did a repo-only lookup regardless of what was asked for);
// see `build_measurements()`'s own docstring in stage_page.py.
// `level` (Slice 21a): the ASKING question's own primary level (see
// app.js's `primaryQuestionLevel` — mirrors `FactLayer._primary_level`'s
// "resource wins when declared" rule). Defaults to 'resource', the
// pre-existing behaviour for every caller that doesn't pass one. A
// container-level question whose analysis has a container-level reader
// gets a per-container breakdown instead of the flat resource scalars —
// found live, owner's question 2026-09-26: "Which schemas carry the
// data...?"'s "numbers behind this" showed the same flat table/column/row
// counts "How big is this database" does, un-broken-down by schema.
export const getMeasurements = (slug, analysisId, entityType = 'repo', level = 'resource') =>
  get(`/api/projects/${encodeURIComponent(slug)}/analyses/${
    encodeURIComponent(analysisId)}/measurements?entity_type=${encodeURIComponent(entityType)}&level=${encodeURIComponent(level)}`);

/** Every analysis this repo could run — the row plus what feeds its popover
 *  (stage, declared run time, availability, perspectives, ruleset link, the
 *  full description) in one call, so a description popover needs no second
 *  fetch (stage-page round, points 1-3).
 *
 *  `entityType` defaults to 'repo', same reasoning and same fix date as
 *  `getMeasurements` above. Was this Tier 1 pass's own "found, not fixed"
 *  item — superseded here by `re/measurements-feedback-fix` (PR #237, merged
 *  ahead of this branch), which built the real `entity_type` dispatch into
 *  `build_analyses_index()` itself rather than routing per entity type. */
export const getAnalysesIndex = (slug, stage = '', entityType = 'repo') =>
  get(`/api/projects/${encodeURIComponent(slug)}/analyses-index?entity_type=${encodeURIComponent(entityType)}${
    stage ? `&stage=${encodeURIComponent(stage)}` : ''}`);

/** Latest structured results for one analysis -- a raw dict whose shape
 *  differs per analysis_id (REPO_ANALYSIS_RESULTS_MAP's own reader
 *  functions). Used directly by the sub-resource survey panel to read its
 *  own findings list on demand, rather than waiting on the by-analysis
 *  dashboard grouping this same data also feeds. */
export const getAnalysisResults = (slug, analysisId) =>
  get(`/api/projects/${encodeURIComponent(slug)}/analyses/${encodeURIComponent(analysisId)}/results`);

/* ── Sub-resources -- the repo scope-narrowing funnel's Select/Catalog/Narrow
 * stages (docs/repo-scope-narrowing-funnel.md D2-D6). Repo-only: the
 * underlying table is generic across resource types, but SubResourceSurveyor
 * only exists for repos (RULING-SUBRESOURCES-PLACEMENT.md §1.2) -- these
 * routes 404 the repo slug they're given, never a resource-type check, so
 * nothing here needs an entityType param the way survey-definitions does. */

/** What's currently tracked locally for this repo -- backs the "already
 *  catalogued" state so re-opening the panel later shows prior selections
 *  (repeatable, not a one-time gate). */
export const listSubResources = (slug) =>
  get(`/api/projects/${encodeURIComponent(slug)}/sub-resources`);

/** Track the selected sub-resources locally, and (by default) publish them
 *  to Egeria as real FileFolder/DataFile assets in the same action.
 *  `publishToEgeria: false` is the sandbox-mode escape hatch. */
export const catalogSubResources = (slug, items, publishToEgeria = true) =>
  post(`/api/projects/${encodeURIComponent(slug)}/sub-resources/catalog`,
       { items, publish_to_egeria: publishToEgeria });

/** Reversible -- removes only RE's local tracking record, never anything
 *  already published to Egeria. `locator` is a query param so the repo's
 *  own root locator ("") is representable. */
export const uncatalogSubResource = (slug, locator) =>
  del(`/api/projects/${encodeURIComponent(slug)}/sub-resources?locator=${encodeURIComponent(locator)}`);

/** Run one analysis's step(s) scoped to a single cataloged sub-resource
 *  (the Narrow stage, D5/D6) rather than the whole repo. Only reachable for
 *  analyses whose target_shape is compatible with the sub-resource's kind
 *  -- the server re-checks this; `isShapeCompatible` below is the client's
 *  own mirror of the same rule, used to decide what to offer in the first
 *  place. */
export const runScopedAnalysis = (slug, analysisId, locator) =>
  post(`/api/projects/${encodeURIComponent(slug)}/sub-resources/analyses/${
    encodeURIComponent(analysisId)}/run`, { locator });

/** Latest structured results for one analysis, scoped to a single cataloged
 *  sub-resource. Empty dict for any analysis_id that never persists scoped
 *  metrics -- that is an absence, not an error. */
export const getScopedAnalysisResults = (slug, analysisId, locator) =>
  get(`/api/projects/${encodeURIComponent(slug)}/sub-resources/analyses/${
    encodeURIComponent(analysisId)}/results?locator=${encodeURIComponent(locator)}`);

/** Mirrors analysis_catalog_reader.is_shape_compatible() -- kept in sync by
 *  hand since this is a small, stable 4-value enum, not worth a round-trip
 *  (same approach classic's index.html took for the same check). */
export function isShapeCompatible(targetShape, kind) {
  if (targetShape === 'corpus') return true;
  if (targetShape === 'single_container') return kind === 'folder' || kind === 'schema';
  if (targetShape === 'single_leaf') return kind === 'file' || kind === 'table' || kind === 'column';
  return false; // whole_resource_only, or unrecognized
}

/** Recent activity for one resource — the runs, with their per-step detail. */
/* ── Members: the things a count counted ─────────────────────────────────
 *
 * The measurement popup opens a number's history; this opens its members.
 * `scope` is "public" or "all" — purpose decides the default (Maintain wants
 * all) — and the response says whether the scope was honoured, since only
 * symbols carry a public/internal marker today.
 */
/** FOUND, NOT FIXED (Tier 1 audit, 2026-09-23): `/api/projects/{slug}/members/
 *  {analysis_id}` (and its `/children`/`/promote` siblings below) have no
 *  database/filesystem equivalent — confirmed by grepping `databases.py`/
 *  `filesystems.py`, no match. `entityType` is accepted on all three so
 *  callers are ready once one exists, but none of the three send it — there
 *  is nowhere to send it — and all three still always hit the repo-only
 *  path. `members.py`'s member model may or may not generalize cleanly to a
 *  database's rows/tables or a filesystem's files; that question is exactly
 *  why this needs its own scoping pass rather than being built here. */
export const getMembers = (slug, analysisId, { metric = '', scope = 'public', limit = 200 } = {}, entityType = 'repo') => { // eslint-disable-line no-unused-vars
  const qs = new URLSearchParams({ scope, limit: String(limit) });
  if (metric) qs.set('metric', metric);
  return get(`/api/projects/${encodeURIComponent(slug)}/members/${encodeURIComponent(analysisId)}?${qs}`);
};

/** Promote a member-list selection. Three acts, one provenance line
 *  composed on the server: work_list (I will deal with this), rfa (someone
 *  must), journal (worth knowing). `members` is a snapshot of names, never
 *  a query. 401 when anonymous. See `getMembers`'s found-not-fixed note
 *  above — same gap, same reason. */
export const promoteMembers = (slug, analysisId, { action, metric = '', members = [], total = 0, facet = '', runAt = '', name = '', suggestTo = [] }, entityType = 'repo') => // eslint-disable-line no-unused-vars
  post(`/api/projects/${encodeURIComponent(slug)}/members/${encodeURIComponent(analysisId)}/promote`,
    { action, metric, members, total, facet, run_at: runAt, name, suggest_to: suggestTo });

export const getMemberChildren = (slug, analysisId, key, { scope = 'public', limit = 200 } = {}, entityType = 'repo') => // eslint-disable-line no-unused-vars
  get(`/api/projects/${encodeURIComponent(slug)}/members/${encodeURIComponent(analysisId)}/children?${
    new URLSearchParams({ key, scope, limit: String(limit) })}`);

export const getResourceRuns = (slug, limit = 40) =>
  get(`/api/activity/?entity_slug=${encodeURIComponent(slug)}&limit=${limit}`);

/** One run's declared-vs-received reconciliation: what each step promised vs what the report actually got. */
export const getDeclaredVsReceived = (entryId) =>
  get(`/api/activity/${encodeURIComponent(entryId)}/declared-vs-received`);

/* ── Work lists and batch runs ───────────────────────────────────────── */

export const listWorkLists = (investigation = '') =>
  get(`/api/work-lists/${investigation ? `?investigation=${encodeURIComponent(investigation)}` : ''}`);

export const getWorkList = (slug) => get(`/api/work-lists/${encodeURIComponent(slug)}`);

export const createWorkList = (displayName, entitySlugs, { investigation = '', rationale = '', description = '', entityType = 'repo' } = {}) =>
  post('/api/work-lists/', {
    display_name: displayName, entity_slugs: [...entitySlugs],
    investigation, rationale, description, entity_type: entityType,
  });

export const promoteWorkList = (slug, survivors, displayName = '', rationale = '') =>
  post(`/api/work-lists/${encodeURIComponent(slug)}/promote`,
       { survivors: [...survivors], display_name: displayName, rationale });

/**
 * Publish a work list to Egeria as a `WorkingSet` collection.
 *
 * NOT `WorkList` — that type does not exist in Egeria. `WorkingSet` is the
 * one whose definition is this feature ("a list of elements that are being
 * worked on"); `WorkItemList` is the neighbouring type and is for activities.
 *
 * Explicit: creating or editing a list publishes nothing.
 */
export const publishWorkList = (slug) =>
  post(`/api/work-lists/${encodeURIComponent(slug)}/publish`);

/**
 * Run one analysis across a SET of resources, concurrently.
 *
 * Returns a `set_id` to poll. One queue row per resource, so one failure is
 * one row — the rest still run.
 */
/** `entityType` only matters when `entitySlugs` is given with no
 *  `workListSlug` — the server derives entity_type from the work list's own
 *  column when one is named, since a work list is homogeneous by
 *  construction (`WorkListCreate.entity_type`); it defaults to 'repo'
 *  otherwise, same as `BatchRunRequest.entity_type`. */
export const enqueueBatch = (analysisId, entitySlugs, workListSlug = '', entityType = 'repo') =>
  post('/api/work-lists/runs/batch', {
    analysis_id: analysisId, entity_slugs: [...entitySlugs], work_list_slug: workListSlug,
    entity_type: entityType,
  });

/** Progress for one batch, derived from the run rows on every read. */
export const getBatchProgress = (setId) =>
  get(`/api/work-lists/runs/sets/${encodeURIComponent(setId)}`);

/** Every fact known about ONE resource, already judged. `entityType`
 *  defaults to 'repo' for existing callers — pass the
 *  `apiEntityType(state.resourceType)`-translated value for a database/
 *  filesystem work-list member (worklist.js already carries `wl.entity_type`
 *  per member), same convention as `getQuestions`/`getAnswer` above. Omitting
 *  it used to mean the backend always built its FactLayer against the repo's
 *  own maps regardless of the resource's real type. */
export const getResourceFacts = (slug, entityType = 'repo') =>
  get(`/api/analyses/facts/${encodeURIComponent(slug)}?entity_type=${encodeURIComponent(entityType)}`);

/**
 * Facts for SEVERAL resources, in one call.
 *
 * ALWAYS pass `analysisIds` when you know them. The cost is dominated by two
 * analyses with expensive results readers — measured on `egeria_git`,
 * `architecture_recovery` 47s and `architecture_diagram` 22s, against under a
 * second for the other 32 combined. Reading all 34 is 70s per resource;
 * reading the 5 that Scouting's questions use is 0.5s. Omitting the scope is
 * what made a four-member work list look hung.
 *
 * Returns `{subjects: {slug: [fact]}, unreadable: {slug: reason}}` — a
 * resource that could not be read is named there rather than coming back as
 * an empty fact list, which would be indistinguishable from one that has no
 * results.
 */
/**
 * The CHEAP projection: for each (resource, analysis), is there stored output
 * and when was it measured — without running the results readers.
 *
 * 0.83s for the four-resource Discovery matrix that costs 65s to read fully,
 * because it never builds the values a grid cell does not display.
 *
 * It cannot tell `measured` from `partial`; the response says so. Render the
 * difference as not-yet-read, never as a state nobody established.
 */
/** `entityType` defaults to 'repo' — a work-list comparison grid mixing
 *  resource types must call this once per entity type (the route builds one
 *  `FactLayer` for the whole batch), the same constraint `getMeasurements`'s
 *  fix already documented for the per-resource measurements route. */
export const getBulkStates = (slugs, analysisIds = [], entityType = 'repo') => {
  const qs = new URLSearchParams({ slugs: [...slugs].join(','), states_only: 'true', entity_type: entityType });
  if (analysisIds.length) qs.set('analysis_ids', [...analysisIds].join(','));
  return get(`/api/analyses/facts?${qs}`);
};

export const getBulkFacts = (slugs, analysisIds = [], entityType = 'repo') => {
  const qs = new URLSearchParams({ slugs: [...slugs].join(','), entity_type: entityType });
  if (analysisIds.length) qs.set('analysis_ids', [...analysisIds].join(','));
  return get(`/api/analyses/facts?${qs}`);
};

/* ── Running an analysis ─────────────────────────────────────────────── */

/** repo -> /api/projects/, database -> /api/databases/ — the per-card "Run"
 *  action's per-analysis route, same dispatch shape as `_questionsPath`/
 *  `_surveyResultsPath` above.
 *
 *  FILESYSTEM HAS NO EQUIVALENT ROUTE YET (audited 2026-09-23): unlike
 *  database, `web/routes/filesystems.py` has no
 *  `POST /{slug}/analyses/{analysis_id}/run` at all — its only survey
 *  trigger is the whole-resource `POST /{slug}/survey`, not a per-analysis
 *  dispatch the way `databases.py`'s `run_single_database_analysis` and
 *  `run_analysis`/`resolve_analysis_plan` (repo) are. Building one needs a
 *  filesystem-side per-analysis step runner first (there is no
 *  `run_filesystem_survey(..., steps=[...])` equivalent of
 *  `run_database_survey` to call) — a real, separate piece of work, not a
 *  routing fix, so it is named here as found-but-deferred rather than
 *  built. Until it exists, a filesystem's "Run"/"re-run" falls back to the
 *  repo path below, which 404s (`registry.get(slug)` against the repo-only
 *  `projects` table) — a loud failure, not a silent wrong-resource write. */
function _runAnalysisPath(entityType, slug, analysisId) {
  const enc = encodeURIComponent(slug);
  const aid = encodeURIComponent(analysisId);
  if (entityType === 'database') return `/api/databases/${enc}/analyses/${aid}/run`;
  return `/api/projects/${enc}/analyses/${aid}/run`;
}

/** `entityType` defaults to 'repo' for every existing caller — pass
 *  `apiEntityType(state.resourceType)` at every /next boundary crossing,
 *  same convention as `getQuestions` above. Before this, EVERY caller
 *  (including a database/filesystem's own "Run"/"re-run" button on a
 *  Questions-checklist row) posted to the repo-only route regardless of the
 *  resource's real type. */
export const runAnalysis = (slug, analysisId, entityType = 'repo') =>
  post(_runAnalysisPath(entityType, slug, analysisId));

/* ── Prerequisite proposals (design §17.1, web/routes/prerequisites.py) ───
 *
 * `/plan` asks what `stepKey` needs before it can answer, WITHOUT running
 * anything — the exact question a UI has to ask before it can offer "answering
 * this needs X first — estimated 40s; run it?" rather than dispatching a run
 * that silently degrades to a skip. `stepKey` is the resolver's own vocabulary
 * (`re_analysis_steps`/`step_registry` keys), not always identical to an
 * `analysis_id` — a caller passing an `analysis_id` that happens not to be a
 * declared step key gets back `status: "satisfied"` (prerequisite_resolver.
 * resolve() returns SATISFIED for an unrecognised key), which is the correct,
 * conservative answer rather than a false proposal.
 *
 * The response is one of `PlanRequest`'s three shapes:
 *   {status: "satisfied"}                                   — run it, nothing to ask
 *   {status: "auto_run", auto_run: [steps...]}               — within budget;
 *     the ordinary run endpoint will run these first on its own, unasked
 *   {status: "proposal", proposal: {...Proposal.as_dict()}}  — crosses tier;
 *     needs the user's yes before anything runs (`runPrerequisites` below)
 *   {status: "unsatisfiable", reason}                        — nothing produces
 *     what is missing; there is no accept button for this one
 */
export const planPrerequisites = (entityType, slug, stepKey) =>
  post('/api/prerequisites/plan', { entity_type: entityType, slug, step_key: stepKey });

/** The user's yes on a `proposal` plan. Runs exactly the steps the plan
 *  named, in the order it named them (`PlanRequest`'s docstring: a proposal
 *  recomputed at accept time could differ from what was shown, so the
 *  client sends back what it displayed rather than a bare "go"). `demandedBy`
 *  is the step that asked for them, recorded on the producers' own
 *  `step_runs` rows so the accepted chain's cost is attributable.
 *
 *  `capabilityConsented` is the second axis's version of the same contract
 *  (REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md §7.1's "run partially
 *  and say so"): the user was shown a credential shortfall and accepted it.
 *  Sent explicitly rather than inferred server-side, for exactly the reason
 *  `steps` is — the server runs what the user was shown. Without it the
 *  re-resolve inside the executor raises the same shortfall again and skips
 *  the step the user just approved. */
export const runPrerequisites = (entityType, slug, steps, demandedBy = '',
                                 capabilityConsented = false) =>
  post('/api/prerequisites/run', { entity_type: entityType, slug, steps,
                                   demanded_by: demandedBy,
                                   capability_consented: capabilityConsented });

/** §7.1's third choice at the gate: raise an RFA naming the step a credential
 *  shortfall blocked. Distinct from the standing RFA the `credential_
 *  capability` probe raises on its own — that one says the grants are narrow,
 *  this one says which analysis somebody could not run because of it. The
 *  server re-resolves rather than trusting anything sent here, so a stale
 *  client-side fraction cannot reach a database owner. */
export const raiseCapabilityRfa = (entityType, slug, stepKey) =>
  post('/api/prerequisites/capability-rfa',
       { entity_type: entityType, slug, step_key: stepKey });

export const getActivityEntry = (entryId) =>
  get(`/api/activity/${encodeURIComponent(entryId)}`);

export const listActivity = (limit = 50) => get(`/api/activity/?limit=${limit}`);
export const listRfas = () => get('/api/activity/rfas');

/** Record a response action (defer | reassign | complete | reopen — "reopen"
 *  is just `status: 'open'` again, the same endpoint) against one RFA.
 *  `web/routes/activity.py:update_rfa_action` — local-only for now (see
 *  next/rfa.js's own note on why), with a best-effort Egeria ToDo sync
 *  attempted server-side, non-blocking of this call's result. */
export const updateRfaAction = (rfaId, { status, assignee = '', deferUntil = '', resolutionNote = '' } = {}) =>
  patch(`/api/activity/rfas/${encodeURIComponent(rfaId)}`,
        { status, assignee, defer_until: deferUntil, resolution_note: resolutionNote });

/**
 * Poll one activity entry until it stops running.
 *
 * Resolves with the finished entry. Rejects on timeout — which is NOT the
 * same as failure, and a caller must render it as "we stopped watching",
 * never as "it failed". A pending marker that cannot distinguish a stalled
 * run from a slow one is worse than no marker at all.
 *
 * @param {object} opts
 * @param {number} opts.intervalMs   gap between polls
 * @param {number} opts.timeoutMs    give up after this long
 * @param {(entry: object) => void} opts.onTick  called with each poll result
 */
export async function pollActivity(entryId, {
  intervalMs = 2000,
  timeoutMs = 5 * 60_000,
  onTick = () => {},
} = {}) {
  const deadline = Date.now() + timeoutMs;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  for (;;) {
    const entry = await getActivityEntry(entryId);
    onTick(entry);
    const status = (entry?.status || '').toLowerCase();
    if (status && status !== 'running' && status !== 'queued' && status !== 'pending') {
      return entry;
    }
    if (Date.now() >= deadline) {
      const err = new Error(`Still running after ${Math.round(timeoutMs / 1000)}s`);
      err.name = 'PollTimeout';
      err.entry = entry;
      throw err;
    }
    await sleep(intervalMs);
  }
}

/* ── Curate ─────────────────────────────────────────────────────────────── */

/** The review-and-commit plan: three columns, the manifest, the record. A
 *  local read on the server, so it renders when Egeria is down. */
export const getCuratePlan = (slug) => get(`/api/projects/${encodeURIComponent(slug)}/curate/plan`);

/** Catalogue →. Returns {curation, activity_id, run_id}; 401 anonymous,
 *  409 outside the population. */
export const curateCommit = (slug, selection) =>
  post(`/api/projects/${encodeURIComponent(slug)}/curate/commit`, selection);

export const getCuration = (slug, id) =>
  get(`/api/projects/${encodeURIComponent(slug)}/curate/commits/${encodeURIComponent(id)}`);

/* ── Price ──────────────────────────────────────────────────────────────── */

/** What a run of this analysis costs -- RunCost: {seconds, steps_seconds,
 *  publish_seconds, basis: measured|declared|unknown, runs, split_runs, via,
 *  sentence}. Read for the run popover and the depth offer; the caller
 *  renders "not known" on any failure rather than withholding the action. */
export const getRunCost = (analysisId) =>
  get(`/api/analyses/${encodeURIComponent(analysisId)}/cost`);

/* ── Depth offer ────────────────────────────────────────────────────────── */

/** The analyses at the analysis and assessment tiers that have never run
 *  on this resource, each priced with the split, and a measured-only total
 *  that names what it excludes. {slug, measured_before, analyses[], total}. */
export const getDepthOffer = (slug) => get(`/api/projects/${encodeURIComponent(slug)}/depth-offer`);

/** Record what happened to the offer on the verdict it belongs to:
 *  outcome declined | accepted | chose, with the ids and run ids. */
export const postDepthOfferOutcome = (githubUrl, { outcome, analysisIds = [], runIds = [] }) =>
  post('/api/discovery/disposition/depth-offer', { github_url: githubUrl, outcome, analysis_ids: analysisIds, run_ids: runIds });

/** DepthOffer's sibling for layer 2 (owner's ruling, 2026-09-15): promoting
 *  accepted architecture-recovery verdicts into real Egeria components,
 *  instead of running never-run analyses. {slug, curation_id, layer1_done,
 *  already_decided, total_components, accepted, remaining_components, cost}. */
export const getCatalogueDepthOffer = (slug) => get(`/api/projects/${encodeURIComponent(slug)}/catalogue-depth-offer`);

/** Record the outcome on ONE catalogue record — once per record, same
 *  outcome vocabulary as DepthOffer's. */
export const postCatalogueDepthOfferOutcome = (slug, curationId, outcome) =>
  post(`/api/projects/${encodeURIComponent(slug)}/curate/commits/${encodeURIComponent(curationId)}/layer2-offer`,
       { outcome });
/* ── Records ────────────────────────────────────────────────────────────── */

/** Save a report: the act of writing a list down. `members` null = the
 *  whole list. The server re-reads the list and snapshots it. */
export const saveReport = (slug, analysisId, { question = '', metric = '', members = null, facet = '', name = '', scope = 'all', corrects = '' } = {}) =>
  post(`/api/projects/${encodeURIComponent(slug)}/members/${encodeURIComponent(analysisId)}/report`,
       { question, metric, members, facet, name, scope, corrects });

/** Both kinds, newest first, reports carrying out_of_date. `entityType`
 *  defaults to 'repo' — pass the `apiEntityType()`-translated value for a
 *  database/filesystem, routed to `list_entity_records`'s sibling endpoint
 *  (Backlog.md, "Disposition is NOT fixed here", 2026-09-22); the repo path
 *  is unchanged. `recordExportHref`/`getRecord` (a bare GET by record id)
 *  needed no sibling — that route never checked entity_type at all. */
export const listRecords = (slug, entityType = 'repo') =>
  entityType === 'repo'
    ? get(`/api/projects/${encodeURIComponent(slug)}/records`)
    : get(`/api/projects/entity/${encodeURIComponent(entityType)}/${encodeURIComponent(slug)}/records`);
export const recordExportHref = (slug, id, fmt) =>
  `/api/projects/${encodeURIComponent(slug)}/records/${encodeURIComponent(id)}?fmt=${fmt}`;

/** The three acts on a report: work_list | rfa | journal. `rows` null = the
 *  whole report. The server acts on the stored snapshot. `entityType` as
 *  above. */
export const actOnRecord = (slug, id, { action, rows = null, name = '', suggestTo = [], journalId = '' } = {}, entityType = 'repo') =>
  post(
    entityType === 'repo'
      ? `/api/projects/${encodeURIComponent(slug)}/records/${encodeURIComponent(id)}/act`
      : `/api/projects/entity/${encodeURIComponent(entityType)}/${encodeURIComponent(slug)}/records/${encodeURIComponent(id)}/act`,
    { action, rows, name, suggest_to: suggestTo, journal_id: journalId },
  );

/* ── Component review ───────────────────────────────────────────────────── */

/** The branches under `prefix` ('' = root): counts, type mix, low-confidence
 *  count, declared ports, the resolved verdict. */
export const getComponentTree = (slug, prefix = '') =>
  get(`/api/projects/${encodeURIComponent(slug)}/components/tree?prefix=${encodeURIComponent(prefix)}`);
export const getComponentLeaves = (slug, branch) =>
  get(`/api/projects/${encodeURIComponent(slug)}/components/leaves?branch=${encodeURIComponent(branch)}`);
/** One verdict row per scope; accepted ones queue their materialisation. */
export const postBranchVerdicts = (slug, scopeLocators, verdict, note = '') =>
  post(`/api/projects/${encodeURIComponent(slug)}/components/verdicts`, { scope_locators: scopeLocators, verdict, note });

/** SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md §2 — clustering.py's candidate
 *  blueprints, each carrying its own verdict/materialization state and its
 *  members'/children's, already resolved server-side. `perspectives` lists
 *  every reading present, since a cluster only exists within one (§3). */
export const getComponentBlueprints = (slug) =>
  get(`/api/projects/${encodeURIComponent(slug)}/components/blueprints`);

/** Accept/reject one cluster. Accepting materialises a real Egeria
 *  SolutionBlueprint (blueprint_materializer.py) and queues its resolvable
 *  members/children for CollectionMembership — the caller does not wait on
 *  that queue, see SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md §4. */
export const postBlueprintVerdict = (slug, perspective, clusterName, verdict, note = '') =>
  post(`/api/curate/blueprint-verdicts/repo/${encodeURIComponent(slug)}`,
       { perspective, cluster_name: clusterName, verdict, note });

/* ── Automate ────────────────────────────────────────────────────────────
 * The 8th intent (`web/routes/automate.py`, `web/routes/schedules.py`).
 * Local-first: subscriptions and schedules live in RE's own registry, not
 * as Egeria NotificationType elements yet — see notification_subscriptions'
 * table docstring in registry.py. */

/** Subscriptions, each carrying `has_schedule` — whether an enabled,
 *  recurring schedule exists for the same (entity, analysis_id). Detection
 *  only ever runs off a scheduled completion, so an active subscription
 *  with no schedule can never fire; callers must show that, not hide it. */
export const listSubscriptions = ({ entityType = '', entitySlug = '', analysisId = '', activeOnly = false } = {}) => {
  const params = new URLSearchParams();
  if (entityType) params.set('entity_type', entityType);
  if (entitySlug) params.set('entity_slug', entitySlug);
  if (analysisId) params.set('analysis_id', analysisId);
  if (activeOnly) params.set('active_only', 'true');
  const qs = params.toString();
  return get(`/api/automate/subscriptions${qs ? `?${qs}` : ''}`);
};

export const setSubscriptionActive = (id, active) =>
  post(`/api/automate/subscriptions/${encodeURIComponent(id)}/${active ? 'activate' : 'deactivate'}`);

/** Create one. `entityType` is always 'repo' from every caller today — the
 *  Questions-checklist engine (app.js) that calls this is itself gated to
 *  `state.resourceType === 'repo'` (DEFECT-UNBUILT... no, see app.js's own
 *  "Repos only, in /next" branch) — but the parameter stays real rather than
 *  hardcoded in the request body, so this doesn't need to change the day
 *  that gate is lifted. */
export const createSubscription = (entityType, entitySlug, analysisId, label = '') =>
  post('/api/automate/subscriptions',
       { entity_type: entityType, entity_slug: entitySlug, analysis_id: analysisId, label });

/** Every scheduled analysis across every resource — what a subscription
 *  actually needs to fire. Global by design; there is no per-resource
 *  variant because the Automate pane's own filter checkbox does that
 *  client-side, same as the current UI's Schedules overview. */
export const listAllSchedules = () => get('/api/schedules/');

export const deleteSchedule = (entityType, entitySlug, analysisId) =>
  request(`/api/schedules/${encodeURIComponent(entityType)}/${encodeURIComponent(entitySlug)}/${encodeURIComponent(analysisId)}`,
          { method: 'DELETE' });

/** Runs THE SCHEDULE, through the same dispatch its timer uses — so what
 *  this does is exactly what the cadence would do, not a separate code
 *  path that could diverge from it. Does NOT advance `next_run`; the
 *  caller is expected to say so, the same distinction classic's
 *  `runScheduleNow` draws (index.html). */
export const runScheduleNow = (entityType, entitySlug, analysisId) =>
  post(`/api/schedules/${encodeURIComponent(entityType)}/${encodeURIComponent(entitySlug)}/${encodeURIComponent(analysisId)}/run`);

/* ── Admin (PLAN-FINISH-REPOS.md item 5) ────────────────────────────────────
 * Read-mostly system/catalog-configuration views, reachable from the header's
 * own ⚙ Admin button — see next/admin/*.js. Every route here already backs
 * classic's index.html Admin panes; nothing new was added on the server. */

/** The Annotation Types registry — every schema RE knows how to publish as
 *  an Egeria annotation, and its property/class bindings. */
export const listAnnotationTypes = () => get('/api/analyses/annotation-types');
export const getAnnotationType = (typeName) =>
  get(`/api/analyses/annotation-types/${encodeURIComponent(typeName)}`);

/** Register/edit/delete an annotation type (SPEC-ADMIN-THE-FOUR-GAPS.md §4 —
 *  the routes already existed; next/admin/annotation_types.js made zero
 *  write calls until this pass). `type` is immutable once registered —
 *  editing sends only the mutable fields, matching classic's own
 *  disabled-Type-Key-on-edit behaviour. */
export const registerAnnotationType = (body) => post('/api/analyses/annotation-types', body);
export const updateAnnotationType = (typeName, body) =>
  put(`/api/analyses/annotation-types/${encodeURIComponent(typeName)}`, body);
export const deleteAnnotationType = (typeName) =>
  del(`/api/analyses/annotation-types/${encodeURIComponent(typeName)}`);

/** Blast-radius number for a delete/rename confirmation — a LOWER BOUND on
 *  how many projects have a local record of publishing this type, never a
 *  full annotation count (see the route's own docstring in analyses.py).
 *  `exact` is always false; callers must not read `projects_published: 0`
 *  as "unused". */
export const getAnnotationTypeUsage = (typeName) =>
  get(`/api/analyses/annotation-types/${encodeURIComponent(typeName)}/usage`);

/** The full, unscoped Question catalog — every authored question with its
 *  funnel stage, perspectives and answering mechanism. Includes retired
 *  entries (flagged via `retired`, never hidden — see question_catalog.js). */
export const listQuestionCatalog = (resourceType = 'repo') =>
  get(`/api/analyses/question-catalog?resource_type=${encodeURIComponent(resourceType)}`);

/** Append-only writes (SPEC-ADMIN-THE-FOUR-GAPS.md §4, project owner
 *  decision 2026-09-20): add a new question, or retire an existing one.
 *  There is deliberately no "edit" call — question_catalog_writer.py
 *  refuses a reworded add (same question text, different fields) as a
 *  duplicate rather than upserting it. */
export const addQuestionCatalogEntry = (body) => post('/api/analyses/question-catalog/questions', body);
export const retireQuestionCatalogEntry = (question) =>
  post('/api/analyses/question-catalog/questions/retire', { question });

/** The in-process log ring buffer (`observability/logging_setup.py`) —
 *  bounded, in-memory, empty after a restart. The response carries buffer
 *  metadata (held/capacity/full/note) alongside the records precisely so a
 *  caller can tell "nothing logged", "buffer emptied by a restart" and "your
 *  filter excluded everything" apart — collapsing them to one empty state is
 *  the absence-as-answer failure this codebase keeps finding. */
export const listLogs = ({ limit = 300, level = '', logger = '' } = {}) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (level) params.set('level', level);
  if (logger) params.set('logger', logger);
  return get(`/api/logs/?${params}`);
};

/** Prefect flow-run status for locally-dispatched (`executes_at: prefect`)
 *  survey steps only — `executes_at: egeria` steps are coordinated by Egeria
 *  itself and are not reflected here. */
export const getPrefectStatus = () => get('/api/prefect/status');
export const listPrefectFlowRuns = (limit = 50) => get(`/api/prefect/flow-runs?limit=${limit}`);
export const cancelPrefectFlowRun = (flowRunId) =>
  post(`/api/prefect/flow-runs/${encodeURIComponent(flowRunId)}/cancel`);

/* ── Egeria Alignment (Resync) — resource_explorer/egeria_resync.py ────────
 * Scanning never writes; apply() runs only the step names it is given, in
 * the module's own dependency order regardless of the order sent. */

/** Four states on purpose (see admin/resync.js): enforced, settling, not
 *  enforced, and "could not tell" all read differently — a private-zone
 *  check that failed must never render the same as one that passed. */
export const getPrivateZone = () => get('/api/egeria/private-zone');

/** Read-only. `reachable: false` must never be presented as "no drift" — an
 *  unread catalog is not a catalog known to be fine. */
export const getResyncScan = () => get('/api/egeria/resync/scan');

/** The scheduled scan-and-clear loop's own status — last_run_at,
 *  consecutive_failures, etc. Cheap: reads in-process state, same contract
 *  as getBootstrapStatus(), never calls Egeria itself. Lets the "already
 *  scheduled" rows distinguish "ran and found nothing" from "hasn't run in
 *  days" — otherwise identical clean rows. */
export const getResyncStatus = () => get('/api/egeria/resync/scheduler-status');

/** Runs exactly the named repair steps — nothing runs unless asked for by
 *  name. `steps` is a plain array of `Finding.repair_step` values. */
export const applyResyncSteps = (steps) => post('/api/egeria/resync/apply', { steps });

/* ── Repair — per-repository correction (resource_explorer/repair.py) ──────
 * A different job from Resync: fixing one repo that was registered wrong,
 * not reconciling the store against Egeria. Every mutation below is a real
 * write and its caller is expected to confirm first, naming the blast
 * radius from the response fields these routes already return. */

export const repairRename = (slug, newSlug) =>
  post(`/api/admin/repair/repos/${encodeURIComponent(slug)}/rename`, { new_slug: newSlug });
export const repairGithubUrl = (slug, newUrl, confirm = false) =>
  post(`/api/admin/repair/repos/${encodeURIComponent(slug)}/github-url`,
       { new_url: newUrl, confirm });
export const repairEnableCollection = (slug, collectionType) =>
  post(`/api/admin/repair/repos/${encodeURIComponent(slug)}/collections/enable`,
       { collection_type: collectionType });
export const getRepairDrift = (slug) =>
  get(`/api/admin/repair/repos/${encodeURIComponent(slug)}/drift`);
export const getRepairMemberships = (slug) =>
  get(`/api/admin/repair/repos/${encodeURIComponent(slug)}/memberships`);
export const repairRepointMembership = (slug, fromInvestigation, toInvestigation) =>
  post(`/api/admin/repair/repos/${encodeURIComponent(slug)}/memberships/repoint`,
       { from_investigation: fromInvestigation, to_investigation: toInvestigation });
export const repairDropMembership = (slug, investigationSlug) =>
  request(`/api/admin/repair/repos/${encodeURIComponent(slug)}/memberships/`
          + `${encodeURIComponent(investigationSlug)}`, { method: 'DELETE' });
