/** Tailwind build for the UI (served at `/` and `/next`) — skin 1c, "dark chrome,
 * paper content". It scans static/next/ and emits static/next/tailwind-next.css.
 * (The Classic UI had its own tailwind.config.js and tailwind.css; both were
 * deleted with Classic, 2026-10-21.)
 *
 * THE VALUES NOW LIVE IN themes.css (packages/trellis-ui, shared with EA; RULING-THEMES-PORTAL-DARK-LIGHT.md,
 * 2026-10-20), one block per look (Dark, Light, Paper). This file holds the
 * token NAMES and points each at a CSS variable: a colour is
 * `rgb(var(--c-<name>) / <alpha-value>)` (channels, so `/20` opacity keeps
 * working), `rule` is a full colour value, fonts are `--font-*`, radii `--r-*`.
 * The Paper block is the old palette unchanged, and the comments below that
 * quote hex values describe Paper. Still ONE place for the values (themes.css);
 * tests/test_themes_contrast.py reads it and fails a pair that loses contrast.
 *
 * THE KEY STRUCTURAL FACT: there are two grounds. Chrome is dark, content is
 * paper. Muted text therefore needs TWO tokens — `chrome-muted` on dark,
 * `ink-muted` on paper. A single shared muted role cannot pass contrast on
 * both, and three attempts to make one work by adjusting greys are what
 * produced this split.
 */
const tokens = require('../../trellis-ui/trellis_ui/static/tokens.js');

module.exports = {
  // RE's files shared with EA (packages/trellis-ui) carry classes too (the look menu, markdown, tables): scanned,
  // or those classes would silently drop out of this sheet.
  // tokens.js is not scanned: it names the tokens, and its strings would be read as classes (accent-ink, ...).
  content: ['../resource_explorer/web/static/next/**/*.{html,js}', '../../trellis-ui/trellis_ui/static/**/*.js',
    '!../../trellis-ui/trellis_ui/static/tokens.js'],
  theme: {
    extend: {
      // The token NAMES are shared with EA (packages/trellis-ui/trellis_ui/static/tokens.js); the values are
      // themes.css's, per look.
      colors: tokens.colors,

      // A 1.15x density scale. Airy by intent — the existing UI is tighter,
      // and mixing the two is the one thing a find-and-replace cannot reason
      // about, so /next commits to this scale throughout.
      // The token NAMES are shared with EA (packages/trellis-ui/trellis_ui/static/tokens.js); the values are
      // themes.css's, per look.
      spacing: tokens.spacing,

      // The token NAMES are shared with EA (packages/trellis-ui/trellis_ui/static/tokens.js); the values are
      // themes.css's, per look.
      borderRadius: tokens.borderRadius,

      // Elevation is a whisper. Nothing here is a drop shadow.
      boxShadow: {
        sm: '0 1px 2px rgba(45, 43, 43, 0.14)',
        md: '0 3px 10px rgba(45, 43, 43, 0.16)',
        lg: '0 12px 32px rgba(45, 43, 43, 0.22)',
      },

      fontFamily: {
        // Headings, nav, labels, buttons. Interface headings cap at 600;
        // display sizes take the normal cut.
        // System sans in Dark and Light, Cormorant Garamond in Paper (--font-heading).
        heading: ['var(--font-heading)'],
        // Body, answers, evidence.
        // System sans in all three looks (--font-body); Lora is gone.
        body: ['var(--font-body)'],
        // Qualified names, ids, paths.
        mono: ['ui-monospace', '"SF Mono"', 'Menlo', 'monospace'],
        // Diagrams and charts. THE ONE PLACE THE TYPE SYSTEM IS DELIBERATELY
        // OVERRIDDEN: SVG text at small sizes in Lora or Cormorant is a bad
        // trade, so every node label, axis tick and legend entry takes the
        // mono stack instead. Exposed to JS as `--font-diagram` too, since
        // Plotly and Mermaid are configured in script, not in CSS.
        diagram: ['ui-monospace', '"SF Mono"', 'Menlo', 'monospace'],
        // The one glyph table's state marks (✓ ∅ ◐ ○ ◌ ? ⚠ ◔ ✕ ⏵ · □ --
        // glyphs.js, REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §1). Several
        // of these (◐ ◌ ∅ ◔) are not reliably present in default system
        // fonts off macOS -- the reply: "In a headless Linux browser, ◐
        // came out as a sliver until I set the font explicitly." The whole
        // vocabulary is pinned to this stack, not only the four named ones,
        // so a reader never sees one glyph in the body serif and its
        // neighbour in a fallback -- a state vocabulary should read as one
        // alphabet. `sans-serif` is the final, browser-default fallback if
        // every named symbol font is absent; the glyph and its word (via
        // `title`/`aria-label`) still carry the meaning even then.
        glyph: [
          '"Noto Sans Symbols 2"', '"Noto Sans Symbols"', '"DejaVu Sans"',
          '"Segoe UI Symbol"', '"Apple Symbols"', 'sans-serif',
        ],
      },

      // The sizes as used on the Questions screen, named by their role so a
      // reader can tell a question title from an answer line without
      // counting pixels.
      // IN REM, so the app's own text-size control moves every one of them.
      // An interface inside an embedded browser cannot rely on the browser's
      // zoom; these scale against `html { font-size }`, which the control
      // sets to 16 / 17.9 / 20px.
      //
      // The floors are the round's, and they are floors: nothing that carries
      // meaning goes below 13px, and caps go no lower than 12 because .09em
      // tracking costs legibility at small sizes on top of the size itself.
      fontSize: {
        brand: ['1.1875rem', '1.2'],      // 19
        intent: ['0.875rem', '1.2'],      // 14
        chip: ['0.8125rem', '1.3'],       // 13 — was 11.5
        resource: ['0.8125rem', '1.35'],  // 13 — was 12.5
        subtab: ['0.875rem', '1.2'],      // 14 — was 13
        name: ['1.625rem', '1.15'],       // 26
        question: ['1.0625rem', '1.25'],  // 17
        // 15px, not the handoff's original 14.5. Lora has a generous
        // x-height but is optically lighter than a grotesque at the same
        // size, and 14.5 was matching the current app's density rather than
        // choosing well — it read as too light on a real screen.
        answer: ['0.9375rem', '1.55'],    // 15
        // A number is not secondary: figures take the same ink and size as
        // body, and only their tabular treatment differs.
        caveat: ['0.9375rem', '1.55'],    // 15 — was 13
        // Dates, ids and analysis names. Informational, so this is the floor.
        provenance: ['0.875rem', '1.45'],  // 14 — was 13, was 11.5 (PLAN-CARDS-AND-TILES.md §5)
        // The big number of a tile. A token, in rem, so the text-size control scales it with everything else.
        figure: ['1.5rem', '1.1'],         // 24
        caps: ['0.75rem', '1.3'],          // 12 — was 11
      },

      letterSpacing: { caps: '.09em', kicker: '.08em' },
    },
  },
  plugins: [],
};
