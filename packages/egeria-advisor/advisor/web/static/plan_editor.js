// ── Plan Editor ────────────────────────────────────────────────────────────────
// Inline editor for Literate Governance plan documents.
// Parses the plan markdown into a structured form (narrative textarea + per-command
// field cards with inter-command notes), synthesises back to markdown on save,
// and exposes Validate / Execute.

'use strict';

// ── State ──────────────────────────────────────────────────────────────────────
let _ped = {
  doc_id:        null,
  draft_id:      null,         // active planning session draft, if any
  isInbox:       true,
  mode:          'basic',      // 'basic' | 'advanced'
  narrative:     '',           // everything before ## Command Sequence
  commands:      [],           // [{stepNum, action, rationale, fields, postNotes}]
  outcome:       '',           // ## Outcome section (read-only)
  templateCache: {},           // "action:level" → [{name,required,type,...}]
  dirty:         false,
};

// ── Public entry points ────────────────────────────────────────────────────────

async function openPlanEditor(doc_id, draft_id) {
  let data;
  try {
    const r = await fetch(`/api/plans/${encodeURIComponent(doc_id)}`, { headers: Auth.getHeaders() });
    if (!r.ok) { alert(`Could not load plan ${doc_id}`); return; }
    data = await r.json();
  } catch (e) { alert(`Error loading plan: ${e.message}`); return; }

  _ped.doc_id   = doc_id;
  _ped.draft_id = draft_id || (typeof _activeDraftId !== 'undefined' ? _activeDraftId : null);
  _ped.isInbox  = (data.folder === 'inbox');
  _ped.dirty    = false;

  const parsed    = _parsePlanMarkdown(data.content);
  _ped.narrative  = parsed.narrative;
  _ped.commands   = parsed.commands;
  _ped.outcome    = parsed.outcome;

  _renderEditor();
  _updateDiscussButton();
  document.getElementById('plan-editor-overlay').classList.remove('hidden');
  document.body.style.overflow = 'hidden';

  // Load template field metadata in background — enriches required/optional and re-renders
  _loadAllTemplateFields();
}

function _updateDiscussButton() {
  const btn = document.getElementById('ped-discuss-btn');
  if (!btn) return;
  const hasDraft = !!_ped.draft_id;
  btn.classList.toggle('hidden', !hasDraft);
}

function closePlanEditor() {
  if (_ped.dirty && !confirm('You have unsaved changes. Close anyway?')) return;
  document.getElementById('plan-editor-overlay').classList.add('hidden');
  document.body.style.overflow = '';
  _ped.doc_id   = null;
  _ped.draft_id = null;
}

// ── Markdown parsing ───────────────────────────────────────────────────────────

function _parsePlanMarkdown(md) {
  const cmdSeqRe    = /^##\s+Command Sequence\s*$/m;
  const cmdSeqMatch = cmdSeqRe.exec(md);

  let narrative = md;
  let cmdBody   = '';
  let outcome   = '';

  if (cmdSeqMatch) {
    // Strip the "---" separator(s) the composer places right before "##
    // Command Sequence" — _synthesizePlanMarkdownFrom() re-adds its own, so
    // leaving one here would duplicate it (and duplicate again on every
    // subsequent parse→save round-trip). Loops in case a document already
    // accumulated more than one from before this fix.
    narrative = md.slice(0, cmdSeqMatch.index).trimEnd();
    while (/\n-{3,}$/.test(narrative)) narrative = narrative.replace(/\n-{3,}$/, '').trimEnd();
    let rest  = md.slice(cmdSeqMatch.index + cmdSeqMatch[0].length).trimStart();

    const outcomeRe = /^##\s+Outcome\s*$/m;
    const outMatch  = outcomeRe.exec(rest);
    if (outMatch) {
      outcome = rest.slice(outMatch.index).trimStart();
      rest    = rest.slice(0, outMatch.index).trimEnd();
    }
    cmdBody = rest;
  }

  const commands = [];
  const blocks   = cmdBody.split(/(?=^<!--\s*(?:Step\s+\d+|Note)\b)/m).filter(b => b.trim());

  for (const block of blocks) {
    // Freestanding note block — see _synthesizePlanMarkdownFrom's "_note" case.
    const noteRe = /^<!--\s*Note\s*-->\s*\n?([\s\S]*)$/;
    const nm     = noteRe.exec(block);
    if (nm) {
      const noteText = nm[1].trim();
      commands.push({
        stepNum: 0, action: '_note', rationale: '',
        fields: [{ name: 'Display Name', value: noteText, required: false, type: 'Simple', validValues: [] }],
        postNotes: '',
      });
      continue;
    }

    const commentRe = /^<!--\s*Step\s+(\d+):\s*([\s\S]*?)-->/;
    const cm        = commentRe.exec(block);
    if (!cm) continue;

    const stepNum     = parseInt(cm[1], 10);
    const cLines      = cm[2].trim().split('\n').map(l => l.trim().replace(/^\s+/, '')).filter(Boolean);
    const action      = cLines[0] || '';
    const rationale   = cLines.slice(1).join('\n').trim();

    // Parse ### FieldName / value sections
    const fields = [];
    const afterComment = block.slice(cm.index + cm[0].length);
    const afterHeading = afterComment.replace(/^\s*##[^\n]*\n/, '');

    // Capture the section before any postNotes (text after the closing ---)
    // Each command block ends with --- (horizontal rule). Anything after is postNotes.
    const hrIdx    = afterHeading.search(/\n---\s*(\n|$)/);
    const fieldsBody = hrIdx !== -1 ? afterHeading.slice(0, hrIdx) : afterHeading;
    const postNotes  = hrIdx !== -1 ? afterHeading.slice(hrIdx).replace(/^\n---\s*\n?/, '').trim() : '';

    const fieldParts = fieldsBody.split(/(?=^###\s)/m);
    for (const fp of fieldParts) {
      const fm = /^###\s+([^\n]+)\n([\s\S]*?)$/.exec(fp.trim());
      if (!fm) continue;
      const name  = fm[1].trim();
      const raw   = fm[2].replace(/\n?---\s*$/, '').trim();
      const value = /<!--\s*TODO/i.test(raw) ? '' : raw;
      fields.push({ name, value, required: false, type: 'Simple', validValues: [] });
    }

    commands.push({ stepNum, action, rationale, fields, postNotes });
  }

  return { narrative, commands, outcome };
}

// ── Markdown synthesis ─────────────────────────────────────────────────────────
// _synthesizePlanMarkdownFrom is pure (no _ped/DOM access) so PlanCanvas can
// reuse it to write the same document format once a plan has been generated,
// instead of maintaining a second markdown serializer. See BACKLOG.md.

function _synthesizePlanMarkdownFrom(narrative, commands, mode, outcome) {
  let md = narrative + '\n\n---\n\n## Command Sequence\n\n';

  for (const cmd of commands) {
    // A freestanding note — no template/header, just the text, tagged with a
    // dedicated marker so _parsePlanMarkdown can read it back as its own
    // reorderable entry rather than folding it into the preceding command's
    // postNotes (plain inter-command text alone is ambiguous on reparse).
    if (cmd.action === '_note') {
      const noteText = (cmd.fields[0] && cmd.fields[0].value || '').trim();
      md += `<!-- Note -->\n${noteText}\n\n`;
      continue;
    }

    const commentLines = [cmd.action];
    if (cmd.rationale) commentLines.push('     ' + cmd.rationale);
    md += `<!-- Step ${cmd.stepNum}: ${commentLines.join('\n')} -->\n`;
    md += `## ${cmd.action}\n\n`;

    for (const f of cmd.fields) {
      // In basic mode, skip empty optional fields to keep the document clean
      if (mode === 'basic' && !f.required && !(f.value || '').trim()) continue;
      const val = (f.value || '').trim() || '<!-- TODO: fill in -->';
      md += `### ${f.name}\n${val}\n\n`;
    }
    md += '---\n\n';

    // Inter-command narrative (postNotes) — free text between commands
    const notes = (cmd.postNotes || '').trim();
    if (notes) md += notes + '\n\n';
  }

  if (outcome) md += '\n' + outcome;
  return md;
}

function _synthesizePlanMarkdown() {
  const narrativeEl = document.getElementById('ped-narrative');
  const narrative   = narrativeEl ? narrativeEl.value : _ped.narrative;
  return _synthesizePlanMarkdownFrom(narrative, _ped.commands, _ped.mode, _ped.outcome);
}

// ── Template field loading ─────────────────────────────────────────────────────

async function _loadAllTemplateFields() {
  const actions = [...new Set(_ped.commands.map(c => c.action).filter(a => a !== '_note'))];
  await Promise.all(actions.map(a => _fetchTemplateFields(a, _ped.mode)));
  _enrichFieldMetadata();
  _renderCommandCards();
}

async function _fetchTemplateFields(action, level = 'basic') {
  const cacheKey = `${action}:${level}`;
  if (cacheKey in _ped.templateCache) return _ped.templateCache[cacheKey];

  try {
    const url = `/api/templates/${encodeURIComponent(action)}/fields?level=${encodeURIComponent(level)}`;
    const r   = await fetch(url, { headers: Auth.getHeaders() });
    if (r.ok) {
      const data = await r.json();
      _ped.templateCache[cacheKey] = data.fields || [];
    } else {
      _ped.templateCache[cacheKey] = [];
    }
  } catch {
    _ped.templateCache[cacheKey] = [];
  }
  return _ped.templateCache[cacheKey];
}

function _enrichFieldMetadata() {
  for (const cmd of _ped.commands) {
    const cacheKey = `${cmd.action}:${_ped.mode}`;
    const tmpl     = _ped.templateCache[cacheKey] || _ped.templateCache[`${cmd.action}:basic`] || [];
    const byName   = Object.fromEntries(tmpl.map(f => [f.name, f]));

    // Update type/required metadata on existing fields
    for (const f of cmd.fields) {
      const td = byName[f.name];
      if (td) { f.required = td.required; f.type = td.type; f.validValues = td.valid_values || []; f.description = td.description || ''; }
    }

    // In advanced mode: add ALL template fields not already present
    // In basic mode: add only required fields not already present
    const presentNames = new Set(cmd.fields.map(f => f.name));
    for (const td of tmpl) {
      const shouldAdd = td.required || _ped.mode === 'advanced';
      if (shouldAdd && !presentNames.has(td.name)) {
        const field = { name: td.name, value: td.default_value || '', required: td.required, type: td.type, validValues: td.valid_values || [], description: td.description || '' };
        td.required ? cmd.fields.unshift(field) : cmd.fields.push(field);
        presentNames.add(td.name);
      }
    }
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function _renderEditor() {
  const overlay = document.getElementById('plan-editor-overlay');

  // Title
  const titleMatch = _ped.narrative.match(/^#\s+(.+)/m);
  const title = titleMatch ? titleMatch[1].replace('Data Management Plan: ', '') : (_ped.doc_id || '');
  overlay.querySelector('#ped-title').textContent = title;

  // Wire toolbar buttons
  overlay.querySelector('#ped-save-btn').onclick     = _savePlanEdits;
  overlay.querySelector('#ped-validate-btn').onclick = _validatePlanDoc;
  overlay.querySelector('#ped-execute-btn').onclick  = _executePlanDoc;

  const modeBtn = overlay.querySelector('#ped-mode-btn');
  if (modeBtn) {
    modeBtn.textContent = _ped.mode === 'basic' ? 'Basic' : 'Advanced';
    modeBtn.onclick     = _toggleMode;
  }

  // Toolbar state depends on whether the plan is in inbox (editable) or outbox (read-only)
  const editable = _ped.isInbox;
  overlay.querySelector('#ped-save-btn').disabled     = !editable;
  overlay.querySelector('#ped-validate-btn').disabled = false;   // validate works on inbox + outbox
  overlay.querySelector('#ped-validate-btn').title    = 'Validate commands against Egeria';
  const execBtn = overlay.querySelector('#ped-execute-btn');
  execBtn.textContent = editable ? '▶ Execute' : '▶ Execute';
  execBtn.disabled    = !editable;
  execBtn.onclick     = _executePlanDoc;
  // Reset button colour (may have been changed in a previous outbox load)
  execBtn.className   = execBtn.className
    .replace('bg-amber-700 hover:bg-amber-600', 'bg-violet-700 hover:bg-violet-600');

  // For outbox plans, show/hide the recovery toolbar
  const recoveryBar = overlay.querySelector('#ped-recovery-bar');
  if (recoveryBar) recoveryBar.classList.toggle('hidden', editable);

  // Narrative textarea
  const narrativeEl = overlay.querySelector('#ped-narrative');
  narrativeEl.value    = _ped.narrative;
  narrativeEl.readOnly = !editable;
  narrativeEl.oninput  = () => { _ped.dirty = true; _updateStatusBar(); };

  // Outcome section (read-only)
  const outcomeEl = overlay.querySelector('#ped-outcome');
  if (_ped.outcome) {
    outcomeEl.classList.remove('hidden');
    const outcomeBody = outcomeEl.querySelector('.ped-outcome-body');
    outcomeBody.innerHTML = renderMarkdown(_ped.outcome);  // render.js: parsed, then sanitised
    // Activate Mermaid diagrams in the outcome (e.g. from View Report)
    outcomeBody.querySelectorAll('code.language-mermaid').forEach(el => {
      const c = document.createElement('div');
      c.className = 'mermaid my-2';
      c.textContent = el.textContent;
      el.parentElement.replaceWith(c);
    });
    if (typeof mermaid !== 'undefined') mermaid.run({ nodes: outcomeBody.querySelectorAll('.mermaid') });
    // Re-run Mermaid when a <details> section is expanded (e.g. Dr.Egeria Execution Output)
    outcomeBody.querySelectorAll('details').forEach(det => {
      det.addEventListener('toggle', () => {
        if (det.open && typeof mermaid !== 'undefined') {
          det.querySelectorAll('code.language-mermaid').forEach(el => {
            if (!el.parentElement.classList.contains('mermaid-done')) {
              const c = document.createElement('div');
              c.className = 'mermaid my-2 mermaid-done';
              c.textContent = el.textContent;
              el.parentElement.replaceWith(c);
            }
          });
          mermaid.run({ nodes: det.querySelectorAll('.mermaid') });
        }
      });
    });
    // Offer a standalone report download when the outcome contains extracted
    // report content (Mermaid diagram / result table from a View Report command)
    if (!editable && /^###\s+Execution Results/m.test(_ped.outcome)) {
      const h3 = Array.from(outcomeBody.querySelectorAll('h3'))
        .find(el => el.textContent.trim() === 'Execution Results');
      if (h3 && !h3.querySelector('.ped-export-report-link')) {
        const link = document.createElement('a');
        link.className = 'ped-export-report-link ml-2 text-xs font-normal text-violet-400 hover:text-violet-200 transition-colors cursor-pointer';
        link.textContent = '📄 Export Report';
        link.href = `/api/plans/${encodeURIComponent(_ped.doc_id)}/report-export`;
        link.target = '_blank';
        h3.appendChild(link);
      }
    }
  } else {
    outcomeEl.classList.add('hidden');
  }

  _renderCommandCards();
  _updateStatusBar();
}

function _renderCommandCards() {
  const container = document.getElementById('ped-commands');
  container.innerHTML = '';
  _ped.commands.forEach((cmd, idx) => container.appendChild(_buildCommandCard(cmd, idx)));
}

// ── Add step ─────────────────────────────────────────────────────────────────
// Reuses the same Dr.Egeria command picker modal as the docked Plan Canvas
// (openCmdPicker, defined in index.html) — the full-screen editor previously
// had no way to add a new command at all, only reorder/edit existing ones.
async function _pedAddStep() {
  if (typeof openCmdPicker !== 'function') {
    alert('Command picker is not available.');
    return;
  }
  openCmdPicker(async (typeName) => {
    if (!typeName || !typeName.trim()) return;
    _ped.commands.push({ stepNum: 0, action: typeName, rationale: '', fields: [], postNotes: '' });
    _renumberCommands();
    _ped.dirty = true;
    await _loadAllTemplateFields();  // fetches template fields for the new action, enriches, re-renders
    _updateStatusBar();
  });
}

// ── Add note ─────────────────────────────────────────────────────────────────
// A freestanding, reorderable/removable note in the command list — matches
// the docked Plan Canvas's "+ Add note" (artifact_canvas.js), which the
// full-screen editor lacked entirely. Serialized via a dedicated "<!-- Note -->"
// marker in _synthesizePlanMarkdownFrom/_parsePlanMarkdown (plain inter-command
// narrative alone can't round-trip as an independent, reorderable entry — it's
// always attributed as the *preceding* command's postNotes on reparse).
function _pedAddNote() {
  _ped.commands.push({
    stepNum: 0, action: '_note', rationale: '',
    fields: [{ name: 'Display Name', value: '', required: false, type: 'Simple', validValues: [] }],
    postNotes: '',
  });
  _renumberCommands();
  _ped.dirty = true;
  _renderCommandCards();
  _updateStatusBar();
}

// ── Remove step ──────────────────────────────────────────────────────────────
function _pedRemoveStep(idx) {
  if (idx < 0 || idx >= _ped.commands.length) return;
  _ped.commands.splice(idx, 1);
  _renumberCommands();
  _ped.dirty = true;
  _renderCommandCards();
  _updateStatusBar();
}

// ── Drag-and-drop reorder ────────────────────────────────────────────────────
let _pedDragSrcIdx = null;

function _renumberCommands() {
  _ped.commands.forEach((cmd, i) => { cmd.stepNum = i + 1; });
}

function _attachCommandDragHandlers(card, idx) {
  card.addEventListener('dragstart', e => {
    _pedDragSrcIdx = idx;
    card.classList.add('opacity-40');
    e.dataTransfer.effectAllowed = 'move';
  });
  card.addEventListener('dragend', () => {
    card.classList.remove('opacity-40');
    document.querySelectorAll('.ped-cmd-card').forEach(c => c.classList.remove('ped-dragging-over'));
  });
  card.addEventListener('dragover', e => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.ped-cmd-card').forEach(c => c.classList.remove('ped-dragging-over'));
    if (_pedDragSrcIdx !== idx) card.classList.add('ped-dragging-over');
  });
  card.addEventListener('dragleave', () => card.classList.remove('ped-dragging-over'));
  card.addEventListener('drop', e => {
    e.preventDefault();
    card.classList.remove('ped-dragging-over');
    if (_pedDragSrcIdx === null || _pedDragSrcIdx === idx) return;
    const [moved] = _ped.commands.splice(_pedDragSrcIdx, 1);
    _ped.commands.splice(idx, 0, moved);
    _pedDragSrcIdx = null;
    _renumberCommands();
    _ped.dirty = true;
    _renderCommandCards();
    _updateStatusBar();
  });
}

function _buildCommandCard(cmd, idx) {
  // ── Note card — free-form text, no template/fields ──────────────────
  if (cmd.action === '_note') {
    const noteCard = document.createElement('div');
    noteCard.className = 'ped-cmd-card bg-amber-950/10 rounded-lg border border-amber-800/30 overflow-hidden';
    noteCard.dataset.idx = idx;
    const noteHdr = document.createElement('div');
    noteHdr.className = 'flex items-center gap-2 px-4 py-2 select-none';
    noteHdr.innerHTML =
      (_ped.isInbox ? `<span class="text-slate-500 cursor-grab shrink-0" title="Drag to reorder">⠿</span>` : '') +
      `<span class="text-amber-400/80 text-xs font-semibold flex-1">📝 Note</span>`;
    if (_ped.isInbox) {
      const rmBtn = document.createElement('button');
      rmBtn.className = 'text-slate-500 hover:text-red-400 text-xs px-1 transition-colors shrink-0';
      rmBtn.title = 'Remove this note';
      rmBtn.textContent = '✕';
      rmBtn.onclick = (e) => { e.stopPropagation(); _pedRemoveStep(idx); };
      noteHdr.appendChild(rmBtn);
    }
    noteCard.appendChild(noteHdr);
    if (_ped.isInbox) {
      noteCard.draggable = true;
      _attachCommandDragHandlers(noteCard, idx);
    }
    const noteBody = document.createElement('div');
    noteBody.className = 'px-4 pb-3';
    const ta = document.createElement('textarea');
    ta.className = 'w-full bg-amber-950/20 text-slate-300 text-sm rounded p-2 resize-none border ' +
                    'border-amber-800/30 focus:outline-none focus:border-amber-500/50 transition-colors';
    ta.rows = 3;
    ta.placeholder = 'Section heading, context, or explanatory text for the plan document…';
    ta.value = (cmd.fields[0] && cmd.fields[0].value) || '';
    ta.oninput = () => {
      if (!cmd.fields[0]) cmd.fields = [{ name: 'Display Name', value: '', required: false, type: 'Simple', validValues: [] }];
      cmd.fields[0].value = ta.value;
      _ped.dirty = true;
      _updateStatusBar();
    };
    noteBody.appendChild(ta);
    noteCard.appendChild(noteBody);
    return noteCard;
  }

  const card = document.createElement('div');
  card.className = 'ped-cmd-card bg-slate-800 rounded-lg border border-slate-700 overflow-hidden';
  card.dataset.idx = idx;

  // ── Card header ──────────────────────────────────────────────────────
  const hdr = document.createElement('div');
  hdr.className = 'flex items-center gap-2 px-4 py-2 cursor-pointer select-none border-b border-slate-700';
  hdr.style.background = '#1e293b';
  hdr.innerHTML =
    (_ped.isInbox ? `<span class="text-slate-500 cursor-grab shrink-0" title="Drag to reorder">⠿</span>` : '') +
    `<span class="text-xs font-semibold text-violet-400 shrink-0">Step ${cmd.stepNum}</span>` +
    `<span class="text-sm font-semibold text-slate-100 flex-1">${_esc(cmd.action)}</span>` +
    `<span class="ped-cmd-status text-xs"></span>` +
    `<span class="ped-cmd-toggle text-slate-500 text-xs ml-1">▼</span>`;
  if (_ped.isInbox) {
    const rmBtn = document.createElement('button');
    rmBtn.className = 'text-slate-500 hover:text-red-400 text-xs px-1 ml-1 transition-colors shrink-0';
    rmBtn.title = 'Remove this step';
    rmBtn.textContent = '✕';
    rmBtn.onclick = (e) => { e.stopPropagation(); _pedRemoveStep(idx); };
    hdr.appendChild(rmBtn);
  }
  card.appendChild(hdr);

  if (_ped.isInbox) {
    card.draggable = true;
    _attachCommandDragHandlers(card, idx);
  }

  // ── Rationale subtitle ───────────────────────────────────────────────
  if (cmd.rationale) {
    const rat = document.createElement('div');
    rat.className = 'px-4 py-1.5 text-xs text-slate-400 italic border-b border-slate-800';
    rat.textContent = cmd.rationale;
    card.appendChild(rat);
  }

  // ── Collapsible body ─────────────────────────────────────────────────
  const body = document.createElement('div');
  body.className = 'ped-card-body';

  // Fields section
  const fieldsDiv = document.createElement('div');
  fieldsDiv.className = 'px-4 py-3 flex flex-col gap-2';

  const visibleFields = cmd.fields.filter(f => _ped.mode === 'advanced' || f.required || f.value.trim() || f.added);
  visibleFields.forEach((f, fi) => {
    // Find the real index in cmd.fields for state updates
    const realIdx = cmd.fields.indexOf(f);
    fieldsDiv.appendChild(_buildFieldRow(cmd, idx, f, realIdx));
  });

  // "+ Add field" button — shows template fields not yet in the command
  if (_ped.isInbox) {
    const addBtn = document.createElement('button');
    addBtn.className = 'mt-1 text-xs text-slate-500 hover:text-slate-300 text-left transition-colors';
    addBtn.textContent = '+ Add field';
    addBtn.onclick = (e) => { e.stopPropagation(); _showAddFieldMenu(idx, addBtn); };
    fieldsDiv.appendChild(addBtn);
  }

  body.appendChild(fieldsDiv);

  // ── Inter-command notes (postNotes) ──────────────────────────────────
  const notesSection = _buildNotesSection(cmd, idx);
  body.appendChild(notesSection);

  card.appendChild(body);

  // Toggle collapse/expand on header click
  hdr.onclick = () => {
    const collapsed = body.classList.contains('hidden');
    body.classList.toggle('hidden', !collapsed);
    hdr.querySelector('.ped-cmd-toggle').textContent = collapsed ? '▼' : '▶';
  };

  _updateCardStatus(card, cmd);
  return card;
}

function _buildNotesSection(cmd, idx) {
  const wrap = document.createElement('div');
  wrap.className = 'border-t border-slate-700/40 px-4 pb-3 pt-2';

  const hasNotes = cmd.postNotes && cmd.postNotes.trim();

  if (!hasNotes && !_ped.isInbox) {
    wrap.classList.add('hidden');
    return wrap;
  }

  // Toggle button row
  const toggleRow = document.createElement('div');
  toggleRow.className = 'flex items-center gap-2';

  const toggleBtn = document.createElement('button');
  toggleBtn.className = 'text-xs text-slate-500 hover:text-slate-300 transition-colors';
  toggleBtn.textContent = hasNotes ? '📝 Notes' : '+ Add note after this command';
  toggleRow.appendChild(toggleBtn);
  wrap.appendChild(toggleRow);

  // Notes textarea (initially hidden if no notes)
  const ta = document.createElement('textarea');
  ta.className = 'mt-2 w-full bg-slate-900 text-slate-300 text-xs rounded p-2 border border-slate-700 resize-y font-mono focus:outline-none focus:border-violet-500';
  ta.rows = 3;
  ta.placeholder = 'Add narrative, context, or instructions between this command and the next…';
  ta.dataset.notesFor = cmd.stepNum;
  ta.value = cmd.postNotes || '';
  ta.readOnly = !_ped.isInbox;
  ta.oninput = () => {
    _ped.commands[idx].postNotes = ta.value;
    _ped.dirty = true;
    _updateStatusBar();
  };

  if (!hasNotes) ta.classList.add('hidden');
  wrap.appendChild(ta);

  toggleBtn.onclick = (e) => {
    e.stopPropagation();
    const visible = !ta.classList.contains('hidden');
    ta.classList.toggle('hidden', visible);
    toggleBtn.textContent = visible
      ? '+ Add note after this command'
      : (ta.value.trim() ? '📝 Notes' : '+ Add note after this command');
    if (!visible) ta.focus();
  };

  return wrap;
}

function _buildFieldRow(cmd, cmdIdx, f, fieldIdx) {
  const isTodo = !f.value;
  const isReq  = f.required;

  const row = document.createElement('div');
  row.className = 'flex items-start gap-2';
  row.dataset.field = f.name;

  const label = document.createElement('label');
  label.className = 'text-xs text-slate-400 w-36 shrink-0 pt-1.5 leading-tight';
  label.title = f.description || '';
  label.innerHTML = _esc(f.name) + (isReq ? '<span class="text-orange-400 ml-0.5">*</span>' : '');

  let input;
  if (f.validValues && f.validValues.length) {
    input = document.createElement('select');
    input.className = `flex-1 bg-slate-900 text-slate-200 text-sm rounded px-2 py-1 border ${isReq && isTodo ? 'border-orange-600' : 'border-slate-700'}`;
    const blank = document.createElement('option');
    blank.value = ''; blank.textContent = '— choose —';
    input.appendChild(blank);
    f.validValues.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v; opt.textContent = v;
      if (v === f.value) opt.selected = true;
      input.appendChild(opt);
    });
  } else {
    input = document.createElement('input');
    input.type = 'text';
    input.className = `flex-1 bg-slate-900 text-slate-200 text-sm rounded px-2 py-1 border ${isReq && isTodo ? 'border-orange-600' : 'border-slate-700'}`;
    input.value = f.value;
    input.placeholder = isTodo && isReq ? '⚠ Required — fill in' : (f.description || '');
  }
  input.disabled = !_ped.isInbox;

  input.addEventListener('change', () => {
    _ped.commands[cmdIdx].fields[fieldIdx].value = input.value;
    _ped.dirty = true;
    const empty = !input.value.trim();
    input.className = input.className.replace(/border-\S+/g, empty && isReq ? 'border-orange-600' : 'border-slate-700');
    _updateCardStatus(input.closest('.ped-cmd-card'), _ped.commands[cmdIdx]);
    _updateStatusBar();
  });

  row.appendChild(label);
  row.appendChild(input);
  return row;
}

function _updateCardStatus(card, cmd) {
  const statusEl = card && card.querySelector('.ped-cmd-status');
  if (!statusEl) return;
  const todos = cmd.fields.filter(f => f.required && !f.value.trim()).length;
  statusEl.textContent  = todos ? `⚠ ${todos} required` : '✓';
  statusEl.className    = `ped-cmd-status text-xs ${todos ? 'text-orange-400' : 'text-emerald-400'}`;
}

function _updateStatusBar() {
  const bar = document.getElementById('ped-status-bar');
  if (!bar) return;
  const todos = _ped.commands.reduce((n, c) => n + c.fields.filter(f => f.required && !f.value.trim()).length, 0);
  const nc    = _ped.commands.length;
  const parts = [
    `${nc} command${nc !== 1 ? 's' : ''}`,
    todos
      ? `<span class="text-orange-400">${todos} required field${todos !== 1 ? 's' : ''} empty</span>`
      : '<span class="text-emerald-400">All required fields filled</span>',
    `<span class="text-slate-500">${_ped.mode === 'advanced' ? 'Advanced' : 'Basic'} template</span>`,
  ];
  if (_ped.dirty) parts.push('<span class="text-amber-400">● Unsaved</span>');
  bar.innerHTML = parts.join(' &nbsp;·&nbsp; ');
}

// ── Basic / Advanced toggle ───────────────────────────────────────────────────

async function _toggleMode() {
  _ped.mode = _ped.mode === 'basic' ? 'advanced' : 'basic';
  const modeBtn = document.getElementById('ped-mode-btn');
  if (modeBtn) modeBtn.textContent = _ped.mode === 'basic' ? 'Basic' : 'Advanced';

  // Load fields for new mode then re-enrich and re-render
  const actions = [...new Set(_ped.commands.map(c => c.action))];
  await Promise.all(actions.map(a => _fetchTemplateFields(a, _ped.mode)));
  _enrichFieldMetadata();
  _renderCommandCards();
  _updateStatusBar();
}

// ── Add optional field dropdown ───────────────────────────────────────────────

async function _showAddFieldMenu(cmdIdx, anchor) {
  const cmd      = _ped.commands[cmdIdx];
  const tmpl     = await _fetchTemplateFields(cmd.action, _ped.mode);
  const fallback = tmpl.length ? tmpl : await _fetchTemplateFields(cmd.action, 'basic');

  if (!fallback.length) {
    _showToast('No template metadata available for ' + cmd.action);
    return;
  }

  const presentNames = new Set(cmd.fields.map(f => f.name));
  const available    = fallback.filter(f => !presentNames.has(f.name));

  if (!available.length) {
    _showToast('All template fields are already in this command.');
    return;
  }

  // Remove any existing menu
  const existing = document.getElementById('ped-field-menu');
  if (existing) existing.remove();

  const menu = document.createElement('div');
  menu.id = 'ped-field-menu';
  menu.className = 'bg-slate-800 border border-slate-600 rounded shadow-xl py-1 text-sm';
  // Use inline z-index — z-70 is not a standard Tailwind class
  Object.assign(menu.style, {
    position:  'fixed',
    zIndex:    '9999',
    maxHeight: '340px',
    overflowY: 'auto',
    width:     '380px',
  });

  // Section headings: required first, then optional
  const required = available.filter(f => f.required);
  const optional = available.filter(f => !f.required);

  const addGroup = (items, label) => {
    if (!items.length) return;
    const hdr = document.createElement('div');
    hdr.className = 'px-3 pt-2 pb-0.5 text-xs font-semibold text-slate-500 uppercase tracking-wider';
    hdr.textContent = label;
    menu.appendChild(hdr);

    items.forEach(f => {
      const item = document.createElement('button');
      item.className = 'w-full text-left px-3 py-1.5 hover:bg-slate-700 text-slate-200 flex flex-col gap-0.5';
      item.innerHTML =
        `<span class="font-medium">${_esc(f.name)}${f.required ? ' <span class="text-orange-400 text-xs">*</span>' : ''}</span>` +
        (f.description ? `<span class="text-xs text-slate-500 leading-snug whitespace-normal">${_esc(f.description)}</span>` : '');
      item.onclick = () => {
        _addFieldToCommand(cmdIdx, {
          name: f.name, value: f.default_value || '', required: f.required,
          type: f.type, validValues: f.valid_values || [], description: f.description || '',
          added: true,
        });
        menu.remove();
      };
      menu.appendChild(item);
    });
  };

  addGroup(required, 'Required');
  addGroup(optional, 'Optional');

  // Position the menu near the anchor using fixed positioning
  const rect = anchor.getBoundingClientRect();
  menu.style.top  = `${Math.min(rect.bottom + 4, window.innerHeight - 350)}px`;
  menu.style.left = `${Math.min(rect.left, window.innerWidth - 396)}px`;

  document.body.appendChild(menu);

  const dismiss = e => {
    if (!menu.contains(e.target) && e.target !== anchor) {
      menu.remove();
      document.removeEventListener('click', dismiss);
    }
  };
  setTimeout(() => document.addEventListener('click', dismiss), 10);
}

function _addFieldToCommand(cmdIdx, field) {
  _ped.commands[cmdIdx].fields.push(field);
  _ped.dirty = true;
  // Re-render just this card
  const container = document.getElementById('ped-commands');
  const oldCard   = container.children[cmdIdx];
  const newCard   = _buildCommandCard(_ped.commands[cmdIdx], cmdIdx);
  container.replaceChild(newCard, oldCard);
  _updateStatusBar();
}

// ── Save ──────────────────────────────────────────────────────────────────────

async function _savePlanEdits() {
  // Flush postNotes from DOM into state before synthesising
  _ped.commands.forEach(cmd => {
    const ta = document.querySelector(`[data-notes-for="${cmd.stepNum}"]`);
    if (ta) cmd.postNotes = ta.value;
  });

  const content = _synthesizePlanMarkdown();
  const btn     = document.getElementById('ped-save-btn');
  btn.disabled  = true; btn.textContent = 'Saving…';
  try {
    const r = await fetch(`/api/plans/${encodeURIComponent(_ped.doc_id)}`, {
      method:  'PUT',
      headers: { 'Content-Type': 'application/json', ...Auth.getHeaders() },
      body:    JSON.stringify({ content }),
    });
    if (!r.ok) throw new Error(await r.text());
    _ped.dirty = false;
    _updateStatusBar();
    btn.textContent = '✓ Saved';
    setTimeout(() => { btn.textContent = 'Save'; btn.disabled = false; }, 1500);
  } catch (e) {
    // Usually means the plan moved out from under this editor — e.g. executed
    // from another tab or via chat since it was opened. Resolve the current
    // doc_id via the draft (self-heals server-side) and reopen rather than
    // leaving a dead editor pointed at a doc_id that no longer exists — don't
    // blind-retry the write itself, which could clobber whatever changed it.
    if (_ped.draft_id) {
      alert(
        `Save failed: ${e.message}\n\n` +
        `This can happen if the plan changed elsewhere (e.g. executed in another ` +
        `tab) since this editor was opened. Reopening with the current version — ` +
        `your last edit was not saved and may need to be redone.`
      );
      const draftId = _ped.draft_id;
      try {
        const dr = await fetch(`/api/drafts/${encodeURIComponent(draftId)}`, { headers: Auth.getHeaders() });
        const freshDocId = dr.ok ? (await dr.json()).doc_id : null;
        if (freshDocId) {
          await openPlanEditor(freshDocId, draftId);
        } else {
          closePlanEditor();
        }
      } catch {
        closePlanEditor();
      }
    } else {
      alert(`Save failed: ${e.message}`);
    }
    btn.textContent = 'Save'; btn.disabled = false;
  }
}

// ── Validate ─────────────────────────────────────────────────────────────────

async function _validatePlanDoc() {
  if (_ped.dirty) await _savePlanEdits();

  const btn    = document.getElementById('ped-validate-btn');
  const panel  = document.getElementById('ped-validate-result');
  if (!panel) { console.error('ped-validate-result panel not found'); return; }
  btn.disabled = true; btn.textContent = 'Validating…';
  panel.classList.remove('hidden');
  panel.innerHTML = '<span class="text-slate-400">Running Dr.Egeria validate…</span>';

  let data;
  try {
    const r = await fetch(`/api/plans/${encodeURIComponent(_ped.doc_id)}/validate`, { method: 'POST', headers: Auth.getHeaders() });
    data = await r.json();
    console.log('[validate] API response:', JSON.stringify(data).slice(0, 500));
  } catch (fetchErr) {
    panel.innerHTML = `<span class="text-red-400">Request failed: ${_esc(fetchErr.message)}</span>`;
    btn.textContent = '✅ Validate'; btn.disabled = false;
    return;
  }

  try {

    // success: boolean field from Dr.Egeria (when available); fall back to query_type check
    const success   = ('success' in data) ? data.success : (data.query_type === 'plan_validated');
    const valErrs   = data.validation_errors || [];
    const execErrs  = data.execution_errors  || [];
    const allErrors = [...valErrs, ...execErrs];

    const passed    = success && allErrors.length === 0;
    const headerCls = passed ? 'text-emerald-400' : allErrors.length ? 'text-amber-400' : 'text-red-400';
    const headerTxt = passed
      ? '✓ Validation passed'
      : allErrors.length
        ? `⚠ ${allErrors.length} issue${allErrors.length !== 1 ? 's' : ''} found`
        : '✗ Validation failed';

    let html = `<div class="flex items-center justify-between mb-2">` +
      `<span class="font-semibold ${headerCls}">${headerTxt}</span>` +
      `<button class="ped-validate-close text-slate-500 hover:text-slate-200 text-xs">✕ close</button></div>`;

    // Structured errors table
    if (allErrors.length) {
      html += `<table class="w-full text-xs mb-2 border-collapse">` +
        `<thead><tr class="border-b border-slate-600">` +
        `<th class="text-left text-slate-500 pb-1 pr-3 font-normal">Step</th>` +
        `<th class="text-left text-slate-500 pb-1 pr-3 font-normal">Command</th>` +
        `<th class="text-left text-slate-500 pb-1 font-normal">Issue</th>` +
        `</tr></thead><tbody>`;
      for (const e of allErrors) {
        const step = (e.step != null) ? e.step : (e.index != null ? e.index : '—');
        const cmd  = e.command || e.name  || '—';
        const msg  = e.message || e.error || (typeof e === 'string' ? e : JSON.stringify(e));
        html += `<tr class="border-t border-slate-700/50">` +
          `<td class="py-1 pr-3 text-slate-400 align-top">${_esc(String(step))}</td>` +
          `<td class="py-1 pr-3 text-slate-300 align-top">${_esc(String(cmd))}</td>` +
          `<td class="py-1 text-red-300 align-top">${_esc(String(msg))}</td></tr>`;
      }
      html += `</tbody></table>`;
    }

    // Always show raw Dr.Egeria output when validation didn't pass —
    // error details may be in the output text even if structured errors weren't parsed.
    const rawOutput = String(data.output || data.response || data.result || '').trim();
    if (!passed && rawOutput) {
      const lines   = rawOutput.split('\n');
      const preview = lines.slice(0, 14).join('\n');
      const rest    = lines.slice(14).join('\n');
      html += `<div class="mt-2 mb-1 text-xs text-slate-500 font-semibold">Dr.Egeria output:</div>`;
      html += `<pre class="text-xs text-slate-300 whitespace-pre-wrap bg-slate-900/60 rounded p-2 max-h-52 overflow-y-auto">${_esc(preview)}</pre>`;
      if (rest) {
        html += `<details class="mt-1"><summary class="text-xs text-slate-500 cursor-pointer hover:text-slate-300">` +
          `Show more (${lines.length} lines total)</summary>` +
          `<pre class="text-xs text-slate-400 whitespace-pre-wrap mt-1">${_esc(rest)}</pre></details>`;
      }
    } else if (passed) {
      html += `<p class="text-xs text-slate-500 mt-1">All commands passed pre-flight checks. Use Execute to apply them to Egeria.</p>`;
    }

    panel.innerHTML = html;
    panel.querySelector('.ped-validate-close')?.addEventListener('click', () => panel.classList.add('hidden'));
    panel.scrollTop = 0;
  } catch (renderErr) {
    console.error('[validate] render error:', renderErr);
    // Absolute fallback — dump the raw API response so something useful is visible
    const raw = data ? JSON.stringify(data, null, 2) : '(no data)';
    panel.innerHTML =
      `<div class="text-red-400 text-xs mb-1">Render error: ${_esc(String(renderErr))}</div>` +
      `<pre class="text-xs text-slate-400 whitespace-pre-wrap max-h-48 overflow-y-auto">${_esc(raw.slice(0, 3000))}</pre>`;
  } finally {
    btn.textContent = '✅ Validate'; btn.disabled = false;
  }
}

// ── Execute ───────────────────────────────────────────────────────────────────

/** The plan confirm gate (server: GovernancePlanAgent.prepare_execute). The first POST runs nothing and
 *  returns the plan as it would run; the user confirms that command sequence; the second POST carries
 *  {pending_id, digest} and runs it once. A plan edited in between is refused by the server, in words.
 *  Returns the server's result, or {cancelled: true}. Throws on an HTTP error (with the server's detail).
 *  Shared by every Execute / Re-run button (index.html and this file). */
async function runPlanConfirmed(url, body) {
  const post = async (b) => {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...Auth.getHeaders() },
      body: JSON.stringify(b),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Server error ${r.status}`);
    return data;
  };
  const shown = await post(body || {});
  if (!shown || !shown.confirm_required) return shown;       // not found, refused, sign in: the server says
  const choice = await planConfirmDialog(shown);
  if (!choice.ok) return { cancelled: true, response: 'Cancelled: the plan was not run, and nothing was written.' };
  const confirmBody = { ...(body || {}), pending_id: shown.pending_id, digest: shown.plan_digest };
  if (choice.ack) confirmBody.ack_non_idempotent = true;
  return post(confirmBody);
}

/** How many command names the confirm dialog lists before it says "first N of M commands". */
const PLAN_CONFIRM_LIST_MAX = 30;

/** The plan confirm dialog: what will run, by name ("first N of M commands" when there are more), the
 *  full Command Sequence (scrollable, never cut), and on a re-run that repeats non-idempotent commands a
 *  checkbox that must be ticked before Run is enabled. Every server value reaches the page as textContent.
 *  Resolves {ok, ack}. */
function planConfirmDialog(shown) {
  return new Promise(resolve => {
    const el = (tag, cls, text) => {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) e.textContent = text;
      return e;
    };
    const section = String(shown.command_section || '');
    const names = Array.isArray(shown.command_names) && shown.command_names.length
      ? shown.command_names
      : [...section.matchAll(/^##\s+(.+?)\s*$/gm)].map(m => m[1]);
    const total = names.length;
    const listed = names.slice(0, PLAN_CONFIRM_LIST_MAX);
    const unsafe = Array.isArray(shown.non_idempotent) ? shown.non_idempotent : [];
    const ackNeeded = !!shown.ack_required && unsafe.length > 0;

    const overlay = el('div', 'fixed inset-0 z-50 flex items-center justify-center bg-black/60');
    overlay.dataset.planConfirm = 'dialog';
    const panel = el('div', 'bg-slate-800 text-slate-100 rounded-lg shadow-xl p-4 w-[min(42rem,92vw)] max-h-[85vh] overflow-y-auto text-sm space-y-3');
    panel.appendChild(el('h3', 'text-base font-semibold', `${shown.rerun ? 'Run plan again' : 'Run plan'} ${shown.doc_id || ''}?`));
    panel.appendChild(el('p', '', `${total} command(s) will be processed in Egeria as you.`));
    const summary = el('p', 'text-slate-400 text-xs', total > listed.length
      ? `First ${listed.length} of ${total} commands:` : `All ${total} commands:`);
    summary.dataset.planConfirm = 'count';
    panel.appendChild(summary);
    const ol = el('ol', 'list-decimal pl-6 space-y-0.5');
    listed.forEach(n => ol.appendChild(el('li', '', n)));
    panel.appendChild(ol);
    if (total > listed.length) panel.appendChild(el('p', 'text-slate-400 text-xs', `…and ${total - listed.length} more, in the full Command Sequence below.`));
    const details = el('details', '');
    details.appendChild(el('summary', 'cursor-pointer text-slate-300', 'Show the full Command Sequence'));
    const pre = el('pre', 'mt-2 max-h-64 overflow-auto bg-slate-900 p-2 rounded text-xs whitespace-pre-wrap', section.trim());
    pre.dataset.planConfirm = 'script';
    details.appendChild(pre);
    panel.appendChild(details);

    let ack = null;
    if (ackNeeded) {
      const warn = el('div', 'border border-amber-500 rounded p-2 space-y-1');
      warn.dataset.planConfirm = 'unsafe';
      warn.appendChild(el('p', 'text-amber-300 font-semibold', 'This is a re-run, and these commands are not safe to run twice:'));
      const ul = el('ul', 'list-disc pl-6');
      unsafe.forEach(u => ul.appendChild(el('li', '', `${u.command}${u.count > 1 ? ` (×${u.count})` : ''}: ${u.why}`)));
      warn.appendChild(ul);
      const label = el('label', 'flex items-center gap-2 mt-1');
      ack = el('input', '');
      ack.type = 'checkbox';
      ack.dataset.planConfirm = 'ack';
      label.appendChild(ack);
      label.appendChild(el('span', '', 'I understand these commands will run again, and want to re-run the plan anyway.'));
      warn.appendChild(label);
      panel.appendChild(warn);
    }

    const buttons = el('div', 'flex justify-end gap-2 pt-1');
    const cancel = el('button', 'px-3 py-1 rounded bg-slate-600 hover:bg-slate-500', 'Cancel');
    cancel.type = 'button';
    cancel.dataset.planConfirm = 'cancel';
    const run = el('button', 'px-3 py-1 rounded bg-violet-700 hover:bg-violet-600 disabled:opacity-40', shown.rerun ? 'Run again' : 'Run');
    run.type = 'button';
    run.dataset.planConfirm = 'run';
    run.disabled = ackNeeded;
    if (ack) ack.addEventListener('change', () => { run.disabled = !ack.checked; });
    buttons.appendChild(cancel);
    buttons.appendChild(run);
    panel.appendChild(buttons);
    overlay.appendChild(panel);

    const done = (result) => { overlay.remove(); resolve(result); };
    cancel.addEventListener('click', () => done({ ok: false, ack: false }));
    run.addEventListener('click', () => {
      if (run.disabled) return;
      done({ ok: true, ack: !!(ack && ack.checked) });
    });
    document.body.appendChild(overlay);
  });
}

async function _executePlanDoc() {
  if (_ped.dirty) await _savePlanEdits();     // saved first, so what is confirmed is what is stored
  const docId = _ped.doc_id;
  const draftId = _ped.draft_id;
  // Direct REST call (not a faked chat message) — "execute the plan X" sent
  // as chat text can be intercepted by context-based routing (e.g. an open
  // Plan Canvas session for the same plan) and mistaken for a plan-
  // modification instruction instead of an execute command. See BACKLOG.md.
  let result;
  try {
    result = await runPlanConfirmed(`/api/plans/${encodeURIComponent(docId)}/execute`, { draft_id: draftId || null });
  } catch (e) {
    if (typeof appendMessage === 'function') appendMessage('assistant', `**Error:** ${e.message}`);
    return;
  }
  if (result.cancelled) return;
  closePlanEditor();
  if (typeof appendMessage === 'function') {
    appendMessage('you', `Execute plan ${docId}`);
    const wrap = appendMessage('assistant', result.response || '', {});
    if (typeof _applyQueryResult === 'function') _applyQueryResult(`execute the plan ${docId}`, result, wrap);
  }
}

// Kept for sidebar retry button compatibility (plan_editor.js is not the caller there)
async function _retryPlanDoc() {
  // Retry without editing — sidebar button is the primary way to do this.
  // From the editor, use "Recover for Editing" instead.
  await pedRecoverForEditing();
}

// ── Recovery (outbox → inbox for editing, no immediate re-execution) ──────────

async function pedRecoverForEditing() {
  if (!confirm(`Recover "${_ped.doc_id}" for editing?\nThis moves it back to inbox so you can edit, validate, and re-execute.`)) return;
  const bar = document.getElementById('ped-recovery-bar');
  if (bar) bar.innerHTML = '<span class="text-amber-300">Recovering…</span>';
  try {
    const r = await fetch(`/api/plans/${encodeURIComponent(_ped.doc_id)}/recover`, { method: 'POST', headers: Auth.getHeaders() });
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || r.statusText); }
    const res = await r.json();
    // Reload editor with the now-inbox version
    const draft_id = _ped.draft_id;
    await openPlanEditor(res.doc_id, draft_id);
    if (typeof loadPlans === 'function') loadPlans();
    _showToast('Plan recovered — you can now edit, validate, and execute.');
  } catch (e) {
    if (bar) bar.innerHTML = `<span class="text-red-400">Recovery failed: ${_esc(e.message)}</span>`;
  }
}

// ── Re-run Now (outbox → outbox, no inbox detour) ─────────────────────────────

async function pedRerunNow() {
  const bar = document.getElementById('ped-recovery-bar');
  if (bar) bar.innerHTML = '<span class="text-emerald-300">Re-running…</span>';
  try {
    const res = await runPlanConfirmed(`/api/plans/${encodeURIComponent(_ped.doc_id)}/rerun`, {});
    if (res.cancelled) { if (bar) bar.innerHTML = ''; return; }
    if (res.state === 'refused') throw new Error(res.response);
    await openPlanEditor(_ped.doc_id, null);
    if (typeof loadPlans === 'function') loadPlans();
    _showToast('Plan re-executed — outcome updated.');
  } catch (e) {
    if (bar) bar.innerHTML = `<span class="text-red-400">Re-run failed: ${_esc(e.message)}</span>`;
  }
}

function _pedTitle() {
  const m = /^#\s+(.+)$/m.exec(_ped.narrative || '');
  return m ? m[1].trim() : _ped.doc_id;
}

async function pedShowVersionHistory() {
  const panel = document.getElementById('ped-version-panel');
  const list  = document.getElementById('ped-version-list');
  panel.classList.remove('hidden');
  list.innerHTML =
    `<div class="flex items-center justify-between mb-2 pb-1 border-b border-slate-700 gap-2">` +
    `<span class="text-slate-500">From the current content:</span>` +
    `<span class="flex gap-1 shrink-0">` +
    `<button class="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-100 transition-colors" ` +
    `title="Save the specification only — no history, no reference data" ` +
    `data-action="save-as">Save As</button>` +
    `<button class="px-2 py-0.5 rounded bg-violet-700 hover:bg-violet-600 text-white transition-colors" ` +
    `data-action="fork-current">Fork Current</button>` +
    `</span></div>` +
    `<div id="ped-version-list-items">Loading…</div>`;
  // Handlers are bound here, never written into onclick strings: doc ids, titles and version file
  // names come from the server, and an inline handler would run them as code.
  list.querySelector('[data-action="save-as"]').addEventListener('click', () => openSaveAsModal(_ped.doc_id, null, _pedTitle()));
  list.querySelector('[data-action="fork-current"]').addEventListener('click', () => openForkModal(_ped.doc_id, null, _pedTitle()));
  const itemsEl = document.getElementById('ped-version-list-items');
  try {
    const r    = await fetch(`/api/plans/${encodeURIComponent(_ped.doc_id)}/versions`, { headers: Auth.getHeaders() });
    const data = await r.json();
    const vers = data.versions || [];
    if (!vers.length) { itemsEl.textContent = 'No saved versions.'; return; }
    itemsEl.innerHTML = '';
    vers.forEach(v => {
      const row = document.createElement('div');
      row.className = 'flex items-center gap-2 py-0.5';
      // Format timestamp "20260614_170122" → "2026-06-14 17:01:22"
      const ts = v.timestamp.replace(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/, '$1-$2-$3 $4:$5:$6');
      const desc = v.description ? `<span class="text-slate-500 italic ml-1">— ${_esc(v.description)}</span>` : '';
      row.innerHTML =
        `<span class="flex-1 text-slate-300">${_esc(ts || v.version_file)}${desc}</span>` +
        `<button class="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-100 transition-colors" ` +
        `data-action="restore">Restore</button>` +
        `<button class="px-2 py-0.5 rounded bg-violet-800 hover:bg-violet-700 text-white transition-colors" ` +
        `data-action="fork">Fork</button>`;
      row.querySelector('[data-action="restore"]').addEventListener('click', () => pedRestoreVersion(v.version_file));
      row.querySelector('[data-action="fork"]').addEventListener('click', () => openForkModal(_ped.doc_id, v.version_file, _pedTitle()));
      itemsEl.appendChild(row);
    });
  } catch (e) {
    itemsEl.innerHTML = `<span class="text-red-400">Failed to load versions: ${_esc(e.message)}</span>`;
  }
}

async function pedRestoreVersion(version_file) {
  if (!confirm(`Restore this version of "${_ped.doc_id}"?\nThe current version will be saved before restoring.`)) return;
  try {
    const r = await fetch(
      `/api/plans/${encodeURIComponent(_ped.doc_id)}/versions/${encodeURIComponent(version_file)}/restore`,
      { method: 'POST', headers: Auth.getHeaders() }
    );
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || r.statusText); }
    document.getElementById('ped-version-panel').classList.add('hidden');
    await openPlanEditor(_ped.doc_id, _ped.draft_id);
    if (typeof loadPlans === 'function') loadPlans();
    _showToast('Version restored to inbox — ready to edit and execute.');
  } catch (e) {
    _showToast(`Restore failed: ${e.message}`);
  }
}

// ── Toast notifications ───────────────────────────────────────────────────────

function _showToast(msg) {
  const toast = document.createElement('div');
  toast.className = 'fixed bottom-6 right-6 bg-slate-700 text-slate-100 text-sm px-4 py-2 rounded shadow-lg';
  toast.style.zIndex = '9999';
  toast.textContent  = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// ── Util ──────────────────────────────────────────────────────────────────────

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
