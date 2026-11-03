/* Admin: what Egeria's platform container can read -- read-only.
 *
 * The map is configuration (EGERIA_MOUNT_MAP) topped up at start from the platform container's mounts and the quickstart
 * compose file; this pane shows each pair with where it came from and when, so "why does this file system say Egeria's
 * mounts are unknown" has an answer a person can read. Nothing here is edited. */
import { esc } from '/static/next/app.js';
import { getEgeriaMounts } from '/static/re-api.js';

export function mountsHtml(m) {
  const pairs = m.pairs || [];
  return `<div class="max-w-[90ch]">
    <h3 class="m-0 mb-s1 font-heading text-name font-normal text-ink">Egeria's mounts</h3>
    <div class="mb-s2 text-provenance text-ink-muted">which host folders Egeria's platform container (${esc(m.container || '')}) can read ·
      read-only · set <span class="font-mono">${esc(m.setting || '')}</span>; the rest is read at start</div>
    ${pairs.length
      ? `<table class="w-full text-caveat" data-mounts-table><thead><tr class="text-left text-ink-muted">
          <th class="pr-s3 font-normal">Host folder</th><th class="pr-s3 font-normal">Egeria sees it as</th><th class="font-normal">Source</th></tr></thead>
          <tbody>${pairs.map((p) => `<tr data-mount-pair class="border-t border-rule">
            <td class="py-[2px] pr-s3 font-mono text-ink">${esc(p.host_prefix)}</td>
            <td class="py-[2px] pr-s3 font-mono text-ink">${esc(p.container_prefix)}</td>
            <td class="py-[2px] text-ink-muted" data-mount-source>${esc(p.label)}</td></tr>`).join('')}</tbody></table>`
      : `<div class="text-answer text-state-warn" data-mounts-empty>${esc(m.empty_words || '')}</div>`}
  </div>`;
}

export async function renderEgeriaMounts(host) {
  host.innerHTML = '<div class="text-caveat text-ink-muted">Reading the map…</div>';
  try {
    host.innerHTML = mountsHtml(await getEgeriaMounts());
  } catch (err) {
    host.innerHTML = `<div class="text-caveat text-state-warn">Could not read the map: ${esc(err.message)}</div>`;
  }
}
