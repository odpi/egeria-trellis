# RULING — Themes: the Portal's dark and light, with RE's Paper kept for one release (2026-10-20)

From the designer session, recording the project owner's decision of the
same day, with the slice for the builder. Read against main at d74719b2:
`frontend-build/tailwind-next.config.js` (22 color tokens, the fonts, the
radii), `static/next/index.html` (base styles, the login overlay),
`app.js` (`tokens()`, the text-size control), `admin/feedback.js`, and the
Portal's `static/app-bar.js` and page palettes in egeria-workspaces.
Drawing: `wireframes/ThemesExperiment.html`, the four renderings the owner
compared (open it directly in a browser).

**Decision (project owner, 2026-10-20):** RE offers three looks.

- **Dark**: the Portal's dark look (rendering 3). This is the default.
- **Light**: the Portal's light look (rendering 4).
- **Paper**: RE's current look with one change: body and detail text in
  the system sans, headings and labels still in Cormorant (rendering 2).
  It's kept **for one release**, to gather user feedback, and then
  dropped or kept on the evidence.

Why: the owner finds RE's detail text hard to read and likes the
Portal's clear contrasts. The measurements agree. RE's colors already pass
(muted ink 8.4:1), so the faintness comes from Lora at 13 to 15px and from
weak separation between surfaces, not from color. All three looks put
detail text in a sans, and the two Portal looks add the Portal's ground,
cards and type. Offering both Portal looks makes RE and the Portal one
family: the same palettes, and the same dark/light choice in the same
place.

## 1. The choice, where and how

**In the header, beside the text-size control** (`app.js`, `wireTextSize`),
as one small control with its current value:

> Look: **Dark** ▾  ·  Text: 100% ▾

Opening it offers Dark · Light · Paper, each with one line ("the Portal's
dark look" · "the Portal's light look" · "RE's paper look, kept for this
release"), and at the foot:

> Which reads best for you? **Tell us ›**

"Tell us" opens RE's existing feedback form (`admin/feedback.js`) with
the subject filled in ("Look: Paper").

- **Remembered per viewer** in browser storage, read before first paint
  (a few lines in `<head>`, as the Portal's `app-bar.js` does), so the page
  never flashes the wrong look. Wrapped in try/catch: no storage means
  Dark.
- **The login overlay follows the look too.** Today it uses inline colors
  on purpose, so it works even if the Tailwind build is broken. It keeps
  that independence by using `var(--token, <today's value>)`, with the
  fallback built in.
- **Not a per-user server setting yet.** That can come later if people
  move between machines. For one release, the browser is enough.

**Feedback the owner can read:** each change of look writes one activity
row ("look changed to Light"). The feedback page in Admin shows a count
per look: how many people are on each, and how many switched away from
each. With the comments from "Tell us", that's the evidence for the
end-of-release call on Paper.

## 2. The token map

The 22 color tokens keep their names, so none of the thousands of class uses
in `/next` changes. Each token's *value* comes from the active look.

| Token | Dark | Light | Paper (today's values) |
|---|---|---|---|
| `chrome` | `#111a30` | `#ffffff` | `#1b1a19` |
| `chrome-alt` | `#141e38` | `#f5f6fa` | `#201f1d` |
| `chrome-surface` | `#1d2a4f` | `#eef1f7` | `#2d2b2b` |
| `chrome-line` | `#0f3460` | `#d3d9e6` | `#444141` |
| `chrome-line-soft` | `#1a2747` | `#e3e7f0` | `#2d2b2b` |
| `chrome-ink` | `#e6e8ef` | `#1f2333` | `#f3f2f2` |
| `chrome-muted` | `#a3abc0` | `#4c5468` | `#9b9797` |
| `accent` | `#4f9cf9` | `#2563eb` | `#b68235` |
| `accent-on-dark` | `#7cb6ff` | `#1d4fc4` | `#e1ad66` |
| `accent-deep` | `#1d3f7a` | `#93b4f5` | `#5a3b0a` |
| `paper` | `#1a1a2e` | `#f5f6fa` | `#f3f2f2` |
| `paper-surface` | `#16213e` | `#ffffff` | `#eae9e9` |
| `rule` | `rgba(160,175,210,.20)` | `rgba(31,35,51,.12)` | `rgba(32,31,29,.16)` |
| `rule-strong` | `#6b7a9c` | `#7a8399` | `#8e8a8a` |
| `ink` | `#e6e8ef` | `#1f2333` | `#201f1d` |
| `ink-muted` | `#a3abc0` | `#4c5468` | `#4a463e` |
| `accent-ink` | `#7cb6ff` | `#1d4fc4` | `#7d5411` |
| `accent-tint` | `#1b2d52` | `#e8efff` | `#fff3e4` |
| `state-ok` | `#5cbf60` | `#256b29` | `#1d6b3f` |
| `state-ok-on-dark` | `#5cbf60` | `#256b29` | `#74c68d` |
| `state-warn` | `#f5b74e` | `#9a4a07` | `#9c4212` |
| `state-warn-on-dark` | `#f5b74e` | `#9a4a07` | `#f2a26a` |
| `state-gap` | `#b39dfa` | `#6d28d9` | `#5b4a9c` |
| `state-gap-on-dark` | `#b39dfa` | `#6d28d9` | `#a99ae0` |

**Where the values come from:** the Portal's own palettes (`--bg`,
`--card`, `--border`, `--text`, its blue, green, amber and violet) with
three deliberate departures, all toward more contrast:

- **Muted text is brighter than the Portal's**: `#a3abc0` in Dark, where
  the Portal uses `#8b93a7`, and `#4c5468` in Light, where it uses
  `#5b6376`. Muted text is exactly what the owner finds hard to read.
- **Light's green and amber are darker than the Portal's**, so status
  words pass 4.5:1 with room to spare.
- **Controls are the Portal's blue**, not its pink-red call-to-action
  color. RE keeps one color for "you can press this", and red reads as a
  warning.

**The `-on-dark` tokens mean "on chrome".** In Light, the chrome is
white, so those tokens carry the dark-ink values. The names are now
misleading. Renaming them to `-on-chrome` is a separate, mechanical
change, kept out of this slice so its diff stays readable.

**Fonts, per look:**

| | Dark | Light | Paper |
|---|---|---|---|
| headings, labels, tabs, buttons, caps | system sans | system sans | Cormorant Garamond |
| body, detail text, answers | system sans | system sans | system sans |
| names, ids, paths | mono (unchanged) | mono | mono |

Lora is no longer used by any look, so its two font files are dropped.
Cormorant stays while Paper exists.

**Corner radius, per look:** Dark and Light take the Portal's 8px for
cards and 6px for controls. Paper keeps today's 2px and 4px.

## 3. Contrast, measured

Every pair the screens use, measured per look (WCAG ratio). Text needs at
least 4.5, and strokes and outlines need at least 3.

| Pair | Dark | Light | Paper |
|---|---|---|---|
| ink on paper | 13.9 | 14.4 | 14.7 |
| ink on paper-surface | 13.0 | 15.6 | 13.6 |
| ink-muted on paper | 7.4 | 7.0 | 8.4 |
| ink-muted on paper-surface | 6.9 | 7.6 | 7.8 |
| ink-muted on accent-tint (selected row) | 5.9 | 6.6 | 8.6 |
| accent-ink on paper | 8.1 | 6.6 | 6.0 |
| state-ok on paper | 7.4 | 6.1 | 5.8 |
| state-warn on paper | 9.6 | 5.8 | 5.9 |
| state-gap on paper | 7.4 | 6.6 | 6.5 |
| chrome-ink on chrome | 14.1 | 15.6 | 15.6 |
| chrome-muted on chrome | 7.5 | 7.6 | 6.0 |
| chrome-muted on chrome-surface | 6.1 | 6.7 | 4.9 |
| accent stroke on paper | 6.1 | 4.8 | (n/a) |
| rule-strong on paper (outlines) | 4.0 | 3.5 | 3.1 |

**This table becomes a test**, so a later edit can't quietly fail one of
them. The test reads the three theme files, computes each ratio, and
fails a text pair under 4.5 or an outline pair under 3.

## 4. The cue rules, mapped (an addendum to the state-cue rulings)

The rules don't change. Only the colors that carry them do.

| Rule | Paper (today) | Dark and Light |
|---|---|---|
| the accent marks what you can press, and nothing else | gold | blue |
| "chosen" is the segment filled in ink | dark fill, light text | Dark: light fill, dark text. Light: dark fill, light text. Always ink, never the accent. |
| a lowered row means not included | muted ink across the row | the same: muted ink, never lighter than `ink-muted`. Lowering must not make text faint. |
| the left-edge rule means "needs you" | `rule-strong` | `state-warn` for a failure, `accent` otherwise |
| gold also means "needs your attention" (links, held chips) | gold | blue (links are controls). The ⚠ "needs you" row stays in ink with its glyph, so no meaning was carried by gold alone. |
| state glyphs take their state color | state tokens | state tokens (the table above) |
| charts and diagrams read the tokens | `tokens()` | the same, redrawn when the look changes (below) |

## 5. The slice

| Piece | Notes |
|---|---|
| Tokens as CSS variables | In `tailwind-next.config.js`, each color becomes `rgb(var(--tok) / <alpha-value>)` (channels, so the one `/20` opacity use keeps working). `rule` is a full color value. Fonts and radii become variables too. |
| Three theme files | `themes.css` with `:root[data-look="dark"]`, `[data-look="light"]` and `[data-look="paper"]`, loaded before the Tailwind sheet. Dark is the fallback when nothing is set. |
| Before first paint | A few lines in `<head>` set `data-look` from storage. |
| `index.html` | The base styles' and login overlay's 28 hex values become `var(--tok, <today's value>)`. |
| JavaScript | The 17 hex values in `/next` (6 in `worklist.js`) go through tokens. Charts redraw on a `re-look-change` event. `tokens()` already reads computed styles, so it picks up the new values. Mermaid gets the same treatment. |
| The control | Look ▾ beside Text ▾, with "Tell us ›". One activity row per change. Counts per look on Admin's feedback page. |
| Fonts | Drop Lora. Keep Cormorant for Paper. |
| The contrast test | §3's table as a test. |
| Rebuild | `tailwind-next.css` rebuilt; the freshness and class-coverage tests pass unchanged, since no class names move. |

**Not in this slice**, so the first change stays a reskin and nothing
else:

- **Bordered cards and big-number tiles** (the structural part of the
  Portal's look). Radius and colors arrive now. Cards and tiles come
  surface by surface, as each is next worked on: Understanding's At a
  glance first, then the Contents manifest, then the definition card.
- **Renaming `-on-dark` to `-on-chrome`.**
- **Classic.** It has its own styling and is being retired.

**The gate, by use:** the owner walks the same three screens in each look
(the Survey & analyses tab on coco_pharma, Curate's Contents tab, a chart
on Understanding), switching looks without a reload. He says, for each,
whether the detail text reads easily and whether anything turned faint or
vanished. Then one sign-in on a fresh browser opens in Dark.

## Left open

1. **The end-of-release call on Paper**, from the counts and the "Tell us"
   comments.
2. **Whether the look should follow the operating system's dark or light
   setting** on a first visit, instead of always opening in Dark. I'd add
   it once Paper's fate is settled, so there are only two looks to map.
