/* Reading an envelope — the honest part.
 *
 * Split out of app.js (BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §D, 2026-09-27) so
 * `readEnvelope` can be imported directly by a node-run test and checked
 * against `facts.py`'s `_renders_text` — its Python mirror, which has no
 * shared source and had already drifted twice (see facts.py's own docstring
 * on `_renders_text`). Moving the code changes nothing about what it does;
 * only where it lives.
 *
 * `esc`/`tnum` are NOT imported from app.js here, even though that is where
 * they are defined and app.js imports this module — importing them back
 * would be circular. They are passed in as parameters instead, the same way
 * format.js's `verdictLineHtml(r, esc)` already does ("esc is passed in
 * because this module has no DOM helpers of its own"). `whenMs` has no such
 * problem — it lives in format.js, which nothing here or in app.js needs to
 * import back — so it is imported normally.
 */
import { whenMs } from '/static/next/format.js';

/** Fact states, from surveyors/result_status.py. */
export const MEASURED = 'measured';
export const NOTHING_FOUND = 'nothing_found';
export const NOT_ESTABLISHED = 'not_established';
export const NEVER_RUN = 'never_run';
export const NO_READER = 'no_reader';
export const PARTIAL = 'partial';

/** Verdict words the design sets at weight 600. Matched only at the head of
 *  a sentence and only when a separator follows, so a headline that merely
 *  starts with "No" as part of a phrase is left alone. Bolding the wrong
 *  word would assert a verdict the analysis did not make. */
export const VERDICT = /^(Yes|No|Partly|Partially|Likely|Unlikely|Mixed|Narrowly maintained|Actively maintained)(\s*[—–,-]\s+)/;

/** Sentence case for a verdict word an analysis wrote in lower case. */
export function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

export function answerHtml(headline, esc, tnum) {
  const m = VERDICT.exec(headline);
  if (!m) return tnum(esc(headline));
  const rest = headline.slice(m[0].length);
  return `<strong class="font-semibold">${esc(m[1])}</strong>${esc(m[2])}${tnum(esc(rest))}`;
}

/**
 * Truncate a multi-sentence analysis explanation to its first sentence, for
 * the headline slot's one-sentence rule (REPLY-DESIGNER-ROUND2-DATABASE-
 * SCREENS.md §2.4/§2.2: a row holds one sentence, or a rollup's lead plus
 * "· N schemas ›" -- the rest belongs behind "the numbers behind this", not
 * duplicated here). Applied to RAW text before it is escaped/marked up, so
 * a verdict prefix's own regex (`VERDICT`, matched at the string's start) is
 * unaffected by where this cuts later in the string.
 *
 * Splits at the first ". " / "! " / "? " followed by a capital letter (an
 * ordinary sentence boundary), or at the first " · " rollup separator,
 * whichever comes first -- never inside a parenthetical, tracked by depth
 * as the string is scanned once. Does not special-case a numbered/bulleted
 * list's own periods (e.g. "1. First. 2. Second.") -- not needed by either
 * row this was built for (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.2's
 * "Which resources cover similar subjects…" and §2.4's fit question), both
 * plain prose paragraphs.
 */
export function firstSentence(text) {
  if (!text) return text;
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '(') { depth++; continue; }
    if (c === ')') { depth = Math.max(0, depth - 1); continue; }
    if (depth > 0) continue;
    if ((c === '.' || c === '!' || c === '?') && text[i + 1] === ' ' && /[A-Z]/.test(text[i + 2] || '')) {
      return text.slice(0, i + 1);
    }
    if (text.startsWith(' · ', i)) {
      return text.slice(0, i);
    }
  }
  return text;
}

/** The analysis's own prose, if it wrote any. Never assembled here. */
export function prose(f) {
  const v = f.value || {};
  const text = v.detail || v.summary || v.description || '';
  return typeof text === 'string' ? text.trim() : '';
}

/** The scalar measures, relayed as `key value` pairs.
 *
 *  Deliberately last-resort. Structured fields (lists, objects) are NOT
 *  flattened into this line — they belong in `evidence`, where they can be
 *  read rather than skimmed. */
export function scalarMeasures(value, max = 6) {
  if (!value || typeof value !== 'object') return '';
  const pairs = [];
  for (const [k, v] of Object.entries(value)) {
    if (v === null || v === undefined || v === '') continue;
    if (typeof v === 'object') continue;
    if (k === 'verdict') continue;   // already used as the verdict word
    const shown = typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v);
    if (shown.length > 60) continue;
    pairs.push(`${k.replace(/_/g, ' ')} ${shown}`);
    if (pairs.length >= max) break;
  }
  return pairs.join(' · ');
}

/** The Mermaid source a fact carries, if it carries any.
 *
 *  `architecture_diagram` writes its source into the fact value, so the
 *  relationship question ("How do its components relate to each other?") has
 *  a real diagram sitting behind it. This is the product path to a diagram —
 *  a chat answer is not the only one, and wiring promotion ONLY to chat left
 *  the feature unreachable for anyone who had not asked a question first.
 */
export function factMermaid(env) {
  for (const f of (env && env.facts) || []) {
    const src = f.value && (f.value.mermaid || f.value.diagram);
    if (typeof src === 'string' && src.trim()) {
      return { source: src.trim(), analysisId: f.analysis_id, lastRun: f.last_run_at || '' };
    }
  }
  return null;
}

/** The Graphviz relationship-graph diagrams a fact carries, if any.
 *
 *  `db_relationship_graph` (`db_derived.py`'s `relationship_graph_by_
 *  container`) writes a `graphviz` object into the fact value — three zoom
 *  levels (`schema_map`, `full` or a `full_fallback_reason` naming why not,
 *  and `by_schema`, one DOT source per schema) built from the exact same
 *  rows the text answer already reads, per the design note ("Design: the
 *  relationship graph, drawn from the real AdventureWorks edges"). Same
 *  shape/spirit as `factMermaid` above, kept separate because a Graphviz
 *  source renders through a different Kroki endpoint and offers three
 *  distinct views rather than one.
 */
export function factGraphviz(env) {
  for (const f of (env && env.facts) || []) {
    const gv = f.value && f.value.graphviz;
    if (gv && typeof gv === 'object' && (gv.schema_map || gv.full)) {
      return {
        schemaMap: gv.schema_map || '',
        full: gv.full || null,
        fallbackReason: gv.full_fallback_reason || '',
        bySchema: gv.by_schema || {},
        tableCount: gv.table_count ?? null,
        analysisId: f.analysis_id,
        lastRun: f.last_run_at || '',
      };
    }
  }
  return null;
}

/**
 * The lead analysis for a multi-analysis question: the first of
 * `entry.analysis_ids` actually NAMED (as a whole word) in `entry.note` --
 * the catalog's own "Answering Analysis" column text, which is written to
 * say which analysis IS the answer and which are its inputs. E.g.
 * `preliminary_fit`'s own note starts "preliminary_fit (design §16.3,
 * §16.5 — zero-fetch comparison of subject_signals, coverage_signals and
 * grain_determination's time grain against a supplied data requirement …",
 * naming itself first even though question_catalog.yaml's own
 * `analysis_ids` lists it LAST, after the three signals it consumes.
 *
 * REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.4: reading `analysis_ids[0]`
 * for this exact question would have led the headline with
 * `grain_determination`'s sentence -- an input, not the fit verdict -- which
 * is not what the reply's own quoted example calls "the actual answer".
 * Several other MIXED questions have a note that names an id OTHER than
 * `analysis_ids[0]` first too (`security_scan`/`cii_badge`,
 * `security_scan`/`repo_conventions`, `ci_quality`/`repo_conventions`), so
 * this is not special-cased to `preliminary_fit` — it reads the note the
 * same way for every question.
 *
 * Returns `null` when there is nothing to prefer (no `analysis_ids`, or a
 * note that names none of them) -- callers fall back to `analysis_ids`'
 * own order and then to fact-arrival order, never to nothing.
 */
export function leadAnalysisId(entry) {
  const ids = (entry && entry.analysis_ids) || [];
  if (!ids.length) return null;
  const note = (entry && entry.note) || '';
  let best = null;
  let bestPos = Infinity;
  for (const id of ids) {
    const escaped = id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const m = new RegExp(`\\b${escaped}\\b`).exec(note);
    if (m && m.index < bestPos) { bestPos = m.index; best = id; }
  }
  return best;
}

/**
 * Turn an envelope into the lines a row shows.
 *
 * The rule this function exists to keep: a fact's state decides the SENTENCE,
 * not just an icon. `nothing_found` is knowledge — a measured zero — and says
 * so. `never_run` says nothing ran. `not_established` says the method could
 * not settle it. Folding any two of those together is the failure the
 * FactLayer was built to prevent, and it would be undone here.
 *
 * `esc`/`tnum` are passed in by the caller (app.js) rather than imported —
 * see this module's header comment.
 */
export function readEnvelope(entry, env, esc, tnum) {
  const facts = (env && env.facts) || [];
  const known = facts.filter((f) => f.is_known);
  const lines = {
    answer: '', caveat: '', sources: [], lastRun: '', canRun: [],
    // Analyses that measured something but have no written summary, so the
    // answer line above is raw measures. Named, because the fix is a
    // headline_reader on that analysis and nobody can act on "somewhere".
    unwritten: [],
    // True when at least one fact is known but no fact recorded WHEN it ran.
    // Missing timestamp is a gap in the record, NOT evidence that nothing
    // ran — printing "never run" here would report a fact about the row as
    // a fact about the repository.
    runTimeUnrecorded: false,
  };

  for (const f of facts) {
    if (f.can_run && f.can_run.length) lines.canRun.push(...f.can_run);
    if (f.analysis_id) lines.sources.push(f.analysis_id);
    if (f.last_run_at && (!lines.lastRun || whenMs(f.last_run_at) > whenMs(lines.lastRun))) lines.lastRun = f.last_run_at;
  }
  lines.sources = [...new Set(lines.sources)];
  lines.canRun = [...new Set(lines.canRun)];

  // The answer, in preference order. Every rung RELAYS something the
  // analysis wrote; none of them composes a verdict here.
  //
  //   1. `headline`  — the analysis's own summary sentence.
  //   2. `value.detail` / `value.summary` — also its own prose, with
  //      `value.verdict` as the bolded verdict word when it states one.
  //   3. the scalar measures themselves, plus a caveat saying the analysis
  //      has no written summary.
  //
  // Rung 3 exists because most analyses do not have a headline_reader wired
  // up yet, and a row that shows a tick with nothing beside it is the exact
  // shape this screen was built to stop: a claim of "answered" with no
  // answer under it.
  // One sentence PER analysis (each fact still contributes at most one),
  // tagged by analysis_id -- `order` keeps the order facts were walked in,
  // for the fallback below.
  const sentenceByAnalysis = new Map();
  const order = [];
  for (const f of known) {
    let sentence = null;
    if (f.state === NOTHING_FOUND && !f.headline && !prose(f)) {
      // A measured zero. Said in words, because the bare number reads as
      // "we didn't look".
      sentence = tnum(esc(`${f.analysis_id} ran and found nothing.`));
    } else if (f.headline) {
      sentence = answerHtml(firstSentence(f.headline), esc, tnum);
    } else {
      const p = prose(f);
      if (p) {
        // The analysis's own verdict word, set at weight 600 like the design's
        // "Yes". Marked up here rather than re-detected from the joined
        // string downstream: this is the one place that knows the word came
        // from a `verdict` field rather than from the first word of a
        // sentence.
        const verdict = f.value && f.value.verdict;
        const pOne = firstSentence(p);
        sentence = verdict
          ? `<strong class="font-semibold">${esc(cap(String(verdict)))}</strong> — ${tnum(esc(pOne))}`
          : tnum(esc(pOne));
      } else {
        const scalars = scalarMeasures(f.value);
        if (scalars) {
          sentence = tnum(esc(scalars));
          lines.unwritten.push(f.analysis_id);
        }
      }
    }
    if (sentence != null) {
      sentenceByAnalysis.set(f.analysis_id, sentence);
      order.push(f.analysis_id);
    }
  }

  // The headline slot holds exactly ONE sentence (REPLY-DESIGNER-ROUND2-
  // DATABASE-SCREENS.md §2.4). Before this, a question backed by several
  // analyses joined every one of their sentences with ' · ' into the same
  // slot -- ~200 words on AdventureWorks' "Could this be in scope..."
  // question, with the actual answer (`preliminary_fit`'s own explanation)
  // roughly two-thirds of the way in.
  //
  // `leadAnalysisId()` (this module) reads `entry.note` -- the catalog's own
  // "Answering Analysis" text -- for which analysis the question is asking
  // FOR, not merely which ones it lists: `analysis_ids`' own order is NOT
  // that ordering (it lists `preliminary_fit` LAST, after the three inputs
  // it consumes). Falls back to `analysis_ids`' own order, then to
  // fact-arrival order, so the slot is never emptied just because a
  // preference list didn't match what the envelope actually carries.
  // NOTE: `lines.answer` is HTML, already escaped by each branch above.
  // Do not run it through esc() or answerHtml() again downstream.
  const lead = leadAnalysisId(entry);
  const preferredOrder = [
    ...(lead ? [lead] : []),
    ...((entry && entry.analysis_ids) || []),
    ...order,
  ];
  const primaryId = preferredOrder.find((id) => sentenceByAnalysis.has(id));
  lines.answer = primaryId ? (sentenceByAnalysis.get(primaryId) || '') : '';
  // The OTHER analyses' own sentences are not duplicated into a second block
  // here -- `provenanceLine`'s own comment on "the numbers behind this"
  // already covers a multi-analysis row this way: "the rest via that
  // analysis's own row on `by_analysis`, not duplicated here." Recede, don't
  // re-render -- the By-analysis tab is where the other readings live.

  // The caveat — the most important content on the screen. These sentences
  // already exist in the survey output; they used to sit three panes away in
  // the chat rail, which is not where the decision is made.
  // A caveat on a multi-analysis row names its analysis, or it does not
  // render (designer, SPEC-ACTIONABLE-AND-HONEST.md point 5): "This
  // analysis ran and found nothing" reads as a claim about whichever
  // number sits above it when three analyses answer one question, and it
  // is usually a claim about a DIFFERENT one. `f.note` is the analysis's
  // own prose, written as if it would be read alone -- attributed here,
  // not rewritten, since the sentence itself is correct and only its
  // referent was ambiguous.
  const attribute = (f, text) => (facts.length > 1 ? `${f.analysis_id} — ${text}` : text);
  const caveats = [];
  for (const f of facts) {
    if (f.note) caveats.push(attribute(f, f.note));
    if (f.state === PARTIAL && !f.note) {
      caveats.push(`${f.analysis_id} covered only part of what it measures.`);
    }
    if (f.state === NOT_ESTABLISHED && !f.note) {
      caveats.push(`${f.analysis_id} ran but could not establish a result.`);
    }
  }
  // A fact offered as evidence rather than as the answer says so, because
  // whether a resource REPLACES something you already have is a decision
  // about intent that no query settles.
  if (known.some((f) => f.evidence_only)) {
    caveats.push('Offered as evidence for a judgement, not as the judgement.');
  }
  lines.caveat = [...new Set(caveats)].join(' ');

  // Which facts have NOT run, when some have. A partly-answered row that
  // shows only the answered half is the confident-wrong-answer shape.
  const unrun = facts.filter((f) => f.state === NEVER_RUN).map((f) => f.analysis_id);
  if (known.length && unrun.length) {
    lines.caveat = `${lines.caveat} ${unrun.join(', ')} ${unrun.length === 1 ? 'has' : 'have'} not run, so this answer is partial.`.trim();
  }
  if (lines.unwritten.length) {
    lines.caveat = `${lines.caveat} ${lines.unwritten.join(', ')} ${
      lines.unwritten.length === 1 ? 'has' : 'have'} no written summary — the figures above are the raw measures.`.trim();
  }

  lines.runTimeUnrecorded = known.length > 0 && !lines.lastRun;
  lines.mermaid = factMermaid(env);

  return lines;
}
