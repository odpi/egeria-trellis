/** A stub of GET/POST /api/projects/{slug}/scope-events for the harness (brief 2a).
 *
 *  It keeps an in-memory record like the server's `resource_scope_events` (the newest event per locator is the
 *  current choice) and answers with the same view shape: rows, manifest, proposals. The container rule is the
 *  server's (a folder above an included file that is not itself included). The real server logic is tested in
 *  tests/test_resource_scope_events.py; this only lets the panes be driven through their real fetch calls. */
export const CANDIDATES = [
  { locator: 'docs', kind: 'folder', label: 'worthy', reason: 'top_level_structural_folder' },
  { locator: 'docs/a.md', kind: 'file', label: 'worthy', reason: 'well_known_file' },
  { locator: 'docs/b.md', kind: 'file', label: 'worthy', reason: 'well_known_file' },
  { locator: 'src', kind: 'folder', label: 'worthy', reason: 'top_level_structural_folder' },
  { locator: 'src/x.py', kind: 'file', label: 'not_worthy', reason: 'plain source' },
];

const ancestors = (loc) => {
  const parts = loc.split('/').slice(0, -1);
  if (!parts.length) return [''];
  const out = [];
  for (let i = parts.length; i > 0; i -= 1) out.push(parts.slice(0, i).join('/'));
  return out;
};

export function makeScopeStore({ candidates = CANDIDATES, published = {}, inEgeria = true, author = 'dan' } = {}) {
  const st = { events: [], posts: [], gets: 0, hold: null, failWith: null, published, inEgeria };
  const current = () => {
    const cur = {};
    for (const e of st.events) cur[e.locator] = e;
    return cur;
  };
  st.view = () => {
    const cur = current();
    const kindOf = (l) => (candidates.find((c) => c.locator === l) || cur[l] || { kind: 'folder' }).kind;
    const inc = Object.keys(cur).filter((l) => cur[l].choice === 'include');
    const files = inc.filter((l) => kindOf(l) === 'file').sort();
    const folders = inc.filter((l) => kindOf(l) !== 'file').sort();
    const containers = new Set(files.flatMap(ancestors));
    folders.forEach((f) => containers.delete(f));
    const locs = new Set([...candidates.map((c) => c.locator), ...containers, ...Object.keys(cur)]);
    const rows = [...locs].sort().map((loc) => {
      const c = candidates.find((x) => x.locator === loc);
      const ev = cur[loc];
      const choice = ev ? ev.choice : '';
      return {
        locator: loc, kind: kindOf(loc), label: c ? c.label : 'container', reason: c ? c.reason : '', candidate: !!c,
        choice, source: choice ? ev.source : '', proposal_rule: choice ? ev.proposal_rule : '',
        by: ev ? ev.author : '', at: ev ? ev.changed_at : '', cleared: !!ev && !choice,
        role: containers.has(loc) ? 'container' : '', proposed: !!c && c.label === 'worthy' && !choice,
        published: published[loc] || null,
      };
    });
    const cands = rows.filter((r) => r.candidate);
    return {
      slug: 'egeria_git', in_egeria: st.inEgeria, rows,
      proposals: rows.filter((r) => r.proposed).map((r) => r.locator),
      manifest: {
        files: files.length, folders: folders.length, containers: containers.size,
        items: files.length + folders.length + containers.size, chosen: [...files, ...folders].sort(),
        container_locators: [...containers].sort(),
        not_selected: cands.filter((r) => !r.choice && !r.proposed).length,
        proposals_not_accepted: cands.filter((r) => r.proposed).length,
        left_out: cands.filter((r) => r.choice === 'leave_out').length,
        published_earlier: Object.keys(published).length,
      },
    };
  };
  /** Answer one request, or return null when the URL is not a scope-events URL. */
  st.handle = async (u, method, body, ok, err) => {
    if (!u.endsWith('/scope-events')) return null;
    if (method === 'GET') { st.gets += 1; return ok(st.view()); }
    st.posts.push(body);
    if (st.hold) await st.hold;
    if (st.failWith) return err(st.failWith.status || 500, st.failWith.detail || 'boom');
    for (const e of body.events) {
      st.events.push({ ...e, choice: e.action === 'clear' ? '' : e.choice, author, changed_at: new Date().toISOString() });
    }
    return ok({ ...st.view(), written: body.events.length });
  };
  return st;
}
