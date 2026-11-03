/* The app's in-page confirm and failure pop-up.
 *
 * A native `window.confirm` is declined by embedded browsers (the press then does nothing, silently) and cannot
 * be styled or tested like the rest of the page. These two draw a modal in the page and answer through a promise:
 *
 *   inPageConfirm({ title, message, confirmLabel })  -> Promise<boolean>   (Cancel, Escape, or a click outside = false)
 *   inPageChoice({ title, message, choices })        -> Promise<value|null>   (a destructive action with more than one way to go)
 *   inPageNotice({ title, message })                 -> Promise<void>      (the failure pop-up: one Close button)
 *   inPagePrompt({ title, message, label, defaultValue, placeholder, confirmLabel, required })
 *                                                    -> Promise<string|null>   (a name or a reason; Cancel, Escape or a click outside = null)
 *
 * No import of app.js, so any admin pane or stage can use it and a test can load it alone. */

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function open({ title, message, buttons, attr }) {
  const doc = document;
  // One dialog of a kind at a time: an older one is answered "no" (never left hanging) before the new one opens.
  doc.querySelectorAll(`[${attr}]`).forEach((n) => (n.__done ? n.__done(false) : n.remove()));
  return new Promise((resolve) => {
    const box = doc.createElement('div');
    box.setAttribute(attr, '1');
    box.setAttribute('role', 'alertdialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', title);
    box.className = 'fixed inset-0 z-50 flex items-start justify-center overflow-auto bg-black/40 p-s4';
    box.innerHTML = `<div class="mt-[6vh] w-full max-w-[560px] rounded bg-paper p-s4 text-caveat text-ink shadow-lg">
      <div data-dialog-title class="text-answer text-ink">${esc(title)}</div>
      <div data-dialog-message class="mt-s1 text-ink" style="white-space:pre-line">${esc(message)}</div>
      <div class="mt-s3 flex flex-wrap gap-s3">${buttons.map((b) =>
    `<button type="button" ${b.attr} ${b.disabled ? 'disabled aria-disabled="true"' : ''} ${b.title ? `title="${esc(b.title)}"` : ''} class="${b.disabled ? 'cursor-default opacity-60' : 'cursor-pointer'} rounded-sm border bg-transparent px-2 py-[2px] text-caveat ${b.primary ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink'}">${esc(b.label)}</button>`).join('')}</div>
    </div>`;
    const done = (v) => { doc.removeEventListener('keydown', onKey); box.remove(); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') done(false); };
    box.addEventListener('click', (e) => { if (e.target === box) done(false); });   // a click outside the card
    box.__done = done;
    doc.addEventListener('keydown', onKey);
    buttons.forEach((b) => box.querySelector(`[${b.attr}]`).addEventListener('click', () => done(b.value)));
    doc.body.appendChild(box);
    box.querySelector(`[${buttons[0].attr}]`)?.focus?.();
  });
}

/** Ask. Resolves true only when the confirm button is pressed. */
export function inPageConfirm({ title = 'Are you sure?', message = '', confirmLabel = 'Confirm', cancelLabel = 'Cancel' } = {}) {
  return open({
    title, message, attr: 'data-confirm-dialog',
    buttons: [
      { attr: 'data-confirm-no', label: cancelLabel, value: false },
      { attr: 'data-confirm-yes', label: confirmLabel, value: true, primary: true },
    ],
  });
}

/** Ask which of several ways to go. Resolves the pressed choice's `value`, or null on Cancel, Escape or a click
 *  outside. `choices` is [{ value, label, disabled?, title? }]; each button carries `data-choice-<value>`, and a
 *  disabled one cannot be pressed (its `title` says why). */
export function inPageChoice({ title = 'Choose', message = '', choices = [], cancelLabel = 'Cancel' } = {}) {
  return open({
    title, message, attr: 'data-choice-dialog',
    buttons: [
      { attr: 'data-choice-cancel', label: cancelLabel, value: null },
      ...choices.map((c) => ({ attr: `data-choice-${c.value}`, label: c.label, value: c.value, primary: true,
        disabled: !!c.disabled, title: c.title || '' })),
    ],
  }).then((v) => (v === false || v === undefined ? null : v));
}

/** Tell: the failure pop-up. Resolves when closed. */
export function inPageNotice({ title = 'That did not finish', message = '' } = {}) {
  return open({
    title, message, attr: 'data-notice-dialog',
    buttons: [{ attr: 'data-notice-close', label: 'Close', value: true, primary: true }],
  }).then(() => undefined);
}

let promptSeq = 0;

/** Ask for a line of text. Resolves the typed string (untrimmed) on Enter or the confirm button, or null on
 *  Cancel, Escape or a click outside. With `required`, an empty (or blank) answer is refused: the dialog stays
 *  open and says so on the field. Focus lands in the input, with its text selected. */
export function inPagePrompt({
  title = 'Enter a value', message = '', label = '', defaultValue = '', placeholder = '',
  confirmLabel = 'OK', cancelLabel = 'Cancel', required = false,
} = {}) {
  const doc = document;
  const attr = 'data-prompt-dialog';
  doc.querySelectorAll(`[${attr}]`).forEach((n) => (n.__done ? n.__done(null) : n.remove()));
  return new Promise((resolve) => {
    const id = `prompt-input-${++promptSeq}`;
    const box = doc.createElement('div');
    box.setAttribute(attr, '1');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', title);
    box.className = 'fixed inset-0 z-50 flex items-start justify-center overflow-auto bg-black/40 p-s4';
    box.innerHTML = `<div class="mt-[6vh] w-full max-w-[560px] rounded bg-paper p-s4 text-caveat text-ink shadow-lg">
      <div data-dialog-title class="text-answer text-ink">${esc(title)}</div>
      ${message ? `<div data-dialog-message class="mt-s1 text-ink" style="white-space:pre-line">${esc(message)}</div>` : ''}
      <label data-prompt-label for="${id}" class="mt-s2 block text-ink">${esc(label || title)}</label>
      <input data-prompt-input id="${id}" type="text" autocomplete="off" value="${esc(defaultValue)}" placeholder="${esc(placeholder)}" ${required ? 'aria-required="true"' : ''} class="mt-s1 w-full rounded-sm border border-rule-strong bg-paper px-2 py-[2px] text-caveat text-ink">
      <div data-prompt-error role="alert" class="mt-s1 text-state-warn" hidden></div>
      <div class="mt-s3 flex flex-wrap gap-s3">
        <button type="button" data-prompt-no class="cursor-pointer rounded-sm border bg-transparent px-2 py-[2px] text-caveat border-rule-strong text-ink">${esc(cancelLabel)}</button>
        <button type="button" data-prompt-yes class="cursor-pointer rounded-sm border bg-transparent px-2 py-[2px] text-caveat border-accent text-accent-ink">${esc(confirmLabel)}</button>
      </div>
    </div>`;
    const input = box.querySelector('[data-prompt-input]');
    const err = box.querySelector('[data-prompt-error]');
    const done = (v) => { doc.removeEventListener('keydown', onKey); box.remove(); resolve(v); };
    const accept = () => {
      if (required && !input.value.trim()) {
        err.textContent = 'This needs a value.';
        err.hidden = false;
        input.setAttribute('aria-invalid', 'true');
        input.focus?.();
        return;
      }
      done(input.value);
    };
    const onKey = (e) => { if (e.key === 'Escape') done(null); };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); accept(); } });
    box.addEventListener('click', (e) => { if (e.target === box) done(null); });
    box.__done = done;
    doc.addEventListener('keydown', onKey);
    box.querySelector('[data-prompt-no]').addEventListener('click', () => done(null));
    box.querySelector('[data-prompt-yes]').addEventListener('click', accept);
    doc.body.appendChild(box);
    input.focus?.();
    input.select?.();
  });
}
