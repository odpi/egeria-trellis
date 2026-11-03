# Two follow-ups from the credential-visibility work: a gating idea, and an unresolved secrets-refresh question

**For:** the architecture session.
**From:** the coordinating session, 2026-09-24.
**Read against:** `main` at `afafd52c` (after `#250`–`#257`).
**Replying to:** nothing directly — both items surfaced while closing out
`ASK`/`REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` and its
implementation PRs.
**Action needed:** an opinion on both, not a full ruling — neither blocks
what's already shipped, and both feed the still-pending §1 model ruling
rather than requiring their own separate one.

---

## 1 · Gate on minimal privilege, then offer to elevate

The project owner's framing, verbatim: *"It might be that for some
databases, we leverage the stages that we have to see if we can disqualify
a database from consideration early, with minimal privileges, and if it
passes that gate, we allow a user to provide new credentials that support
additional surveys."*

This is the same shape as `#241`'s cost-tier gating (`prerequisite_resolver`
— run cheap, decide whether to pay more before committing further) applied
to a different axis: credential capability instead of dollars/seconds. It
also lines up with the project owner's separate, earlier point about
`interface_surface`'s bundling question — preferring to wire small surveys
together over building bigger ones.

**The concrete shape, as best we can state it without your input:**

1. With whatever credential a database is first registered under — even a
   maximally narrow one, catalog-tier only — run the Scouting-tier steps
   that are already catalog-safe (per
   `DATABASE-STEP-CAPABILITY-AUDIT.md`, `#254`: `credential_capability`,
   `privilege_audit`, `db_external_dependencies`, and now
   `postgres_schema_and_stats`'s catalog-only fallback for structure/names,
   `#257`). This alone can answer "does this database look like it's worth
   more attention at all" — is it empty, is it a duplicate/copy
   (`db_fingerprint` needs `postgres_column_profile`... actually check:
   does a meaningful "worth pursuing" signal exist at catalog tier alone,
   or does it need at least one `read`-tier probe? Worth confirming before
   assuming the gate is real.).
2. If it passes, prompt the user: "this looks worth cataloguing further —
   provide broader credentials to unlock \[read/stats-tier analyses]."
3. The credential provided at step 2 needs somewhere to go. **This is
   exactly §1 of the REPLY's still-pending ruling** — is it a raw
   `db_user`/`db_password` update (`update_database_credentials`, `#256`,
   already built) or a new labelled Egeria `Connection`? If the answer is
   "a new Connection," this gating flow is the natural first real UI for
   building that, rather than a separate later feature.

**What we'd want your view on:**
- Is there a real, useful "worth pursuing" signal available at catalog tier
  alone, or does any meaningful disqualification decision already need at
  least `read`-tier data (in which case the gate's first stage needs to
  request `read`, not just accept whatever narrow credential happens to be
  configured)?
- Should "disqualify early" tie into RE's existing disposition/lifecycle
  concept (Curate's verdict-setting, `undecided`/`certified`/etc.), i.e. a
  database that fails the gate gets proposed a "not worth cataloguing
  further" disposition automatically, or does that overreach what a cheap
  probe should be allowed to conclude on its own?
- Same question as always for this class of idea: does the credential this
  flow requests belong in RE's own field (§1's "index, not source of truth"
  recommendation) or as a new Egeria `Connection` — and if the latter, is
  *this* the moment to build that UI rather than deferring it further?

No code should be built against this until you've had a look — it's a
design direction, not a specced feature.

---

## 2 · The `.omsecrets` file — confirmed wired, refresh timing unconfirmed

Found while checking whether repointing a database's credential (`#256`)
would need to touch anything besides RE's own registry.

**Confirmed, not guessed:**

- `egeria-workspaces-fs/runtime-volumes/quickstart-platform-data/secrets/resource-explorer.omsecrets`
  is a real `YAMLSecretsStoreConnector`-format file (confirmed against
  `open-metadata-implementation/adapters/open-connectors/secrets-store-connectors/
  yaml-secrets-store-connector/`), holding `secretsCollections` keyed by
  name, including `localhost_docker_coco_pharma::PostgreSQL Secret` and
  `localhost_docker_coco_ods::PostgreSQL Secret`, both currently
  `egeria_user`/`user4egeria`.
- `coco_pharma`'s own Egeria `Connection` ("coco_pharma connection", the
  single unlabelled template connection identified in the prior exchange)
  **does reference this exact collection name** — confirmed live via
  `ConnectionMaker.get_connections_by_name`, which returns an
  `embeddedConnections[0]` whose `configurationProperties.secretsCollectionName`
  is literally `"localhost_docker_coco_pharma::PostgreSQL Secret"`. This
  file is genuinely wired to the asset, not an orphaned artifact from an
  earlier redeploy.
- The refresh mechanism, traced in `SecretsStoreConnector.java` +
  `YAMLSecretsStoreConnector.java`: every secret-getter call runs
  `checkSecretsStillValid()`, which calls `refreshSecrets()` (a fresh file
  read) only once `secretsTimeout` — set at
  `now + refreshTimeInterval * 60 * 1000` ms — has passed.
  **`refreshTimeInterval: 60` in this file means 60 MINUTES, not 60
  seconds** — worth stating precisely, since an earlier message in this
  thread said "within a minute," which is wrong by a factor of 60.

**What's genuinely unconfirmed, and is the actual ask:**

`secretsTimeout` is a field on the connector *instance*
(`SecretsStoreConnector`'s `private Date secretsTimeout`), not something
that lives in the file or in shared server state. Whether editing the file
takes effect promptly depends entirely on **connector lifecycle** inside a
running engine host, which this session hasn't traced:

- If each governance-action/survey run causes the `ConnectorBroker` to
  instantiate a fresh `SecretsStoreConnector` for the occasion, `start()`
  calls `checkSecretsStillValid()` immediately on a connector whose
  `secretsTimeout` defaults to `new Date()` (i.e. already expired) — so a
  fresh run would read the current file content right away, and an edit
  takes effect on the *next survey*, no matter when it was made.
- If the engine host keeps one long-lived connector instance across
  multiple runs (plausible for a connector embedded in a re-used
  `VirtualConnection`), an edit could sit uncollected for up to
  `refreshTimeInterval` minutes after the *previous* refresh, not after the
  edit — meaning the actual worst-case delay depends on when the last
  refresh happened, which nothing in RE can observe.

We don't know which of these is true for this deployment's engine host, and
didn't want to guess further without checking the actual `ConnectorBroker`/
engine-host-services instantiation pattern — which is exactly the kind of
Java-source tracing this session has been asking you to do rather than
inferring from behavior.

**Separately, the project owner is unsure this file is genuinely load-bearing
for what RE actually exercises today** — worth confirming whether *any*
native survey has actually been run against `coco_pharma` using this
connection (as opposed to RE's own local execution path, which never reads
this file at all and goes through `databases.db_user`/`db_password`
instead, per `#256`). If no native survey has run yet, the file's wiring
is real but untested in practice.

**What we'd want your view on:**
1. For this deployment's engine host, does a survey/governance-action run
   get a fresh connector instance per run, or a long-lived shared one? This
   determines whether editing the `.omsecrets` file to point at `surveyor`
   takes effect on the next survey or could sit stale for up to an hour.
2. Is there a way to force a refresh deterministically (a restart scope
   narrower than the whole platform, an admin operation, or is a full
   engine-host restart genuinely the only lever)?
3. Given RE's local path and Egeria's native path already resolve
   credentials from two entirely separate places (registry field vs.
   `.omsecrets` file) for the *same* conceptual database connection — is
   that divergence something §1's eventual model is expected to unify
   (RE's registry becomes an index into the same secrets collections
   Egeria's native path already uses), or are they expected to stay
   independent even after the ruling?

## Reply

A `REPLY-` or `RULING-`-prefixed doc, same convention as prior rounds, or a
conversational answer if that's faster for you — this doc's purpose is to
hand over what's been confirmed by tracing rather than to force a
particular reply format.
