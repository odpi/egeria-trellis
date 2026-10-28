# Resource Explorer — work timeline, 2026-08-30 to 2026-09-02

**Status: source material, not a design document.** A commit-derived narrative of one work window, kept as raw material for write-ups. Not superseded — it does not describe current state and is not meant to.

Raw material for blog posts. Built from `git log` on `trellis` (`main`,
currently 4 commits ahead of `upstream/main` — an extension-guide doc
sequence, `9b2573a..c3bf19f`, not otherwise referenced below). Scope: 171
non-merge commits touching `packages/resource-explorer` in the window,
filtered down to the ones that carry a measurement, a defect, or a shipped
decision. Every number below is quoted verbatim from the commit message
named beside it, or is a re-run of a `git log` command against this repo —
nothing is estimated.

## 1. Day-by-day narrative

### 2026-08-30 — outbox, survey flows, the acquisition cache

The day's spine is the **outbox**: a queue for writes to Egeria, built and
measured in sequence (`dcb8e8f` "steps 1-2 are already built, and measure
the volume"; `8de7ff8` an outbox drained on the existing scheduler loop;
`d9005e5` retention and dead letters; `8503d8c` repo publisher writes
through it; `18241cd` investigation memberships too, plus a Publish Queue
UI). The volume estimate that justified all of it was wrong and got
corrected the same day (`dee07af`, see §3).

In parallel, a run of "silent step" fixes: fourteen-plus commits
(`210e97f`, `0d47dfa`, `a059e01`, `e1789a8`, `6384ce2`, `368cbe5`, `80cc15c`,
`19ace88`, …) each making one survey step report what it actually achieved
instead of going quiet on an empty or declined result, culminating in
`b483e58`: "Step-outcome coverage complete: 34 of 34 sub-surveyors."

The performance thread starts here too: `63e7ec6` found git rename
detection was fetching blob content through a treeless clone (41s → 0.03s);
`a7e5364`/`f8710ef` then cached the acquired artifact itself (22.6s → 1.3s
warm); `17ff750` measured whether the `max_files=50` cap on that work was
still justified (it was, but not for the reason previously assumed). Also:
`d786c36` dropped four superseded per-analysis tables; `5ad469d` tombstoned
188 orphaned component scopes no ordinary survey run could ever reach;
`f34d3c5`/`782a34c` materialized accepted architecture proposals as real
Egeria SolutionComponents (Curate, slice 1); `4bd5e32` deliberately broke
`availability` from deriving off `run_time`.

### 2026-08-31 — the redeploy, verification catching its own gaps, Gradle

Redeploy day. `7c84608` added `verify_perspectives`, a check that had never
existed for whether the 12 Perspective elements actually exist in the live
platform — and its first live run was itself wrong in the dangerous
direction (`f2ba8f1`, see §2). `98dd963` closes the loop: "the redeploy is
done, and the measurement error is the lesson."

Elsewhere: `946a1b3` added Gradle to the dependency parser (Egeria itself
had zero dependency rows before this — see §3); `1486f03` found every
Parquet/Feather/Arrow column had been reporting `null_pct: 0.0` without
measuring anything; `b75034e` and `f00a0f7` are an audit of all 15
under-200-character catalog descriptions against what the code actually
does, catching two more instances of the same overclaim shape; `83381f1`
found a 30-second stage load was 59 sequential round trips; `dbb5e7b`,
`212f899`, `1e07b01` build out Phase 2 of the proposal/curation flow
(deliberately left unwired); `ce200f4` persisted docs-as-source's unmatched
terms and found the tests fail once measured; `4fb4807`/`26320f8` regroup
the admin UI from nine flat tabs to grouped-by-question.

### 2026-09-01 — questions, fact answers, the ruleset gate, annotation linking

The busiest single day. Three threads converge on "the number was computed
and never reached the user":

- `77605f1` adds two new catalog questions and, walking them end to end,
  documents the two failure modes that make a question look wired when it
  is not (§2). `6491e68`, `ddadef8`, `e9b9db7` are the fixes for the three
  specific fields those failure modes named — lines of code, complexity,
  documentation coverage (§3).
- `c88fd4d` fixes the chat surface itself: a real answer sentence was being
  computed correctly and then buried under a collapsed "internal dialog"
  line in the UI (§2). `5acaf88` and `6ca1afa` are the same surface's
  earlier passes that day (answer from measured values, not a count; fix
  display names; offer Run/Schedule).
- `f44a5e1` is the day's largest single fix: the secret scanner was
  running with none of its vendored ruleset's per-rule gates applied,
  live-measured at 48,581 → 5 matches on the same repo (§3, §2).

Also: `f364657`/`1ecb286` shipped Phase 0–2 of annotation-to-annotation
linking (measure the relationship type's link semantics against live
Egeria first, then capture GUIDs, then link within a run); `83e22af` fixed
the outbox retrying writes that had already succeeded, discovered live
against the ~48,500 writes `f44a5e1`'s bug had queued; `6774f5e` shipped
RFA dismissal (suppress-with-visibility, keyed by finding content, never a
delete); `313b47e` fixed the RFA drawer mislabeling passing checks;
`c7390ea`/`cb99d72`/`fa68c96` closed an auth posture gap between two
feedback surfaces; `19833d1`/`8f4e4ca` fixed and then extended a
neutral-chat-vote scoring landmine; `e091992`/`e8a8c6d` warmed survey
caches at boot and put a measured cost on doing so; `0e8514d` registered
four GAP-analysis surveyor modules built standalone the day before
(`33aef6c`); `46a77c2` resolved Gradle dependency versions with
provenance, following up on 08-31's Gradle parse.

### 2026-09-02 (partial day)

Four commits, all documentation: `9b2573a`/`ab809f4` write and refine a
guide to extending RE (questions, steps, annotation types, survey types);
`68bc957`/`c3bf19f` correct an internal analysis of step-vs-implementation
duplication, reclassifying it as a trade-off and marking one question
explicitly open rather than answered.

## 2. Recurring defect shapes

Four shapes recur across this window, each with real instances — not
imposed from outside, but read off the commits themselves.

**A correct number attached to the wrong claim, or the wrong population.**
- `dee07af`: the outbox design doc, two commit messages, and a claim made
  to another session all quoted 13,813 as "one blueprint publish" — it was
  actually one repo's *all-history* count spread over 14 runs. One run's
  real proposal is 466–2,092 components. Caught by asking "over how many
  runs is that spread?", not by re-measuring — re-measuring reproduces the
  same correct-but-mislabelled number.
- `6491e68`/`77605f1`: `ingestion_lines_of_code` genuinely counts every
  newline in every text-suffixed file; the name and every downstream
  reader treated it as source-code line count.
- `f00a0f7`: `documentation_coverage`'s `collection_present` signal reads
  *our own ingested pgvector collections*, not the project's published
  docs — so an unread repo scores as poorly documented.
- `17ff750`: raising `max_files` looked like a quality/noise control; it
  is purely additive at every level tested (20x cap range: components move
  13%, pairs move 144x) — it is a volume/cost cap, not a signal filter,
  and the name invited the wrong mental model.
- Within `946a1b3`'s own fix: an unresolved Gradle variable was first
  recorded as the literal string `${jacksonVersion}` — a value, syntactically
  present, describing nothing that was actually resolved.

**Built into a surface that nothing downstream reads.**
- `77605f1` names this directly as failure mode 1: `api_structure`'s
  surveyor derives a complexity summary from
  `project_code_symbols.complexity` and persists only
  `symbol_count`/`relationship_count`/`by_language` — the number is
  computed at survey time and never reaches the question layer. `ddadef8`
  is the fix.
- `e9b9db7`: `project_code_symbols.docstring` is populated at ingestion
  (3,767/6,752 = 55.8% on egeria-python); `documentation_coverage` never
  read it, scoring instead off five signals none of which touch whether
  code is documented.
- `f364657`'s Phase 1: `publish_annotations()` had captured each
  annotation's GUID and discarded it at the return statement for an
  unknown period before this fix.

**Absence rendered as a confident verdict instead of "not measured."**
- `f2ba8f1`: `verify_perspectives` reported "9 of 12 perspectives are
  absent" on its first live run; the true answer was 12 of 12 present. A
  displayName fallback matched elements of the wrong type. Framed
  explicitly: "Under-reporting an absence is worse than over-reporting
  one, because '9 missing' reads as 'three are fine, stop looking.'"
- `1486f03`: every Parquet/Feather/Arrow column ever profiled reported
  `null_pct: 0.0` — "0.0 is the value that means 'measured, and clean,'
  so the absence was rendered as the most reassuring possible finding."
- `a059e01`: `security_features` emitted nothing for 54 of 60 catalogued
  repos (GitHub withholds `security_and_analysis` from non-admins) — "the
  survey report and the catalog were silent about security for 90% of the
  corpus... silence about security reads as 'nothing to report' rather
  than 'we were not allowed to look.'"
- `b23b75b`: `cve_scan` declining to run (no dependency versions
  available) and `cve_scan` never having executed produced the identical
  downstream signal — the NOTHING_FOUND/NEVER_RUN conflation the
  codebase's own `result_status.py` module exists to prevent, found in a
  place that doesn't use it.
- `e9b9db7` again, the guard against this shape generalizing badly: a
  Go-dominant repo must report "not established," never "0%," when the
  extractor can't read the language at all.

**Tests green for the wrong reason.**
- `f44a5e1`: the first known-negative test for the secret-scan fix used
  the project owner's real leaked-looking line — but *both* new gates (entropy and
  allowlist) independently caught it, so deleting either gate alone still
  passed, and the suite would have shipped with half the fix silently
  missing. Replaced with two cases, each isolating one gate.
- `e9b9db7`: the first coverage-regression test compared two repos that
  already differed in coverage, which also passed against a break that
  shifted both equally (a flat "+1 if any measurable language" bonus).
  Replaced with a test pinning the score's exact composition.
- `a059e01`: the author's own edit-verification step used `ast.parse`,
  which accepted a file with duplicate keyword arguments — an error only
  `compile()` catches. Surfaced as 37 unrelated collection errors before
  being caught and switched.
- `946a1b3` (adjacent case): `cve_scan` already correctly flags a range or
  unparseable version — which meant it would have "correctly" rejected
  `${jacksonVersion}` as unparseable, "a true statement about the wrong
  thing," before the parser was fixed to record it as an empty version
  instead.

## 3. Numbers worth quoting

| Number | What it measures | Commit |
|---|---|---|
| 48,581 → 5 | Secret-scan matches on egeria-python, same repo/1,842 files/ruleset commit, before vs. after applying the vendored ruleset's per-rule entropy/allowlist/stopword gates | `f44a5e1` |
| 48,577 of 48,581 | Of the pre-fix matches, how many were one rule (`generic-api-key`) | `f44a5e1` |
| 130 of 222 | Rules in the vendored gitleaks.toml that declare an entropy threshold | `f44a5e1` |
| 26 of 130 (→107 active) | Entropy-declaring rules skipped because they use RE2-only syntax Python's `re` can't compile — a standing, disclosed under-coverage | `f44a5e1` |
| 1,118,195 vs. 156,902 (14%) | `ingestion_lines_of_code` (every newline, every text-suffixed file) vs. real Python code lines on egeria-python | `6491e68` |
| 1,118,195 vs. 156,297 (14%) | Same comparison, restated three commits later with a corrected code-line count | `77605f1` |
| 40.7% / 28.2% | Share of egeria-python's counted lines that are JSON / Markdown | `6491e68` |
| 7.5% vs. 69.3% | Docstring coverage on milvus (Go-dominant): naive (all languages, 5,968/80,094) vs. guarded to measurable languages only (5,968/8,607) | `e9b9db7` |
| 55.8% / 94.4% | Public-symbol docstring coverage measured directly from `project_code_symbols.docstring`: egeria-python (3,767/6,752) vs. egeria (33,750/35,745) | `e9b9db7` |
| 0.32 vs. 2.69 | Cyclomatic-complexity average on milvus: naive across all languages (n=71,798) vs. guarded to Python only (n=8,662) | `ddadef8` |
| 22.64s → 1.28s | Cold vs. warm acquisition time for one repo checkout, after caching the acquired artifact instead of re-cloning every run | `a7e5364` |
| 110.5s → 30s → 14.4s | Full acquisition route for `egeria_python_git`: original, after the exact-rename fix, after the artifact cache | `a7e5364` |
| 41.09s → 0.03s | `git log` cost for co-change analysis on a cold treeless clone: default inexact rename detection vs. `--find-renames=100%` | `63e7ec6` |
| 86 | Separate `git-upload-pack` network round trips inside one `git log` call, caused by inexact rename detection fetching blob content | `63e7ec6` |
| 92.1s vs. 2.8s | `architecture_recovery`'s coupling step against a treeless-clone route vs. the same step against a local checkout | `1904171` |
| ~2,100 vs. ~14,000 | Corrected outbox volume estimate (per-run Egeria writes) vs. the earlier figure that was actually a 14-run all-history total | `dee07af` |
| 48,583 → 7 | Egeria writes queued by one Committed Secrets run before the ruleset fix, vs. after (same repo) | `83e22af` |
| 39,603 of 48,113 | Rows matched by a hand-written DELETE cancelling wrongly-queued outbox writes — count only known because it was printed before deleting | `83e22af` |
| 165 / 23 | Orphaned component scopes withdrawn by the tombstoning script on egeria_git / egeria_workspaces_git | `5ad469d` |
| 1035 → 870 | egeria_git's enumerated component-scope count after tombstoning; DEPLOYMENT perspective 35 → 8 | `5ad469d` |
| p50=6, p90=55, max=2796 | Distribution of co-change pairs per commit across four real repos, used to validate `max_files=50` sits near p90 (89.8% of pair-bearing commits kept) | `17ff750` |
| 703 → 820 (13%) vs. 4,151 → 597,225 (144x) | egeria's components vs. pairs, moving `max_files` across a 20x range — evidence the cap is a volume control, not a quality one | `17ff750` |
| 85 | Dependency coordinates recovered on Egeria's own build.gradle files, up from 0, after adding Gradle parsing | `946a1b3` |
| 84 of 85 | Of those, coordinates with no inline version (resolved via BOM instead) — recorded as empty rather than dropped | `946a1b3` |
| 216 | Dependency rows produced on a full `egeria_git` survey after the Gradle parser landed (was 0) | `b23b75b` |
| 30s → 0.06s / 0.16s | `cochange` end-to-end runtime on a cold treeless clone, and on a real `egeria_git` checkout, before vs. after exact-rename detection | `63e7ec6` |

## 4. Candidate blog angles

1. **"The number was right; the label was wrong."** A single defect
   shape — a correctly-computed figure attached to the wrong claim or
   population — recurs at every layer of this system: a volume estimate
   quoted from the wrong denominator (`dee07af`), a line-count field named
   for what it doesn't measure (`6491e68`, `77605f1`), a coverage signal
   that measures our own ingestion instead of the subject
   (`f00a0f7`), and a "quality control" cap that is actually a cost
   control (`17ff750`). The fix in every case was re-deriving the number a
   second way, not re-running the same measurement.

2. **"Silence is not zero."** Four independent surfaces —
   perspective verification (`f2ba8f1`), Parquet null rates (`1486f03`),
   security-features detection (`a059e01`), and CVE scanning
   (`b23b75b`) — each collapsed "we didn't measure this" into the exact
   value that means "we measured it and it's fine." One commit
   (`f2ba8f1`) states the general principle outright: under-reporting an
   absence is worse than over-reporting one, because it reads as partial
   good news instead of a missing measurement.

3. **"A 48,581-match false positive is a ruleset problem, and it cascades."**
   `f44a5e1` traces one over-broad secret scan back to three ungated
   loader fields; the bug's blast radius shows up two commits later in the
   outbox layer (`83e22af`), which had queued ~48,500 Egeria writes off
   the same bad run and needed a hand-written DELETE against live data to
   recover. One measurement error propagating through a pipeline with no
   caps at any stage.

4. **"Verified in production, on purpose."** Several of this window's
   most consequential fixes (`f2ba8f1`, `946a1b3`, `83e22af`, `b23b75b`,
   `c88fd4d`) were caught by running the real thing against live data or a
   live redeploy rather than by code review or a pre-written test — and in
   `a059e01` and `f44a5e1`, the verification step itself turned out to be
   weaker than assumed (`ast.parse` vs. `compile()`; a known-negative test
   both new gates independently satisfied).

5. **"What got measured and deliberately not built."** Several sizeable
   pieces of work stopped at measurement or a standalone module by
   design: `53736a0`/`1904171` measured `architecture_recovery`'s true
   cost (100s+, then isolated to acquisition) without re-tiering it, an
   explicit maintainer call left open; `f364657` shipped only Phase 0–1 of
   annotation linking, with Phase 2's cross-run matching named and
   deferred; `212f899` built a Phase 2 proposal builder "deliberately NOT
   wired"; `83e22af` logged an unbounded one-Egeria-element-per-finding
   pattern and a missing purge path for queued work as backlog rather than
   fixing them same-day.

## Sourcing note

All figures above are quoted from `git log -1 --format=%B <sha>` output on
this checkout. Nothing here is rounded or estimated; where a commit's own
number changed between two commits (the 14% line-count figure), both are
listed rather than reconciled.
