# Database surveys reflect the connecting credential's visibility, silently — how should RE model that?

**For:** the architecture session.
**From:** the coordinating session, 2026-09-24.
**Read against:** `main` at `de967c05`.
**Replying to:** nothing directly — raised by a live bug report against
`coco_pharma` (below), and by the project owner's own framing of the fix
("something more fundamental" than a permissions grant).
**Action needed:** a ruling on where credential-capability modeling belongs
(RE vs. Egeria) before any code gets written. Not a drawing — a decision on
the three questions in "What needs an answer" below.

## The incident that surfaced this

Scouting `coco_pharma` (a real Postgres database, `localhost_docker`), "How
big is this database" reported **3 tables, 32 columns, 0 rows, 344 KB**. The
project owner, looking at the same database in pgAdmin, saw **9 schemas**,
one of which (`coco_ods`) alone has **23 tables**, one of which (`customers`)
has **20 rows**. First read: a data-accuracy bug in `schema_inventory`.

Traced instead of assumed. Connected directly as the exact credential RE
uses (`egeria_user`) and reproduced the discrepancy outside RE entirely:

```
information_schema.schemata (privilege-filtered) as egeria_user:
  coco_ods, coco_sus, eu_sales, public, target_sales, us_sales   (6 of 8 real schemas)

pg_namespace (unfiltered) — the real schema list:
  coco_ods, coco_sus, demo, demo_auth, eu_sales, public, target_sales, us_sales

has_schema_privilege('egeria_user', 'coco_ods', 'USAGE')  -> true
has_table_privilege('egeria_user', 'coco_ods.customers', 'SELECT')  -> false

Tables egeria_user can actually see, per schema:
  coco_ods: 0   coco_sus: 0   eu_sales: 1   public: 0   target_sales: 1   us_sales: 1
  -> 3 tables total. Exactly what the UI reported.
```

`egeria_user` has `USAGE` on `coco_ods` (can see the schema exists) but no
`SELECT` grant on any table inside it, and no visibility into `demo`/
`demo_auth` at all. `information_schema` is privilege-filtered by design in
Postgres — `schema_inventory`'s query is correct. RE is not lying; it is
accurately reporting what the credential it was given can see, with **no
signal anywhere that this is a partial view rather than the whole database**.

Separately, the project owner named the reason this isn't just a one-off
grant to fix: Egeria may hold **multiple credential sets for the same
database**, with genuinely different scopes by design, not by accident —
e.g. (illustrative, not necessarily this environment's real names)
`egeria_user` (narrow, what RE happened to be given), `surveyor` (broader
schema read access, restricted write), and a full-access identity. Which one
a survey runs as determines what it can see and do, and today RE has no
model for that at all: one `db_user`/`db_password` pair per
`DatabaseEntity`, no capability introspection, no indication to the user
that "the answer you're looking at is scoped to this credential," and no way
to pick a broader credential for a survey that needs one.

## Why this is more than a grants problem

Fixing `egeria_user`'s grants on `coco_pharma` would fix this one database.
It would not fix the general case: **any** database RE surveys, connected
with **any** credential, can silently under-report in exactly this shape,
and nothing today tells the user which parts of the answer are "measured
and complete" versus "measured only within what this credential can reach."
That is the same failure class this codebase's own conventions
(`find-absence-as-answer`, the "declared vs. measured" distinction already
built into `interface_surface`/`step_preconditions`/the cost-vector work in
`#241`) exist to prevent for every other kind of absence — this one just
hasn't been named yet.

## Two separable pieces — only one needs a ruling

**1. Make the blind spot visible — small, no architecture decision needed,
worth building regardless of how the bigger question resolves.** A one-time
capability probe when a database connection is established (`has_schema_
privilege`/`has_table_privilege` introspection — never a trial write),
producing a first-class "connected as `egeria_user` — sees N of M schemas
with data, read access confirmed on N of M visible tables, no write access"
fact, surfaced persistently (a banner, or a Scouting-tier finding of its
own) rather than discovered by diffing against pgAdmin. This is scoped,
buildable now, and the coordinating session can dispatch it without
architecture input.

**2. Filter which surveys are even offered by what the credential can
support — this is the one that needs a ruling.** A new axis alongside the
cost-tier gating `#241`/`#247` already built: not "how expensive" but "how
much can this credential even see or do." That means real design questions
this session cannot answer alone:

## What needs an answer

1. **Does Egeria's own `Connection`/`Endpoint` model already support
   multiple named credential sets per database asset**, with some
   declared or discoverable notion of scope (e.g. via `SecuredProperties`,
   multiple `Connection`s linked to one `Asset`, or something else
   pyegeria exposes) — or would RE need to invent and own a
   multi-credential store of its own (a new `database_credentials` table,
   `DatabaseEntity` gaining a list instead of one `db_user`/`db_password`
   pair)? This determines almost everything else below, and is exactly the
   kind of thing this session doesn't have visibility into without asking.
2. **If Egeria already models this, what's the actual mechanism to
   enumerate "the known credential sets for this asset" and pick one for a
   given survey run** — a pyegeria call, a stored property RE reads at
   survey time, something else? If it does not, is inventing that model RE's
   job at all, or does it belong in Egeria/pyegeria as a prerequisite (the
   project owner's own framing: "elevate them is out of scope for RE" —
   does "let the user choose among several already-registered credentials"
   fall on the same side of that line, or the other one)?
3. **Should the analysis/survey catalog declare a minimum required
   capability**, mirroring how `fetch_cost`/`compute_cost` already gate
   tier — e.g. `requires_capability: schema_visible` vs.
   `table_select` vs. `write` — so a survey RE can't usefully run with the
   current credential is filtered out of what's offered rather than run and
   silently under-report? If yes, what's the right vocabulary for the axis
   (schema-visibility / table-read / write, or something coarser or finer),
   and does it live per-step (`StepInfo`) or per-analysis
   (`AnalysisCatalogEntry`)?

## Reply

A `REPLY-` or `RULING-`-prefixed doc in this folder, same convention as
prior rounds. Piece 1 (the visibility banner) does not need to wait for a
reply — it's being scoped as its own follow-up regardless. Piece 2 is
blocked on at least question 1's answer before any registry-schema or
catalog-schema change is worth writing.
