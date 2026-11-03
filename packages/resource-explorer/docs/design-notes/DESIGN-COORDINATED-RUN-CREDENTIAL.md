# One-run credential for Egeria-coordinated runs

*(Status refreshed 2026-10-28: built on main 2026-10-18 (commit `72d1eddd`, "one-run credential for Egeria-coordinated runs"); the Q3 words are still placeholders in `engine_host.py`. The original status line is kept below.)*

Status: **design note, not built; owner answered Q1, Q2 and Q4 on 2026-10-18, Q3 (designer words) open.** Written 2026-10-18 by the RE coordinator session ("do the next items"). It covers
the design-queue item recorded 2026-10-16. Build tier: **full review** (credential handling), Opus builder.

**Decision (project owner, 2026-10-16):** "I want a per-run elevated credential that doesn't replace the stored one."

## 1. What already exists, and the gap

The stored surveyor credential for coco_pharma reads 3 of 61 tables. So step 1 of Scouting and steps 1–2 of Analysis
are refused every time ("needs `read`: connected as surveyor, which has SELECT on 3 of 61 relation(s)"). The owner
wants to give a stronger login for one run without replacing the stored one.

**For RE's own runs this already exists**, and this note does not change it:
- **Definition "Re-run…"** (`web/routes/survey_definitions.py`, `OVERRIDE_SCOPE = "this run"`) and **analysis
  "with options…"** (`openAnalysisOverrideDialog`, app.js) take a credential typed into a dialog.
- `run-credential.js` holds it in a module-level Map in the tab, per database slug: never localStorage, a cookie,
  a URL, a log line or the markup.
- It is sent in the body of one call. The server runs that run in its own thread, not queued, not stored, not
  retried, on RE's own engine (never Prefect).
- The run records `ran_as: {user, scope: "this run"}`, so the run line can say who it ran as.

**The gap is Egeria-coordinated runs** ("Run/Re-run · coordinated by Egeria →"). RE only *starts* those:
- `initiate_process_run` starts the process instance.
- Egeria then creates one engine action per step.
- RE's engine host claims each one seconds or minutes later, on its sweep, as `Daemon(ENGINE_HOST)`.
- `_stored_credentials(registry, slug)` hands the step the database registration's stored login.

There is no request body at claim time to carry a credential, so today a coordinated run can only use the stored one.

## 2. Design

### 2.1 Where it is typed

The coordinated control gains the same affordance as RE's own Re-run: **"Run · coordinated by Egeria · with
options…"**, which opens the existing credential dialog unchanged (same words, same `run-credential.js` memory). The
designer gives the label. "Go" posts the credential in the body of the start call, and nowhere else.

### 2.2 Where it lives on the server

A process-local, in-memory **run-credential holder** in the 8813 process, keyed by **process instance GUID**:
- **Set:** after `initiate_process_run` returns the instance GUID, inside the same request. If the initiate fails,
  nothing is held.
- **Read:** only by the engine host. In `_decide_one`, when the claimed action's `process_instance_guid` has an entry,
  that credential replaces `_stored_credentials` for that step. No other code path can read it; the holder exposes
  no listing.
- **Cleared:**
  - when the run reaches a terminal state (the run line's own derivation: completed, partial, failed or cancelled);
  - on Cancel run;
  - on a TTL of 2 hours from the start (configurable, never unbounded);
  - on process exit, because it was only ever in memory.

  Whichever comes first wins.
- **Never written** to the registry, `runs.target`, `step_runs.declared`, the activity log, an Egeria property, a
  log line or an exception message. Every error path that formats the step's connection must go through the
  existing `scrub_text` / `scrub_secret`.
- **Not encrypted at rest**, because it is never at rest. The rejected alternative was an encrypted ephemeral table
  with a TTL: it adds a store to secure, wipe and audit, and survives a restart, which it should not.

### 2.3 What a step does with it

- **RE steps (`re-*` request types):** use it in place of the stored login, and record on the step row (a proof row)
  `ran_as: {user, scope: "this run"}`, the same shape RE's own override runs record. Never the password.
- **Egeria's native steps** (PostgreSQLSurvey and other Java survey services in a mixed process) read Egeria's
  secrets store, not RE. **The one-run credential does not apply to them.**
  - The confirm and the run line must say so per step. Placeholder: "step N runs in Egeria with the stored
    credential".
  - Rejected: writing a temporary secrets-store entry. It writes a secret into Egeria, needs cleanup on every
    failure path, and a crash would leave an elevated login behind.

### 2.4 Restarts and missing entries

- If 8813 restarts mid-run, the holder is empty. A later step of that instance must **not** fall back silently to
  the stored login, because that changes what the run measured under the owner's feet.
- Instead the step completes `survey-invalid` with "the one-run credential was lost when Resource Explorer
  restarted; re-run with options" (placeholder), and the run line shows it.
- The start call records `run_credential: {user, scope: "this run"}` (no secret) on the run's own row. A step can
  then tell "this run asked for a one-run credential" (refuse when it is gone) from "it never did" (use the stored
  one).

### 2.5 Who may supply one

- Only the signed-in person who starts the coordinated run, as the run's starter. This follows the owner's
  end-user identity rule and the Cancel run rule (Q2/Q3).
- No one can attach a credential to a run started elsewhere, or to an instance RE did not start.
- An admin cannot read or reuse another person's one-run credential.

### 2.6 Concurrency

- **Keyed by instance GUID, not by database.** So a second run on the same database, coordinated or RE's own,
  never picks up the first run's credential.
- **The guard is the instance GUID check.** The engine host uses an entry only for an action whose
  `process_instance_guid` equals the key: never by slug, never "the latest".

## 3. Threats and the guard for each

| Threat | Guard |
|---|---|
| Leak via logs or exception text | Holder never formats its value. Step connection errors go through `scrub_text`. Test: a step that raises with the password in the message leaves no trace in logs or rows. |
| Leak via proof rows (`step_runs.declared`, `runs.target`, activity) | Only `{user, scope}` is recorded. Test: JSON of every row written during a run contains no password. |
| Reuse after the run | Cleared at terminal state, Cancel, TTL and exit. Tests for each clear path, including failure and cancel. |
| Pick-up by a concurrent run on the same database | Keyed by instance GUID only. Test: two instances on one slug, one with a credential, each step uses its own. |
| Silent fallback after restart | A row-level `run_credential` marker makes a missing entry a refusal, not the stored login. Test. |
| Applied to a native Egeria step | Never consulted for non-`re-*` request types. Test. Words on screen say so. |
| Someone else supplies or reads it | Start route requires the signed-in starter. No read API. Test: the holder has no listing or read route. |

## 4. Questions and answers

**Decision (project owner, 2026-10-18):** "Keep the one-run credential design: I'll answer Q1-Q3 (TTL 2h ok, refuse
after restart), open the docs PR."

- **Q1. TTL:** 2 hours, as proposed. It stays configurable and is never unbounded.
- **Q2. After a restart:** refuse the remaining steps with the lost-after-restart words. Never fall back to the stored
  login (§2.4).
- **Q3, designer (open):** the label for "coordinated by Egeria · with options…", the per-step "runs in Egeria with
  the stored credential" words, and the lost-after-restart words. The builder uses the placeholders in this note
  until they arrive.
- **Q4. Still wanted:** yes. This design is kept; coco_pharma's stored credential is not changed instead.

## 5. Not in this note

- Changing how RE's own Re-run override works.
- Egeria-side secrets (Q4's other route).
- Server-level credentials for the shared server element 56a974f8 (ops item 4, an Egeria write needing the owner's
  word).
