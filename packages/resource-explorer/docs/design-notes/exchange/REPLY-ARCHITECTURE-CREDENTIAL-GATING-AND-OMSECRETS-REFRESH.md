# REPLY — Architecture session: credential gating, and the .omsecrets refresh (2026-10-01)

To `ASK-CREDENTIAL-GATING-AND-OMSECRETS-REFRESH.md` (coordinating session,
2026-09-24). Late: the ask sat unanswered for a week; the designer's
discovery-sources reply (2026-10-01, §2) drew the screen half of item 1
before this was written, and the native-survey work of 2026-09-30 answered
item 2 by observation. Both are folded in rather than re-derived.

## 1 · Gate on minimal privilege, then offer to elevate

**Yes, the gate is real at catalog tier, for disqualification only.** With a
credential that can CONNECT and read the catalog, nothing more, these are
answerable from what the Scouting steps already collect:

| disqualifier | catalog-tier reader | note |
|---|---|---|
| empty, or structure only | `postgres_schema_and_stats` catalog fallback | zero tables is ∅ nothing found, a measured fact |
| duplicate or copy of a known database | `db_fingerprint` over names and types | structural similarity needs no row |
| no activity, probable archive | `pg_stat_database` counters across two runs (`db_change_rates`) | needs two readings; one reading says "insufficient history", not "idle" |
| nobody can say what it is | `subject_signals` over names and comments | a wholly structural name set is itself a finding |
| owner role and licence unknown | `datdba`, Context observations | human-supplied; the gate reports absence |

What catalog tier **cannot** do is qualify: "worth more attention" needs row
counts, profiles and data classes, all read-tier. So the gate's first stage
accepts whatever credential registration brought and answers "is there a
reason to stop here", never "is this good". That is the shape the owner
described, and it is already how `prerequisite_resolver` treats cost.

**Disqualify early must not set a disposition.** The designer's rule from
2026-09-29 holds here as everywhere: a survey proposes only observations,
never judgements, and a disposition is a judgement. The gate's output is a
finding with its evidence on the resource's row, "∅ no tables", "looks like
a copy of *X* · 94% of table names match", "? activity unknown · one
reading", and the person sets `ignored` or `tracking` by hand from it. A
cheap probe that filed databases as not worth it would be exactly the
confident wrong answer the honesty rules exist to prevent, and it would be
invisible, because an ignored row is folded away.

**The elevate step is already drawn.** The discovery-sources reply lists
databases the credential cannot CONNECT to with "? can't connect with this
credential" instead of filtering them, and the Enrichment rail shows
"◐ structure only — row counts weren't readable with this credential". The
control beside those marks is "provide broader credentials ›", which opens
the existing credential update (`update_database_credentials`, #256) with a
connect test before saving (follow-up logged 2026-09-30). No new UI beyond
that control.

**Where the credential goes: RE's own field now, the Egeria Connection via
the secrets collection on publish, as today.** The §1 "index, not source of
truth" recommendation stands. RE holds the credential encrypted in its
registry; publishing writes a Connection whose embedded secrets-store
Connection names the collection, and RE projects the credential into the
`.omsecrets` file. A labelled Egeria Connection per capability tier
("catalog", "read", "stats") is the right end state, because a survey
definition could then declare which tier it needs and Egeria's engine host
would pick the Connection, but it is not this moment's UI: the gating flow
works with one Connection per database whose secret is replaced on
elevation, and nothing shipped depends on more than one. Build the tiered
Connections when a native survey definition first needs a tier the
registration credential lacks, and the gating flow is where that need will
show up.

## 2 · The `.omsecrets` file: load-bearing, and refreshed per engine action

Both unconfirmed points were answered by what happened on 2026-09-29 and
2026-09-30, by observation rather than by tracing `ConnectorBroker`; the
Java trace is still worth doing for the long-lived-connector case, and the
observation is stated as what it is.

- **Load-bearing: yes.** A native Egeria survey of `laz_local_adventureworks`
  ran through this connection on 2026-09-29 (14 minutes, 638 annotations
  read back). The quickstart redeploy later that day reset
  `/deployments/secrets/` to shipped defaults and removed
  `resource-explorer.omsecrets`. The next native survey failed inside
  Egeria with `FATAL: role "default" does not exist`: with no secret, the
  JDBC connector fell back to the container's OS user. So RE's local path
  never reads the file, as the ask says, and Egeria's native path reads
  nothing else.
- **Refresh timing for this deployment: the next engine action reads the
  current file.** After the owner re-projected the credentials on
  2026-09-30, the next native survey submission succeeded with no engine
  host restart and no hour's wait. That matches the fresh-connector case in
  the ask (each governance action instantiates its connector, `start()`
  finds `secretsTimeout` already expired, the file is read). Whether a
  long-lived connector elsewhere in the engine host would still hold a
  stale secret for up to `refreshTimeInterval` minutes is not observed; it
  would matter only for a connector reused across runs, which the survey
  path does not appear to do.
- **Consequences already acted on:** a Run precondition now refuses with
  "Egeria has no credentials for this database · re-project secrets" when
  the configured secrets path lacks the collection, and warns "can't
  confirm Egeria has credentials · secrets path not configured" when no
  path is set; `EGERIA_SECRETS_STORE_LOCAL_PATH` must be set for the guard
  to protect anything. Logged follow-ups: a `reproject-secrets` command and
  re-projection at startup and resync, so a redeploy heals itself without
  a typed password; a setup-doc line that the quickstart redeploy resets
  the secrets directory.

## What this does not decide

The §1 model ruling the ask defers to (one Connection with a replaced
secret versus labelled Connections per tier) stays open as stated above,
with a trigger named: the first native survey definition that needs a
tier the registration credential lacks.
