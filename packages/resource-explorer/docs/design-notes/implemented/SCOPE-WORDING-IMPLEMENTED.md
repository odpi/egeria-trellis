# Select-bar scope wording: implemented (2026-10-03)

Environment proof: `resource_explorer.__file__` =
/Users/dwolfson/localGit/egeria-v6/trellis-re-scope-wording/packages/resource-explorer/resource_explorer/__init__.py

## What changed (app.js `selectActionsHtml`, new `SCOPE_WORDING` constant)
- Investigation current: "＋ add to <name>" / "− remove from <name>"; name cut at 24
  characters (23 plus an ellipsis), full name in the title.
- None current: "＋ add to an investigation…" and "− remove from investigation", both disabled,
  title "Choose an investigation first", and the visible text "no investigation selected" in the bar.
- "save as work list…" now has the ellipsis and the quiet chrome border (no accent).
- No new "delete" text; no new Tailwind classes needing regeneration (coverage and freshness tests pass).
- Test: `resource-controls.test.mjs` "the Select bar scope wording: three states..." (red on old wording, green now).

## Provisional / not done
- Design wants the none-current add button ENABLED, opening an investigation picker (REPLY-DESIGNER-WORK-LISTS
  §4/§2). No such picker exists in the code (only the sidebar `<select>` and the "New investigation" dialog),
  and building one is W1 item 4, so the button stays disabled rather than enabled-and-inert.
- The "Added 3 to Customer 360, now your current investigation." note belongs with that picker.
- The "remove from investigation" string with none current is not design wording; one constant to change.

## Not verified
- Not looked at in a live browser; jsdom harness only.
