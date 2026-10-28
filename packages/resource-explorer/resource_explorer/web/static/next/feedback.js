// Floating "Feedback" button + modal for the /next UI.
//
// The product-feedback modal below is deliberately independent of app.js and
// re-api.js (a historical choice, from when another agent was editing app.js
// concurrently) — plain `fetch`, no shared session-id helper, no imported
// ApiError. Self-initialises on import: builds the button, appends it, and
// lazily builds the modal the first time it's opened.
//
// The PER-ANSWER feedback bar further down (the "Was this right?" control on
// Questions-checklist rows) is a different story: it now imports
// `feedbackVotesHtml` from app.js (2026-09-23 consolidation) so its buttons
// are the SAME icon-based markup chat.js's per-turn vote uses, rather than
// its own independent "Right/Partly/Wrong" text links — the two had been
// built against the identical agree/partly/disagree vocabulary, posting to
// the same endpoint, and never unified. See that section's own comment.
//
// Field names below are read from resource_explorer/web/routes/feedback.py's
// FeedbackSubmission model — verify there before changing any key.

import { feedbackVotesHtml } from '/static/next/app.js';
import { inPagePrompt } from '/static/next/confirm-dialog.js';

const CATEGORIES = [
  { value: '', label: 'Category (optional)' },
  { value: 'bug', label: 'Bug' },
  { value: 'confusing', label: 'Confusing' },
  { value: 'suggestion', label: 'Suggestion' },
  { value: 'praise', label: 'Praise' },
];

// Per-tab session id, namespaced separately from the old UI's `re_session_id`
// so the two shells never collide if someone has both open.
const SESSION_KEY = 're_next_session_id';
function _sessionId() {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id = 'next-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function _escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

let _rating = 0;
let _modalEl = null;

function _starsHtml() {
  let out = '';
  for (let i = 1; i <= 5; i++) {
    out += `<button type="button" class="fb-star cursor-pointer bg-transparent text-[1.1rem] leading-none px-[1px]" data-star="${i}" aria-label="${i} star${i === 1 ? '' : 's'}" aria-pressed="${i <= _rating}">${i <= _rating ? '●' : '○'}</button>`;
  }
  return out;
}

function _renderStars() {
  const wrap = _modalEl.querySelector('#fb-stars');
  if (wrap) wrap.innerHTML = _starsHtml();
}

function _buildModal() {
  const overlay = document.createElement('div');
  overlay.id = 'fb-modal-overlay';
  overlay.className = 'fixed inset-0 z-[90] flex items-center justify-center';
  overlay.style.background = 'rgba(27,26,25,0.55)';
  // `hidden` attribute loses to the `flex` utility (display:flex wins over
  // preflight's [hidden]); the `hidden` CLASS is ordered after `flex` and wins.
  overlay.classList.add('hidden');

  overlay.innerHTML = `
    <div id="fb-modal" role="dialog" aria-modal="true" aria-labelledby="fb-heading"
         class="w-[min(380px,92vw)] rounded-sm border border-chrome-line bg-paper p-s4 text-ink">
      <h2 id="fb-heading" class="mb-s3 font-heading text-caps tracking-caps uppercase text-ink">Feedback</h2>
      <div id="fb-body"></div>
    </div>
  `;

  document.body.appendChild(overlay);
  _modalEl = overlay;

  overlay.addEventListener('mousedown', (e) => {
    if (e.target === overlay) closeFeedbackModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _modalEl && !_modalEl.classList.contains('hidden')) closeFeedbackModal();
  });

  return overlay;
}

function _showError(msg) {
  const err = _modalEl.querySelector('#fb-error');
  if (!err) return;
  err.textContent = msg;
  err.hidden = false;
}

function openFeedbackModal() {
  if (!_modalEl) _buildModal();
  // Reset form state on every open — also undoes a previous submission's
  // "Thank you" message if the modal is reopened quickly.
  _rating = 0;
  _resetForm();
  _modalEl.classList.remove('hidden');
  const textarea = _modalEl.querySelector('#fb-message');
  if (textarea) textarea.focus();
}

function _resetForm() {
  if (!_modalEl) return;
  const body = _modalEl.querySelector('#fb-body');
  // Rebuild the body fresh in case a previous submission replaced it with
  // the "Thank you" message.
  body.innerHTML = `
    <div class="mb-s3">
      <div id="fb-stars" class="mb-[2px]">${_starsHtml()}</div>
    </div>
    <select id="fb-category" class="mb-s3 w-full rounded-sm border border-chrome-line bg-paper px-s2 py-[6px] text-resource text-ink">
      ${CATEGORIES.map(c => `<option value="${c.value}">${c.label}</option>`).join('')}
    </select>
    <textarea id="fb-message" rows="4" placeholder="What's on your mind?"
      class="mb-s3 w-full resize-y rounded-sm border border-chrome-line bg-paper px-s2 py-[6px] text-resource text-ink"></textarea>
    <input id="fb-email" type="email" placeholder="Email (optional)"
      class="mb-s3 w-full rounded-sm border border-chrome-line bg-paper px-s2 py-[6px] text-resource text-ink">
    <label class="mb-[6px] flex items-center gap-s1 text-chip text-ink-muted">
      <input type="checkbox" id="fb-wants-response"> I'd like a response
    </label>
    <label class="mb-s3 flex items-center gap-s1 text-chip text-ink-muted">
      <input type="checkbox" id="fb-consent"> OK to contact me about this feedback
    </label>
    <div id="fb-error" class="mb-s3 text-chip text-state-warn" hidden></div>
    <div class="flex justify-end gap-s2">
      <button id="fb-cancel" type="button" class="cursor-pointer rounded-sm border border-chrome-line bg-transparent px-s3 py-[6px] text-resource text-ink-muted">Cancel</button>
      <button id="fb-send" type="button" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[6px] text-resource text-accent-ink">Send</button>
    </div>
  `;
  body.querySelector('#fb-stars').addEventListener('click', (e) => {
    const btn = e.target.closest('.fb-star');
    if (!btn) return;
    const n = Number(btn.dataset.star);
    _rating = _rating === n ? 0 : n;
    _renderStars();
  });
  body.querySelector('#fb-cancel').addEventListener('click', closeFeedbackModal);
  body.querySelector('#fb-send').addEventListener('click', submitFeedback);
}

function closeFeedbackModal() {
  if (_modalEl) _modalEl.classList.add('hidden');
}

async function submitFeedback() {
  const body = _modalEl.querySelector('#fb-body');
  const message = body.querySelector('#fb-message').value.trim();
  const email = body.querySelector('#fb-email').value.trim();
  const category = body.querySelector('#fb-category').value;
  const wantsResponse = body.querySelector('#fb-wants-response').checked;
  const consent = body.querySelector('#fb-consent').checked;

  const err = body.querySelector('#fb-error');
  if (err) err.hidden = true;

  const payload = {
    session_id: _sessionId(),
    page: location.pathname + location.search,
    element_guid: '',
    rating: _rating > 0 ? _rating : null,
    category,
    message,
    email,
    wants_response: wantsResponse,
    consent_to_contact: consent,
    build_version: '',
    user_agent: navigator.userAgent,
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    locale: navigator.language || '',
  };

  try {
    const res = await fetch('/api/feedback', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (res.status === 401) {
      _showError('Sign in to send feedback');
      return;
    }
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const data = await res.json();
        if (data && data.detail) detail = data.detail;
      } catch {
        // body wasn't JSON — keep the plain status message
      }
      _showError(detail);
      return;
    }

    // Success — replace the form body with a single confirmation line, then
    // auto-close so the modal doesn't linger.
    body.innerHTML = `<p class="text-resource text-ink">Thank you — recorded.</p>`;
    setTimeout(closeFeedbackModal, 1500);
  } catch (e) {
    _showError(_escapeHtml(e && e.message ? e.message : 'Could not reach the server'));
  }
}

function _buildButton() {
  const btn = document.createElement('button');
  btn.id = 'next-feedback-btn';
  btn.type = 'button';
  btn.textContent = 'Feedback';
  btn.className =
    'fixed z-40 cursor-pointer rounded-sm border border-accent bg-chrome px-s3 py-[6px] ' +
    'text-chip text-accent-on-dark';
  btn.style.bottom = '16px';
  btn.style.right = '16px';
  btn.addEventListener('click', openFeedbackModal);
  document.body.appendChild(btn);
}

_buildButton();

/* ════════════════════════════════════════════════════════════════════════
 * Per-answer feedback
 *
 * The button above is about the product. This is about one ANSWER, and it
 * lives here rather than in app.js for two reasons: app.js is being split
 * into per-stage modules by another stream, and this control has to survive
 * that split unchanged; and the whole question-row surface is rebuilt with
 * innerHTML as each answer lands, so anything attached to a row has to be
 * re-attached rather than bound once.
 *
 * Hence: a MutationObserver on the rows container, and delegated clicks on
 * document. Nothing here imports app.js and app.js does not import this.
 *
 * WHICH rows get the control: only those carrying an `evidence` button.
 * That is app.js's own marker for `answered | automatic` (provenanceLine) —
 * the two states where there IS an answer to agree or disagree with.
 * Offering "was this right?" under "never run" would be asking about a
 * sentence that makes no claim.
 * ════════════════════════════════════════════════════════════════════════ */

/** vote value (chat.js's own vocabulary — see its own VOTE_VERDICT) -> the
 *  verdict `/api/feedback/answer` takes (feedback.py's `VALID_VERDICTS`).
 *  Both surfaces record all three; only `disagree` raises a gap. */
const VOTE_VERDICT = { 1: 'agree', 0: 'partly', '-1': 'disagree' };

/** The bar's original content — "Was this right?" plus the three shared
 *  icon buttons (app.js's `feedbackVotesHtml`, 'paper' theme for this light
 *  question row). Pulled out on its own so a cancelled "Wrong" comment
 *  prompt (see `_sendAnswerVerdict` below) can rebuild exactly this rather
 *  than stranding the bar in whatever state was left when `window.prompt`
 *  was dismissed. */
function _barButtonsHtml() {
  return `<span>Was this right?</span>${feedbackVotesHtml({ theme: 'paper' })}`;
}

function _rowQuestion(row) {
  const el = row.querySelector('span.font-heading.text-question');
  return el ? el.textContent.trim() : '';
}

function _currentSlug() {
  const el = document.getElementById('scope-slug');
  // `data-slug` first: #scope-slug is the resource's menu button and its text
  // reads "<slug> ▾", so the text is no longer the bare slug.
  const s = el ? (el.dataset.slug !== undefined ? el.dataset.slug : el.textContent).trim() : '';
  return s && s !== 'no resource selected' ? s : '';
}

/** `#scope-slug`'s `data-entity-type`, set by app.js's `renderTopBar()`
 *  alongside the slug it already reads from that same element — a plain DOM
 *  read, not an app.js import, matching this module's stated independence
 *  above. No default: a missing attribute throws rather than filing the vote against 'repo'. */
function _currentEntityType() {
  const el = document.getElementById('scope-slug');
  const kind = el && el.dataset.entityType;
  if (!kind) throw new Error('feedback: entityType is required (no data-entity-type on #scope-slug)');
  return kind;
}

function _attachTo(row) {
  if (!row) return;
  // Idempotence is keyed on the BAR, not on a flag on the row. app.js
  // replaces a row's innerHTML in place when its answer lands
  // (`replaceRow`), which destroys the bar while leaving the row element —
  // and its dataset — intact. A flag on the row would survive that and stop
  // the bar ever coming back.
  if (row.querySelector('[data-fb-answer]')) return;
  // `evidence` is app.js's marker for a row that actually answered.
  if (!row.querySelector('[data-evidence]')) return;
  const question = _rowQuestion(row);
  if (!question) return;

  const bar = document.createElement('div');
  bar.className = 'ml-[22px] mt-[6px] flex flex-wrap items-center gap-s2 text-provenance text-ink-muted';
  bar.dataset.fbAnswer = question;
  bar.innerHTML = _barButtonsHtml();
  row.appendChild(bar);
}

function _scanRows() {
  const rows = document.getElementById('question-rows');
  if (!rows) return;
  rows.querySelectorAll('[id^="qrow-"]').forEach(_attachTo);
}

/** Replace the bar with a sentence. Every outcome says what was recorded —
 *  a control that silently accepts a "this is wrong" and shows nothing is
 *  the same failure as a vote that does not record. */
function _said(bar, text, warn) {
  bar.innerHTML = `<span class="${warn ? 'text-state-warn' : ''}">${_escapeHtml(text)}</span>`;
}

/** Like `_said`, but puts the three vote buttons back afterward instead of
 *  leaving the bar a dead end — used when nothing was recorded and the
 *  person should be able to try again immediately (the cancelled-comment-
 *  prompt path below), as opposed to `_said`'s terminal states (recorded,
 *  or failed against the server) where there is nothing left to retry. */
function _saidAndReset(bar, text) {
  bar.innerHTML = `<div class="mb-[2px] text-state-warn">${_escapeHtml(text)}</div>${_barButtonsHtml()}`;
}

async function _sendAnswerVerdict(bar, vote) {
  const verdict = VOTE_VERDICT[String(vote)];
  const question = bar.dataset.fbAnswer || '';
  const slug = _currentSlug();
  if (!slug) { _said(bar, 'No resource selected — not recorded.', true); return; }

  // Only a disagreement asks for words. Making everyone type turns a
  // one-click signal into a form nobody fills in; asking the person who says
  // "wrong" what they know is the one case where the words are the point.
  let comment = '';
  if (verdict === 'disagree') {
    const typed = await inPagePrompt({ title: 'What is wrong?', message: 'What is wrong with this answer? (optional)', label: 'What is wrong', confirmLabel: 'Record' });
    if (typed === null) {
      // Cancelled. This function's own design principle (see `_said`'s doc
      // comment above) is that every path through it ends in a visible,
      // honest statement of what happened — a bare `return` here left the
      // bar showing whatever the native prompt left behind, which reads
      // exactly like the "Wrong" click did nothing. Say plainly that
      // nothing was recorded, and put the buttons back so the person is not
      // stranded with no way to record a verdict.
      _saidAndReset(bar, 'Cancelled — not recorded. Click "Wrong" again to record it without a comment.');
      return;
    }
    comment = typed.trim();
  }

  _said(bar, 'Recording…');
  try {
    const res = await fetch('/api/feedback/answer', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        slug, question, verdict, comment,
        session_id: _sessionId(),
        page: location.pathname + location.search,
        entity_type: _currentEntityType(),
      }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const d = await res.json(); if (d && d.detail) detail = d.detail; } catch { /* not JSON */ }
      _said(bar, `Not recorded: ${detail}`, true);
      return;
    }
    const data = await res.json();
    // The server's own sentence, not one composed here — it is the only side
    // that knows whether a gap was raised and against which analysis.
    _said(bar, data.gap
      ? `Recorded — ${data.gap_reason}. It is now in this project's gaps, marked ${data.gap.destination}.`
      : `Recorded — ${data.gap_reason}.`);
  } catch (e) {
    _said(bar, `Not recorded: ${e && e.message ? e.message : 'could not reach the server'}`, true);
  }
}

document.addEventListener('click', (e) => {
  // `data-vote` (not the old `data-fb-verdict`) since the buttons are now
  // the shared `feedbackVotesHtml()` markup chat.js's per-turn vote also
  // uses — `closest('[data-fb-answer]')` is what still scopes this listener
  // to feedback.js's own bars and leaves chat.js's own [data-vote] buttons
  // (which carry no [data-fb-answer] ancestor) to chat.js's own listener.
  const btn = e.target.closest('[data-vote]');
  if (!btn) return;
  const bar = btn.closest('[data-fb-answer]');
  if (!bar) return;
  _sendAnswerVerdict(bar, Number(btn.dataset.vote));
});

function _watchRows() {
  const rows = document.getElementById('question-rows');
  if (!rows) {
    // The container is in index.html, but this module can load before the
    // element exists in some orders; retry on the next frame rather than
    // binding to nothing and failing silently.
    requestAnimationFrame(_watchRows);
    return;
  }
  _scanRows();
  new MutationObserver(_scanRows).observe(rows, { childList: true, subtree: true });
}

_watchRows();
