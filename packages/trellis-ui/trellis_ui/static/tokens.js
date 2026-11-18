/* The Tailwind names of the looks' tokens, shared by RE and EA. The VALUES live in themes.css, one block per look;
 * this file only names them, so a class like `text-chrome-muted` or `p-s2` means the same in both apps.
 *
 * Read two ways, so there is one list:
 *   - RE's build (frontend-build/tailwind-next.config.js): require('.../trellis_ui/static/tokens.js')
 *   - EA's pages (Tailwind's in-page build): <script src="/static/trellis-ui/tokens.js"> sets
 *     window.trellisTokens, which the page's tailwind.config reads.
 * Colours are channel triples (`rgb(var(--c-x) / <alpha-value>)`) so `/20` opacity keeps working; `rule` is a full
 * colour. */
(function (root) {
  const c = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;
  const tokens = {
    colors: {
      chrome: c('chrome'), 'chrome-alt': c('chrome-alt'), 'chrome-surface': c('chrome-surface'),
      'chrome-line': c('chrome-line'), 'chrome-line-soft': c('chrome-line-soft'),
      'chrome-ink': c('chrome-ink'), 'chrome-muted': c('chrome-muted'),
      accent: c('accent'), 'accent-on-dark': c('accent-on-dark'), 'accent-deep': c('accent-deep'),
      paper: c('paper'), 'paper-surface': c('paper-surface'), rule: 'var(--c-rule)',
      ink: c('ink'), 'ink-muted': c('ink-muted'), 'accent-ink': c('accent-ink'), 'accent-tint': c('accent-tint'),
      'rule-strong': c('rule-strong'), card: c('card'), 'card-line': c('card-line'),
      'state-ok': c('state-ok'), 'state-ok-on-dark': c('state-ok-on-dark'),
      'state-warn': c('state-warn'), 'state-warn-on-dark': c('state-warn-on-dark'),
      'state-gap': c('state-gap'), 'state-gap-on-dark': c('state-gap-on-dark'),
    },
    spacing: { s1: '4.6px', s2: '9.2px', s3: '13.8px', s4: '18.4px', s5: '23px', s6: '27.6px', s8: '36.8px' },
    borderRadius: { sm: 'var(--r-sm)', md: 'var(--r-md)', lg: 'var(--r-lg)', pill: '11px' },
    fontSize: { provenance: ['0.875rem', '1.45'], caps: ['0.75rem', '1.3'] },
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = tokens;
  else root.trellisTokens = tokens;
})(typeof self !== 'undefined' ? self : this);
