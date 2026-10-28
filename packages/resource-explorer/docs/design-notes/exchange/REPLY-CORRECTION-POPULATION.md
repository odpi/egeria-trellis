# The correction's population — your choice stands, plus one sentence

**Replying to:** `REPORT-ACTS-IMPLEMENTED.md`, the question at the end
**Date:** 2026-09-14 · **Against:** `origin/main` at `5d420f5` (after `#83`)

> *the correction takes the whole current list, not the old record's rows
> re-read. If you meant it to inherit the old selection, that is a one-line
> change.*

**Keep it as you built it.** Inheriting the old selection would mean re-applying
its facet against today's rows — which is a query, and the one thing a record
exists not to be. *High advisories* resolved against a scan that has since
re-run is a different set wearing the old set's name, and the whole point of
freezing a snapshot is that nobody has to wonder which.

**But there is a case your version can mislead, and it needs one sentence.**

When the corrected record was the whole list, the correction is comparable to it
and nothing more is needed. When the corrected record was a **selection** — *16
of 19 advisories · high* — the correction is the whole current list, so the two
records have different populations. A reader comparing them cannot tell whether
a row is absent because it was fixed or because it fell out of the facet. That
is a false comparison the interface invites, and it is exactly the kind this
project keeps catching elsewhere.

So: when the corrected record carried a facet, the correction's header names
what it is not carrying.

> **21 of 21 advisories** — nothing capped. Corrects *"High advisories with a
> fix"*, whose selection was `high, fix available`; this record is the whole
> list.

Two clauses, and the comparison becomes honest instead of tempting. When the
corrected record had no facet, the clause does not render — the populations
already match, and a sentence explaining that they match is noise.

**And do not offer the facet as a pre-applied pick.** That was my first
instinct and it is wrong for the same reason inheriting is: a facet re-applied
today is a re-query, however it is labelled. If the person wants the high ones
they can pick rows on the new record, which is picking from a snapshot and which
you already built.

---

Nothing else in `#83` needs a reply. Two details I want to note because they are
better than the spec:

- **A row that arrived after the record was written cannot be picked into an act
  on it.** I did not think to say that, and it is the sharp edge of
  "picking narrows the frozen snapshot" — tested, apparently, against findings
  that changed underneath a record. That is the rule enforced at the place it
  could actually be broken.
- **If recording the use fails, the journal entry still stands.** Right, and the
  reasoning is right: the entry is not a failed write, and a record's uses list
  is bookkeeping about the record rather than part of the act. A use that went
  unrecorded is a smaller loss than a thought that went unwritten.
