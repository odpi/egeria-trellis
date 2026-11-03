/* Admin: which addresses RE may fetch a registered API document from -- read-only.
 *
 * The setting is configuration (RE_API_FETCH_ALLOW), not something edited here; this pane shows what is in force
 * and where it came from, so "why was my URL refused" has an answer a person can read. */
import { esc } from '/static/next/app.js';
import { getApiFetchPolicy } from '/static/re-api.js';

export function policyHtml(p) {
  const allow = p.allow || [];
  return `<div class="max-w-[70ch]">
    <h3 class="m-0 mb-s1 font-heading text-name font-normal text-ink">API fetch addresses</h3>
    <div class="mb-s2 text-provenance text-ink-muted">where RE may read a running instance's OpenAPI document from ·
      <span data-policy-source>${esc(p.source === 'default' ? 'default' : 'set in the environment')}</span> · read-only</div>
    <div class="text-answer text-ink" data-policy-allow>${allow.length
      ? allow.map((a) => `<span class="mr-s2 font-mono">${esc(a)}</span>`).join('')
      : '<span class="text-state-warn">nothing is allowed: no valid entry in the setting</span>'}</div>
    <div class="mt-s2 text-provenance text-ink-muted">never, whatever the list says:
      <span class="font-mono" data-policy-deny>${esc((p.always_deny || []).join(' '))}</span></div>
    <div class="mt-s2 text-provenance text-ink-muted">${esc(p.note || '')} Setting: <span class="font-mono">${esc(p.setting || '')}</span>.</div>
  </div>`;
}

export async function renderApiFetch(host) {
  host.innerHTML = '<div class="text-caveat text-ink-muted">Reading the setting…</div>';
  try {
    host.innerHTML = policyHtml(await getApiFetchPolicy());
  } catch (err) {
    host.innerHTML = `<div class="text-caveat text-state-warn">Could not read the setting: ${esc(err.message)}</div>`;
  }
}
