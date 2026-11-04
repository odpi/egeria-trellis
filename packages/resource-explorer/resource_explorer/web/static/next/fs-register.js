/* Register a file system on /next (Files step 1, parity PI-022).
 *
 * Reached from the sidebar's "＋ Add a file system" (the Find action while the FS chip is on). The form posts to
 * the existing `POST /api/filesystems/` with the route's own model and nothing else:
 *
 *   slug, display_name, local_mount_point, canonical_mount_point, group_slug
 *
 * There is NO Egeria field. The platform URL and view server come from the platform configuration the server
 * already reads (classic's modal asked for them per resource; that is dropped on purpose). Registering sends
 * nothing to Egeria: attaching the root folder is the Publish on the file system's Curate page.
 *
 * Validation is a cue on the field (a glyph and a short word), not a sentence below the form:
 *   slug already registered     ✕  an ERROR: Register is held until the slug changes
 *   mount this server can't read ⚠  a WARNING: registering is still allowed (the server may see another path)
 * The word beside the glyph says which one applies. Words here are placeholders for the designer.
 *
 * On success the file system is in the sidebar under FS and selected, read back from the re-fetched list.
 *
 * Remote reach (outcome 3, S3 slice 2026-11-02): "Where it is" chooses a local mount or an S3 bucket. The S3 fields
 * are endpoint URL (blank = AWS), region, bucket, root prefix, path style, TLS verify, and the credential source:
 * a public bucket, a key held in the secrets store (typed here, sent once, held by reference only), or the server's
 * host profile. "Check reach" makes one list call before Register and shows the outcome sentence or the refusal.
 * When no secrets store is configured the form says so BEFORE the press and holds Register for a typed key.
 */
import { esc, state, ensureResourceListLoaded, refreshGroupsAndSidebar, selectResourceFromChat }
  from '/static/next/app.js';
import { openDialog, closeCellDetail } from '/static/next/worklist.js';
import { registerFilesystem, checkFilesystemMount, listGroups, checkS3Reach, listReachMechanisms }
  from '/static/re-api.js';

/** Placeholder words, in one table so the designer's words replace them in one place. */
export const FS_REGISTER_WORDS = {
  title: 'Add a file system',
  sub: 'Registering stores it here. Nothing is sent to Egeria.',
  slugTaken: 'already registered',
  mountUnreadable: "this server can't read this path · you can still register",
  mountNotFolder: 'this is a file, not a folder · you can still register',
  mountReadable: 'readable here',
  mountChecking: 'checking',
  register: 'Register',
  registering: 'Registering…',
  where: 'Where it is',
  whereLocal: 'A folder this server mounts',
  whereS3: 'An S3 bucket (or MinIO)',
  credential: 'Credential',
  credNone: 'None · a public bucket',
  credKey: 'A key held in the secrets store',
  credProfile: "This server's host profile",
  keyNote: 'The key is sent once and held in the secrets store by reference; the registry keeps only the last four characters of its id (…XXXX), never the id or the secret.',
  noStore: 'no secrets store is configured on this server · a key cannot be held · choose a public bucket or a host profile',
  checkReach: 'Check reach',
  checking: 'Checking…',
  notChecked: 'not checked yet',
};

const blank = () => ({
  slug: '', display_name: '', local_mount_point: '', canonical_mount_point: '', group_slug: '',
  protocol: 'local', endpoint_url: '', region: '', bucket: '', root_prefix: '', path_style: false, tls_verify: true,
  credential_kind: 'none', credential_ref: '', access_key_id: '', secret_access_key: '',
});

const view = {
  f: blank(),
  groups: [],
  mount: { state: 'unchecked', path: '' },   // unchecked | checking | readable | not_readable | not_a_folder
  takenByServer: '',                          // a slug the server itself refused as already registered
  reg: { phase: 'idle', error: '' },          // idle | saving | error
  reach: { phase: 'idle', sentence: '', reachable: null },   // the S3 "Check reach" answer, for the fields as typed
  storeConfigured: null,                      // can this server hold a key? null = could not ask
};

/** The slug as the registry stores it (dashes become underscores, lower case). */
export const storedSlug = (slug) => String(slug || '').trim().replace(/-/g, '_').toLowerCase();

/** Is this slug already a registered file system? From the list on screen, and from the server's own refusal. */
export function slugIsTaken(slug) {
  const s = storedSlug(slug);
  if (!s) return false;
  if (view.takenByServer && storedSlug(view.takenByServer) === s) return true;
  return (state.filesystems || []).some((r) => storedSlug(r.slug) === s);
}

const isS3 = (f) => f.protocol === 's3';
const S3_KEYS = new Set(['endpoint_url', 'region', 'bucket', 'root_prefix', 'path_style', 'tls_verify',
  'credential_ref', 'access_key_id', 'secret_access_key']);
/** A typed key cannot be held when the server has no secrets store: said before the press, and Register is held. */
export const keyCannotBeHeld = (f, storeConfigured) => isS3(f) && f.credential_kind === 'secret_ref' && storeConfigured === false;
export function requiredFilled(f, storeConfigured = true) {
  if (!(f.slug && f.display_name)) return false;
  if (!isS3(f)) return !!f.local_mount_point;
  if (!f.bucket) return false;
  if (f.credential_kind === 'secret_ref') return !keyCannotBeHeld(f, storeConfigured) && !!(f.access_key_id && f.secret_access_key);
  return true;
}
const canRegister = () => requiredFilled(view.f, view.storeConfigured) && !slugIsTaken(view.f.slug) && view.reg.phase !== 'saving';

/** The cue for the slug field: '' when fine. */
function slugCue() {
  return slugIsTaken(view.f.slug)
    ? { kind: 'error', glyph: '✕', word: FS_REGISTER_WORDS.slugTaken } : null;
}
/** The cue for the mount field: '' until it has been checked. */
function mountCue() {
  const s = view.mount.state;
  if (s === 'checking') return { kind: 'info', glyph: '◔', word: FS_REGISTER_WORDS.mountChecking };
  if (s === 'readable') return { kind: 'ok', glyph: '✓', word: FS_REGISTER_WORDS.mountReadable };
  if (s === 'not_readable') return { kind: 'warning', glyph: '⚠', word: FS_REGISTER_WORDS.mountUnreadable };
  if (s === 'not_a_folder') return { kind: 'warning', glyph: '⚠', word: FS_REGISTER_WORDS.mountNotFolder };
  return null;
}
const cueHtml = (cue, field) => (cue
  ? `<span data-fs-cue="${field}" data-cue-kind="${cue.kind}" class="${
    cue.kind === 'error' ? 'text-state-warn' : cue.kind === 'warning' ? 'text-ink' : 'text-ink-muted'}"><span class="font-glyph" aria-hidden="true">${cue.glyph}</span> ${esc(cue.word)}</span>`
  : `<span data-fs-cue="${field}" data-cue-kind="none"></span>`);

const inputClass = (cue) => `w-full rounded-sm border ${
  cue && cue.kind === 'error' ? 'border-state-warn' : cue && cue.kind === 'warning' ? 'border-state-gap' : 'border-rule'
} bg-transparent px-2 py-[3px] text-caveat text-ink`;

function formHtml() {
  const f = view.f;
  const sc = slugCue();
  const mc = mountCue();
  const field = (label, cueSlot, inner) => `<div>
    <div class="mb-[2px] flex flex-wrap items-baseline gap-s2"><label class="text-caps uppercase tracking-caps text-ink-muted">${esc(label)}</label>${cueSlot}</div>${inner}</div>`;
  const input = (key, placeholder, cue) =>
    `<input data-fsr="${key}" type="text" placeholder="${esc(placeholder)}" autocomplete="off" value="${esc(f[key] ?? '')}"
       class="${inputClass(cue)}">`;
  const groupOptions = view.groups.map((g) =>
    `<option value="${esc(g.slug)}" ${f.group_slug === g.slug ? 'selected' : ''}>${esc(g.display_name)}</option>`).join('');
  const saving = view.reg.phase === 'saving';
  return `
    <div class="grid grid-cols-2 gap-s2">
      ${field('Slug *', cueHtml(sc, 'slug'), input('slug', 'my-data', sc))}
      ${field('Display name *', '', input('display_name', 'My shared mount'))}
    </div>
    <div class="mt-s2">${field(FS_REGISTER_WORDS.where, '', `<select data-fsr="protocol" class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      <option value="local" ${isS3(f) ? '' : 'selected'}>${esc(FS_REGISTER_WORDS.whereLocal)}</option>
      <option value="s3" ${isS3(f) ? 'selected' : ''}>${esc(FS_REGISTER_WORDS.whereS3)}</option></select>`)}</div>
    ${isS3(f) ? s3Html(field) : `
    <div class="mt-s2">${field('Mount (local path) *', cueHtml(mc, 'mount'), input('local_mount_point', '/path/to/local/data', mc))}</div>
    <div class="mt-s2">${field('Canonical mount (blank = the mount)', '', input('canonical_mount_point', 'file://shared-nfs/data'))}</div>`}
    <div class="mt-s2">${field('Group', '', `<select data-fsr="group_slug" class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      <option value="">No group</option>${groupOptions}</select>`)}</div>
    <div class="mt-s3 flex items-center gap-s2">
      <button data-act="fsr-register" ${canRegister() ? '' : 'disabled'}
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink disabled:cursor-default disabled:opacity-60"
        >${saving ? FS_REGISTER_WORDS.registering : FS_REGISTER_WORDS.register}</button>
      <span data-fsr-state class="text-caveat text-state-warn">${view.reg.error ? esc(view.reg.error) : ''}</span>
    </div>`;
}

/** A plain text input for the S3 fields (a literal class: no interpolation in a class attribute). */
const s3Input = (key, placeholder, type = 'text') =>
  `<input data-fsr="${key}" type="${type}" placeholder="${esc(placeholder)}" autocomplete="off" value="${esc(view.f[key] ?? '')}"
     class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">`;
const s3Check = (key, label) =>
  `<label class="flex items-center gap-s2 text-caveat text-ink"><input data-fsr="${key}" type="checkbox" ${view.f[key] ? 'checked' : ''}> ${esc(label)}</label>`;

/** The S3 fields, the credential source and Check reach. */
export function s3Html(field) {
  const f = view.f;
  const kind = f.credential_kind;
  const opt = (value, word) => `<option value="${value}" ${kind === value ? 'selected' : ''}>${esc(word)}</option>`;
  const checking = view.reach.phase === 'checking';
  const reachLine = view.reach.phase === 'done'
    ? (view.reach.reachable
      ? `<span data-fs-reach="reachable" class="text-caveat text-ink"><span class="font-glyph" aria-hidden="true">✓</span> ${esc(view.reach.sentence)}</span>`
      : `<span data-fs-reach="unreachable" class="text-caveat text-state-warn"><span class="font-glyph" aria-hidden="true">✕</span> ${esc(view.reach.sentence)}</span>`)
    : `<span data-fs-reach="not_checked" class="text-caveat text-ink-muted">${esc(FS_REGISTER_WORDS.notChecked)}</span>`;
  const keyFields = kind === 'secret_ref'
    ? (keyCannotBeHeld(f, view.storeConfigured)
      ? `<div class="mt-s2"><span data-fs-no-store class="text-caveat text-state-warn">${esc(FS_REGISTER_WORDS.noStore)}</span></div>`
      : `<div class="mt-s2 grid grid-cols-2 gap-s2">
          ${field('Access key id *', '', s3Input('access_key_id', 'AKIA…'))}
          ${field('Secret access key *', '', s3Input('secret_access_key', '', 'password'))}
        </div>
        <div class="mt-s2"><span class="text-caveat text-ink-muted">${esc(FS_REGISTER_WORDS.keyNote)}</span></div>`)
    : kind === 'profile'
      ? `<div class="mt-s2">${field('Profile name (blank = default)', '', s3Input('credential_ref', 'analytics-ro'))}</div>`
      : '';
  return `
    <div class="mt-s2 grid grid-cols-2 gap-s2">
      ${field('Endpoint URL (blank = AWS)', '', s3Input('endpoint_url', 'http://127.0.0.1:8200'))}
      ${field('Region', '', s3Input('region', 'us-east-1'))}
    </div>
    <div class="mt-s2 grid grid-cols-2 gap-s2">
      ${field('Bucket *', '', s3Input('bucket', 'datalake'))}
      ${field('Root prefix', '', s3Input('root_prefix', 'demo/'))}
    </div>
    <div class="mt-s2 flex items-center gap-s2">${s3Check('path_style', 'Path style (MinIO)')} ${s3Check('tls_verify', 'Verify TLS')}</div>
    <div class="mt-s2">${field(FS_REGISTER_WORDS.credential, '', `<select data-fsr="credential_kind" class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      ${opt('none', FS_REGISTER_WORDS.credNone)}${opt('secret_ref', FS_REGISTER_WORDS.credKey)}${opt('profile', FS_REGISTER_WORDS.credProfile)}</select>`)}</div>
    ${keyFields}
    <div class="mt-s2 flex items-center gap-s2">
      <button data-act="fsr-check-reach" ${checking || !f.bucket || keyCannotBeHeld(f, view.storeConfigured) ? 'disabled' : ''}
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink disabled:cursor-default disabled:opacity-60"
        >${checking ? FS_REGISTER_WORDS.checking : FS_REGISTER_WORDS.checkReach}</button>
      ${reachLine}
    </div>`;
}

/** The S3 fields as the routes take them (the secret only when a key is the source). */
export function s3Payload(f) {
  const key = f.credential_kind === 'secret_ref';
  return {
    protocol: 's3', endpoint_url: f.endpoint_url, region: f.region, bucket: f.bucket, root_prefix: f.root_prefix,
    path_style: !!f.path_style, tls_verify: !!f.tls_verify, credential_kind: f.credential_kind,
    credential_ref: f.credential_kind === 'profile' ? f.credential_ref : '',
    access_key_id: key ? f.access_key_id : '', secret_access_key: key ? f.secret_access_key : '',
  };
}

/** Read the inputs into memory. */
function capture(host) {
  host.querySelectorAll('[data-fsr]').forEach((inp) => {
    view.f[inp.dataset.fsr] = inp.type === 'checkbox' ? inp.checked
      : inp.dataset.fsr === 'secret_access_key' ? inp.value : inp.value.trim();
  });
}

/** Re-render the whole form (the protocol or the credential source changed: the fields shown change). */
function rerender(host) {
  host.innerHTML = formHtml();
  bind(host);
}

/** Repaint the cues and the Register button in place (no re-render: the typing focus stays where it is). */
function paintCues(host) {
  const set = (field, cue, inputKey) => {
    const slot = host.querySelector(`[data-fs-cue="${field}"]`);
    if (slot) slot.outerHTML = cueHtml(cue, field);
    const inp = host.querySelector(`[data-fsr="${inputKey}"]`);
    if (inp) inp.className = inputClass(cue);
  };
  set('slug', slugCue(), 'slug');
  set('mount', mountCue(), 'local_mount_point');
  const reg = host.querySelector('[data-act="fsr-register"]');
  if (reg) reg.disabled = !canRegister();
  const chk = host.querySelector('[data-act="fsr-check-reach"]');
  if (chk) chk.disabled = view.reach.phase === 'checking' || !view.f.bucket || keyCannotBeHeld(view.f, view.storeConfigured);
}

function bind(host) {
  host.querySelectorAll('[data-fsr]').forEach((inp) => {
    inp.addEventListener('input', () => {
      capture(host);
      if (inp.dataset.fsr === 'local_mount_point' && view.mount.path !== view.f.local_mount_point) {
        view.mount = { state: 'unchecked', path: '' };       // a changed path is not the checked one
      }
      if (S3_KEYS.has(inp.dataset.fsr) && view.reach.phase === 'done') {
        view.reach = { phase: 'idle', sentence: '', reachable: null };   // a changed field is not the checked one
        const line = host.querySelector('[data-fs-reach]');
        if (line) line.outerHTML = `<span data-fs-reach="not_checked" class="text-caveat text-ink-muted">${esc(FS_REGISTER_WORDS.notChecked)}</span>`;
      }
      paintCues(host);
    });
  });
  host.querySelector('[data-fsr="local_mount_point"]')?.addEventListener('change', () => checkMount(host));
  host.querySelector('[data-fsr="group_slug"]')?.addEventListener('change', () => capture(host));
  host.querySelectorAll('[data-fsr="protocol"], [data-fsr="credential_kind"]').forEach((sel) => {
    sel.addEventListener('change', () => { capture(host); view.reach = { phase: 'idle', sentence: '', reachable: null }; rerender(host); });
  });
  host.querySelectorAll('input[type="checkbox"][data-fsr]').forEach((box) => {
    box.addEventListener('change', () => { capture(host); paintCues(host); });
  });
  host.querySelector('[data-act="fsr-check-reach"]')?.addEventListener('click', () => checkReach(host));
  host.querySelector('[data-act="fsr-register"]')?.addEventListener('click', () => submit(host));
}

/** "Check reach": one list call for the fields as typed. Stores nothing. */
async function checkReach(host) {
  capture(host);
  if (view.reach.phase === 'checking') return;
  view.reach = { phase: 'checking', sentence: '', reachable: null };
  rerender(host);
  let answer;
  try {
    answer = await checkS3Reach({ slug: view.f.slug, ...s3Payload(view.f) });
    view.reach = { phase: 'done', sentence: answer.sentence || '', reachable: !!answer.reachable };
  } catch (err) {
    view.reach = { phase: 'done', sentence: `not checked · ${err && err.message ? err.message : err}`, reachable: false };
  }
  rerender(host);
}

/** Ask the server whether it can read the mount. A warning at most; it never gates Register. */
async function checkMount(host) {
  capture(host);
  const path = view.f.local_mount_point;
  if (!path) { view.mount = { state: 'unchecked', path: '' }; paintCues(host); return; }
  view.mount = { state: 'checking', path };
  paintCues(host);
  let answer;
  try {
    answer = await checkFilesystemMount(path);
  } catch (_) {
    if (view.mount.path === path) view.mount = { state: 'unchecked', path: '' };   // could not ask: no claim either way
    paintCues(host);
    return;
  }
  if (view.mount.path !== path) return;                          // a newer path is being checked
  const st = answer && answer.state;
  view.mount = { state: ['readable', 'not_readable', 'not_a_folder'].includes(st) ? st : 'unchecked', path };
  paintCues(host);
}

async function submit(host) {
  capture(host);
  if (view.reg.phase === 'saving') return;                       // a second press is ignored
  if (!canRegister()) { paintCues(host); return; }
  const f = view.f;
  view.reg = { phase: 'saving', error: '' };
  paintCues(host);
  const btn = host.querySelector('[data-act="fsr-register"]');
  if (btn) btn.textContent = FS_REGISTER_WORDS.registering;
  let out;
  try {
    out = !isS3(f)
      ? await registerFilesystem({
        slug: f.slug, display_name: f.display_name, local_mount_point: f.local_mount_point,
        canonical_mount_point: f.canonical_mount_point, group_slug: f.group_slug,
      })
      : await registerFilesystem({ slug: f.slug, display_name: f.display_name, group_slug: f.group_slug, ...s3Payload(f) });
  } catch (err) {
    const taken = err && err.status === 400 && /already registered/i.test(String(err.message || ''));
    if (taken) {
      // The cue is on the slug field; there is no sentence to add below the form.
      view.takenByServer = f.slug;
      view.reg = { phase: 'idle', error: '' };
    } else {
      view.reg = {
        phase: 'error',
        error: err && err.status === 401 ? 'not registered · sign in to register a file system'
          : `not registered · ${err && err.message ? err.message : err}`,
      };
    }
    const line = host.querySelector('[data-fsr-state]');
    if (line) line.textContent = view.reg.error;
    const b = host.querySelector('[data-act="fsr-register"]');
    if (b) b.textContent = FS_REGISTER_WORDS.register;
    paintCues(host);
    return;
  }
  // Registered. What follows is showing it: the list is re-read and the new row selected from that read.
  let selected = false;
  try {
    await ensureResourceListLoaded('filesystem');
    await refreshGroupsAndSidebar();
    selected = await selectResourceFromChat('filesystem', out.slug);
  } catch (_) { selected = false; }
  if (selected) {
    view.f = blank();
    view.mount = { state: 'unchecked', path: '' };
    view.reg = { phase: 'idle', error: '' };
    closeCellDetail();
    return;
  }
  view.reg = { phase: 'error', error: 'registered, but the list could not be re-read' };
  const line = host.querySelector('[data-fsr-state]');
  if (line) line.textContent = view.reg.error;
  const b = host.querySelector('[data-act="fsr-register"]');
  if (b) b.textContent = FS_REGISTER_WORDS.register;
  paintCues(host);
}

/** Open the dialog from a clean form. */
export async function openRegisterFileSystemDialog() {
  const el = openDialog(FS_REGISTER_WORDS.title, FS_REGISTER_WORDS.sub);
  view.f = blank();
  view.mount = { state: 'unchecked', path: '' };
  view.takenByServer = '';
  view.reg = { phase: 'idle', error: '' };
  view.reach = { phase: 'idle', sentence: '', reachable: null };
  const body = el.querySelector('#wl-detail-body');
  // The list the duplicate check reads must be there before the first keystroke.
  await ensureResourceListLoaded('filesystem');
  try { view.groups = (await listGroups()) || []; } catch (_) { view.groups = []; }
  try { view.storeConfigured = !!((await listReachMechanisms()) || {}).secrets_store_configured; } catch (_) { view.storeConfigured = null; }
  body.innerHTML = formHtml();
  bind(body);
  return el;
}
