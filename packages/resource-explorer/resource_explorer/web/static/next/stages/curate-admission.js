/* Whose boundary is a node? (DESIGN-BLUEPRINT-NODE-ADMISSION.md)
 *
 * A node enters a repository's blueprint by the evidence class of its boundary, and the class is shown on
 * the node: "built here · from <file>" or "shipped here · image <name>". A service that runs an image this
 * repository neither builds nor publishes is not a node at all: it is a runtime dependency (Dependencies ·
 * runtime). A person may move a node across that line, with a reason, which the server records.
 *
 * State is a glyph plus a short word, never a sentence the eye has to find; the longer evidence is on
 * demand (the title). The reclassify control answers a press at once: the button becomes a reason box.
 * This file draws; the class, the evidence and the lines come from the server, never from a click.
 *
 * Self-wiring: one delegated listener, so the Curate pane only has to put the HTML in.
 */
import { postNodeReclassify } from '/static/re-api.js';
import { state, esc } from '/static/next/app.js';

const CLASS_CUE = {
  built_here: { glyph: '◼', word: 'built here' },
  shipped_here: { glyph: '▲', word: 'shipped here' },
};

/** The class on a node: cue + short word, the evidence on demand, a reclassification with its reason. */
export function admissionHtml(l) {
  wireAdmission();
  const cue = CLASS_CUE[l.admission] || CLASS_CUE.built_here;
  const why = l.admission_evidence || '';
  const moved = l.reclassified
    ? `<span class="text-ink">· moved by ${esc(l.reclassified.by)}: ${esc(l.reclassified.reason)}</span>` : '';
  const note = l.reclassification_note ? `<span class="text-state-warn">· ⚠ ${esc(l.reclassification_note)}</span>` : '';
  return `<span data-admission="${esc(l.admission || 'built_here')}" class="text-ink-muted" title="${esc(why)}">
      <span class="font-glyph" aria-hidden="true">${cue.glyph}</span> ${cue.word}${why ? ` · ${esc(why)}` : ''}</span>${moved}${note}
    <span data-admission-box="${esc(l.path)}"><button type="button" data-admission-open="${esc(l.path)}"
      class="cursor-pointer bg-transparent p-0 text-ink-muted underline">only referenced</button></span>`;
}

/** What was found and not admitted, and the zero-component sentence, under the blueprint selector. */
export function admissionNoteHtml(a) {
  if (!a || (!a.sentence && !(a.left_out || []).length)) return '';
  return `<div data-admission-note class="mb-s2 text-provenance text-ink-muted">
    ${a.sentence ? `<div data-admission-sentence class="text-ink">${esc(a.sentence)}</div>` : ''}
    ${(a.left_out || []).map((x) => `<div data-admission-left-out>left out · ${esc(x)}</div>`).join('')}
  </div>`;
}

function openBox(box) {
  const scope = box.dataset.admissionBox;
  box.innerHTML = `<input type="text" data-admission-reason aria-label="reason" placeholder="why is it only referenced?"
      class="rounded-sm border border-rule-strong bg-transparent px-2 py-[1px] text-ink">
    <button type="button" data-admission-submit="${esc(scope)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">move</button>
    <button type="button" data-admission-cancel="${esc(scope)}" class="cursor-pointer bg-transparent p-0 text-ink-muted underline">cancel</button>`;
  box.querySelector('[data-admission-reason]').focus();
}

async function submitBox(box, button) {
  const scope = box.dataset.admissionBox;
  const reason = box.querySelector('[data-admission-reason]').value.trim();
  if (!reason) {
    box.querySelector('[data-admission-reason]').placeholder = 'a reason is needed';
    box.querySelector('[data-admission-reason]').focus();
    return;
  }
  button.disabled = true;
  button.textContent = 'moving…';
  try {
    await postNodeReclassify(state.selectedSlug, scope, 'referenced_only', reason);
    box.innerHTML = `<span data-admission-moved class="text-ink">● moved to Dependencies · runtime</span>`;
  } catch (err) {
    box.innerHTML = `<span class="text-accent-ink">could not move: ${esc(err.message)}</span>`;
  }
}

const wired = new WeakSet();
export function wireAdmission(root = globalThis.document) {
  if (!root || wired.has(root)) return;
  wired.add(root);
  root.addEventListener('click', (ev) => {
    const t = ev.target.closest?.('[data-admission-open],[data-admission-submit],[data-admission-cancel]');
    if (!t) return;
    const box = t.closest('[data-admission-box]');
    if (!box) return;
    if ('admissionOpen' in t.dataset) openBox(box);
    else if ('admissionCancel' in t.dataset) {
      box.innerHTML = `<button type="button" data-admission-open="${esc(box.dataset.admissionBox)}"
        class="cursor-pointer bg-transparent p-0 text-ink-muted underline">only referenced</button>`;
    } else submitBox(box, t);
  });
}
wireAdmission();
