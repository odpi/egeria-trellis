/* EA embedded in Trellis Studio (/?embed=studio, E1). Does nothing outside embed mode.
 *
 * - Compact layout: no sidebar, a short header (html.ea-embed, ea_looks.css).
 * - "Open in Studio" (owner decision B): saves the spec this conversation is eliciting as a pyegeria.authoring
 *   SpecDraft, as you, in your private draft zone (POST /api/studio/open-draft), then posts
 *   {type:'studio.openDraft', draft_ref} to Studio's exact origin. Only the reference crosses; Studio re-reads the
 *   draft from Egeria as you.
 * - "Ask EA about this draft": Studio posts {type:'ea.askAbout', draft_ref}; EA reads that draft as you
 *   (POST /api/studio/draft-context) and puts it in the question box for you to ask about.
 * Every listener checks event.source and event.origin; every post names Studio's exact origin (Auth.embed.post). */
(function () {
  'use strict';
  const A = window.Auth;
  if (!A || !A.embed || !A.embed.active) return;

  const activeSpecDraft = () => {
    try {
      const ctx = JSON.parse(sessionStorage.getItem('ea_ctx') || 'null') || {};
      if (ctx.task === 'report_spec_elicitor' && ctx.draft_id) return ctx.draft_id;
    } catch { /* storage blocked */ }
    return null;
  };

  function say(text, cls) {
    const el = document.getElementById('ea-embed-status');
    if (!el) return;
    el.textContent = text || '';
    el.className = `text-xs ${cls || 'text-slate-400'}`;
  }

  async function headingOf(draftId) {
    try {
      const r = await fetch(`/api/reports/drafts/${encodeURIComponent(draftId)}`, { headers: A.getHeaders() });
      if (!r.ok) return '';
      const d = await r.json();
      return (d.answers && d.answers.Heading) || '';
    } catch { return ''; }
  }

  async function openInStudio() {
    const draftId = activeSpecDraft();
    const name = document.getElementById('ea-embed-name');
    if (!draftId) { say('Start or open a report spec in this conversation first.', 'text-amber-300'); return; }
    const label = (name.value || '').trim();
    if (!label) { say('Give the spec a name for Studio.', 'text-amber-300'); name.focus(); return; }
    if (!A.embed.parentOrigin()) { say("Studio's session hasn't arrived, so EA can't hand the draft over.", 'text-amber-300'); return; }
    say('Saving to your private drafts in Egeria…');
    let r, body = {};
    try {
      r = await fetch('/api/studio/open-draft', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...A.getHeaders() },
        body: JSON.stringify({ draft_id: draftId, label }),
      });
      body = await r.json();
    } catch (e) {
      say(`Not saved: ${e.message}`, 'text-red-300'); return;
    }
    if (!r.ok) { say(`Not saved: ${body.detail || r.status}`, 'text-red-300'); return; }
    A.embed.post({ type: 'studio.openDraft', draft_ref: body.draft_ref });
    say(`Saved as ${body.qualified_name}; opening it in Studio.`, 'text-emerald-300');
  }

  async function askAbout(ref) {
    say('Reading your Studio draft as you…');
    let r, body = {};
    try {
      r = await fetch('/api/studio/draft-context', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...A.getHeaders() },
        body: JSON.stringify({ draft_ref: ref }),
      });
      body = await r.json();
    } catch (e) { say(`Couldn't read the draft: ${e.message}`, 'text-red-300'); return; }
    if (!r.ok) { say(`Couldn't read the draft: ${body.detail || r.status}`, 'text-red-300'); return; }
    const box = document.getElementById('query-input');
    if (box) {
      box.value = `About my Studio draft (${ref.kind} "${ref.name}"), read from Egeria as me:\n\n` +
                  '```markdown\n' + body.markdown + '\n```\n\nMy question: ';
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    }
    say(`Draft ${ref.name} is in the question box; add your question.`, 'text-emerald-300');
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent) return;
    const parent = A.embed.parentOrigin();
    if (!parent || event.origin !== parent) return;   // only the Studio whose session EA accepted
    const d = event.data || {};
    if (d.type === 'ea.askAbout' && d.draft_ref && typeof d.draft_ref.name === 'string') askAbout(d.draft_ref);
  });

  function mount() {
    const header = document.querySelector('header');
    if (!header || document.getElementById('ea-embed-bar')) return;
    const bar = document.createElement('div');
    bar.id = 'ea-embed-bar';
    bar.className = 'flex items-center gap-2 text-xs';
    bar.innerHTML =
      '<input id="ea-embed-name" placeholder="Spec name for Studio" class="bg-slate-700 text-slate-100 rounded px-2 py-0.5 border border-slate-600 w-48">' +
      '<button id="ea-embed-open" class="px-2 py-0.5 rounded bg-cyan-800 hover:bg-cyan-700 text-white">Open in Studio</button>' +
      '<span id="ea-embed-status" class="text-xs text-slate-400"></span>';
    header.appendChild(bar);
    document.getElementById('ea-embed-open').addEventListener('click', openInStudio);
    const refreshName = async () => {
      const id = activeSpecDraft();
      const name = document.getElementById('ea-embed-name');
      document.getElementById('ea-embed-open').disabled = !id;
      if (id && name && !name.value) name.value = await headingOf(id);
    };
    refreshName();
    setInterval(refreshName, 3000);   // the conversation sets the active draft as it goes
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
