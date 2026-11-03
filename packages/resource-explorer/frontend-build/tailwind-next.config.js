/** Tailwind build for the UI (served at `/` and `/next`) — skin 1c, "dark chrome,
 * paper content". It scans static/next/ and emits static/next/tailwind-next.css.
 * (The Classic UI had its own tailwind.config.js and tailwind.css; both were
 * deleted with Classic, 2026-10-21.)
 *
 * THE VALUES NOW LIVE IN static/next/themes.css (RULING-THEMES-PORTAL-DARK-LIGHT.md,
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
module.exports = {
  content: ['../resource_explorer/web/static/next/**/*.{html,js}'],
  theme: {
    extend: {
      colors: {
        // ── Chrome (dark): nav, sidebar, right rail ──────────────────
        chrome: 'rgb(var(--c-chrome) / <alpha-value>)',              // ground; one step below neutral-900
        'chrome-alt': 'rgb(var(--c-chrome-alt) / <alpha-value>)',        // right rail / secondary chrome ground
        'chrome-surface': 'rgb(var(--c-chrome-surface) / <alpha-value>)',    // selected row, raised block
        'chrome-line': 'rgb(var(--c-chrome-line) / <alpha-value>)',       // borders and dividers on chrome
        'chrome-line-soft': 'rgb(var(--c-chrome-line-soft) / <alpha-value>)',  // inner hairlines within a panel
        'chrome-ink': 'rgb(var(--c-chrome-ink) / <alpha-value>)',        // primary text on chrome
        'chrome-muted': 'rgb(var(--c-chrome-muted) / <alpha-value>)',      // secondary text on chrome — 6.7:1.
                                        // Do not go darker. There is no dim
                                        // role below this one.
        accent: 'rgb(var(--c-accent) / <alpha-value>)',              // strokes, borders, underlines
        'accent-on-dark': 'rgb(var(--c-accent-on-dark) / <alpha-value>)',    // accent TEXT on chrome
        'accent-deep': 'rgb(var(--c-accent-deep) / <alpha-value>)',       // accent border on chrome (accent-800)

        // ── Paper (light): the content pane ──────────────────────────
        paper: 'rgb(var(--c-paper) / <alpha-value>)',
        'paper-surface': 'rgb(var(--c-paper-surface) / <alpha-value>)',
        rule: 'var(--c-rule)', // hairline divider
        ink: 'rgb(var(--c-ink) / <alpha-value>)',
        // #4a463e, not #605d5d. Two reasons, and the second is why a palette
        // that measures fine was genuinely too light in place:
        //
        //  1. Drift. "No muted tokens on content lines" was settled in the
        //     fix round, and secondary greys crept back onto text that
        //     carries meaning — analysis ids, dates, descriptions.
        //  2. Perception. The dark chrome SURROUNDS the paper pane, and after
        //     the eye adapts to a dark surround, mid-greys on white read
        //     lighter than they measure. This pane wants to be set a step
        //     firmer than a normal light UI, not the same.
        //
        // ~9:1 on paper. Informational text goes no lighter than this.
        'ink-muted': 'rgb(var(--c-ink-muted) / <alpha-value>)',
        'accent-ink': 'rgb(var(--c-accent-ink) / <alpha-value>)',        // accent TEXT on paper; #b68235 is
                                        // not legible at body size
        'accent-tint': 'rgb(var(--c-accent-tint) / <alpha-value>)',       // selected-row tint
        // 3.05:1 on paper. Was #d7d3d3 at 1.33:1 — a chip outline that faint
        // is a boundary you cannot see, and the round's done-criteria put a
        // floor under anything carrying meaning.
        'rule-strong': 'rgb(var(--c-rule-strong) / <alpha-value>)',

        // ── Cards and tiles (PLAN-CARDS-AND-TILES.md §1) ─────────────
        // The fill and the border of a card, per look. The border is stronger than the Portal's
        // (1.9:1 on the ground in Dark and Light, 1.5:1 in Paper).
        card: 'rgb(var(--c-card) / <alpha-value>)',
        'card-line': 'rgb(var(--c-card-line) / <alpha-value>)',

        // ── State roles ──────────────────────────────────────────────
        //
        // Hue is BACK, alongside the glyph rather than instead of it. The
        // mono-only first pass applied the design system's constraint where
        // it does not belong: a list of rows scanned for exceptions is
        // exactly where hue earns its keep.
        //
        // Two variants per role, for the same reason `ink-muted` and
        // `chrome-muted` are two tokens. ONE value cannot hold 4.5:1 against
        // both grounds — measured, not assumed: passing on paper needs
        // luminance <= 0.159, passing on chrome needs >= 0.222, and that
        // window is empty. Any single "state-ok" would fail on one ground.
        //
        // Measured contrast, each against its own ground:
        //   state-ok        5.82:1     state-ok-on-dark    8.44:1
        //   state-warn      5.87:1     state-warn-on-dark  8.40:1
        //   state-gap       6.47:1     state-gap-on-dark   6.94:1
        //
        // Gold is NOT a state role. It means "needs your attention" and is
        // used for the human-input row, links and held chips. KNOWN RISK:
        // `state-warn` and gold are both warm and within 1.02:1 of each
        // other in luminance, so they separate by hue alone. Tolerable only
        // because colour is never the sole channel here — the two states
        // carry different glyphs (○ vs ⚠) and different words in the legend.
        'state-ok': 'rgb(var(--c-state-ok) / <alpha-value>)',
        'state-ok-on-dark': 'rgb(var(--c-state-ok-on-dark) / <alpha-value>)',
        'state-warn': 'rgb(var(--c-state-warn) / <alpha-value>)',
        'state-warn-on-dark': 'rgb(var(--c-state-warn-on-dark) / <alpha-value>)',
        'state-gap': 'rgb(var(--c-state-gap) / <alpha-value>)',
        'state-gap-on-dark': 'rgb(var(--c-state-gap-on-dark) / <alpha-value>)',
      },

      // A 1.15x density scale. Airy by intent — the existing UI is tighter,
      // and mixing the two is the one thing a find-and-replace cannot reason
      // about, so /next commits to this scale throughout.
      spacing: {
        s1: '4.6px',
        s2: '9.2px',
        s3: '13.8px',
        s4: '18.4px',
        // s5 was missing (used by app.js's gap-s5/mb-s5/mt-s5 with no rule
        // ever compiled for it) -- added to fit the existing sN = N*4.6px
        // progression rather than changing the usages, per
        // TAILWIND-NEXT-FRESHNESS-CHECK-IMPLEMENTED.md.
        s5: '23px',
        s6: '27.6px',
        s8: '36.8px',
      },

      borderRadius: {
        // Per look (themes.css): Dark and Light take 6px for controls and 8px for
        // cards; Paper keeps 2px and 4px.
        sm: 'var(--r-sm)',   // chrome and dense controls
        md: 'var(--r-md)',   // default; cards
        lg: 'var(--r-lg)',
        pill: '11px',
      },

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
