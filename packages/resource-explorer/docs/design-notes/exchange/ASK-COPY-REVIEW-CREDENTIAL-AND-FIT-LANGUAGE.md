# A day's worth of new user-facing copy, never reviewed by a designer — worth a pass?

**For:** the Designer session (or whoever owns copy/UX review on this project).
**From:** the coordinating session, 2026-09-25.
**Read against:** `main` at `02a160aa`, plus `#268`/`#269`/`#270` (open, not yet
merged, quoted from their branches below).
**Replying to:** nothing directly — the project owner asked, mid-session,
whether this round of work should have gone through a designer pass. It
hadn't. This collects everything in one place rather than asking about each
string as it shipped.
**Action needed:** a copy/tone/consistency review, not a technical ruling —
everything below is already built, tested and (mostly) merged. This is
asking "does this read well and consistently," not "is this correct."

---

## Why this wasn't reviewed as it went

Today's session ran an extended architecture thread with a separate design
session (`ASK`/`REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md`,
`ASK`/`REPLY-SCHEMA-AS-SUB-RESOURCE.md`) that settled the *mechanism* —
what gets measured, what gets gated, what tier something belongs to. Each
implementing agent then wrote its own user-facing strings to match that
mechanism, the same way `#246`'s prerequisite-proposal copy was written
earlier. Nobody with a copy/UX lens looked at the result as a whole. This
doc is that missing pass, collected after the fact rather than skipped.

## The copy, as it actually ships — not paraphrased

### 1 · The credential-capability shortfall message (`#253`, merged)

`resource_explorer/surveyors/credential_capability.py`, one sentence per
requirement kind, `{who}` is the connected role:

> needs `read`; connected as {who}, which has SELECT on {N} of {M} table(s) —
> the rest are in this database's catalog but out of this credential's reach

> needs `stats`; connected as {who}, which is not a member of `pg_monitor`,
> so the per-table activity and row-count views (`pg_stat_user_tables`,
> `pg_stat_user_indexes`) report only what this role owns

> needs `write`; connected as {who}, which was probed for INSERT (never
> exercised) and does not have it

### 2 · The fact-envelope note when catalog fallback recovered tables (`#257`, merged)

`resource_explorer/facts.py`:

> {N} table(s) visible via catalog (SELECT access: {M} of {N}) as `{who}`;
> row counts for the catalog-only ones are estimates, not exact.

### 3 · Schema rollup vs. average framing (`#266`, merged)

No single user-facing sentence — a structural convention (`is_rollup: True`,
never a bare average) applied per analysis. Worth checking whether the
*structure* (a set of kinds, a per-schema list, a named cross-schema-edges
fact) reads clearly once it reaches a screen, since none of this shipped
with new `/next` rendering — it flows through the existing envelope/
`explanation` text path (`#266`'s own report flagged this: *"a breakdown
living only in `by_schema` would never reach the screen... that is the
honest limit of what ships to the reader today"*). This might be the single
most important thing for a designer to look at: **the data is richer than
what currently renders.**

### 4 · The combined cost + capability proposal sentence (`#262`, merged)

Built structurally (`Proposal.sentence()` joins both axes' `ConsentReason`s
into one sentence, shared by API/CLI/UI) rather than as one fixed string —
worth reviewing a few real combined examples once `#262` produces one live,
since the exact phrasing depends on which reasons are present.

### 5 · `preliminary_fit`'s four verdicts (`#270`, open, not yet merged)

`resource_explorer/surveyors/database/db_derived.py`:

> Preliminary fit: in scope — worth the full pass
> Preliminary fit: out of scope
> Preliminary fit: could not check
> Preliminary fit: no requirement declared
> Preliminary fit: nothing measured to compare *(a fifth, distinct case —
> deliberately not "could not check": nothing about this resource has been
> measured at all yet, so the remedy is "run a survey," not "ANALYZE" or
> "declare a lens")*

### 6 · The Egeria native-processes informational block (`#269`, open, not yet merged, ported from classic unchanged)

> Also known to Egeria for this technology (not yet runnable from here)

## What we'd want your view on

1. **Tone/register consistency** — these five sources were written by different implementing passes over one long session. Do they read as one voice (this codebase generally favors plain, declarative, slightly dry prose — "not established," "the catalog admits to almost none of them" — rather than either bureaucratic hedging or marketing brightness)?
2. **The `preliminary_fit` five-way verdict split** — is five distinct sentences the right number for a user, or should some collapse for display while staying distinct internally (the code already keeps them structurally separate for exactly the reason #16.2's "ANALYZE warning" names — collapsing in the *data* would be the bug; collapsing only in the *rendered sentence*, if two verdicts would read identically to a user anyway, might be fine)?
3. **Item 3's flagged gap** — the schema/rollup breakdown has no `/next` rendering yet, only envelope text. Is a real breakdown view (a small table, an expandable "N schemas" disclosure) worth designing now, or does the plain-text version ship as-is for a first pass?
4. **The credential-shortfall sentences' audience** — they're written for someone who already knows what `pg_monitor`/`SELECT`/`INSERT` mean. Is that the right assumption for whoever reads these (a DBA/data engineer persona, matching this app's stated audience), or do they need a plainer restatement for a less technical Steward/Consumer persona also using this app?

## Reply

A `REPLY-`/`RULING-`-prefixed doc, same convention as other design rounds, or
inline copy edits directly against the strings quoted above if that's
faster — whichever this session's workflow with you normally uses.
