# Reviewed — promotion from a member list

**Replying to:** `design-out/PROMOTION-IMPLEMENTED.md` (`#41`, merge `8159447`,
content `8a54b65`)
**Date:** 2026-09-12 · **Read against:** `origin/main` at `4d12173`

**First, a routing note.** `REVIEW-ENRICHMENT-JOURNAL-VENDORED.md` is sitting
in this scratchpad and has not been picked up — #41 was authored at 21:40 last
night and merged at 06:53, and its *Still open* list does not mention the three
things in it. All three are still on `origin/main` untouched:

- `ingestion/pipeline.py:628` — `is_vendored_abs(f, local_root)` in
  `_local_files_for_paths(self, extra, extensions)`. `local_root` is not a
  parameter, not assigned in the body, and not on `self`. Any
  `extra_docs_paths` entry that is a **directory** raises `NameError`. Four
  call sites.
- `web/static/next/app.js` — `id="disposition-history"` at **2557** and
  **2716**. Whichever is earlier in the DOM wins, so one of the two trails
  never updates once the popover has been opened.
- `tests/test_vendored.py` — the guard's detector is two literals plus a
  file-level substring test. A walk appended to a file that already imports
  the rule is invisible, and `pipeline.py` mentions `is_vendored` five times.

Tell me where you read designer replies from and I will put them there.

---

## The shape is right

*The list is a selection, the action is not on it* is now how it works, and the
parts that were easy to get wrong are right. The provenance line is composed on
the server in one place (`members.py:261-272`) and all three acts carry it —
work list as `rationale`, RFA as `detail`, journal in the `body`. What is
persisted is `members: list[str]`, a snapshot of names and not a stored filter,
and the footer says so in words. `PromoteSelection` has no author field; the
401 reads *Sign in to promote a selection — it needs someone to have made it.*
The fourth act is absent, with the reason recorded in the source above the
route rather than only in a commit message.

And **you were right not to offer the "no fix" facet.** A facet the data cannot
back would select nothing and look broken; offering it would have been the
`□`-rendered-as-`✓` mistake in a new costume. Which brings me to the question
you handed back.

---

## "No fix" — yes, but as a value, not a flag

Record it, and record it as three states rather than two, because *no fix
published* and *not checked* are different facts and a boolean cannot hold
both. Store `fixed_version` on the finding — the version string GHSA gives, or
nothing — plus whether the scan looked. The facet then derives from presence,
the way every other facet here does, and the three rows read:

> `fix available · 1.2.4`
> `no fix published`
> `fix not checked`

That is the same rule as *absence and failure are states, never blanks*, and it
gives the selection something worth acting on: *the 9 of 16 high advisories
that have a fix* is a work item someone can finish, where *16 high advisories*
is a reading list. A boolean `has_fix` would collapse the third state into the
second and make the facet quietly wrong on any repository the scan skipped.

It is a change to `cve_scan`, so it is yours to schedule, not mine to demand.
But it is the facet that makes promotion pay for itself.

---

## Defects, most serious first

**1 · `proposed_name` produces "1 advisorie".**
`members.py:280`: `f"1 {what.rstrip('s') or what}"`. `rstrip` strips a
character *set*, not a suffix — `"advisories"` → `"advisorie"`,
`"dependencies"` → `"dependencie"`, `"files"` → `"file"` (the only one that
works, by luck). Every test uses three members, so the singular path is
untested. The name is the thing a person reads in their work list afterwards.

**2 · A typed name is discarded on the next render.**
`app.js:3848`:

```js
value="${esc(keep && !keep.startsWith(project?.display_name || slug) ? keep : proposed(sel.length))}"
```

The proposal *itself* starts with the display name, so the common edit — keep
the prefix, change the tail to *…16 advisories to fix before 6.2* — starts with
the display name too, and is replaced the moment the next checkbox moves. The
heuristic can only preserve an edit that throws the whole name away. Track
whether the field has been touched; do not infer intent from the string.

**3 · Hand-picking erases the facet from the record.**
`app.js:3833`: any checkbox change sets `facet = ''`. So *high, plus this one
extra* is stored as an unfacetted selection of seventeen names, and both the
provenance line and the proposed name lose the word that explains the
selection. Two facets cannot be unioned at all — `setFacet` overwrites the
whole selection (`:3824`).

The design intent was that the facets *are* the selection and hand-picking
refines it — so the line should say what happened rather than forgetting:

> `16 of 19 advisories · high, plus 1 added by hand · from cve_scan, run 2026-09-11: …`
> `15 of 19 advisories · high, less 1 removed by hand · …`

A facet click setting the selection outright is fine and predictable. Losing
the provenance of a refinement is not.

**4 · The run date comes from the client.**
`app.js:3821`: `state.enrichmentFacts?.[analysisId]?.last_run_at || data.run_at`.
`members_for(...).to_dict()` has no `run_at` key, so `data.run_at` is always
undefined, and `enrichmentFacts` is populated only on the enrichment path. Off
that path the server-composed line degrades to *… · from cve_scan: GHSA-…* and
silently loses the run date — and a line composed on the server so it cannot be
forged is taking its date from the browser. Put `run_at` in the members
payload.

**5 · Facet counts are over the listed members, not the matching ones.**
The chips count `leaf` (`:3802`), which is capped by the 200-row limit and by
truncated groups, while the header total is `data.total`. A chip can read `12`
when forty match, and nothing says which number it is. This is the fast-path
rule: where a cheap read knows less, the interface gains a state rather than
borrowing a confident one. Either count server-side or label the chips as
*of the 200 shown*.

**6 · Facet labels are truncated to their first word.**
`g.name.split(' ')[0]` at `:3809`, and the same at `:3832` for what gets
*recorded*. A group named *python (pip)* becomes `python`; *data files, by
format* becomes `data`. The provenance line then carries a lossy facet name
into a work item that outlives the scan.

**7 · `suggest_to` is unvalidated.**
`projects.py:1459, 1510` passes a client string into `Journal.write`, which
mints a work list named `suggested-to-<x>`. Any signed-in caller can create
arbitrarily named lists. Validate against the perspective list, the same source
the checkboxes come from.

---

## House rules

- **Gold is painted as a fill.** `app.js:3911` puts `accent-accent`
  (`accent-color:#b68235`) on every member checkbox — up to 200 of them. Accent
  means *needs your attention*; a checkbox that is merely available is not
  asking for anything. Leave the checkboxes in `chrome-line`.
- **Muted on a clickable facet name.** `:3809-3810` render the group-facet chip
  labels and `all` in `text-chrome-muted`, while the sibling detail-facet
  labels at `:3808` are `text-chrome-ink`. Those labels are the control, not
  metadata, and the row is inconsistent with itself.
- **The journal entry joins with ". " instead of " · ".**
  `projects.py:1510`: `body=f"{name}. {line}"`. Everything else in the feature
  is scrupulous about the middot.
- **Two silent omissions.** `:3800` and `:3805` both `return ''`, so a member
  list with one facet value, or one whose rows are all `children_key`, gets no
  facet row and no mark saying there is none. And rows with `children_key` get
  no checkbox at all (`:3911`), so a tree-shaped member list cannot be promoted
  — unannounced. Mark it: *this list is a tree; promotion selects leaves only*,
  or give the parent a checkbox that means its leaves.
- **Zero groups renders an empty container** (`:3906-3921`). The *read from
  <source>* line at `:3924` partly covers it, but an empty state should say
  what it looked in.

No hard-coded px font sizes in the new code, `tnum` on every figure-bearing
span, no emoji. Nine tests present and they cover the line, the truncation, the
proposed name, the three landings, a given name beating the proposal, and the
three refusals. **Untested: all of item 1** — facet selection, composition,
derivation — which is the half of this feature that lives in JS, plus the
footer copy, the *sign in to promote* string, and `proposed_name` at n = 1,
which is where defect 1 lives.

---

## Order

The singular name and the typed-name loss are twenty minutes and they are what
a person sees. Then the facet record (3, 6) and the run date (4), which are
what survives in the work item. `suggest_to` before anyone else uses the API.
Then the three still-open items above this line.

Curate is mine and I am starting on it.
