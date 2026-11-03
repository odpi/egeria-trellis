# Resource Explorer — Admin Guide

**Status:** live. Operator guide; the best-maintained user-facing document.

**Last revised:** 2026-10-14

This guide covers installation, configuration, Egeria integration, scheduled analyses, and troubleshooting.

---

## Requirements

| Component | Minimum | Notes |
|-----------|---------|-------|
| Python | 3.12 | 3.13 supported |
| uv | any recent | package manager |
| PostgreSQL/pgvector | pg17 | RE's own vector store + registry — required for RAG; shared with Egeria Advisor's `egeria_advisor` database in a Trellis checkout, or a standalone instance for a from-scratch environment |
| SQLite | 3.35+ | ships with Python; optional fallback for the registry only, via `REGISTRY_DATABASE_URL` |
| Ollama | any | default LLM backend; can use OpenAI/Anthropic instead |
| Egeria | 5.x | required for catalog/survey; optional for RAG-only use |
| PostgreSQL (target databases) | 12+ | only needed for surveying *external* databases via the database surveyor — unrelated to RE's own storage backend above |
| `ast-grep-cli` | ≥0.45.1 | **not** a system prerequisite — a `uv sync` dependency, same as any Python package. It's wheel-distributed with prebuilt binaries for macOS arm64/x86_64, manylinux aarch64/x86_64, and Windows, so `uv sync` provisions the binary and `uv.lock` pins its version; there's nothing to `brew install`. Backs Architecture Recovery's code-marker detection (see below). |

---

## Installation

```bash
git clone https://github.com/PDR-Associates/resource-explorer
cd resource-explorer
uv sync
uv sync --extra dev --extra phoenix   # dev tools + Arize Phoenix tracing

cp .env.example .env
# Edit .env — see Configuration section below
```

---

## Configuration

All settings are in `.env` (loaded via `resource_explorer/config.py` using Pydantic Settings).

### Required

```bash
GITHUB_TOKEN=ghp_...          # GitHub personal access token (repo scope)
```

### LLM backend (default: Ollama)

```bash
LLM_BACKEND=ollama            # ollama | openai | anthropic
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# If using OpenAI:
LLM_BACKEND=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# If using Anthropic:
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-8
```

### Model tier and compile limits

`EXPLORER_MODEL_TIER` picks a machine profile: `dev` (the default), `demo-gpu` or `demo-cpu`. A tier sets the
Ollama model, `num_ctx`, the RAG token budget and the two compile limits below. `LLM__OLLAMA__MODEL` overrides the
tier's model. The resolved values are logged at start-up on the `LLM tier resolved:` line.

The chat compiles stored analysis results into the prompt as evidence. Two limits shape that compile:

| Setting (tier preset) | Env override | Default, every tier | Meaning |
|---|---|---|---|
| `compile_evidence_budget_chars` | `EXPLORER_COMPILE_EVIDENCE_BUDGET_CHARS` | 6000 | Characters of evidence in the prompt. Whole integer, 1 or more. |
| `compile_max_sections` | `EXPLORER_COMPILE_MAX_SECTIONS` | 12 | Ranked evidence sections that compete for the budget. 0 means no cap. |

Precedence is the same as for the model: the env var if set, then the tier's preset. An unusable value (not an
integer, or below the minimum) is logged and ignored, so a typo falls back to the tier's value rather than stopping
the server. Both values are in each compile's identity (`compile_id`), and the compile panel shows the budget and
the cap beside the other manifest rows. The multi-resource compile (compare, integration) uses the same budget and
measures its matrix and preamble as assembled; it holds no reserved share, so there is no setting for one.

**Why 12 sections.** Measured 2026-09-09 on the three experiment repos, 6 questions each, at budget 6000:
uncapped, about 26 sections share the budget and only 22% reach FULL detail (the rest sit at SUMMARY, which the
model then narrates as if it were the answer). With the cap at 12, 88% of sections reach FULL and 52 of 54
top-ranked sections are at FULL, with the budget (median 5,610 characters used), not the cap, deciding the last
sections in. A cap of 8 gives 90% FULL but leaves the budget under-used (median 4,368). The comment above
`MAX_EVIDENCE_SECTIONS` in `context_compile.py` has the full table.

**Keep the budget consistent with `num_ctx`.** The budget is characters, `num_ctx` is tokens. 6,000 characters of
evidence is roughly 1,500 to 2,000 tokens, which fits comfortably in the 8,192-token window the demo tiers use, with
room for the instructions, the question, tool results and the answer (`llm.ollama.max_tokens`, 4096). If you lower
`num_ctx` below 8,192, lower `compile_evidence_budget_chars` with it; a prompt that overflows the window is
truncated by Ollama without warning. Raising the budget on `dev` (32,768 tokens) is safe well past 6,000, but
re-measure the cap if you do, since the cap was chosen at 6000.

### The web UI

`/` and `/next` serve the one UI. (The Classic UI and `RE_DEFAULT_UI` were removed 2026-10-21.)

```bash
# Optional. The URL a browser on another machine uses to reach this RE (the same value as the
# Portal's EGERIA_RESOURCE_EXPLORER_URL). A published work list's WorkingSet carries
# additionalProperties.resourceExplorerUrl = <this>/next?worklist=<slug>, so the Portal can link back.
# Unset: no link is written (there is no localhost default).
RE_PUBLIC_URL=http://<box-hostname>:8810
```

### pgvector / registry

```bash
# Defaults already point at the shared egeria_advisor Postgres instance
# (localhost:5442, database egeria_advisor, schema resource_explorer) — no
# .env entries needed in a Trellis checkout. Override for a from-scratch
# environment:
PGVECTOR_HOST=localhost
PGVECTOR_PORT=5442
PGVECTOR_DBNAME=egeria_advisor
PGVECTOR_USER=egeria_advisor
PGVECTOR_PASSWORD=advisor

# Registry defaults to the same Postgres instance/database, own schema. To
# fall back to local SQLite instead:
REGISTRY_DATABASE_URL=sqlite:///data/registry.db
```

### Egeria

```bash
EGERIA_PLATFORM_URL=https://localhost:9443
EGERIA_VIEW_SERVER=view-server
EGERIA_ENGINE_HOST=engine-host
# RE's daemon identity: RE's own Egeria account (see "RE's daemon account" below).
# Development bootstrap; not for a shipped product.
EGERIA_USER_ID=resourceexplorernpa
EGERIA_USER_PASSWORD=…
# Optional login-form prefill; never the daemon's userId (which is never offered or accepted)
RE_LOGIN_DEFAULT_USER=
# Further Egeria platforms RE may send a credential to (comma separated); the platform above is
# always allowed, any other URL a resource names is refused before a request is made
EGERIA_ALLOWED_PLATFORM_URLS=
EGERIA_KAFKA_ENDPOINT=localhost:9092

# Default governance zones assigned to new assets when cataloging (JSON list)
EGERIA__DEFAULT_CATALOG_ZONES=["data-lake","analytics"]

# Default governance zones for survey-triggered asset creation
EGERIA__DEFAULT_SURVEY_ZONES=["survey-zone"]
```

Zone names can also be specified per-operation via the Egeria publish panel in the web UI (comma-separated text field), or via the `zone_names` field in the `POST /api/egeria/{slug}/publish` body.

**Egeria is a hard dependency as of 2026-09-04**, because it is also the identity provider — see
"Authentication" below. Beyond signing in, it is required for:
- `resource-explorer survey --publish` (writes to Egeria catalog)
- Egeria-native database survey
- RFA lifecycle management
- Reading Egeria annotation results back into the survey report

### Who RE calls Egeria as

**Decision (project owner, 2026-10-09):** a person's direct actions run as that person; queued and
background work runs as RE's daemon identity, with the requester recorded.

- **Inside a browser request** (bulk operations, link repairs, reads, publishes, inline outbox
  drains): the signed-in person, on their Egeria bearer token. With no sign-in, or an expired one,
  the call is refused (401), never retried as anyone else. An Egeria security refusal is shown as
  "refused by Egeria" with Egeria's own sentence.
- **Queued work** (surveys, analyses, a queued Publish, Curate commit or catalog commit): RE's
  daemon identity (see "RE's daemon account" below), with the person recorded as the run's `requested_by`, in the
  element's `additionalProperties.requestedBy`, and in its Ownership classification (the owner
  declared on the resource's Context when there is one, otherwise the requester). A queued
  person-action with no requester recorded fails rather than writing anonymously.
- **Background loops** (scheduler, the outbox drain loop, bootstrap, resync, reachability): RE's
  daemon identity. The **outbox drain loop runs as the daemon**.
- **Per-resource Egeria credentials are no longer used.** The Egeria user/password once typed into
  the database-server, database and file-system registration dialogs are neither accepted, stored
  nor read; a request that still sends them has them ignored, with a log warning that names the
  fields (never the values). A resource's Egeria URL and view server still choose *which* Egeria,
  under the allowed-platforms rule above. Values stored before 2026-10-09 remain in the registry's
  columns until the owner decides how to clear them.

### RE's daemon account (Brief L)

Background and queued work runs as RE's own Egeria account, `resourceexplorernpa`: a DIGITAL
account in Egeria's platform user directory, in its `governanceEngine` and `omagServer` lists
(ITProfile `ITProfile::ResourceExplorer`). Egeria's administrator creates and maintains the
account; RE holds only the credential it presents.

- **Today: `.env`.** `EGERIA_USER_ID` / `EGERIA_USER_PASSWORD`. **Development bootstrap; not for a
  shipped product.** RE logs one INFO line at startup naming the source, e.g. `daemon identity:
  'resourceexplorernpa' from .env (EGERIA_USER_ID) — development bootstrap; not for a shipped
  product` (never a password).
- **The product source is an open decision**, pending a discussion with the Egeria lead. The
  options: an RE-side secret store or file, or an Egeria API path (being researched). Whatever is
  chosen replaces one function, `egeria_clients._daemon_credential()`, and nothing else.
- **The password in `.env` and the account's password in Egeria's user directory must be the
  same.** Egeria 6.2 caches users: editing an EXISTING user (its password or its lists) takes
  effect only after a platform restart (pyegeria ISSUE-130).
- **The daemon is a service account, not a person.** An interactive sign-in as its userId (the
  login form, a Portal token, or a raw Egeria bearer on the agent endpoint) is refused with "this
  is Resource Explorer's own service account; sign in as yourself", and the public login-form
  defaults (`GET /api/auth/defaults`) never offer it: they return `RE_LOGIN_DEFAULT_USER` when
  set, else nothing. Anyone who used to sign in with the old shared persona in `EGERIA_USER_ID`
  now signs in as themselves.
- **Where to see it.** The connection popover's "Background work as" row and `GET
  /api/egeria/whoami` (`daemon: {user_id, source}`).

### RE in Egeria's actor graph (7h)

- **At startup** (the worker's one-shot bootstrap, as the daemon) RE ensures the ITProfile
  `ITProfile::ResourceExplorer`, a UserIdentity `UserIdentity::<EGERIA_USER_ID>` and their
  ProfileIdentity link. It adopts existing ones by qualifiedName, creates only when Egeria says
  they are absent, and refuses (creating nothing) on duplicates, on a UserIdentity for the same
  userId under another qualifiedName, or on a link to another profile. A changed daemon userId
  gets its own UserIdentity on the same ITProfile; the old link stays (RE has no delete path).
  The outcome is one log line and the popover's "RE identity in Egeria" row: `present`,
  `created (...)` or `could not check (reason)`. Startup never waits on it.
- **Ownership for a person** names their profile (owner = the profile's qualifiedName,
  ownerTypeName = its type, ownerPropertyName `qualifiedName`, as Egeria's own OpenLineage
  cataloguer writes it) when their UserIdentity is linked to one, else their userId
  (`UserIdentity`/`userId`). The lookup is read-only, cached (5 minutes; a failure 1 minute),
  memoized per press and bounded (15 s); a failure falls back to the userId form and shows in the
  popover. RE never creates an identity for a person, and never sets `Ownership.userIds`.
- **Known limits.** Two RE deployments with separate registries pointed at one Egeria could both
  create on a first run (Egeria has no upsert by qualifiedName; the next start refuses the
  duplicate and reports it). An ITProfile or UserIdentity in a zone the daemon cannot read comes
  back as no element, so it reads as absent. Both are open questions for the Egeria lead.

### Authentication

Login is required on every non-public path (`docs/runtime-architecture-plan.md` §4). Users sign in
with an **Egeria user id and password**; RE exchanges the password for an Egeria bearer token once
and issues its own session JWT carrying that token. No password is ever stored or signed into a
token.

Set these in `.env` (like every other RE setting) or as real exported process env vars — a real
env var still wins over `.env` either way. `RE_JWT_SECRET`/`RE_PORTAL_SECRET` each have a
`TRELLIS_*`-named alternative (`TRELLIS_JWT_SECRET`/`TRELLIS_PORTAL_SECRET`) for a compose overlay
that feeds in the app-agnostic name instead; the `RE_*` name wins if both are set.

```bash
# Required in any deployment. Without it (neither RE_JWT_SECRET nor TRELLIS_JWT_SECRET) RE
# derives a per-host secret and logs a warning — workable on a single-box checkout, but
# sessions do not survive a move to another machine.
RE_JWT_SECRET=<a long random string>

# Only if this deployment accepts Portal SSO. Must match the Portal's own shared secret.
RE_PORTAL_SECRET=<shared with the Egeria Portal>

# Optional. Default 8; the effective session length is whichever is shorter, and Egeria's
# own bearer tokens last one hour.
RE_JWT_TTL_HOURS=8
```

Policy knobs, read as `TRELLIS_<NAME>` first then `EXPLORER_<NAME>` (the app-specific one wins):

| Variable | Default | Effect |
|---|---|---|
| `EXPLORER_REQUIRE_LOGIN` | `true` | `false` turns the gate off entirely. **Not a supported deployment mode** — it exists so a test or a one-off experiment can opt out explicitly rather than by deleting the middleware |
| `EXPLORER_ANONYMOUS_READ` | `false` | Dev-box override: unauthenticated `GET`/`HEAD` is allowed, **writes still 401**. That asymmetry is the point — an anonymous reader cannot create anything, so nothing lands in Egeria without an identity behind it |
| `EXPLORER_PUBLIC_PATHS` | — | Comma-separated, **added** to the defaults, never replacing them (an operator adding an A2A path must not silently drop the login route) |
| `EXPLORER_EXPOSE_OPENAPI` | `false` | Adds `/docs` and `/openapi.json` to the allowlist |

**Sessions last as long as Egeria's token** — one hour by Egeria's default, and every token dies
when the platform restarts (the quickstart leaves `rsa.key-id` empty, so the signing key is random
per restart). Browsers re-log-in; the CLI caches a session (`resource-explorer login`) and says
`session expired at HH:MM` when it lapses.

### Admin gating (who may do admin actions)

There is no admin role in the Egeria sign-in. RE has one small, separate admin check, configured in `.env`
(`FeedbackConfig` in `config.py`):

```bash
# Either or both. Unset = no admin configured.
FEEDBACK_ADMIN_TOKEN=<a long random string>   # a shared secret, sent by the browser as the X-Admin-Token header
FEEDBACK_ADMIN_USERS=alice,bob                # signed-in user ids (the session JWT's `sub`) that are admins; also accepts a JSON list ["alice","bob"], a single name, or empty (no admins)
# Local dev only: with no admin configured, let any signed-in user do the require_admin actions below.
# RE_ADMIN_OPEN_WHEN_UNCONFIGURED=true
```

An admin is a signed-in request whose session `sub` is listed in `FEEDBACK_ADMIN_USERS`, or one that presents a
matching `X-Admin-Token`. The browser sends `X-Admin-Token` from the token you enter once in **Admin → Feedback**
(held in `sessionStorage` under `re_admin_token`, for that tab only, and shared with the Classic UI and
`/admin/feedback`). A client-sent `X-Admin-User` header is ignored: until 2026-10-14 it was trusted as sent, so any
signed-in user who knew an admin's name could claim to be them.

**Decision (project owner, 2026-10-14):** with no admin configured, admin actions fail closed; the open behaviour
of 2026-10-09 survives only behind `RE_ADMIN_OPEN_WHEN_UNCONFIGURED=true`, for local dev.

| Gate | What it protects | When no admin is configured |
|---|---|---|
| `is_admin_request` | Curate's feedback listing, Admin → Feedback | **Denies everyone** (the dev flag does not apply) |
| `require_admin` | Publish Queue **Retry**, **Run bootstrap now**, **bulk** Egeria Links resolve | **Denies everyone** ("no admin configured"), unless `RE_ADMIN_OPEN_WHEN_UNCONFIGURED=true`, which lets any signed-in user act |

An anonymous caller is always refused by `require_admin`. Once an admin is configured the dev flag is ignored. The
Next controls read `GET /api/egeria/admin-status` (`{admin, configured, open}`) and show **admin only** on a control
whenever the server would refuse the caller: not an admin, and either an admin is configured or the deployment is
not `open`.
Resolving a single resource's stale Egeria link (the banner on a repo) is open to any signed-in user either way, and
**Delete in Egeria** is not an RE admin action at all: it keeps only the archive-cascade block (ISSUE-117) on archive/delete.

The block is lifted for ONE database at a time, by `RE_ISSUE_117_BLOCK_OFF=<who>/<UTC ISO time>/<database slug>` (the variable keeps its ISSUE-117 name for compatibility), for 60 minutes after the time stamp. Example: `RE_ISSUE_117_BLOCK_OFF=dwolfson/2026-10-21T14:05:00Z/throwaway_db`. Any other shape (a bare clearance, no slug, a bad or future time, an expired stamp) leaves it ON and logs the reason; every other database stays blocked, and an investigation archive (which names no database) stays blocked. The start-up line reads `archive-cascade block: ON` or `archive-cascade block: OFF for <slug> until <UTC>`.

Deletes, for the record:

| Action | RE's rule |
|---|---|
| **Delete locally** (Classic's bulk delete, `/linkage/delete-local`) | Signed-in users only. RE records no creator on a resource, so it cannot yet say "only the creator"; a creator rule needs a column (DDL) and is a later change. |
| **Delete in Egeria** (`/linkage/delete-in-egeria`) | No RE gate: Egeria decides, and the archive-cascade block still refuses every archive/delete while it is ON (per database slug). RE calls Egeria as the **signed-in person**, so Egeria's decision is theirs; a refusal is shown as "refused by Egeria" and never retried as the service account. |

### Governance zones

RE stamps **no zone by default** (owner ruling 2026-10-08: draft is a content status, not a zone). A draft zone exists only if you configure one, and then it is promoted out of on curate-accept:

```bash
# OPTIONAL, no default. When set, unreviewed publishes land in this zone and the worker creates
# it at startup if absent. When unset, RE sends no ZoneMembership at all.
# EXPLORER_DRAFT_ZONE=resource-explorer-draft

# Where curate-accept promotes an element. NO default (configured-only, 2026-10-07): when unset,
# accept clears RE's draft zone and writes no zone ("zones left to Egeria"). Set it only on purpose.
# EXPLORER_PUBLISH_ZONES=<zone>[,<zone>]
# A Curate catalogue commit for a database writes this zone only when it is set, and as its last
# write. Unverified for a second commit (it may lock the service identity, the survey engine and
# the cataloguer out of the element): first use on a throwaway database, with TWO commits.
```

### Who may curate and publish (zones decide)

**Decision (project owner, 2026-10-10):** "rely on Egeria's security model and emulate it - so if zones are used
they can control access - if zones aren't being used then it is open". One check
(`workflows/curate.curation_access`) answers every verdict route (Next branch and blueprint, Classic component and
blueprint, dependency confirmations, node reclassify), every Publish item, the CLI's `curate materialize` and A2A:

1. **Not signed in:** refused.
2. **Portal `curator` or `admin` role claim:** allowed. An interim override until Egeria role appointments are
   read; it skips the zone check.
3. **Zones not in use:** allowed for any signed-in user. "Not in use" means the element, when it is in Egeria,
   carries no `ZoneMembership`, and RE configures no draft, publish or private zone for it
   (`EXPLORER_DRAFT_ZONE`, `EXPLORER_PUBLISH_ZONES` / `egeria.default_catalog_zones`, a private investigation).
   With nothing configured and the element not yet in Egeria, RE reads nothing from Egeria to decide.
4. **Zones in use:** RE asks what Egeria's own access connector would answer
   (`OpenMetadataAccessSecurityConnector.validateZoneAccess`; mirrored in `zone_access.py`, with file and line
   cited) for **every operation the action performs**: a verdict or a scope decision needs `UPDATE_PROPERTIES`;
   a Publish item needs `CREATE` (new) or `UPDATE_PROPERTIES` (existing), `CLASSIFY` (Ownership and requestedBy)
   and `PUBLISH` (the zone change of promotion), and a blueprint also `ATTACH` on itself and on each member
   component and child blueprint. RE reads the element's zones (or, for an element not in Egeria yet, the zones
   RE will give it), the caller's user account first (an unknown or disabled account is refused, as Egeria
   does), and each zone's security access control. A zone named after the caller grants; a zone with no control
   is ignored; a secured zone grants only through its list (`allUsers`, the account-type group, the account's
   groups or roles, `instanceOwner`, maintainer groups). Reads run as RE's daemon account (reason
   `access_check`), which needs the same platform-operator rights the private-zone setup already needs.
5. **Could not check** (Egeria unreachable, or a zone, control or account read failed): refused, with
   "could not check access in Egeria (<reason>)". RE never opens by default when zones might apply.
   This is stricter than the connector, which treats an unreadable control as an unsecured zone.

A verdict's `decided_by` is attribution only: who made the call. It no longer decides who may change the element
next, so two people's decisions on one plan never lock each other out.

**Publish checks each item, twice.** At the press, an item the presser may not publish is skipped and reported as
its own result row, "not permitted · <reason>"; the rest of the press is written. The queued run checks again, as
the requester, immediately before each write and against the element it actually resolved (a component adopted
from Egeria's content pack, an element found already in Egeria, members adopted mid-write), so a zone secured
after the press, or an element the press could not see, is refused rather than written. A Portal curator or
admin role seen at the press is carried to the run.

**Database catalog and publish.** The Catalog commit, every catalog-scope decision (depth, include or leave
out, confirm, override, clear, redeclare), the database publish and the repository publish use the same check
on the resource's own element. The schema and table elements a commit writes are not yet checked one by one. Only a press where nothing is permitted is refused
as a whole (HTTP 403 with the reason). Verdicts are checked before anything is recorded, so a refused branch
batch records nothing. The reason appears on refused rows only.

### Tags and groups in Egeria (Brief T)

**Decision (project owner, 2026-10-10):** "tags should be public", "Use Folio", "Yes, removing a tag removes it in
Egeria". `resource_explorer/curation_egeria.py` owns the mapping; every write goes through the outbox.

| In RE | In Egeria | Outbox kind | Destructive |
|---|---|---|---|
| a tag on a resource in Egeria | a public `InformalTag` (found by display name first; created with qualifiedName `InformalTag::<tag>` only when absent) linked by `AttachedTag` | `informal_tag_link` | no |
| removing the tag | the tag removed from the element (`remove_tag_from_element`): Egeria's `removeTagFromElement` deletes EVERY `AttachedTag` between the two ends, including copies added directly in Egeria | `informal_tag_detach` | yes |
| the resource's group | a `Folio`, qualifiedName `Folio::RE::group::<slug>` (found first, created when absent), the asset a member by `CollectionMembership` | `group_folio_membership` | no |
| leaving or changing the group, or deleting it | the membership removed (`remove_from_collection`) | `group_folio_membership_detach` | yes |

- **RE never deletes an InformalTag or a Folio.** Those are element deletes under the ISSUE-117 block; the owner
  deletes them by hand. A group delete answers with the Folio's qualifiedName and "left in Egeria".
- **Sent with the page's Publish (Brief F, 2026-10-11):** a tag or group change is saved in RE and checked for
  access when it is made; nothing is sent until the resource's Publish (repository, database or file system), which
  sends it as the presser. The Findable band has no send control; `POST /api/curate/egeria-sync` only takes a
  person's `retry` of one dead row (409 otherwise).
- **Only a person's press sends (round 2):** `EgeriaPublisher.publish` no longer sends tags or groups, so analysis
  auto-publish, bulk operations, resync and the CLI (all as the Daemon) never send them, nor an unlink. The presser
  is checked per item for the operations Egeria checks (curation check above); a refused item is retired unsent
  and reads "not permitted · <reason>"; the next Publish asks again. A gated report (no kept survey, no project)
  still lets the rest of the press go when the resource is already in Egeria.
- **Tag spelling:** RE keeps tags lower-case; the spelling as typed is kept in the `curate_tag_added` activity entry
  (no column), and the Publish uses the newest one as the InformalTag's displayName. The qualifiedName is
  `InformalTag::<tag>`; an existing tag is found ignoring case. A qualifiedName already taken under another
  displayName is adopted rather than failing the row.
- **Order:** a link and an unlink of the same tag (or membership) on the same asset apply in the order queued: a
  row waits (pending, no attempt counted) while an earlier opposite row is pending, being sent or retrying, so
  "unlinked" never shows while the tag is still on the asset. A change that loses a race with the drain re-reads
  the plan instead of trusting it.
- **Unlinks follow the destructive-kind rules**: a failed or lapsed unlink is dead at once
  ("not retried: destructive write · …"), the drain never re-sends it, a later sync never re-queues it, and Admin
  → Publish Queue shows no Retry on it. The person presses **retry** on the Findable band, which queues a new row.
- **Who writes:** a change in a person's request is sent inline as that person (the Caller); a left-over row is
  drained in the background as `Daemon(OUTBOX, requested_by=<the row's by>)`. Each row records its own requester
  when it is queued: a publish records its own requester, or none (then nothing is stamped); it is never borrowed
  from another decision's author.
- **Access:** the curation check (above) for each change: `UPDATE_PROPERTIES` on the resource's element plus the
  operation Egeria checks: `ADD_FEEDBACK` to tag and `DELETE_FEEDBACK` to untag
  (`validateUserForElementAddFeedback`/`DeleteFeedback`), `ATTACH` to join a group's Folio and `DETACH` to leave
  one. A refusal records nothing in RE. A group delete checks DETACH on every member first and is refused as a
  whole (403, naming the members) if any is refused; it needs a signed-in person.
- **Not yet in Egeria:** tags and the group stay in RE and are sent by the next repository, database or file
  system publish. Group changes made from the CLI (`group assign` / `unassign` / `remove`) or a CSV import take
  the same access check (the CLI refuses without a signed-in session; an import row fails with "not permitted ·
  …") and reach Egeria at the resource's next Publish.
- **Public:** Egeria 6's InformalTag has no public/private flag; "private" is only a filter on the creator. RE's
  tags are public because RE reuses a tag by name instead of creating one per person.

### Ratings and notes in Egeria (Brief F)

**Decision (project owner, 2026-10-11):** "It goes under the user who is logged in and entering the ratings etc. It
is also possible for another user to login and evaluate the same resource and put in different feedback." /
"Ratings and notes should be public too." / notes are never edited (a correction is a later note). A per-user
public/private choice is backlog. `resource_explorer/feedback_egeria.py` owns the mapping;
`resource_explorer/single_publish.py` is what the page's ONE Publish sends besides the report.

| In RE | In Egeria | Outbox kind | Destructive |
|---|---|---|---|
| a person's newest rated entry (stars, message) | that person's `Rating` on the asset (`add_rating_to_element`, public): StarRating by name (`FIVE_STARS` …), the message as `review`. Egeria keeps ONE rating per user per element and updates the caller's own (FeedbackHandler) | `feedback_rating` | no |
| each journal entry | one `Note` (qualifiedName `Note::RE::journal::<entry id>`, anchored to the asset) in the asset's `NoteLog` (`NoteLog::RE::<asset GUID>`, found by qualifiedName, created attached to the asset only when absent; a duplicate-name refusal adopts the existing one) | `feedback_note` | no |

- **Authorship.** Egeria records the calling user as the author, so a feedback row is sent only in its author's own
  Publish request, as that person (Caller). Rows carry `by` and live under the run id
  `Feedback::<type>::<slug>::<user>`. The background (Daemon) drain never claims a feedback row; a Caller drain
  applies one only when the row's `by` is that caller (otherwise it is handed back untouched: "waiting for its
  author's own Publish"). An admin "Retry" in Publish Queue only returns the row to pending: it still waits for its
  author's next Publish.
- **Failures are not re-sent on their own.** A failed feedback row is dead at once with Egeria's words; Egeria
  unreachable leaves "Egeria could not be reached; nothing was sent". The person's next Publish queues a new row.
- **Never an update or a delete.** RE never calls update/remove for a Note, a NoteLog or a Rating, and never
  deletes an InformalTag, Folio, Rating or Note element: those are the owner's, by hand.
- **Access.** Brief Z's curation check with the operations Egeria checks for feedback, judged on the asset as the
  anchor with the person as maintainer (egeria af400039c2): a new Rating, NoteLog or Note is an anchored member
  (`validateUserForAnchorMemberCreate`, CREATE, OpenMetadataAccessSecurityConnector.java:1965-1990, called from
  OpenMetadataAPIAnchorHandler.java:357; the parent link is unchecked, MetadataElementHandler.java:2229-2313); an
  update of the caller's own Rating is feedback (`validateUserForAnchorAddFeedback`, ADD_FEEDBACK, :2224-2250, via
  OpenMetadataAPIGenericHandler.java:670-686). NoteLog and Note are not feedback types to Egeria
  (OpenMetadataAPIGenericHandler.java:672-675). So a rating asks CREATE + ADD_FEEDBACK, a note CREATE; no
  UPDATE_PROPERTIES. A refusal is a dead row "not permitted · <reason>" for that item only.
- **Documentation sources** declared on a repository are sent by its Publish too (`publish_local_doc_sources`, as
  the database and file system publishes already did).
- **Confirmed.** Line 1 of the Publish band says "confirmed" only when the publish proof's read-back found the
  report with the same annotation count (`repo_publish.confirmation`, the one "same result" function). While that
  report's own outbox rows (by the run id the proof records) are still pending, sending or backing off, it reads
  "not yet confirmed · N sending". Once they are settled the rows decide: none dead and every annotation done is
  "confirmed"; dead rows read "Egeria shows N" (N = the annotations that landed). A proof with no run rows falls
  back to the count read at publish time.
- **Counting a run (round 4):** a re-publish of the same kept survey reuses the run id, so the newest row per
  annotation stands, and "confirmed" is judged against what was queued (the outbox may cap a run), not the survey
  size. A tag or group row with no recorded requester follows the no-requester rule (open only with no zone in
  use). An access re-check Egeria cannot answer keeps the row pending ("waiting to re-check access") and is not
  tried again for five minutes; at press time it is queued, not refused. Refused feedback rows are born cancelled,
  so no retry or revive brings them back.
- **Round 5:** per annotation, done anywhere is done (the qualifiedName is the key); a lone cancelled row is "not
  sent"; a capped run reads "confirmed · N not sent (capped)" with both counts. A no-requester tag/group row waits
  during an outage like any other. A Portal curator/admin seen at the press is carried on the row (`portal_role`,
  from the verified caller only) and the drain re-check honours it. Feedback whose access cannot be read at the
  press reads "not sent · couldn't check access" and goes with the next Publish.
- **Refusals are decided before any row exists** (round 3): a refused tag or group change is recorded as a row
  born cancelled with its words, so no drain can send it; the drain also re-checks each tag and group row as its
  requester before sending it. One press per resource runs at a time, gated or not (409 otherwise).
- **Replaced ratings.** A person's older rating reads "replaced" only once their newer one is in Egeria; until then
  the older one still reads "published MM-DD".

### Observability (optional)

```bash
MLFLOW_TRACKING_URI=http://localhost:5025
PHOENIX_ENDPOINT=http://localhost:6006
```

### Value sampling (optional)

The steps that read real values from a database, data-class and reference-data matching and the JSON/XML
column profile, sample them. What they sample is resolved from four scopes, narrowest wins:

| Scope | Where it is set |
|---|---|
| Global | `SAMPLING_DEFAULTS` |
| Per resource type | `SAMPLING_BY_RESOURCE_TYPE`, keyed by `database` |
| Per database | `resource-explorer database sampling <slug> --set '<JSON>'` (and `--clear`) |
| Per run | the step's `sampling` request parameter, on a Survey Definition step or an engine action |

Each setting is a JSON object with any of these fields:
- `strategy`: `catalog_stats_only`, `head`, `random`, `systematic`, `stratified` or `full`;
- `max_rows`, `max_bytes` and `max_values`;
- `seed`, `time_budget` (seconds) and `strata_column`.

```bash
SAMPLING_DEFAULTS='{"max_rows": 5000, "time_budget": 30}'
SAMPLING_BY_RESOURCE_TYPE='{"database": {"strategy": "random"}}'
```

- **Unreadable deployment settings are ignored, never fatal.** A deployment setting that is not a JSON
  object is ignored with one warning in the log.
- **A database's own setting and a run's setting are strict.** An unknown field (for example `stratgy`) or
  a bad value is refused when it is set, never stored and quietly ignored.
- **Check what a survey will use.** `resource-explorer database sampling <slug>`, without options, shows
  each scope and what a run with no override resolves to.
- **Every result names its scope.** A survey's result records the resolved settings and which scope
  supplied them (`scope`: `global`, `resource_type`, `resource` or `run`).

---

## Starting external services

### pgvector (usually already running)

In a Trellis monorepo checkout, the shared instance is normally already up — managed by `egeria-workspaces-fs`'s `compose-configs/shared-infra/shared-infra.yaml`. Check with:

```bash
docker ps --filter name=egeria-shared-postgres
```

### Standalone pgvector (from-scratch environment, Docker)

```bash
docker run -d --name resource-explorer-pgvector -p 5442:5432 \
  -e POSTGRES_DB=egeria_advisor -e POSTGRES_USER=egeria_advisor -e POSTGRES_PASSWORD=advisor \
  pgvector/pgvector:pg17
```

### Ollama

```bash
ollama pull llama3.1:8b
ollama serve           # starts on :11434
```

### MLflow (optional)

```bash
mlflow server --port 5025
```

### Arize Phoenix (optional)

```bash
python -m phoenix.server.main    # starts on :6006
```

---

## Running the app

```bash
# Web UI (production-ready with uvicorn)
uv run resource-explorer web

# Development mode with auto-reload
uv run uvicorn resource_explorer.web.app:app --reload --port 8810
```

---

## Database storage

The SQLite registry is created automatically at first run:

```
Default location: ~/.resource-explorer/registry.db
Override: REGISTRY_DB_PATH=/path/to/registry.db
```

Back up this file to preserve all registered resources, survey history, activity log, and schedules.

---

## Adding resources

### Git repositories

```bash
uv run resource-explorer add-project https://github.com/org/repo

# Then index it (downloads + chunks + stores in pgvector)
uv run resource-explorer index my-repo
```

### Discovering repos via the web UI

Adding one repo at a time by URL doesn't answer "which repos should we even be looking at." **Scouting → Discover** (shown when no repo is selected) is a general GitHub search: keyword, min stars, language, license, pushed-after, org, topic. Archived repos and forks are excluded by default. Check the ones you want, pick a target group, and **Import Selected** — registration runs in a background thread (catalog-only, no RAG ingestion) and shows up in the Activity log as it completes.

Repeat searches are worth saving as a **discovery source** (⚙ Admin → Discovery Sources, or "💾 Save as new source" right on the search) so you don't retype the same filters. Two source types:

- **search** — the same structured filters as the ad-hoc form, saved under a name.
- **list** — a manually-curated set of `github_url`s. Needed for foundations that don't fit the "one org, one search" model — Eclipse spreads 450+ projects across hundreds of distinct GitHub orgs, and LF AI & Data curates a *member list* of projects living in unrelated orgs (Egeria itself is `odpi/egeria`, nothing to do with the `lfai` org's own governance repos). A `list` source is also how you'd register your own enterprise/internal repos the same way. There's no auto-fetching of an external structured list (CNCF's `landscape.yml`, LFX Insights' API, Eclipse's own project index) yet — paste the URLs in by hand.

If you're pointed at an Enterprise GitHub instance rather than public GitHub, set `GITHUB_BASE_URL` in `.env` (for example `https://ghe.example.com/api/v3`) and restart. It is the single source: discovery, surveys and ingestion all use it, and there is no runtime override.

### Disposition and working set

After scouting a repo, **Scouting → Disposition** records whether it's worth pursuing: `undecided` (default) → `tracking` / `investigating` → `recommended`, `abandoned`, or `ignored`, with a full history of every decision, not just the latest. Fully reversible in any direction at any time — this isn't a linear workflow, so any state can move to any other. `recommended` is the positive terminal state — "decided *for* it," distinct from the group/survey/publish activity that only implies "yes" indirectly. `ignored` means passed-on-early; `abandoned` means you went further and then decided against it — kept distinct so the history reads honestly. `ignored`/`abandoned` hide the repo from the sidebar's default list (behind "Show hidden (N)") — reversible, not a delete; `recommended` stays visible, same as `tracking`/`investigating`.

Separately, each sidebar row has a working-set toggle (👁/🚫) — a personal "not in front of me right now" filter, independent of disposition. A repo someone else carried all the way to Curate can still be toggled out of your own daily view without changing its canonical disposition, and vice versa.

### PostgreSQL databases

```bash
uv run resource-explorer add-database \
  --host db.example.com \
  --port 5432 \
  --name production \
  --display-name "Production DB"
```

Or use the web UI: select **Databases** in the left sidebar → **+ Add Database**.

**Which account to register.** The *catalog identity* only — a read-only
account that can see the catalog and statistics with no table `SELECT`
(`docs/security-model.md` §2). On PostgreSQL that is `CONNECT`, `USAGE` on
the schemas of interest and, for the resilience checks, `pg_read_all_stats`.
Resource Explorer probes the account at registration and shows what it can
see; surveys that need to read rows ask for a *data identity* later, at the
point of use. Do not register an owner or superuser account for surveying.

**Where the password goes.** Encrypted at rest in the registry with a key
from `RE_DB_CREDENTIAL_KEY` (or `TRELLIS_DB_CREDENTIAL_KEY`); with neither
set, a per-host key is derived and a warning is logged — set one for any
deployment that might move machines. The same credential is written, in one
operation, to Resource Explorer's own secrets file for Egeria's engine host
(`EGERIA_SECRETS_STORE_LOCAL_PATH`, normally
`/deployments/secrets/resource-explorer.omsecrets` in the quickstart) so a
native Egeria survey can open the database; an edit to that file is picked
up by the next survey run without a restart. The collection name is fixed
at registration and must not be re-derived if a database is renamed.

```bash
# Encryption key for stored database passwords (any long random string)
RE_DB_CREDENTIAL_KEY=…
# Host-visible path of RE's own .omsecrets projection, read by Egeria's engine host
EGERIA_SECRETS_STORE_LOCAL_PATH=/path/to/deployments/secrets/resource-explorer.omsecrets
```

---

## Surveys

### Manual survey

```bash
# Local survey only
uv run resource-explorer survey my-repo

# Local survey + publish to Egeria
uv run resource-explorer survey my-repo --publish
```

The survey writes a row to `activity_log` and updates `project_file_type_counts`. Run it again to accumulate history for the diff banner and survey history chart.

### Scheduled surveys

In the web UI, open **Analyses** for a resource, scroll to the **Schedule** section at the bottom, and set an interval per analysis. The background scheduler (started automatically with `resource-explorer web`) executes due analyses every 15 minutes.

To check what schedules are configured:

```python
from resource_explorer.registry import ProjectRegistry
r = ProjectRegistry()
print(r.get_schedules('repo', 'my-repo'))
print(r.get_due_schedules())
```

### Architecture Recovery — two new steps, and a new resource cost

Architecture Recovery (Analysis intent, `configdata/analysis_catalog.yaml`'s `architecture_recovery` entry) runs as two independent survey steps rather than one:

| Step | Reads | What it does |
|---|---|---|
| `repo_arch_detect` | a zipball checkout | Package manifests, deployment artifacts (Dockerfile/compose), and `ast-grep` code markers — the deterministic half of the recovery. |
| `repo_arch_coupling` | a zipball checkout **and** a git clone | Import and co-change coupling — proposes the components manifests and deployment files structurally can't see (shared libraries, orchestrators). Needs real `git log` history, not just a checkout. |

Both are **`fast` tier**, measured at **5.3 seconds per repo** for the whole toolchain — that number is the reason the tier is `fast` rather than `minutes`, not an aspiration. Both write into the existing `project_analysis_findings`/`project_analysis_metrics` tables under `kind="architecture_recovery"` — no new table.

**`repo_arch_coupling` is the first architecture-recovery step that clones.** Every other repo-analysis step in RE works off a zipball download (`_acquire_zipball_root`) — a snapshot of files, no `.git`. Co-change coupling needs real commit history, which a zipball doesn't have, so `repo_arch_coupling` is the first step to pull in a second resource type: `git_clone_root`. That introduces network and disk cost this feature's other step, and every zipball-only step before it, never had.

It's cheaper than a normal clone, but not free. `git_clone_root` does a **treeless clone** — `git clone --filter=blob:none --no-checkout` — which fetches commit and tree metadata but defers file *contents* to on-demand fetches, and skips checking out a working tree entirely. `--no-checkout` matters specifically: without it, a treeless clone still checks out HEAD's working tree, which under `--filter=blob:none` means fetching every blob in HEAD from the remote during clone — silently paying for exactly what the treeless filter exists to avoid. See `clone_git_root()`'s docstring in `resource_explorer/github/client.py` for the full reasoning, including why nothing downstream of this provider should read file contents out of the clone root (that triggers a blob fetch per file and erodes the whole point of treeless).

Practically, this means: sizing a deployment that will run Architecture Recovery at any scale should budget for one treeless clone per repo per `repo_arch_coupling` run, on top of the zipball download every other step already pays for — cheap per repo, but a real, new, per-run cost that wasn't part of RE's resource profile before this feature.

---

## Egeria integration

### Catalog a repository

```bash
uv run resource-explorer survey my-repo --publish
```

This runs a local survey, then calls `EgeriaPublisher` to create the repository's own `Asset` element in Egeria with file type annotations.

### Survey a database in Egeria

In the web UI, open a database survey report and click **Catalog & Survey in Egeria →**. This:
1. Creates a `PostgreSQLServer` and `PostgreSQLDatabase` element in Egeria (from templates)
2. Triggers `initiate_postgres_database_survey()` — async Egeria-native survey
3. Simultaneously runs a local schema scan for immediate display

Results from the Egeria survey appear with a ☁ badge when the survey completes (poll with **Re-survey in Egeria** button).

**Which account Egeria uses.** Egeria's engine host chooses the connection
through its security connector, for *its own* user, and Resource Explorer
cannot pass one. Until two upstream defects in that selection are fixed
(`docs/security-model.md` §5), the engine host's user must be able to see
exactly one connection per database asset — the catalog identity. A native
Postgres survey reads only the catalog and statistics, so it needs nothing
more; its most-common-values figures come from `pg_stats` and carry the
same "estimate as of the last ANALYZE" caveat as Resource Explorer's own.

### RFA lifecycle

When a survey produces `RequestForAction` annotations, they appear in the **RFAs** panel. Assign and answer them there. Answers are currently stored locally in `resource_context`; Egeria write-back is planned.

### Question catalog (Scouting "Questions" tab)

The questions shown in Scouting's **Questions** checklist tab, and the corresponding `Question` elements published to Egeria, are both generated from `docs/dr-egeria/resource_questions.csv`. See [`docs/dr-egeria/resource_questions_guide.md`](dr-egeria/resource_questions_guide.md) for the full column reference, editing workflow, and regeneration commands.

### Engine host: Resource Explorer as an Egeria SurveyActionEngine

Resource Explorer's worker can act as an Egeria **engine host**. Egeria then sees RE's Python engine as a
`SurveyActionEngine` named `ResourceExplorerSurvey`, with one request type per step RE can run (today the
database step `db_derived`, as request type `re-db-derived`). When Egeria creates an engine action for that
engine, the worker discovers it, claims it, runs the step and tells Egeria the result, the way a native engine
host does. Design: [`design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md`](design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md).

- **It is off unless you turn it on.** Set `RE_ENGINE_HOST_ENABLED=true` for the worker. Unset or false (the
  default), the worker starts no loop and makes no engine-host call to Egeria. Only one worker runs the loop at
  a time (the `engine-host` leader lock).
- **It acts as a daemon, never as the requester.** Every Egeria call the engine host makes runs under RE's own
  service account. The user who asked for the action is recorded on the run row as a name and is never used as
  a credential.
- **Private work is left alone.** An action whose target is a database in somebody's private investigation is
  left **unclaimed**. The engine host is unattended, so private work never runs as the service account; its
  owner runs it from RE while signed in.
- **What it claims.** Only actions that are approved, not yet claimed, and for a request type RE declares. An
  action with no target, or a target that is not a registered database, is claimed and then completed as
  "survey-invalid" with the reason. If another host claims first, RE skips it quietly.
- **Declaring the engine in Egeria** is a one-off step, run by an admin with the Dr.Egeria runner (next
  section): `resource-explorer dr-egeria run engine-host/resource-explorer-survey-engine.md`. The document is
  generated; do not edit it. Make sure no Java engine host lists `ResourceExplorerSurvey`, or it will race RE
  for every claim.

### The Dr.Egeria runner (ad hoc)

Besides the documents RE applies itself at startup (the bootstrap heal), an admin can run **one** Dr.Egeria
document on request:

```bash
uv run resource-explorer dr-egeria run <path.md>             # run it
uv run resource-explorer dr-egeria run <path.md> --dry-run   # parse and validate; list the commands; write nothing
uv run resource-explorer dr-egeria run <path.md> --force     # run a document already applied
```

`<path.md>` is a Markdown file under `docs/dr-egeria/` (or under `RE_DR_EGERIA_DOCS_DIR` if you set it). Any
other path is refused. There is also an admin route, `POST /api/bootstrap/dr-egeria/run`, with the body
`{"path": ..., "dry_run": false, "force": false}`.

- **Who may use the route.** The signed-in user must be listed in `FEEDBACK_ADMIN_USERS`. The shared
  `FEEDBACK_ADMIN_TOKEN` alone is not enough, and a dry run needs the same right. With no admin list
  configured, nobody passes. The CLI needs shell access to the host instead.
- **It runs as RE's service account**, and one activity-log row (operation `dr_egeria`) records the run, with
  one item per command and Egeria's words for each. A dry run writes nothing.
- **A repeat guard.** Dr.Egeria's *Create* and *Update* commands can be repeated safely, but link commands add a
  second relationship each time. RE names the commands that are not safe to repeat and refuses a second run of a
  document whose content it has already applied (409 from the route). `--force` (or `"force": true`) runs it on
  purpose; confirm first.
- **A verb allow-list.** Only *Create, Update, Display, View, Search, Find, Validate, Link, Attach, Add,
  Classify, Reclassify, Setup* and *Set* commands run. Everything else (Delete, Remove, Unlink, Detach,
  Declassify, Publish, Run, Process, Import and any verb added later) is refused, whatever the document says.

### Admin settings at a glance

| Setting | What it does |
|---|---|
| `FEEDBACK_ADMIN_USERS` | Egeria user ids who count as admin: comma-separated (`alice,bob`), a JSON list (`["alice","bob"]`), a single name, or empty for none. Whitespace is trimmed. A value that cannot be read logs one warning at startup and counts as empty (no admins); it never stops the server. Required for the Dr.Egeria runner route. |
| `FEEDBACK_ADMIN_TOKEN` | A shared secret an admin pastes once in Admin → Feedback (sent as `X-Admin-Token`). Counts as admin for the gates in "Admin gating", but not for the Dr.Egeria runner route. |
| `RE_ENGINE_HOST_ENABLED` | Turns the engine host on (see above). Default off. |
| `RE_DR_EGERIA_DOCS_DIR` | Moves the folder the Dr.Egeria runner reads documents from. |
| `EXPLORER_DRAFT_ZONE`, `EXPLORER_PUBLISH_ZONES` | Optional zones RE stamps; see "Governance zones". |

`RE_ADMIN_OPEN_WHEN_UNCONFIGURED` is **not on main**. It belongs to PR #665 (`re/admin-auth-strict`), which was
still open when this was written. What main does today is what "Admin gating" above says: the admin gate reads
`X-Admin-Token` or a client-sent `X-Admin-User` header, and the Dr.Egeria route requires a listed user. Once
#665 merges, identity comes from the signed-in session only and the client-sent header is ignored; check that
PR's description for the final behaviour of the unconfigured case before relying on it.

### Startup log lines

When the worker starts it checks the zones it stamps and writes one line for all of them:

```
zone controls: <zone> · control present · lists READ, UPDATE_PROPERTIES · DEFAULT yes · ok; <zone> · ...
```

It is logged at INFO when every zone is `ok`. If any zone's access control would lock everyone out, only
allows reading, or could not be read, the same line is logged at WARNING with a note that publishing into that
zone is refused until its control is fixed. With no zone stamped the line reads `zone controls: no zone
stamped`. The private zone is always checked, so the line normally names `resource-explorer-private`.

**Coming (not on main when this was written):** a `connector type: ok` line, from the branch
`re/connector-check-coverage-bar`. It checks that the connector type RE writes secrets through is still on the
expected provider class, and reads Egeria without changing anything. It will also say `MISMATCH`, `not created
yet` or `could not check`. Until that branch merges, no such line appears; do not look for it in the log.

---

## Observability

### MLflow

Visit `http://localhost:5025` to see experiment runs, query latencies, and retrieval scores. Each query creates an MLflow run automatically (non-blocking background thread).

### Arize Phoenix

Visit `http://localhost:6006` to see LLM traces, token usage, and agent spans. Instrumented via `openinference-instrumentation-beeai`.

---

## Upgrading

```bash
git pull
uv sync
uv run resource-explorer web   # schema migrations run automatically on startup
```

The registry schema uses `CREATE TABLE IF NOT EXISTS` guards — new tables are added transparently. No manual migration steps are needed for additive changes.

---

## Troubleshooting

### "Egeria connection failed"

Check `EGERIA_PLATFORM_URL` and that your Egeria chassis is running. Test connectivity:

```bash
curl -k https://localhost:9443/open-metadata/platform-services/users/garygeeke/server-platform/origin
```

### "No module named 'resource_explorer'"

Run commands via `uv run`:

```bash
uv run resource-explorer web     # correct
resource-explorer web            # wrong — uses system Python
```

### Survey history chart not showing

The diff banner and history chart require at least 2 survey runs. Run the survey twice:

```bash
uv run resource-explorer survey my-repo
# wait a moment, then run again
uv run resource-explorer survey my-repo
```

### pgvector connection refused

Verify the shared instance is running:

```bash
docker ps --filter name=egeria-shared-postgres
psql -h localhost -p 5442 -U egeria_advisor -d egeria_advisor -c '\dn'   # should list resource_explorer schema
```

If it's down and owned by `egeria-workspaces-fs`, start it via that project's own compose file — never a new one owned by Resource Explorer. For a standalone from-scratch instance, see "Standalone pgvector" above.

### Tests failing

```bash
uv run pytest tests/ -v --tb=short
```

Pre-existing external-service tests (pgvector, Egeria, GitHub) are skipped when the services are unavailable. All core tests should pass without external services.

---

## API reference

Interactive API docs are available at `http://localhost:8810/docs` when the web server is running.

Key endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/projects/` | List registered repos |
| `POST` | `/api/projects/{slug}/refresh` | Re-index a repo |
| `GET` | `/api/databases/` | List registered databases |
| `POST` | `/api/databases/{slug}/survey` | Run database survey |
| `GET` | `/api/egeria/{slug}/survey-report` | Get the deep survey report (local SQLite — no Egeria needed) |
| `POST` | `/api/egeria/{slug}/survey` | Run local survey only |
| `POST` | `/api/egeria/{slug}/publish` | Run survey + publish to Egeria (accepts `{zone_names:[…]}`) |
| `GET` | `/api/egeria/{slug}/diff` | Get file count delta vs previous survey |
| `GET` | `/api/databases/{slug}/diff` | Get schema delta vs previous survey |
| `GET` | `/api/projects/{slug}/scouting-overview` | Get the light Scouting-tier overview (description, stats, lifecycle badges) |
| `POST` | `/api/projects/{slug}/scouting-scan` | Run the coarse "Repo Coarse Scout" Survey Definition (repo_health + repo_language only) |
| `POST` | `/api/projects/{slug}/analyses/{analysis_id}/run` | Run only the named analysis's sub-surveyor step(s), not the whole survey |
| `GET` | `/api/projects/{slug}/analyses/{analysis_id}/results` | Get the named analysis's latest structured results |
| `GET` | `/api/projects/{slug}/analyses/{analysis_id}/trend` | Get the named analysis's run history, for the trend chart |
| `GET` | `/api/activity/` | List activity log entries (filterable by entity_type, intent, operation, status) |
| `GET` | `/api/activity/rfas` | List open RequestForAction annotations |
| `GET` | `/api/analyses/{resource_type}` | List available analyses (`?intent=…&perspective=…`) |
| `GET` | `/api/context/{entity_type}/{slug}` | Get resource context |
| `POST` | `/api/context/{entity_type}/{slug}` | Save resource context |
| `GET` | `/api/schedules/{entity_type}/{slug}` | Get analysis schedules |
| `POST` | `/api/schedules/{entity_type}/{slug}` | Save analysis schedule |
| `POST` | `/api/query/` | Submit a question (routed by intent — includes `survey_meta`) |
