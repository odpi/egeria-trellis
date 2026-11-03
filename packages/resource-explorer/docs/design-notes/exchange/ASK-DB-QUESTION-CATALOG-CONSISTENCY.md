# Database question catalog — two consistency decisions

**For:** the designer session.
**From:** the coordinating session, 2026-09-23.
**Read against:** `main` at `0f2158af`.
**Replying to:** nothing directly — raised by
`DB-FS-SURVEY-QUESTION-COVERAGE-AUDIT.md` (same folder, merged in #229), which
this doc quotes from. Read that doc's §2.1 and §2.4 for full context; this ask
restates only what needs a ruling.

**Action needed:** two independent decisions. Neither needs drawings — a
sentence naming the choice is enough to unblock the next catalog-correction
pass.

## Decision 1 — retire "who owns this" for database/filesystem, or keep it as an honest permanent gap?

`database_questions` and `filesystem_questions` both carry:

> Who owns this resource (accountable owner), and who administers it?

wired to `repository_health + chaoss_metrics (contributor identities and
concentration)` — an analysis that reads **git commit history**. A database
or filesystem has no commit graph; "who commits most" has no equivalent for
either type. Ownership for these types is either an Egeria
`Ownership`/`ProjectCharter` classification (Enrichment, human-supplied) or
nothing.

Two options:

1. **Retire the question for database/filesystem** — replace its `Answering
   Analysis` with the Enrichment-supplied ownership field that already
   exists (matching how several other rows already declare `kind: human`),
   since the concept ("who is accountable") is real but the *mechanism* named
   today cannot ever apply to these types.
2. **Keep it as a permanent, honest `gap`** — the question stays meaningful
   without a machine-computable answer, and `gap` already means exactly "no
   analysis produces this," so leaving it there costs nothing except a
   slightly misleading `note` (which can be reworded to stop naming an
   analysis that structurally cannot apply).

The audit recommended (1) on the grounds that the current `note` names an
analysis that cannot apply, which reads worse than an honest "human-supplied
only" `kind` — but did not treat that as a ruling. Which of the two, and does
this generalize (are there other repo-shaped questions worth auditing for the
same pattern once this one is decided), or is this a one-off?

## Decision 2 — one bar for "built, no results reader," or the bar `data_class_match` already broke?

Three sibling analyses (`db_derived.py`'s per-column matching family) all
have the identical disposition: a real, registered `analysis_catalog.yaml`
entry (`action: survey`), a real survey step that produces a result, but
**no results reader wired** into `DATABASE_ANALYSIS_RESULTS_MAP` — so the
question layer cannot show anything from a completed run, only that it ran.

- `data_class_match` — reclassified from `gap` to `analysis` in an earlier
  PR, despite this disposition.
- `reference_data_match` and `nested_column_profile` — left as `gap` in the
  #229 audit, using the bar "a real results reader must exist, not just a
  registered analysis," on the grounds that "built and registered" isn't the
  same claim as "the answer-lookup path can show something."

Both cannot be right at once. Two ways to resolve it:

1. **Loosen the bar to match `data_class_match`'s precedent** — "ran, no
   stored view yet" is good enough to leave `gap` behind (matching repo's
   own `repository_health` precedent, which the audit notes is already
   accepted as honest for a similar disposition). Reclassify
   `reference_data_match` and `nested_column_profile` to `analysis` too.
2. **Tighten to match the #229 audit's bar** — a results reader is required.
   Downgrade `data_class_match` back to `gap` until its own results reader
   exists.

Which bar, applied to all three consistently? This also unblocks §2.4's
follow-on: `docs/Backlog.md` already logs the underlying local-store gap
("Database per-column match results have no local store" —
`upsert_finding()` requires a registered repo `Project`, which a database
survey doesn't have) as separate, harder work; this decision is only about
what the *catalog* claims in the meantime, not about building that store.

## Reply

A `REPLY-` or `RULING-`-prefixed doc in this folder, same convention as prior
rounds (`RULING-SUBRESOURCES-PLACEMENT.md` etc.). Both decisions are
independent — a ruling on one does not require or block a ruling on the
other, and either can land as its own small catalog-correction PR once
decided.
