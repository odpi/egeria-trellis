/* Egeria Advisor's Tailwind configuration: its palettes follow the look (A-U3).
 *
 * A classic script, loaded right after Tailwind's in-page build and /static/trellis-ui/tokens.js, as Tailwind's
 * own instructions for that build say: set `tailwind.config` before the page is processed.
 *
 * Every shade of the palettes EA uses points at a variable in ea_looks.css (`--ea-<palette>-<shade>`), which
 * names a role whose value depends on the look. So `bg-slate-800` is "a card" and `text-violet-300` is "accent
 * text" in Dark, Light and Paper alike, and EA's classes stay as they are. The shared tokens (chrome-*, ink,
 * accent-ink, s1..s8, ...) are added too, for the shared look menu and markdown, which use them. */
(function () {
  const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 750, 800, 900, 950];
  const palette = (name) => Object.fromEntries(
    SHADES.map((s) => [s, `rgb(var(--ea-${name}-${s}, var(--ea-${name}-${s === 750 ? 800 : s})) / <alpha-value>)`]));
  const accent = palette('violet');
  const ok = palette('emerald');
  const warn = palette('amber');
  const danger = palette('red');
  const t = window.trellisTokens || { colors: {}, spacing: {}, fontSize: {} };

  window.tailwind = window.tailwind || {};
  window.tailwind.config = {
    darkMode: 'class',
    theme: {
      extend: {
        colors: {
          ...t.colors,
          slate: palette('slate'),
          violet: accent, cyan: accent, sky: accent, blue: accent, indigo: accent,
          emerald: ok, green: ok, teal: ok,
          amber: warn, orange: warn, yellow: warn,
          red: danger, rose: danger, pink: danger,
        },
        spacing: t.spacing,
        fontSize: t.fontSize,
      },
    },
  };
})();
