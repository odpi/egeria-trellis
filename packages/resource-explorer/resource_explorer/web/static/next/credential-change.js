/** Change a stored database credential, test-before-save (parity G2, PI-021).
 *
 *  The server does the safety work (PATCH /api/databases/{slug}/credentials):
 *  it connects with the new credential BEFORE anything is stored, refuses a
 *  credential the database refuses (HTTP 400, nothing stored, nothing
 *  projected), writes one activity row naming the new user and sets
 *  `credential_changed_at`. This component only asks, shows the server's
 *  sentence, and then READS BACK the drift check the CLI prints:
 *  "in registry · in .omsecrets · in sync".
 *
 *  The password lives in the input and nowhere else: never an attribute, never
 *  a URL, never in a row or a message, and the field is emptied on a save. A
 *  pressed control looks pending and ignores a second press. */
import { esc } from '/static/next/app.js';
import { changeDatabaseCredentials, getCredentialDrift } from '/static/re-api.js';
import { ago } from '/static/next/format.js';

/** The drift check as one short line, from the route's own fields: a side that
 *  could not be checked says so rather than reading as "in sync". */
export function driftLine(d) {
  const parts = [d.in_registry ? 'in registry' : 'not in registry'];
  if (!d.omsecrets_configured) {
    parts.push('.omsecrets not checked (no secrets path is set)');
  } else {
    parts.push(d.in_omsecrets ? 'in .omsecrets' : 'not in .omsecrets');
    parts.push(d.in_sync === true ? 'in sync' : 'drift');
  }
  return parts.join(' · ');
}

/** The form's markup. `db` supplies the current user to prefill (never a password).
 *  `opts` re-titles it for another use of the same test-before-save form (Table preview's "Your credential for
 *  <db>"): `titleHtml`, `noteHtml`, `saveLabel`, `prefill: false` (leave the user blank), `passwordPlaceholder`.
 *  With no opts the markup is exactly what it always was. */
export function credentialChangeHtml(db, opts = {}) {
  const titleHtml = opts.titleHtml || `Change the stored credential for <span class="font-mono">${esc(db.slug)}</span>`;
  const noteHtml = opts.noteHtml || 'It is tested against the database first; if the database refuses it, nothing is saved.';
  const prefill = opts.prefill === false ? '' : (db.db_user || '');
  return `<div data-cred-change="${esc(db.slug)}" class="text-caveat text-ink">
    <div class="mb-s1 text-ink">${titleHtml}</div>
    <p class="mb-s2 max-w-[70ch] text-ink-muted">${noteHtml}</p>
    <div class="flex flex-wrap items-center gap-s2">
      <input data-cred-user type="text" autocomplete="off" placeholder="user" value="${esc(prefill)}"
        class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      <input data-cred-password type="password" autocomplete="off" placeholder="${esc(opts.passwordPlaceholder || 'new password')}"
        class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      <button type="button" data-cred-save
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
        >${esc(opts.saveLabel || 'Test and save')}</button>
      <button type="button" data-cred-cancel class="cursor-pointer bg-transparent text-ink underline">Cancel</button>
    </div>
    <div data-cred-result class="mt-s2"></div>
  </div>`;
}

/** Wires a rendered form. `ctx.onSaved(summary)` lets the caller refresh its own
 *  copy of the row; `ctx.onCancel()` closes the form. */
export function bindCredentialChange(host, slug, ctx = {}) {
  const saveLabel = ctx.saveLabel || 'Test and save';
  const notWord = ctx.notWord || 'not saved';
  const user = host.querySelector('[data-cred-user]');
  const pw = host.querySelector('[data-cred-password]');
  const save = host.querySelector('[data-cred-save]');
  const result = host.querySelector('[data-cred-result]');
  host.querySelector('[data-cred-cancel]')?.addEventListener('click', () => {
    pw.value = '';
    if (ctx.onCancel) ctx.onCancel();
  });
  save.addEventListener('click', async () => {
    if (save.disabled) return;                          // a pressed control ignores a second press
    const u = user.value.trim();
    const p = pw.value;
    if (!u || !p) {
      result.innerHTML = `<span data-cred-error class="text-state-warn">${notWord} · a user and a password are both required</span>`;
      return;
    }
    save.disabled = true;
    save.textContent = 'Testing, then saving…';
    result.innerHTML = `<span class="text-ink-muted">… testing the new credential</span>`;
    try {
      if (ctx.saveFn) {
        // Another use of the form (Table preview): the caller tests and holds the credential, and answers
        // with the line to show. The password is emptied the same way.
        const line = await ctx.saveFn(u, p);
        pw.value = '';
        result.innerHTML = line || '';
        if (ctx.onSaved) ctx.onSaved({ db_user: u });
        return;
      }
      const summary = await changeDatabaseCredentials(slug, { user: u, password: p });
      pw.value = '';                                    // its job is done
      let drift = '';
      try {
        drift = driftLine(await getCredentialDrift(slug));
      } catch (err) {
        drift = `the drift check could not be read (${err && err.message ? err.message : err})`;
      }
      const when = summary && summary.credential_changed_at ? ago(summary.credential_changed_at) : '';
      result.innerHTML = `<div data-cred-saved class="text-ink">✓ credential changed · ${esc(summary.db_user || u)}${
        when ? ` · ${esc(when)}` : ''}</div>
        <div data-cred-drift class="text-ink-muted">${esc(drift)}</div>`;
      if (ctx.onSaved) ctx.onSaved(summary);
    } catch (err) {
      result.innerHTML = `<span data-cred-error class="text-state-warn">${
        err && err.status === 401 ? `${notWord} · sign in to change a credential`
          : `${notWord} · ${esc(err && err.message ? err.message : String(err))}`}</span>`;
    } finally {
      save.disabled = false;
      save.textContent = saveLabel;
    }
  });
}

/** The one opener for the form, wherever it is asked for (the resource header's door, a run line's credential link).
 *  Fills `slot` with the form for `p` (a database row) and wires it. The row follows what the server saved (never a
 *  password). `onSaved(summary)` and `onCancel()` are the caller's extra reactions; with no onCancel Cancel empties the slot. */
export function openCredentialForm(slot, p, { onSaved = null, onCancel = null } = {}) {
  slot.innerHTML = credentialChangeHtml(p);
  bindCredentialChange(slot, p.slug, {
    onSaved: (summary) => {
      p.db_user = summary.db_user || p.db_user;
      p.credential_changed_at = summary.credential_changed_at || p.credential_changed_at;
      p.credential_status = 'ok';
      if (onSaved) onSaved(summary);
    },
    onCancel: () => { slot.innerHTML = ''; if (onCancel) onCancel(); },
  });
}
