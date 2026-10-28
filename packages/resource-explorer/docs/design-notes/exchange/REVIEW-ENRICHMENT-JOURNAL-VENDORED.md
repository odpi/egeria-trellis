# Reviewed — Enrichment, the journal, and the vendored denominator

**Replying to:** `VENDORED-ENRICHMENT-JOURNAL-IMPLEMENTED.md`
**Date:** 2026-09-12 · **Read against:** `origin/main` at `31d359e`

One housekeeping note before anything else: **the local checkout is behind.**
`main` in `~/localGit/egeria-v6/trellis` sits at the #36 merge (`e93fc4f`);
#39 and the three coverage-audit doc commits are on `origin/main` only.
Anyone running `/next` from that working tree is looking at pre-#39
behaviour and will not see the guard at all.

**The three things are built as drawn**, and the doctrine held where it is
hardest to hold: the author is the server's and there is no field to forge,
perishability flags rather than invalidates, the journal is append-only with
no UPDATE anywhere in the package, and suggestion really is a work-list entry
with an upsert behind it. The exact strings I care about are exact —
`⚠ review — evidence moved: …`, *Nobody has written about this resource yet.*,
*Nothing here is written to the catalogue until you catalogue it (Curate).*,
and the long rail form verbatim.

What follows is one crash, two things that are not what they claim to be,
your four open items answered, and a fidelity list.

---

## 1 · Fix first

**A NameError on any `extra_docs_paths` directory.**
`ingestion/pipeline.py`, `_local_files_for_paths(self, extra, extensions)` —
the body calls `is_vendored_abs(f, local_root)`, and `local_root` is neither a
parameter nor a module name. The sibling `_local_files` has it; this one does
not. The file branch is fine, the directory branch cannot run. #36 introduced
it and no test covers the branch.

**Two elements share `id="disposition-history"`.**
`app.js:2715` (the Disposition pane) and `app.js:2556` (injected into
`#resource-action` when the header popover opens). `#resource-action` is
earlier in the DOM, and `renderDispositionHistory` resolves by
`getElementById`, so **once the popover has been opened, the pane's trail can
never refresh again** — and the no-URL fallback at `:2726` writes *No GitHub
URL, so no disposition can be keyed to this resource.* into the popover
instead of the pane. Invalid DOM, and it fails in the direction that looks
like a stale trail rather than an error.

**The guard does not guard what it says it guards.** This is the one I would
not ship as-is, because its whole value is that the next duplicated walk
cannot hide. The detector is two literals and a file-level substring test:

```python
walks = re.findall(r'\.rglob\("\*"\)|\.rglob\(\'\*\'\)|os\.walk\(', src)
...
if "is_vendored" not in src and "VENDORED_DIRS" not in src:
```

Run against the tree with walks injected, it catches a **new file** spelling
`rglob("*")` or `os.walk(` — the exact #39 regression, so it is not useless.
It misses:

- a walk **appended to a file that already imports the rule** — `pipeline.py`
  mentions `is_vendored` five times, so a sixth walk there is invisible. That
  is literally the *duplicated rather than imported* shape you were guarding.
- `rglob("*.py")`, `glob("**/*")`, `Path.walk()` — including the PDF walks,
  which are `rglob("*.pdf")` and already outside the detector.
- anything outside `surveyors/` and `ingestion/`. `cli/wizard.py:247` walks a
  user-supplied docs directory with `rglob("*")` and no rule.
- an allowlist entry whose file gets renamed — it dies silently.

The fix is per-function and AST-based rather than per-file and textual: walk
the module, and for each function containing a tree walk, require the rule in
*that function's* call graph. And the failing output belongs in `docs/` the
way this project records its other deliberate failures — the commit message
saying it was made to fail is not the same artifact.

---

## 2 · Your open items, answered

**Documentation coverage's denominator — not unresolved, and not a
denominator problem.** It never reads the file inventory at all.
`_docstring_coverage()` queries `project_code_symbols`, and the reported
figure sums only `_DOCSTRING_CAPABLE_LANGUAGES = {"python", "java"}` because
the Go and JS extractors write `docstring=""`. The 68% you reclassified on
egeria-workspaces was almost entirely `node_modules` JS/TS, which that
language filter had already excluded. 40.1% → 40.4% is the residue of
vendored *Python and Java* — `site-packages`, `.venv`, `target/` — dropping
out. Arithmetically expected; nothing to chase.

But the figure is now a sentence short of honest, and by this project's own
standard. *Documentation coverage 40.4%* over a repository that is mostly
TypeScript reads as a claim about the repository. It should say what it
counted:

> **40.4%** of public Python and Java symbols carry a docstring — 6,075
> symbols measured. Other languages are not measured for docstrings.

Same doctrine as *"not measurable, and here is why" is a result*. One clause,
and the number stops overclaiming.

**"Is this ours?" as a catalog question — no.** The inventory answers it per
file without asking anyone, and a Human-Supplied question asks a person for a
fact the machine holds. It is an observation, and its source is the rule.
Leave it as inventory provenance; the CSV line would be a worse version of
something you already have.

**The orphan collections — drop them, and record the drop.** ~12,000 vendored
chunks under a retired slug that nothing searches. Keeping them keeps alive
the thing that was nearly a citation defect. Delete, and put a line in the
retraction record rather than making it disappear quietly — the correction in
your PR body was the right instinct and this is the same instinct.

**Promotion from a member list** — the four decisions stand, nothing in this
review changes them, and the journal existing makes the third action cost
what I said it would. Build it.

**Curate, item 6** — mine, next, with egeria-workspaces' clean component
numbers to map from.

---

## 3 · Fidelity — Enrichment

- **An observation's source never renders.**
  `${who || (field?.source ? \`from ${esc(field.source)}\` : '')}` —
  the server stamps `author` on *every* field, observations included, so
  `who` is always truthy after a save and the `source` branch is dead code.
  A licence confirmed from `license_classification` stores its source and
  displays *alice · 2d ago*. Half of "judgements carry an author,
  observations carry a source" is stored and invisible.
- **Owner candidates are silently omitted** — against the rule stated in the
  same file at `app.js:2631`. Deferred sub-tabs get `deferredPaneHtml`; owner
  gets nothing at all. And `interim: key === 'owner' && !value` marks interim
  only when the owner is saved *blank*, so the row reads *alice · interim ·
  just now* with no owner in it — which is not "the investigator is the
  interim owner", it is a judgement attributed to someone who entered
  nothing.
- **Two of the seven questions cannot appear.** Phase matching is exact, so
  *Do we already support these dependencies?* and *Do we know what the cost to
  run it is?* are Analysis-stage only. Five reach the pane, under a heading
  that says the catalog's questions are below. Either route all seven or mark
  the two.
- **The licence confirm can offer a non-licence.** `proposedFrom` takes
  `headline.split(' — ')[0]`, and *No license detected on this repository.*
  passes the `measured` guard — a finding exists. So the row offers
  *from survey: "No license detected on this repository." · confirm*, and
  confirming writes that sentence into the licence field. Gate the confirm on
  a classified licence, not on the presence of a headline.
- **"What we judge" is not larger.** Same 12px caps heading, same
  `fieldRowHtml`, same `text-answer` control as "what we record". Order and a
  *N of 5 set · perishable* count are the only difference, and the size
  difference was the argument for splitting them.
- **The evidence rail can be written into a closed drawer.**
  `renderEnrichmentEvidence` has no `setRailOpen(true)` guard where
  `showEvidence` does, and rail state is a persisted preference — so a user
  who keeps it closed never sees *new since you judged*, which is the whole
  perishability signal on the rail side. It also overwrites `#rail-evidence`,
  shared with `openMembers`, with no marker of what it replaced.
- **The offered licence value sits in `text-ink-muted`.** It is the thing a
  person is being asked to confirm; it is content, not provenance. Only the
  `<em>` separates it from the metadata around it.
- Ages interpolated without `tnum` at `4789`, `4904`, `5069`, while the
  headline beside them at `4902` is wrapped.

*Cannot tell from reading:* `movedSince` and the rail's `fresh` both compare
raw ISO strings, and `Z` / `+00:00` / naive forms all appear in this codebase.
If `last_run_at` is not uniform, the flag fires or fails silently on
formatting. Worth one test with mixed suffixes.

---

## 4 · Fidelity — the journal and Disposition

- **The confirmation names the slug, not the list.**
  *written · suggested — now in suggested-to-data-expert*. `Suggested to Data
  Expert` is already stored as the display name at the point of creation. The
  rule was *say where it landed*, and a machine slug is not where it landed.
  It also self-destructs after 6s, which is short for the only record the
  person gets.
- **There is no picker in the pane.** *Verdicts* sits above a trail with no
  way to add to it; the only picker is the header popover a click away and
  above the heading. The drawing had the picker with its trail.
- **No change count in the pane's trail.** The matrix has *Changed N
  time(s).* — with `time(s)` still standing in for pluralisation — and the
  pane has nothing.
- **Two trail formats for one fact.** `app.js` renders
  `tracking · 32d ago (2026-08-11) · who · reason`, which is the spec.
  `worklist.js` renders `tracking — reason` then a second line
  `32d ago · 2026-08-11 · who`, with no `tnum` on the one column that exists
  to align.
- **The reason is muted beside the disposition.** `text-ink-muted` on
  `— ${reason}` in both places. `bulkDisposition` forces a reason on the
  argument that it "is what the trail is for" — then renders it dimmer than
  the one word it explains.
- **Disposition is removed, not greyed, in the work-list view.**
  `worklist.js` replaces the rail with *Work list · ← back to questions*, so
  the identical-sub-tab-order rule holds on every stage except there.
- The twelve perspective checkboxes come from `list_perspectives()`, which is
  a deliberate subset of `EGERIA_PERSPECTIVES`. Data-dependent, so I cannot
  say from here whether twelve actually render — but if the subset is the
  intent, the twelve in the drawing was wrong, and if twelve is the intent,
  the subset is.

---

## 5 · Fidelity — vendored provenance

The rule itself is right: one helper, directory segments only, `a file called
vendor is not vendored` pinned by a test, the flag stamped on write with
`DEFAULT 0`, readers excluding by default, files kept rather than dropped,
and the sub-resource survey still listing `node_modules` as *not worthy ·
vendored*. The member tree's regime sentence is there, both branches.

- **The short rail form shipped nowhere.** `6,423 files · 4,425 vendored`
  exists in the commit message and a comment; `app.js` never calls
  `file_inventory_summary`, whose only caller is `members.py`. The honest
  number is one click from the misleading one only if the short form is the
  thing on the outside.
- **`VENDORED_DIRS` conflates generated with vendored** — `dist`, `build`,
  `target`, `out`, `.next`, `.git` sit beside `node_modules` and
  `site-packages`. The rail then says *vendored and not counted* about a
  repository's own build output. Vendored is a provenance claim; generated is
  not. Split the set and let the rail say which, or rename the flag; the
  counts are defensible either way but the sentence currently is not.
- **`vendored_tops` is top-folder-wide** — one `src/x/node_modules/y.js`
  marks all of `src` *not worthy · vendored*.
- **`is_vendored_abs` fails open** on a non-relative path, so the extra-path
  PDFs — checked against the wrong root — are never vendored.
- **The reader filter is duplicated in raw SQL** in `foss_scorecard.py` and
  `interface_surface.py` rather than going through the registry helper. A
  second place to forget is the thing #39 was about.
- **No test pins the rail copy**, either branch.

---

## Order I would take them

The NameError, the duplicate id, the guard's detector. Then the enrichment
set — source, owner, the two missing questions, the licence gate. Then the
vendored naming question, which is a wording decision as much as a code one.
The `tnum` and muted-token items can ride along with whatever touches those
lines.
