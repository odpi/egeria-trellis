# Renaming `project` → `resource`, and `group` → `org`

> **Status (2026-11-19, 6.3 Tech Preview):** **not done.** The code and CLI still use `Project`, `ProjectRegistry` and `project_slug` (the `projects` table); no rename of `project` → `resource` or `group` → `org` has landed in 6.3.

**Status: plan, not started (2026-08-28).** Sequenced behind in-flight work — see §5.

## 1. The vocabulary, as it now stands

| term | means | today's code |
|---|---|---|
| **Resource** | one thing in the inventory — a repo, a filesystem, a database, and in principle any kind | `Project`, `ProjectRegistry`, `project_slug`, `projects` table |
| **Org** | a group of resources | `group`, `group_create`, `group_assign` |
| **Project** | an **Egeria Project** — nothing else | collides with the above |
| **Investigation** | one body of work; members are resources | already correct |

`docs/investigation-framing-design.md` §7 recorded that "project" meant three different things and
called it half the problem. Those three were separated in the domain; the code kept the old word
for all of them.

**`resource`, not `repo`.** RE ingests repos, filesystems and databases through one
`ResourceTypeAdapter` funnel, and a metadata repository was deliberately absorbed as "just another
resource type" rather than a new concept. `repo` is accurate for most rows today and wrong for
rows that already exist.

## 2. What renames

- `ProjectRegistry` → `ResourceRegistry`; `Project` → `Resource`
- `project_slug` → `resource_slug` throughout (parameters, columns, dict keys)
- `projects` table → `resources`, and every table with a `project_slug` column
- `group_*` CLI commands and their registry methods → `org_*`
- `_CTYPE_LANGUAGE`, route paths (`/api/projects/...`), UI labels, help text

## 3. What does not rename, and why

**Egeria-side names stay.** `entity_egeria_project_context`, `egeria_project_guid`,
`set_investigation_egeria_project` all refer to real Egeria Projects. Those are correct already and
renaming them would recreate the collision from the other direction.

**pgvector collection names — but see §4.** Collections are `f"{project_slug}_{ctype.name}"`
(`ingestion/pipeline.py:134`), so slugs are baked into live vector-store collection names. Renaming
the *identifier* is mechanical; renaming the *data* means migrating every collection.

## 4. The wipe changes the shape of this

A pgvector wipe-and-reingest is wanted eventually — "not urgent but clean". **It should happen
with this rename, not separately.**

Doing them apart means either carrying a legacy `{project_slug}_` wire format indefinitely behind
renamed code, or migrating collection names once and wiping them again later. Doing them together
makes the only genuinely hard part of the rename free: there is no data to migrate if the data is
being rebuilt.

Sequence: export the resource inventory (slugs, URLs, collections, org membership, investigation
membership) → rename the code → wipe pgvector → re-ingest from the export. The export is worth
building regardless; nothing today can reproduce the inventory if the store is lost.

## 4a. Decision (2026-08-28): all three layers land together, with the wipe

Layer 1 — Python identifiers — was **done and proven** on branch
`re/vocabulary-rename` (`ad1e1ee`, off `4ad6133`): 526 NAME tokens across 54 files, full suite
green at 2739, app builds, registry reads all 60 live resources.

> **Correction (2026-08-31): this section said `ad1e1ee` is "not merged and should not be". It
> was merged, upstream, via PR #22 — contrary to this decision — and what merged is narrower
> than "layer 1" reads.**
>
> Measured on `origin/main` rather than inferred from the plan:
>
> | | renamed | still old |
> |---|---|---|
> | `project_slug` → `resource_slug` | 507 | **282** |
> | `ProjectRegistry` → `ResourceRegistry` | 0 | **524** |
> | `class Project` → `Resource` | 2 | 9 |
> | `/api/projects` route paths | 0 | **44** |
> | quoted `"project_slug"` keys | 0 | **32** |
> | SQL tables/columns | 0 | **24** |
> | `group_slug` → `org_slug` | 0 | **173** |
>
> So `ad1e1ee` renamed the **`project_slug` identifier** and nothing else — the 526 NAME tokens
> were overwhelmingly that one name. The classes, routes, table names and the whole `group` → `org`
> half are untouched.
>
> **The consequence is a mixed vocabulary, and it is worse than either endpoint.** `resource_slug`
> now outnumbers `project_slug` 507:282 while every class is still `Project*`, so nothing in the
> code says which vocabulary is authoritative, and a reader cannot tell intent from usage.
> `AliasRequest.project_slug` — called out in §4b below as the one place layers 1 and 2 are the
> same edit — is now half-done. This is upstream, so odpi has it too.
>
> **What this does not change:** the sequencing argument below still holds, and the wipe still
> gates layers 2 and 3 for exactly the reasons given. Only the claim about what has already
> happened was wrong. Re-measure before starting rather than trusting this table, which is itself
> a snapshot — see SESSIONS.md on timestamped claims.

**The durable artifact is the procedure, not the branch.** The rename is a script plus three
hand-judgements; re-running it against whatever the code looks like at wipe time is cheaper and
safer than nursing a branch that drifts every time someone writes `project_slug`. Expect
`ad1e1ee` to be stale by then and treat it as a worked reference, not a merge candidate.

### The procedure, so it can be re-run rather than reconstructed

**1. Use a tokenizer, not a regex.** Rename `NAME` tokens only, never `STRING` tokens:

```python
for tok in tokenize.generate_tokens(io.StringIO(src).readline):
    if tok.type == tokenize.NAME and tok.string == 'project_slug':
        ...record tok.start, edit by position, in reverse
```

A line-based pass was tried first and failed instructively: it classified lines as SQL by
keyword, which cannot see a continuation line inside a multi-line `CREATE TABLE` — that line has
no keyword to find. `project_slug TEXT NOT NULL,` was renamed as an identifier, schema columns
diverged from the queries reading them, and the suite produced 105 failures and 1,013 errors.
The fault was asking the wrong question, not writing a leaky regex.

**2. Named SQL bind placeholders are decided PER SITE.** `:project_slug` contracts with the key
of the dict feeding it, not with the schema, and the two are indistinguishable at the
placeholder — you have to read the caller.

| site | dict built as | placeholder |
|---|---|---|
| `registry.py`, `github/stats_fetcher.py` | quoted `"project_slug": ...` | **unchanged** |
| `tests/test_agents.py` | `dict(resource_slug=...)` kwargs | **renamed** |

Renaming them uniformly broke 150 tests. Uniform is the wrong instinct here.

**3. Pydantic fields are the wire key.** `QueryRequest.project_slug` and
`AliasRequest.project_slug` are the one place layers 1 and 2 are the same edit. Layer 1 handled
them with `alias="project_slug"` plus `populate_by_name`, so the code reads the new vocabulary
and `index.html` keeps working. **When all three layers land together the alias is unnecessary**
— rename the field and the frontend in the same pass, and delete the alias rather than shipping
it.

**4. Expect assertions keyed on a name rather than on the property the name had.** A rename
surfaces them the way closing a gap surfaced a test that had hard-coded a question as its
example of an unanswered gap.

## 5. Sequencing

1. **Admin repair operations land first.** They are in flight and touch `registry.py`,
   `cli/main.py` and the web routes — exactly the rename's surface. Renaming underneath them would
   guarantee conflicts in a checkout several sessions share.
2. **Build the inventory export.** Independently useful, and §4 depends on it.
3. **Wipe and re-ingest** from the export, and **rename in the same pass** — all three layers at
   once (identifiers, wire keys and route paths, SQL columns). Decided 2026-08-28: the wipe is
   what makes the SQL-column layer free, since there is no data to migrate when the data is being
   rebuilt, and one vocabulary change reviews better than three. A half-migrated vocabulary is
   worse than either end state.

## 6. Guardrails

- A single mechanical pass, reviewed as one diff. No behaviour changes smuggled in.
- The full suite (2680 passed, 10 skipped) is the gate. It exercises the registry heavily, so a
  missed rename in a query string surfaces there rather than in production.
- `tests/test_no_silent_success.py` keys its baseline on `path::function`, so **renaming a function
  makes its baseline entry stale and fails the ratchet.** Expect to rewrite baseline keys; that is
  a rename, not a regression, and the file may still only shrink in count.
- Investigation membership, dispositions and working sets all key on `entity_slug` with an
  `entity_type` of `"repo"`. That literal string is data, not an identifier — decide deliberately
  whether it becomes `"resource"`, and if so migrate the rows.
