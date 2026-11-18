# Egeria operations — recovery, resync, and republishing

**Status:** consolidated runbook. Current as of 2026-09-02; §2.0, §2.3, §2.5, §7 and §8.1 rewritten
2026-11-12 for the Egeria DB-clear recovery tooling (record-egeria-reset, the clear script, re-attach by
qualifiedName).
**This is the operational manual.** For how Resource Explorer integrates with
Egeria by design, see `egeria-integration.md`.

> **This document consolidates five runbooks:** recovery and resync (the spine),
> platform-reset recovery, republishing a changed survey definition, authoring a
> database survey definition, and invoking lineage capture.

---

## 0. The one thing to read if you read nothing else

**Not one of the five bugs found during the 2026-08-31 recovery raised an error.**

A successful promotion showed a red toast. A scan reported zero while 36 items needed
repair. A card never rendered. A claim outlived the report it named. A count could not
decrease no matter how much work was completed.

Every one of them looked exactly like *nothing to do here*. So the governing rule of this
document is:

> **A zero is a claim, not a result.** Before believing a recovery is complete, confirm
> the thing you want is PRESENT. Do not accept that the thing you feared is ABSENT.

Concretely: after relinking, do not check that "unlinked members = 0". Ask Egeria for the
collection's member list and count what is in it. Those two questions came apart on the
night — the scan said 0 because it could not see the members at all.

---

## 1. Which flavour are you in?

The flavours are distinguished by **what was lost**, not by what you did.

| # | Flavour | Symptom | What was lost | Go to |
|---|---|---|---|---|
| 1 | **Platform reset / redeploy** | Everything in Egeria is gone; RE looks fine | All Egeria elements. **Nothing of RE's.** | §2 |
| 2 | **Changed definition** | A survey gained/lost a step | Nothing — this is a deliberate edit | [publishing-a-changed-definition](egeria-operations.md (§9)) |
| 3 | **Drift without a reset** | "I published it but cannot find it" | One asset, or one link | §3 |
| 4 | **Half-finished recovery** | Some repos catalogued, some not | Nothing new — but see §4, this flavour has its own trap | §4 |
| 5 | **RE registry lost** | RE is empty; Egeria is fine | RE's own tables | §5 |

**Flavour 1 is the common one and the one most often mis-diagnosed.** A wipe destroys
Egeria and leaves RE's registry untouched *by design* — that is the premise
`egeria_resync.py` is built on. Which leads directly to:

### 1a. How to tell whether a wipe has actually happened

On the night, a session answered "has Egeria been wiped?" by counting rows in RE's
`projects` and `project_analysis_findings` tables, found 60 and 68,215, and concluded it
had not. Those tables read **identically on both sides of a wipe**, so the measurement
could not distinguish the two states at all. A correct count of the wrong population.

Ask something that only exists on one side of the event:

```bash
uv run python -c "
from resource_explorer.registry import ProjectRegistry
with ProjectRegistry()._conn() as c:
    print('assets  :', c.execute(\"SELECT count(*) FROM projects WHERE coalesce(egeria_asset_guid,'')<>''\").fetchone()[0])
    print('reports :', c.execute('SELECT count(*) FROM project_egeria_surveys').fetchone()[0])"
```

Both zero, on a corpus that was previously published, means the assets are gone and the
sweep has already cleared the pointers. Both non-zero means you are not in flavour 1.

---

## 2. Flavour 1 — platform reset

### 2.0 The procedure after an Egeria metadata clear, in order (2026-11-12)

This is the one procedure for "Egeria's metadata database was cleared and RE must be brought back into
line". §8.1 points here. Each command is labelled **read-only** or **writes (owner's word)**: a write to the
shared registry, or a stop or start of the shared RE on 8813, needs the owner's direct word, each one
separately.

**An image upgrade with `--no-clear` needs none of this.** Egeria keeps its store, so RE's pointers still
resolve. Run the Alignment Scan once (step 6, read-only) to confirm, and stop there.

**The platform side comes first, and is not in this document.** The egeria-workspaces page
`portal-docs/Internals/upgrading-or-resetting-egeria.md` (the `egeria-upgrade-reset` tool) clears and
upgrades Egeria. Its `freeze-writers` step stops RE on 8813 and EA on 8880. Its `unfreeze` step restarts them
through `~/.re-gate/start-8813.sh` and `~/.re-gate/start-ea-8880.sh`. Start here once that run has reached
`unfreeze`.

**Run the order exactly.** Re-attach comes after the clear script and never before it, for the reason in
step 4.

1. **Stop RE.** **Writes (owner's word):** stopping 8813 is a shared-service action, done by the Coordinator.
   If you came from `egeria-upgrade-reset`, its `unfreeze` has just restarted RE, so stop it again. Steps 2–4
   refuse to apply while an RE writer is active. (A maintenance mode that replaces this stop is planned; until
   it lands, stop the process.)

2. **Record the reset.** **Writes (owner's word):**

   ```bash
   resource-explorer registry record-egeria-reset --reset-at <UTC of the clear, e.g. 2026-11-11T20:27Z> \
       --old-digest <old image digest> --new-digest <new image digest>
   ```

   - *What it changes:* adds one row to `egeria_resets` in RE's registry. From then on, every proof row read
     before that time reads "published before the <date> reset · not in Egeria now", never ✓.
   - *What it never touches:* Egeria, and every other registry table. A mistyped time is corrected with
     `--supersedes <id>`, which adds a new row; the old row stays on record.
   - Use the actual clear time from the `egeria-upgrade-reset` run folder
     (`runtime-volumes/egeria-resets/<UTC>/`) when it is recorded. Otherwise use the egeria-main container's
     `StartedAt`.

3. **Clear RE's Egeria pointers:** `scripts/clear_egeria_pointers_after_reset.py`.
   - **Dry run: read-only** (it writes only a plan file).
   - **Apply: writes (owner's word).**
   - Then a second dry run, **read-only**, which must show nothing left to clear.

   ```bash
   cd packages/resource-explorer
   REGISTRY_DATABASE_URL='postgresql://…/egeria_advisor?options=-csearch_path=resource_explorer' \
       uv run python scripts/clear_egeria_pointers_after_reset.py --reset-at <same UTC as step 2>
   # then paste the "to apply:" line it printed, with --cleared-by <who>/<UTC now>
   # then run the dry run again: every row 0
   ```

   - *What it changes:*
     - It clears every non-empty Egeria GUID column RE keeps that **no longer resolves**: asset GUIDs,
       project and collection GUIDs, report GUIDs, work-list, doc-source and RFA GUIDs.
     - It **keeps a pointer that still resolves** (**Decision (project owner, 2026-11-13):** fix the script so it
       can keep live pointers, after the 11-11 recovery's dry run would have blanked three written after the
       reset). Each GUID is read by GUID first, as RE's service account. A row whose GUID resolves is kept
       ("keep: live, created <time>"), and so is one whose GUID cannot be read ("keep: unreadable (<why>): not
       treated as gone"). The kept rows, and what each GUID answered, are in the plan and its hash, so an answer
       that changes before the apply refuses the apply. A resource with a kept asset or report pointer gets no
       `egeria_reset` marker. If Egeria cannot be reached at all, the run refuses.
     - "Gone" means **not found for RE's service account**, not "absent from Egeria". Since e9fd817f4c a zone
       without a control is private, so an element in a zone that account cannot see answers not found too,
       and its pointer is cleared. Before the apply, check that any element you expect to survive is visible
       to RE's service account.
     - It warns about a publish made after the reset that left no proof row: a scheduled survey publishes
       through the survey-run publisher, which records `project_egeria_surveys` but writes no proof row, so the
       proofs-only warning missed docling's 2026-11-12 publish.
     - It sets a project context or investigation that was `linked` to `unbound`.
     - It removes cache rows: materialized blueprints, `survey_definition_cache`, `egeria_linkage_status`, and
       the Egeria claim settings.
     - It supersedes outbox rows that are not terminal.
     - It writes one `egeria_reset` marker proof per resource.
     - It writes a 0600 snapshot of every changed row first, and one `activity_log` row.
   - *What it never touches:* Egeria (it only reads GUIDs there, never writes), any proof row, verdicts, scope events,
     enrichment, the journal, survey history, `done` outbox rows, and **the qualifiedName mirrors**. Those
     names are what step 4 re-attaches by.
   - *Guards:*
     - The apply needs `--plan-file`, `--plan-hash`, `--database`, `--schema` and `--cleared-by`.
     - The plan must be less than 15 minutes old and identical when it is recomputed.
     - It refuses while an RE writer is active.
     - A Postgres URL with no `search_path` is refused.

4. **Re-attach by qualifiedName.** **Coming: not on main yet.** It is branch `re/reattach-egeria-by-name`; the
   PR is pending. Until it merges, skip this step. The contexts stay `unbound` and their publishes are gated
   until someone rebinds them.

   Once it merges, the command will be as follows. The dry run is **read-only** (a plan file, plus Egeria
   reads); the apply **writes (owner's word)**; then a second dry run must show `to re-attach: 0`.

   ```bash
   resource-explorer registry reattach-egeria-by-name                     # dry run
   resource-explorer registry reattach-egeria-by-name --show missing      # the rows of one verdict
   # then the printed "to apply:" line, with --reattached-by <who>/<UTC now>
   ```

   - *What it will change:* for each stored qualifiedName whose GUID is empty or dead, if Egeria holds exactly
     one element of the right type under that name, it writes that element's GUID. A project context or
     investigation that is `unbound` becomes `linked`. That is the only status change it makes.
   - *What it never touches:* Egeria (it reads only), and any row whose answer is not exactly one element.
   - **Why it runs after step 3 and never before:** the clear script blanks **every** pointer, re-attached ones
     included. Run it after the re-attach and the re-attach is undone.
   - Its details will be filled in here from the merged CLI help.

5. **Start RE.** **Writes (owner's word):** the Coordinator restarts 8813 with `~/.re-gate/start-8813.sh`
   (the launcher that sets RE's environment), then checks that `/health` answers 200 and that the start log
   has no errors.

6. **Admin → 🔄 Egeria Alignment** (see §2.3 for what Scan and Apply each do).
   - **Scan: read-only.**
   - **Apply: writes (owner's word),** in this order:
     1. `catalog_assets`;
     2. `relink_investigation_members`;
     3. `republish_survey_results`, which is the slow one and downloads archives.
   - Re-scan between the passes (§2.4).

7. **Databases and file systems:** republish each from its own panel (Curate → Publish).
   - **Writes (owner's word):** these are Egeria writes.
   - Alignment never republishes a database or a file system. Its `flag_stale_dbfs_assets` step only flags
     them.

**The result to expect after a DB clear: many `missing · needs republish`, which is the right answer.** Only
names that Egeria itself re-seeded can re-attach, such as the Coco projects and the survey definitions RE's
bootstrap re-authors. Every other name is honestly gone, and is republished in steps 6–7. A large `missing`
count is not a fault in the tooling.

**What the platform side already did, so do not repeat it:** `bootstrap.check_and_heal()` re-authors the
survey definitions unattended once RE is up (§2.1). Verify it (§2.2); do not hand-run the documents.

### 2.1 Let bootstrap heal. Do not hand-run Dr.Egeria documents.

`bootstrap.check_and_heal()` re-runs any batch whose canary is missing, every 10 minutes
while the web server is up, then runs the post-heal reconciler. On a wiped platform every
canary is missing, so it heals unattended.

**Two people intervening on top of that is the worst available outcome.** Dr.Egeria's link
commands do not dedupe — one deliberate publish on 2026-08-31 produced 40 duplicate edges
across two definitions. There is a reconciler for those. For the questions batch's 168
perspective links **there is none**, so duplicates there have no cleanup path at all.

If a fix is needed, **one person drives and everyone else stays off Egeria entirely.**

### 2.2 Verify the heal — the canary is not the verification

**A canary proves a batch RAN, not that every element in it exists.** That is exactly how
8 of 49 catalog questions sat missing for weeks while the batch reported healthy.

Four checks, and all four must pass:

```
questions              49/49 resolve
RepoAssessmentSurvey   14 steps · reducer present · runs after all 8 inputs
RepoFullSurvey         35 steps · reducer present · runs after all 8 inputs
reconciler --dry-run   0 duplicate / 0 stale across all EIGHT definitions
```

Check **"runs after all its inputs"**, not "runs last". They are different requirements and
only the second is real: `repo_security_summary` is terminal in Assessment but *not* in
Full, where `repo_rag_ingestion` must stay last. "Last" passed under two different wrong
arrangements on 2026-08-31.

Perspectives are **not** covered by any of the above and must be checked separately:

```bash
uv run python scripts/verify_perspectives.py   # 0 all present · 1 missing · 2 undetermined
```

> Its first live run produced **three false OKs**, because it fell back to `displayName`,
> which is not unique across types — `Community`, `Privacy` and `Steward` resolved to
> unrelated elements. It reported 9 of 12 missing when the answer was 12. It is
> qualifiedName-only now. The lesson generalises: **run a new check against a known state
> before trusting it**, because a check that has never been seen to fail is a check nobody
> knows works.

### 2.3 Then use Admin → 🔄 Egeria Alignment for everything else

This is the part no older runbook describes. Repairs run only for the steps you tick, always in dependency
order, whatever order you tick them in.

**Scan is read-only.**
- It is the page's Re-scan button, or `GET /api/egeria/resync/scan`.
- It runs registry SELECTs and Egeria reads, and nothing else.
- A lookup that fails is listed as *undetermined*. It is never counted as drift and never cleared.
- `GET /api/egeria/resync/scheduler-status` shows when the unattended pass last ran and what it did.

**Apply writes.**
- It is the "Apply selected" button, or `POST /api/egeria/resync/apply`.
- The `clear_*` and `flag_*` steps write RE's registry only.
- `reauthor_survey_definitions`, `catalog_assets`, `republish_survey_results` and
  `relink_investigation_members` write to Egeria.
- The page never offers the clear script of §2.0 step 3: that stays a stopped-RE CLI step.

`REPAIR_STEPS` (current main); the order is load-bearing:

| # | Step | What it does | Writes | Unattended? |
|---|---|---|---|---|
| 1 | `reauthor_survey_definitions` | Re-runs the definitions batch + reconciler | Egeria; slow, unticked | never |
| 2 | `clear_stale_assets` | Drops repo asset GUIDs that no longer resolve | registry | **yes** |
| 3 | `clear_orphan_publish_claims` | Drops claims naming reports that are gone | registry | **yes** |
| 4 | `flag_vanished_publishes` | Flags (never deletes) a publish whose report vanished | registry | **yes** |
| 5 | `flag_stale_dbfs_assets` | Flags a database/file-system asset GUID that no longer resolves; keeps the GUID | registry | **yes** |
| 6 | `clear_stale_investigations` | Clears dead Project/Collection GUIDs (investigations become `local`)† | registry | never |
| 7 | `clear_stale_contexts` | Resets contexts pointing at dead Projects to `unset`, and blanks their name† | registry | never |

† **On main today these two erase what re-attach needs, and they are ticked by default.** Untick them after
an Egeria clear until the re-attach PR merges.

The re-attach branch changes both steps to keep the name. Each blanks only the GUID; the stored
qualifiedName is kept (the clear script's rule: the name is the decision, the GUID is the pointer). A context,
or an investigation that was `linked`, becomes `unbound`, which gates a publish exactly as `unset` does.
`reattach_by_name` can then recover the row once Egeria holds the name again.
| 8 | `catalog_assets` | Publishes `repo_health` → registers the asset | Egeria; **no downloads** | never |
| 9 | `republish_survey_results` | Full survey → SurveyReport | Egeria; **slow, downloads archives**, unticked | never |
| 10 | `relink_investigation_members` | Attaches members to their assets | Egeria | never |

**Coming with re-attach** (branch `re/reattach-egeria-by-name`, not on main yet):
- A manual step, `reattach_by_name`, placed before `clear_stale_investigations` and `clear_stale_contexts`
  in the ordered pass. Re-attaching first means nothing is left for them to clear. With the same change,
  both steps keep the name (†), so they no longer destroy what a later re-attach needs.
- It is unticked by default: it rewrites links by name.
- It is never unattended.
- Its Apply refuses when the plan changed since the Scan, or while another RE writer is active.

**The scheduler's unattended pass.**
- What it is: `egeria_resync.scan_and_clear`. It runs about every 600 s while RE is up, and once at start.
- What it applies: only `SAFE_SCHEDULED_STEPS`, which are `clear_stale_assets`, `clear_orphan_publish_claims`,
  `flag_vanished_publishes` and `flag_stale_dbfs_assets`. Each of those writes RE's registry only, after
  verifying live, and never needs a decision.
- What it never runs unattended: any step that writes to Egeria, and the two that can unbind a Project a person
  chose (`clear_stale_investigations`, `clear_stale_contexts`).
- So after a clear, by the time you open the page, the scheduler may already have cleared stale repo asset
  GUIDs and flagged databases and file systems. The Scan shows what is left.

**Why the order matters, stated as failures rather than rules:**

- Publishing before clearing reuses a dead GUID.
- Relinking before cataloguing can do nothing — a member with no asset behind it cannot be
  attached. *This is why a promotion on a freshly rebuilt platform reports every member
  unlinkable, and it is not a fault.*
- Clearing orphan claims used to be outrun by cataloguing (see §4).

### 2.4 It is a TWO-PASS operation, and the first pass looks wrong

Expect this, because it was mistaken for failure twice on the night:

**Pass 1** — tick `catalog_assets`. Its card then goes to **0 and disappears**, and a
*different* card (`registration_only`) appears in its place with a similar count. On
2026-08-31 that read as "28 → 23, so 5 failed". Nothing had failed; all 28 succeeded.

**Pass 2** — click **Re-scan**. Only now can these appear, because they depend on the
assets existing:

- `orphan_publish_claims` — tick it
- `unlinked_members` — tick it; this is the one that ends the "0 linked" state
- `registration_only` — the slow full publish, unticked by default

**The counts line separates the two kinds:** *"0 repairable · 28 slow, not selected"*. The
second number does not go down when you complete work — it is work you declined, and it
persists across every re-scan by design.

### 2.5 Read-only looks you can take at any time

All of these are **read-only** and need no owner decision beyond "go":
- The Alignment Scan, and `GET /api/egeria/resync/scheduler-status`.
- The clear script's dry run. It writes only its plan file; the plan expires after 15 minutes, which is
  fine for a look.
- Counts of the pointer columns, done in SQL. For example, compare the number of rows with a GUID against
  the number with a qualifiedName:

  ```sql
  SELECT count(*) FILTER (WHERE coalesce(egeria_project_guid,'')<>'') AS with_guid,
         count(*) FILTER (WHERE coalesce(egeria_project_qualified_name,'')<>'') AS with_name
  FROM resource_explorer.entity_egeria_project_context;
  ```
- `egeria-upgrade-reset plan` on the platform side. It writes only its own run folder.

Run the scripts **from a separate trellis checkout or venv, never inside 8813's serving worktree**
(`.claude/worktrees/wt-relgraph`); see §7.

---

## 3. Flavour 3 — drift without a reset

Run the Alignment scan. It reports drift in both directions, and splits it into what has
**one correct repair** (a button) and what **needs a person** (no button, marked *your
call*). The second category is deliberate: RE does not know which Egeria Project an
unbound resource should join, and a guess written into the catalog reads exactly like a
decision.

For the single-asset case, see
[`egeria-operations.md (§8)` §3](egeria-operations.md (§8)) — unchanged and still correct.

---

## 4. Flavour 4 — the half-finished recovery, and why it has its own section

**A partially-completed recovery can put work permanently out of reach of the repair that
was meant to do it.** This is the least obvious flavour and produced the worst bug of the
night.

`clear_orphan_publish_claims` originally keyed on *"the project has no asset"*. So the
moment `catalog_assets` gave a repo its asset back, that repo's pre-wipe claims became
invisible to both the repair and the scan that surfaces it. The claims survived
**precisely because the situation had been half-repaired** — running the repairs in the
order the panel offers them meant the cataloguing step outran the clearing step.

Measured: the scan reported **0** while **36 orphaned claims across 12 repos** existed.

Both are now keyed on the claim itself — a claim whose `egeria_report_guid` is absent from
`project_egeria_surveys` names a report nothing stands behind, whether or not the project
currently has an asset. It can no longer be outrun.

**The general lesson for anyone writing a repair:** key it on the damage, not on a
correlate of the damage that another repair is going to change.

---

## 5. Flavour 5 — RE's registry lost

Not covered here, and deliberately so: nothing in this document restores RE's own tables.
RE's registry is the system of record for survey results — Egeria holds what was
*published*, which is a subset. Restore it from your Postgres backup. Re-surveying is not
equivalent: it re-derives current state, and loses the history that
`project_analysis_findings` and the `surveyed_at`-stamped tables carry.

---

## 6. What does *not* need doing

- **Re-surveying before publishing.** Publish runs `SurveyOrchestrator.run(slug)` itself,
  so a survey first just does the work twice.
- **Re-promoting an investigation that already has a Project GUID.** Dr.Egeria batches are
  not idempotent, and perspective links have no reconciler.
- **Anything to RE's local data after an Egeria wipe.** It is intact. Check before acting.

---

## 7. Traps, each of which cost time

- **Do not run the clear script after the re-attach.** Since 2026-11-13 it keeps a pointer that resolves, so a
  re-attached one should survive, but that is a safety net, not the procedure: the order is the clear script,
  then the re-attach (§2.0). And it keeps only what RE's service account can SEE: an element in a zone that
  account cannot read answers not found and is cleared (§2.0 step 3). `scripts/sweep_stale_egeria_guids.py
  --apply` and the Alignment steps `clear_stale_investigations` / `clear_stale_contexts` also clear project
  and collection pointers, so none of them goes between the clear script and the re-attach either.
- **On main today, `clear_stale_contexts` also blanks the stored name, and it is ticked by default.** So
  pressing Apply on the Alignment page with the default ticks erases the names re-attach needs, and those
  rows cannot be recovered by name afterwards. Untick `clear_stale_contexts` and `clear_stale_investigations`
  after a clear. Once the re-attach PR merges, both steps keep the name (§2.3 †).
- **Do not run `uv run` or `uv sync --package` inside the 8813 worktree** (`wt-relgraph`). Either can
  re-sync that venv and drop the nine RE survey steps installed in it. Run the scripts from a separate
  trellis checkout or venv.
- **The clear script needs `?options=-csearch_path=resource_explorer` in `REGISTRY_DATABASE_URL`.** Without
  it the script refuses, dry run included: the tables would resolve in `public`, which is not where RE keeps
  them. It also compares the open connection's `current_schema()` with that schema.
- **`node --check` with the wrong Node.** nvm can move `node` under you; v14 cannot parse
  `||=` and reports valid code as a syntax error. Verify with a runtime matching browsers.
- **Start the app WITHOUT `--reload`.** `SourceCache` uses the relative path
  `data/source-cache`, so zipballs extract inside the tree uvicorn watches. Every large
  survey restarts the server and kills itself **silently** — no error, just zero rows. A
  `manifest_parse` ran ten minutes and wrote nothing before this was found.
- **A stale panel is not stale code.** The Alignment panel caches its scan in the page. On
  2026-08-31 a fix was correctly loaded and running while the browser showed the old
  numbers. Use the panel's **Re-scan** button, not just a browser refresh. To settle it,
  ask the server: `curl -s localhost:8810/api/egeria/resync/scan`.
- **`git add <path>` is per-file, not per-hunk.** An uncommitted edit to a file another
  session is also touching gets swept into their commit.
- **An empty result is not an unknown result.** pyegeria returns the *string*
  `"No members found"` for an empty collection. Treating that as malformed sent all three
  investigations to `undetermined`, so the relink card never rendered — in exactly the
  state the repair exists for.

---

## Related

- [`egeria-operations.md (§8)`](egeria-operations.md (§8)) — the step-by-step for flavour 1
- [`egeria-operations.md (§9)`](egeria-operations.md (§9)) — flavour 2
- [`open-stack-checklist.md`](open-stack-checklist.md) §7 — redeploy order and live checks
- [`egeria-integration.md (§7)`](egeria-integration.md (§7)) — **the outbox restores nothing**; see reset-recovery §3a

---

# Part II — Republishing, authoring, and platform reset

*Merged 2026-09-02 from four runbooks.*

## 8. Before, and after, a platform reset

### 8.0 Before the reset (added 2026-10-03)

The reset erases the Egeria state that explains a failed publish, so read the evidence first.

1. **Open Admin, Publish Queue (classic UI) and check pending, running and dead rows.** Nothing should be
   pending. Dead rows have used up their 8 attempts and will not retry on their own; read each row's
   `last_error` before the reset. The usual cause is a collection or Project GUID Egeria no longer holds
   (a 404 on the target), which is a stale pointer, not an outage. Do not retry such rows.
2. **Run the Egeria Alignment scan once** (it never writes) and keep the list: it names the stale
   investigation GUIDs the dead rows point at, and any that were Projects bound by hand.
3. **Stop RE before Egeria goes down, and start it only when Egeria is fully up.** The scheduler drains the
   outbox about every 15 minutes. With no Egeria client at all, rows stay pending and burn no attempts; with
   a reachable but half-deployed Egeria, every write fails and counts, and a good row can dead-letter.
4. Do not start `reauthor_survey_definitions` yourself afterwards (see §2.1).

### 8.1 After the reset

**The procedure is §2.0** (rewritten 2026-11-12): stop RE, record the reset, run the clear script, re-attach by
qualifiedName (once it lands), start RE, then Alignment, then databases and file systems from their panels.
What follows is background to it.

1. **Let the bootstrap heal the definitions, then verify it.** `bootstrap.check_and_heal()` re-runs any
   batch whose canary is missing, every 10 minutes while the web server is up. On a wiped platform it heals
   unattended. Let it, and verify the result (§2.2) rather than hand-running documents on top of it.
2. **The stale pointers are the clear script's job (§2.0 step 3), not the sweep's.**
   `scripts/sweep_stale_egeria_guids.py` predates the clear script and covers only repo assets,
   investigation Projects and working-set Collections. It is kept for drift without a reset (§3), not for
   this.
3. **Do not expect the outbox to restore anything.**

> ### The outbox does NOT restore anything
>
> This is the most confidently wrong state in the registry after a reset. A row is marked `done`
> with the GUID it created, `apply_element` short-circuits on a recorded GUID and returns without
> writing, and a `done` row is never claimed again. Those rows **assert a completed publish, point
> at an element that is gone, and the retry machinery deliberately will not touch them.**
>
> **Recovery is re-running the surveys, not draining the queue.** *"The outbox guarantees delivery"*
> is the natural assumption and it is wrong across a wipe: the queue guarantees delivery of work it
> still considers *pending*, and a reset does not make completed work pending again.

The sweep keys on the resource having no asset GUID rather than looking each row up: an annotation
hangs off a SurveyReport which hangs off the asset, so if the asset is gone, everything published
beneath it is gone.

## 9. Republishing a changed survey definition

*(from `egeria-publishing-a-changed-definition.md`, written from an actual publish)*

Author in dependency order, do not reconcile in the middle, reconcile once at the end, then **prove
the deletes happened** — and verify against the definitions rather than against the output.

Four traps, each of which cost real time:

- **A step's position in `STEP_REGISTRY` is its position in the Full Survey chain.** "Repo Full
  Survey" is generated from the `*` sentinel — every registry step *in that dict's own order*. A
  new step added beside related ones lands wherever they are: a reducer written next to the other
  security steps landed at index 21 of 34, **ahead of two of its own inputs**. Read the generated
  chain before publishing it.
- **"Runs last" and "runs after its inputs" are different requirements.** Moving that reducer to the
  end fixed Full Survey and broke `test_rag_ingestion_runs_last` — `repo_rag_ingestion` is the most
  expensive step and nothing reads it, so it stays terminal. Two steps wanted the same slot; only
  one needed it. **Assert the property you actually need.**
- **Authored is not published.** Regenerating a document changes only the file. Both problems that
  day were this same state: a definition regenerated and never run, and a questions document
  regenerated months earlier and never re-run, leaving 8 of 49 terms absent. A live smoke test
  catches the first. **Nothing catches the second** — a batch's canary proves the batch *ran*, not
  that every element in it exists.
- **A blocked Egeria write is not something to work around.** If the operation is refused, it makes
  permanent changes to someone's platform. Hand it to the person who owns it.

## 10. Authoring a database survey definition

*(from `egeria-database-survey-definition.md`)*

A validated Dr.Egeria recipe for defining a database survey: create the process steps, create the
governance action process, link the first step, chain the rest. This is the template to copy for any
new survey definition, database or otherwise — the mechanics do not vary by resource type.

## 11. Invoking lineage capture

*(from `egeria-lineage-invocation.md`)*

The `sql_analysis` step parses view definitions with `sqlglot` and produces two kinds of lineage:

- **Design lineage** — column-to-column. Physical columns in base tables mapped to derived columns
  in the view, as `LineageMapping` relationships between `DatabaseColumn` entities.
- **Process lineage** — the transformation itself. A `Process` entity for the view, linked to source
  tables via `DataFlow` input relationships and to its output attributes via `DataFlow` outputs.

Invoked through a Dr.Egeria plan in the same shape as §10.
