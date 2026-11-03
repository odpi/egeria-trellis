/* csv-guard.js -- a credential never leaves the browser in a CSV.
 *
 * Owner ruling 2026-10-01/02: credentials never appear in a CSV, in or out. The
 * server never reads a credential-like column (batch_io.parse_csv_strict), but a
 * file a person picked can still HAVE one, and sending its text would put the
 * value on the wire. So the cells under any credential-like header are blanked
 * here, before the text is posted or kept; the HEADER stays, so the server can
 * still name the column once ("Credential column(s) ignored: db_password").
 *
 * The pattern is the same string as batch_io._CREDENTIAL_COLUMN_RE; a test pins
 * the two together. `connection_ref` is a reference NAME and is never one.
 */
const CREDENTIAL_COLUMN = /(passw|pwd|passphrase|secret|credential|token|api[_-]?key|private[_-]?key|connection[_-]?string|conn[_-]?str|(^|[_-])dsn($|[_-]))/i;

export function isCredentialColumn(name) {
  const n = String(name || '').trim().toLowerCase();
  if (n === 'connection_ref') return false;
  return CREDENTIAL_COLUMN.test(n);
}

/** Split `text` into physical-line-preserving records: each record is its raw
 *  cells (quotes kept) and the exact text between record boundaries. A newline
 *  inside quotes does not end a record. */
function records(text) {
  const out = [];
  let cells = [];
  let cell = '';
  let inQuotes = false;
  let raw = '';
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '"') inQuotes = !inQuotes;       // "" inside a quoted cell toggles twice: net unchanged
    if (!inQuotes && ch === ',') { cells.push(cell); cell = ''; raw += ch; continue; }
    if (!inQuotes && (ch === '\n' || ch === '\r')) {
      if (ch === '\r' && text[i + 1] === '\n') { i += 1; out.push({ cells: [...cells, cell], eol: '\r\n', raw }); } else {
        out.push({ cells: [...cells, cell], eol: ch, raw });
      }
      cells = []; cell = ''; raw = '';
      continue;
    }
    cell += ch;
    raw += ch;
  }
  if (cell !== '' || cells.length || raw) out.push({ cells: [...cells, cell], eol: '', raw });
  return out;
}

const unquote = (c) => String(c).trim().replace(/^"(.*)"$/s, '$1').replace(/""/g, '"').trim();

/** The same text with every cell under a credential-like header emptied. Blank
 *  lines and `#` comment lines pass through untouched (line numbers stay the
 *  file's own). Returns { text, columns } where `columns` names what was blanked. */
export function blankCredentialCells(text) {
  const recs = records(String(text || '').replace(/^﻿/, ''));
  let header = null;
  let drop = [];
  const dropped = [];
  const out = recs.map((r) => {
    const blank = r.cells.every((c) => !String(c).trim());
    const comment = r.cells.length && String(r.cells[0]).trimStart().startsWith('#');
    if (blank || comment) return r.raw + r.eol;
    if (!header) {
      header = r.cells.map(unquote);
      drop = header.map((h) => isCredentialColumn(h));
      header.forEach((h, i) => { if (drop[i]) dropped.push(h); });
      return r.raw + r.eol;
    }
    return r.cells.map((c, i) => (drop[i] ? '' : c)).join(',') + r.eol;
  });
  return { text: out.join(''), columns: dropped };
}
