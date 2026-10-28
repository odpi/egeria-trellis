# Consolidation: what is actually open, and in what order

**Status: DATED SNAPSHOT — 2026-08-24. Superseded; do not act on it.** It planned the work ahead of a PR that has since merged. Kept as a record of what was open on that date. Anything still live is in `Backlog.md`.

**Written 2026-08-24, at the point where `re/deferred-cleanup-followups` is 190 commits
ahead of `main`.** Purpose: decide what has to happen before a PR, what can run in parallel
after it, and which backlog entries are stale rather than open. Put in the repo rather than
said in a session, because two cross-session messages were lost to session turnover today and
the durable record is what reached people.

---

## 1. Where we are

| | |
|---|---|
| `origin/main` | `4ff3cdd`, last touched 2026-08-18 |
| `origin/re/deferred-cleanup-followups` | **190 commits ahead of main** |
| `origin/re/results-presentation` | identical to it (worktree at `../trellis-presentation`) |
| Test suite | 1948 passed, 10 skipped |
| CI | green, ~4.5 min, runs on `main` and `re/**`, with a real Postgres tier |
| Sessions | ~11 live; at least 4 have committed to the shared branch today |

**190 commits is the headline problem.** Everything below is downstream of it: a branch that
far from `main` cannot be reviewed as one PR in any meaningful sense, and every day it grows.

---

## 2. Before a PR — in this order, and only these

These are sequential. Each blocks the next.

1. **Stop the multi-writer churn.** Today four sessions committed to one branch through one
   shared checkout. Twice, two of us coordinated carefully and were surprised by a third who
   was not in the conversation (`852955f`, `c370fa8` and `6c0cb2a` all reached the branch
   without review by the people merging). Until writers are reduced to one, "consolidate" has
   no fixed point — the branch moves while you describe it. **This is the actual precondition,
   not a nicety.**

2. **~~Reconcile `.../trellis`~~ — done, and it exposed the real problem.** `6c0cb2a` (54 EA
   Curation templates) is now on origin: the project owner instructed a merge that left the commit alone,
   and it rode along. **Its author still has not chosen to publish it, and it is published.**
   That is the second commit from the same writer to reach the remote by someone else's push
   (`c370fa8` was the first).

   **Nobody knows who that writer is, and one confident guess was already wrong.** It was
   attributed to `egeria-advisor-7a` — including in the first version of this document —
   because the commit touches `packages/egeria-advisor/`. They checked and it is not theirs.
   Every session commits under the project owner's identity and signs with their key, so **nothing in commit
   metadata distinguishes sessions**, and topic-to-session mapping is a guess wearing the
   clothes of identification. The reflog narrows it only to "an EA-template writer committing
   directly into the shared checkout at 13:50:32 and 13:55:31" — concurrent and active, not a
   leftover.

   The sharpened version of §2.1: the sessions in worktrees caused each other no trouble all
   afternoon. **The one writer working directly in the shared checkout is the one whose
   commits keep being published by other people, and the one nobody can name.** That is the
   setup to change.

3. **Prune the backlog.** 33 entries read as open; several were closed by today's work and
   never marked (see §4). A reviewer cannot tell a live item from a finished one, and neither
   can we — which is the "lots of almost-closed issues" feeling.

4. **Decide the PR's shape.** 190 commits spanning the pgvector migration, the Egeria survey
   definitions, architecture recovery, the presentation layer and CI is not one reviewable
   change. Options, in increasing order of effort: one squash per theme; several stacked PRs
   off `main`; or merge as-is with the history intact and accept it is an integration branch,
   not a reviewed change. **This is a judgement call for the project owner, not for a session.**

---

## 2a. Branch inventory and ownership — established, not guessed

Every attribution below was confirmed by the owning session. Earlier guesses in this document
were wrong; sessions commit under one identity and sign with one key, so nothing in git
metadata distinguishes them.

| branch | ahead of main | owner | state |
|---|---|---|---|
| `re/deferred-cleanup-followups` | 200 | shared — several sessions | clean, in sync |
| `re/results-presentation` | 200 | presentation session | identical to the above |
| `ui/question-catalog-admin` | **146** | egeria-advisor session | clean, pushed, **never merged into the others** |
| `fix/dr-egeria-template-sync-clean` | 2 | question-catalog session | pushed, deliberately split off |

The shared checkout `.../trellis` is now clean (`341d2f5`) apart from `.claude/launch.json`,
left visible deliberately as shareable dev-server config — the project owner's call whether it belongs in
the repo.

**Question-catalog work has two owners, not one.** The data layer (CSV, `question_catalog.yaml`,
`check_registry.yaml`, the generator scripts, the reader, tests) belongs to one session and
touches no frontend; `ui/question-catalog-admin` belongs to another. Adjacent work, two
owners — a cross-session reconciliation rather than a tidy-up.

`c370fa8` is redundant: an identical blob to a change already on
`fix/dr-egeria-template-sync-clean` from 24h earlier. It resolves as a no-op on merge and
explains why one template change appears twice in the history.

---

## 2b. The merge hazard CI cannot catch

**`ui/question-catalog-admin` changes `renderAnalysisCatalogCards`'s signature**, inserting a
positional parameter at index 1:

```js
// re/deferred-cleanup-followups
renderAnalysisCatalogCards(analyses, view, {…})
// ui/question-catalog-admin
renderAnalysisCatalogCards(analyses, candidates, view, {…})
```

Any **new** direct call written on either branch merges cleanly against the other — different
hunk, no conflict markers — and lands `view` in `candidates`. JavaScript type-checks nothing,
the call site stays syntactically valid, and CI goes green. The failure appears at runtime, in
a pane that renders successfully and renders the wrong thing.

There is **exactly one direct call site on each branch**, which is what keeps this cheap.
Today's Discovery panel (`fb710f0`) was routed through `_loadAnalysisCatalogPanel` — byte
identical on both branches — specifically to avoid adding a second. **If these become two PRs,
the second one needs a human read of that function, not a passing build.**

Scale, for the same decision: that branch carries **+745/−190** in `index.html`; today's work
added **+460/−9** to the same file.

*(This was sent to the owning session as a message and did not arrive — that session had
ended. Three cross-session messages were lost to turnover today. It is written here because
the repo is what reaches people.)*

---

## 2c. What to hold out of the PR

- **Remove `scripts/arch-spike/llm_cache/`** — 22 tracked files, 244K, raw model output
  (`qwen2.5-coder:32b`, elapsed times, full prompt/response bodies). Nothing imports it;
  `adjudicate.py` regenerates on miss. One cached response contains `"type": "Third Party
  Process"` — an invalid vocabulary value the guardrail now rejects, i.e. a cached wrong answer
  from a superseded run. Reviewers should not be reading it.
- **Keep `scripts/arch-spike/README.md`** despite being the largest file in the spike (204K).
  Product code cites it by finding number — findings 4, 19, 20, 34, 35, 36, 37 and 44 all
  appear in `arch_recovery/` comments. Cutting it orphans live references. The obvious cut is
  the wrong one.
- The rest of the spike is cleanly separable: no product code imports it, and it imports no
  product code. 836K tracked across 54 files. Include or exclude on its merits.

---

## 2d. Pre-shutdown sweep: what sessions were holding

The project owner began closing sessions to reduce complexity. Every reachable session was asked whether it
held anything not in the repo — uncommitted work, undocumented decisions, claims now known
wrong, or anything a successor would otherwise repeat. Asked rather than inferred: session
ownership was guessed wrongly three times today.

| session | answer |
|---|---|
| question-catalog data layer | had **one unpushed commit** — now `341d2f5` (rebased from `7129c9d`). Clean. |
| architecture recovery / suggested_action | everything committed and pushed |
| FK-guard session | everything committed and pushed (`d7302fc`) |
| scheduled export task ×2 | never touched a repo; findings below |
| egeria-python ×2, egeria-workspaces-fs | asked, pending |

**One unpushed commit was found and would have been lost silently** — `+317/−48` across seven
files including 128 new lines of `question_catalog.yaml`. It existed only in the shared
checkout. Nothing else was outstanding anywhere: three worktrees clean, four branches pushed.

### `stash@{0}` in `.../trellis` is orphaned

WIP on `985d485` ("Prometheus scores 0/11"), 13 lines in `scripts/arch-spike/score.py`, created
2026-08-22. **Four sessions have confirmed it is not theirs.** Its parent commit is
architecture-recovery work, so the likeliest owner is a session that has already ended. Safe to
drop, but that is the project owner's call — nobody left can judge whether those 13 lines were superseded.

### `.claude/launch.json` is a decision, not an oversight

`.claude/` holds three different kinds of thing: `settings.local.json` is machine-local,
`worktrees/` is transient, and `launch.json` is dev-server config the preview tooling reads —
genuinely shareable project config. The first two were gitignored in `6766539`; `launch.json`
was left visible deliberately so it stays a decision. Ignoring it means every fresh clone
recreates the dev-server config by hand. Recorded here so that after the session closes it is
not mistaken for something forgotten.

### Orphaned work in OTHER repos, found by the same sweep

Asking every session — not just the Resource Explorer ones — turned up work outside this repo
that would also have gone quiet. Recorded here because this document is the only durable
artifact of the sweep; the owning repos are where it should actually be resolved.

**`egeria-python`** (`/Users/dwolfson/localGit/egeria-python`, the canonical checkout):

- A new `hey_egeria` command — a general element-register view over any Open Metadata Type,
  grouped by real subtype — existed **only as uncommitted working-tree changes**. Its session
  committed and pushed it on being asked: **PR #301, not yet merged.**
- **Four pre-existing stashes**, verified, none belonging to any session still running:

  ```
  stash@{0}  On docs/issue-48-recheck: schema attr def work
  stash@{1}  On fix/mcp-run-report-reliability: pre-existing uv.lock drift
  stash@{2}  On fix/mcp-run-report-reliability: tmp
  stash@{3}  WIP on Dr.E-Refactoring: 461bb56 Updating pyegeria tests
  ```

- A **staged, uncommitted deletion** of `PR_SUMMARY_2026-08-02.md`, likewise unowned.

Nobody currently running has context on any of it, so none of it should be dropped on a guess
— but it should be looked at before that environment cycles. Two repos, five stashes and a
staged deletion between them: stashes are where work goes to be forgotten, and nothing
surfaces them.

A second `egeria-python` session reported work already committed and pushed (**PR #300**,
ISSUE-73 multi-link relationship GUID fix), plus two pieces of knowledge it wrote down on being
asked: the `Exception` relationship type is `MULTI_LINK` live with no pyegeria OMVS wrapper and
has never been checked against a live server for a REST endpoint; and ISSUE-68's claim that
`SolutionLinkingWire` is `UNI_LINK` **is now stale** — it flipped to `MULTI_LINK` server-side,
confirmed by live query. Both are in `PYEGERIA_ISSUES.md` via that PR rather than only in a
session.

Worth noting how it avoided the collision this document keeps describing: it used an isolated
`git worktree` **because** another session had live uncommitted work in the shared checkout,
and routed around it rather than working alongside it. That checkout is now on
`feat/element-register` with one file still dirty and **four stashes still present** —
verified after the sweep, so the stashes are outstanding regardless of the branch tidy-up.

### A recovery path for ended sessions

**Closed sessions' transcripts are on disk**, which changes the cost of a session ending
mid-conversation. `~/Documents/Claude Conversations/` holds **87 exported transcripts**,
verified, including several from today (`Opis-Egeria Resource Explorer redesign`,
`Opus- Resource explorer architecture recovery design`). So when a session ends before
answering — which happened three times today — its context up to the last export is readable
rather than gone.

Not a substitute for committing: an export is a transcript, not a decision, and reading one to
recover a rationale is far more expensive than the two lines the session could have written.
But "the session ended" is no longer the same as "the reasoning is lost", and that is worth
knowing before anyone reconstructs something from scratch.

### Knowledge held outside any repo (session-export task)

Not egeria work and not in git; recorded because it would otherwise be rediscovered from
scratch. The exporter lives at `~/.claude/scheduled-tasks/export-claude-sessions/`, and its
`SKILL.md` does **not** reference the script — so a future run may reimplement it. One line in
`SKILL.md` fixes that.

- `mcp__ccd_session_mgmt__list_sessions` reports 43 sessions where 74 transcripts exist on
  disk — using it alone silently misses ~40%.
- Ground truth is `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`, top level only;
  files one level deeper under `<id>/subagents/` are sidechains, not sessions.
- The title↔ID join lives in `~/Library/Application Support/Claude/claude-code-sessions/`,
  mapping `sessionId` ↔ `cliSessionId`. Nothing in `~/.claude` contains that mapping.
- **Rejected, so nobody retries it:** exporting tool inputs/results untruncated. 558 MB of
  jsonl produces an unusable export; capping input at 1500 chars, results at 2000 and thinking
  at 4000 yields 69 MB and loses nothing that reads as substance.

**The exporter is itself unbacked.** `export_sessions.py` (8.5K) is in no git repository —
verified. If `~/.claude` is ever reset it is gone, along with the four findings above, and the
87 transcripts it produced lose their means of being regenerated. Not this consolidation's
problem to fix, but it is the one artifact in this document with no copy anywhere.

---

## 3. Outstanding threads, and how they may run

### Can run in parallel — independent, different files

- **Presentation** (`web/`, readers, `result_status.py`). Remaining: render
  `suggested_action` as an input to the Disposition sub-tab rather than a second badge; the
  schedule check before offering a `monitor` subscription (a subscription with no active
  schedule for its `analysis_id` silently never fires).
- **Architecture recovery** (`github/`, `arch_recovery/`, spike). Owned elsewhere. Live
  threads: the LLM adjudicator at 0/27 on the deployment perspective; `misgrouped` has no
  emitter; `investigate` unexercised against real data.
- **Question catalog / check registry** (`configdata/`, `question_catalog*`). A session exists
  and has committed; it is in a worktree and has caused no trouble. Not in any coordination
  conversation so far, so its plans are unknown rather than conflicting.
- **Dependency parsing** — `DependencyParser` covers Python/Node/Go/Maven; Gradle and Cargo
  are missing, so `egeria_git` reports zero dependencies while shipping a `build.gradle`. The
  arch session has an independently-tested Gradle `include` parser (~30 lines) offered for
  reuse.

### Must be sequential

- **`repo_data_profiling` cost tier.** Declared `compute_cost="medium"`, measures 0.0s median
  across 21 repos. **Not yet a finding**: needs a timing on a repo that HAS data files, or it
  is an absence being read as a measurement. Blocks nothing; do not log until measured.
- **Corpus growth.** Several open items ("grow the corpus as a bug-finding strategy",
  "re-check Phase 1 once there are more samples") depend on more repos, and more repos make
  every survey run longer. Sequence it after the PR, not before.

### Genuinely blocked

- **Anything downstream of adjudication.** 0/27 on the deployment perspective; the owning
  session has asked that nothing be built against those numbers yet.

---

## 4. Backlog entries that are stale, not open

Checked against today's work. These read as open and are not:

- **`HIGH — populate IR.ports and IR.wires (§5.5f)`** (§5.5f is now
  `architecture-recovery-extraction-design.md`) — done. Persistence wired
  (`architecture_interfaces` findings), reader and deployment-topology view built, verified on
  egeria-workspaces (68 ports, 38 wires across 18 topologies).
- **`Repo classification — what the repo represents`** — done. Step, catalog entry, Egeria
  survey definitions, and the card.
- **`(original entry) the scorer cannot express a partial component match`** — superseded by
  finding 73, which is marked DONE two entries above it. Three entries describe one item.
- **`repo_classification declares fetch_cost="none"`** — fixed (`api_heavy`, plus the
  org-listing and 5x-resolution optimisations: ~10 min/repo → ~25s median).

The pattern: entries are appended when found and rarely struck when closed, so the file grows
monotonically and its length stops meaning anything. This document is subject to the same
failure — its own §2.2 was wrong within an hour of being written. Worth a pass that marks closures in place
— the file already does this well where it has been done (`~~struck~~` with the resolving
commit named).

---

## 5. What was built today, for the PR description

Presentation layer:

- `result_status.py` — six reader states (`measured` / `nothing_found` / `not_established` /
  `never_run` / `skipped_by_design` / `misgrouped`), deliberately separate from
  `step_outcome.py` because a run asks "is my zero provable?" and a reader asks "what should I
  do about it?"
- Empty states that name which kind of empty they are. Previously one string —
  *"No results yet — click Run to scan"* — served three situations and asserted the one that
  was usually false; measured wrong for 51 persisted outcomes.
- The `repo_classification` card (roles ranked, gate with reason, artifacts grouped by
  **where they are**, not whether they exist).
- The deployment topology view (per-deployment, "declared startup dependencies, not data
  flow" stated on the view).
- `manifest_parse` had a render mode declared and no renderer, showing "No results view for
  this analysis" while holding 61 dependencies.

Instrumentation and infrastructure:

- `step_cost_observer.py` — observed vs declared cost per step; reports, never corrects.
- **First CI in this repository**, with `RE_REQUIRE_POSTGRES=1` so a misconfigured service
  fails the job instead of silently skipping the integration tier.
- All three production stores moved off SQLite; `remove()` had been raising
  `ForeignKeyViolation` on Postgres for every surveyed project.

---

## 6. The one thing worth carrying forward

Nearly every defect found today was the same shape: **something produced a result nobody could
distinguish from nothing.** A chart reading a deleted SQLite file; a security check searching a
table that could not contain its answer; ports computed and never stored; a step declared
zero-fetch that blocked on the network; and — twice — the *measuring instrument* having the
bug. The cheap check that kept working was asking **"who calls this, outside its own tests?"**
and **"what would this look like if it were broken?"**
