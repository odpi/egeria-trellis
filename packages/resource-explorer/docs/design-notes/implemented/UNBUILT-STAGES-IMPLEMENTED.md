# Implemented: unbuilt stages render as built

**Replies to:** `DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md`
**Read against:** `main` at `831a55fc`.

---

**§3's fix, exactly as specified, all three sites in `next/app.js`:**

- `:581` (nav item) — `if (s.unbuilt)` → `if (!s.built && !s.frame)`.
- `:3001` (sub-tab strip) — `if (t.id === 'questions' || t.built)` →
  `if (stageDef?.built && (t.id === 'questions' || t.built))`, computing
  `stageDef` from `state.stage` at the point of use.
- `:5053` (pane message) — `if (stageDef?.frame || stageDef?.unbuilt)` →
  `if (stageDef?.frame || !stageDef?.built)`.

**Also added `built: true` to `understanding`** in `STAGES` (§3's "worth
checking" — it renders real charts via `loadChartsPane()`, unconditionally,
already; the flag was just never added when that landed).

**What I scoped out:** the "Start here" item about `renderWorkListPane`
being "never called and not among `app.js:26`'s imports" — checked and it's
wrong. Traced the full path: nav click → `state.workListIndex = true` →
`loadPane()` renders `workListIndexHtml()` with `data-open-wl` buttons per
`state.workLists` (populated by `listWorkLists()`, called at `app.js:1995`
and `:6834`) → click sets `state.workListSlug` → `loadPane()`'s other branch
calls `openWorkList()` (imported, `app.js:26`) → which calls
`renderWorkListPane` internally (`worklist.js:1864`). Every link in that
chain exists and is reachable. No code change made for this half of the
item — it was already correct, not a fix that landed.

**What I could not test:** no browser verification this session (Egeria is
the identity provider and I don't hold sign-in credentials) — the three
`STAGES`/`SUB_TABS` inversions are read carefully against the exact lines
the defect doc cited, but not click-tested. Worth a look once merged:
confirm the `investigation` frame stage's sub-tabs (if it has any) still
render correctly under the new `stageDef?.built` gate — `investigation` has
`frame: true`, not `built: true`, and I did not independently verify that
combination renders as intended beyond following §3's fix as written.
