// jsdom harness for static/ea_panel.js (S4). Usage: node ea_panel_harness.cjs <jsdom node_modules dir> <ea_panel.js>
// Prints one JSON line of results; the Python test asserts on it.
const path = require('path');
const fs = require('fs');
const { createRequire } = require('module');
const req = createRequire(path.join(process.argv[2], 'noop.js'));
const { JSDOM } = req('jsdom');
const src = fs.readFileSync(process.argv[3], 'utf8').replace('export function createEaPanel', 'window.createEaPanel = function createEaPanel');

const EA = 'http://localhost:8880';
function setup({ token = 'jwt-abc', timeoutMs = 50 } = {}) {
  const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:8840/', runScripts: 'outside-only' });
  const win = dom.window;
  win.eval(src);
  const opened = [];
  const panel = win.createEaPanel({ advisorUrl: `${EA}/`, getToken: () => token, onOpenDraft: (r) => opened.push(r),
                                    doc: win.document, win, timeoutMs });
  win.document.body.append(panel.root);
  const posted = [];
  panel.frame.contentWindow.postMessage = (msg, target) => posted.push({ msg, target });
  const send = (data, origin = EA, source = panel.frame.contentWindow) =>
    win.dispatchEvent(new win.MessageEvent('message', { data, origin, source }));
  return { win, panel, posted, opened, send };
}
const out = {};
(async () => {
  let t = setup();
  out.src = t.panel.src;
  t.send({ type: 'ea.ready' }, 'http://evil.example:8880');
  out.wrongOriginIgnored = t.posted.length === 0;
  t.send({ type: 'ea.ready' }, EA, t.win);   // right origin, not our iframe
  out.wrongSourceIgnored = t.posted.length === 0;
  t.send({ type: 'ea.ready' });
  out.sessionSent = t.posted.length === 1 && t.posted[0].target === EA && t.posted[0].msg.type === 'trellis_auth'
                    && t.posted[0].msg.jwt === 'jwt-abc';
  t.send({ type: 'studio.openDraft', draft_ref: { kind: 'spec', name: 'Open Glossaries' } });
  t.send({ type: 'studio.openDraft', draft_ref: { kind: 'table', name: 'x' } });
  t.send({ type: 'studio.openDraft', draft_ref: { kind: 'spec', name: 'Evil' } }, 'http://evil.example:8880');
  out.opened = t.opened;
  out.askSent = t.panel.askAbout({ kind: 'spec', name: 'Open Glossaries' }) &&
                t.posted[1].target === EA && t.posted[1].msg.type === 'ea.askAbout';

  t = setup({ token: null });
  t.send({ type: 'ea.ready' });
  out.noTokenNothingSent = t.posted.length === 0;
  out.noTokenSays = t.panel.root.querySelector('.st-ea-status').textContent;

  t = setup({ timeoutMs: 30 });
  t.panel.load();
  await new Promise((r) => setTimeout(r, 80));
  out.unreachableSays = t.panel.root.querySelector('.st-ea-status').textContent;
  out.askWhenUnreachable = t.panel.askAbout({ kind: 'spec', name: 'x' });
  out.postedWhenUnreachable = t.posted.length;
  // S4 R1: sign-out unloads the frame; a new session loads it again and hands over the NEW user's JWT.
  let tok = 'jwt-first-user';
  const getTok = () => tok;
  t = (() => {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:8840/', runScripts: 'outside-only' });
    const win = dom.window; win.eval(src);
    const panel = win.createEaPanel({ advisorUrl: `${EA}/`, getToken: getTok, onOpenDraft: () => {}, doc: win.document, win, timeoutMs: 1000 });
    win.document.body.append(panel.root);
    const posted = [];
    // jsdom gives the frame a new window when its src changes: re-attach the spy after each (re)load.
    const spy = () => { panel.frame.contentWindow.postMessage = (msg, target) => posted.push({ msg, target }); };
    const send = (data) => win.dispatchEvent(new win.MessageEvent('message', { data, origin: EA, source: panel.frame.contentWindow }));
    return { win, panel, posted, send, spy };
  })();
  t.panel.load();
  t.spy();
  t.send({ type: 'ea.ready' });
  out.firstUserJwt = t.posted.length === 1 && t.posted[0].msg.jwt === 'jwt-first-user';
  tok = null;
  t.panel.unload();
  out.unloadedFrameSrc = t.panel.frame.getAttribute('src');
  out.unloadedReady = t.panel.state.ready;
  t.panel.sessionChanged();                       // no session yet: stays unloaded
  out.staysUnloadedWithoutSession = t.panel.state.unloaded && t.panel.frame.getAttribute('src') === 'about:blank';
  tok = 'jwt-second-user';
  t.panel.sessionChanged();                       // a new sign-in: EA loads again
  out.reloadedSrc = t.panel.frame.getAttribute('src');
  t.spy();
  t.send({ type: 'ea.ready' });
  out.secondUserJwt = t.posted.length === 2 && t.posted[1].msg.jwt === 'jwt-second-user';
  if (t.panel.state.timer) t.win.clearTimeout(t.panel.state.timer);
  console.log(JSON.stringify(out));
})();
