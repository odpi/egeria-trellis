# WORKING RULES — standing clearances, hard gates, and how sessions report (2026-10-08)

From the design session at the owner's request, after a day in which a
redeploy took five hours of rounds. The owner has retired the coordinator
session. These rules replace per-action clears with a written policy;
a session acts on this page and reports afterwards. Anything not on this
page that writes to something shared is asked about, once, with the
question in one line.

## 1. Sessions

| Session | Owns | Asks the design session only for |
|---|---|---|
| Design and gate (this one) | briefs, rulings, designer asks, gate verdicts with the owner | — |
| Build, test and PR | builders as its own subagents, test rounds, PRs, the 8813 gate build, registry snapshots | a design decision, or a hold it wants lifted |
| Workspaces | the Egeria platform, docker, content packs | nothing; the owner directs it |

No relays. A message carries a question or a result, never a copy of what a
third party said. The owner's word is given to the session that acts on
it, in that session.

## 2. Needs no word from anyone (act, then report in one line)

- A test round of any tier on a scratch schema (`resource_explorer_test_*`
  through the pg_registry fixture), with `RE_LIVE_EGERIA_WRITES_CLEARED`
  and `REGISTRY_DATABASE_URL` unset, snapshots of the real schema before
  and after, and the scratch schema dropped.
- A restart of 8813 on a merged main, with: the running-work check (no
  `_runner` row, no running `activity_log` row for the 8813 pid, no
  non-terminal outbox row), a DDL grep of the range, and a uvicorn boot
  on a spare port under the uvloop loop on the exact SHA. A failed
  restart rolls back to the previous SHA first and reports second.
- A docs-only PR, and its merge on green, when every commit is signed.
- Opening a PR for a branch whose full suite is green and whose boot
  check passed.
- Reading anything: source, logs, the registry, Egeria by GUID.
- A builder's commits on its own branch, signed and DCO'd.

## 3. Hard gates (the owner's explicit word, given directly to the acting session)

- DDL against the shared registry (5442), including a scratch schema that
  is not dropped in the same run.
- Any write to live Egeria from a test or script (`--live-egeria-writes`
  with its clearance variable naming who and when).
- Any archive or delete in Egeria (the ISSUE-117 block stays on until the
  6.2 re-proof).
- The registry clean-up script's `--apply` against `resource_explorer`.
- A restart of the Egeria platform, a connector, or a database server.
- Changing a credential, a zone configuration, or anything under Admin.
- Merging a PR that changes Python, Egeria writes or credentials: the
  owner merges; the build session may merge docs and front-end-only PRs
  on green when the owner has said so for that batch.

A relay of the owner's word is not his word. A peer's "done" is not a
clearance.

## 4. Proof in code, not in chat

- A guard is proven by a committed test that makes it fail on purpose.
  Once that test is in the suite, nobody re-proves the guard by hand.
- A status word on screen derives from a proof row; a proof row proves one
  element by GUID after a read-back. Unchanged.
- Anything destructive is briefed with its full guard list before the
  first build (for a script: schema named, live `current_schema()`
  checked, version in the plan, plan hash, 0600 files, idempotent re-run,
  refusals tested). One proof run, not one per finding.
- CI boots the web app under the gate's loop. A green suite on a main
  that cannot start is the failure this prevents.

## 5. Cadence

- One merge window a day when the owner is walking gates: batch the PRs,
  restart 8813 once, walk once. Front-end-only changes stay in one
  `re/ui-batch-<date>` PR.
- Design branches land as docs PRs in one batch, not one PR per note.

## 6. Reporting

- Lead with what changed. Unchanged snapshots are the word "unchanged";
  counts appear only when they differ.
- A test round reports pass or fail, the names of tests that were asked
  for by name, and anything written outside the scratch schema. Nothing
  else.
- A hold states the risk in one sentence and what lifts it.
- No status lists, no restating of the pending work; the owner asks when
  he wants the list.

## 7. What this does not change

The honesty rules, the signing and DCO rules, the no-bare-stash rule, the
rule that tests never touch the shared registry or live Egeria, the
running-work check before any RE restart, and the owner's merges of
Python, Egeria-write and credential PRs.
