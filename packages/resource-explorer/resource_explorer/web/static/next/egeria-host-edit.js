/** Edit the Egeria-visible host of a server or a database (PATCH .../egeria-host, that one field only).
 *
 *  The line shows the saved value; "Change…" opens an input. A press looks pending and ignores a second press;
 *  a save changes the line itself (the value, a ✓ mark and a short word) rather than only printing a sentence.
 *  On a server the reply lists its databases that still carry the old value: they are offered, one press, and
 *  never changed without it. A database with its own value is not in that list. */
import { esc } from '/static/next/app.js';
import { changeDatabaseEgeriaHost, changeServerEgeriaHost } from '/static/re-api.js';

const LABEL = 'Egeria-visible host';

/** `inheritFrom` (a database line only): its server's value. A database that takes the server's value shows it
 *  muted, "(from its server)"; one that differs shows it in ink, "differs from its server" -- inheritance drawn as
 *  on the scope tree. The server's "use it for them" offer is unchanged. */
export function egeriaHostLineHtml(kind, slug, value, inheritFrom) {
  const tracked = kind === 'database' && inheritFrom !== undefined;
  const inherits = tracked && (!value || value === inheritFrom);
  const shown = value || (inherits ? inheritFrom : '');
  return `<div data-eh="${esc(kind)}:${esc(slug)}" ${tracked ? `data-eh-from="${esc(inheritFrom)}"` : ''} class="mt-[2px] text-provenance text-ink-muted">
    ${LABEL}: <span data-eh-value ${tracked ? `data-eh-inherited="${inherits ? '1' : '0'}"` : ''}
      class="font-mono ${inherits ? 'text-ink-muted' : 'text-ink'}">${esc(shown || 'not set')}</span>${
      tracked ? ` <span data-eh-source>${inherits ? '(from its server)' : 'differs from its server'}</span>` : ''}
    <span data-eh-mark class="text-state-ok"></span>
    <button type="button" data-eh-change class="cursor-pointer bg-transparent p-0 text-accent-ink underline">Change…</button>
    <div data-eh-body class="mt-s1"></div>
  </div>`;
}

/** Wires one rendered line. `ctx.onCarried(slugs, value)` lets the caller update the database lines of a server. */
export function bindEgeriaHostEdit(host, kind, slug, ctx = {}) {
  const valueEl = host.querySelector('[data-eh-value]');
  const markEl = host.querySelector('[data-eh-mark]');
  const body = host.querySelector('[data-eh-body]');
  const call = (v, carryFrom) => (kind === 'server'
    ? changeServerEgeriaHost(slug, v, carryFrom) : changeDatabaseEgeriaHost(slug, v));
  const fail = (err) => `<span data-eh-error class="text-state-warn">not saved · ${
    err && err.status === 401 ? 'sign in to change this'
      : esc(err && err.message ? err.message : String(err))}</span>`;

  host.querySelector('[data-eh-change]').addEventListener('click', () => {
    const current = valueEl.textContent === 'not set' ? '' : valueEl.textContent;
    body.innerHTML = `<div class="flex flex-wrap items-center gap-s2">
      <input data-eh-input type="text" autocomplete="off" placeholder="host.docker.internal" value="${esc(current)}"
        class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      <button type="button" data-eh-save class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink">Save</button>
      <button type="button" data-eh-cancel class="cursor-pointer bg-transparent text-ink underline">Cancel</button>
      <span data-eh-result></span></div>`;
    const input = body.querySelector('[data-eh-input]');
    const save = body.querySelector('[data-eh-save]');
    const result = body.querySelector('[data-eh-result]');
    body.querySelector('[data-eh-cancel]').addEventListener('click', () => { body.innerHTML = ''; });
    save.addEventListener('click', async () => {
      if (save.disabled) return;
      save.disabled = true;
      save.textContent = 'Saving…';
      try {
        const r = await call(input.value.trim());
        valueEl.textContent = r.egeria_host || 'not set';
        if (host.hasAttribute('data-eh-from')) {              // the saved value redraws its own inheritance cue
          const same = !r.egeria_host || r.egeria_host === host.getAttribute('data-eh-from');
          valueEl.dataset.ehInherited = same ? '1' : '0';
          valueEl.classList.toggle('text-ink-muted', same);
          valueEl.classList.toggle('text-ink', !same);
          const src = host.querySelector('[data-eh-source]');
          if (src) src.textContent = same ? '(from its server)' : 'differs from its server';
        }
        markEl.textContent = '✓ saved';
        body.innerHTML = '';
        const notes = [];
        if (r.note) notes.push(`<div data-eh-note class="text-ink">${esc(r.note)}</div>`);
        const carriable = r.carriable || [];
        if (carriable.length) {
          notes.push(`<div data-eh-carry class="text-ink">${carriable.length} database${carriable.length === 1 ? '' : 's'} on this server still ${
            carriable.length === 1 ? 'has' : 'have'} <span class="font-mono">${esc(r.previous || 'no value')}</span>.
            <button type="button" data-eh-carry-go class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[2px] text-caveat text-accent-ink">Use ${esc(r.egeria_host)} for them</button>
            <span data-eh-carry-result></span></div>`);
        }
        body.innerHTML = notes.join('');
        const go = body.querySelector('[data-eh-carry-go]');
        if (go) {
          go.addEventListener('click', async () => {
            if (go.disabled) return;
            go.disabled = true;
            go.textContent = 'Carrying…';
            const out = body.querySelector('[data-eh-carry-result]');
            try {
              const c = await call(r.egeria_host, r.previous || '');
              const slugs = (c.carried || []).map((x) => x.slug);
              go.textContent = `✓ carried to ${slugs.length}`;
              if (c.note) out.innerHTML = `<div data-eh-note class="text-ink">${esc(c.note)}</div>`;
              if (ctx.onCarried) ctx.onCarried(slugs, c.egeria_host);
            } catch (err) {
              go.disabled = false;
              go.textContent = `Use ${r.egeria_host} for them`;
              out.innerHTML = fail(err);
            }
          });
        }
        if (ctx.onSaved) ctx.onSaved(r);
      } catch (err) {
        save.disabled = false;
        save.textContent = 'Save';
        result.innerHTML = fail(err);
      }
    });
  });
}
