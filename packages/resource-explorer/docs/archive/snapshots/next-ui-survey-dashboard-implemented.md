# Survey and Dashboard pass — implemented

**Status:** implemented. Survey and dashboard pass, 2026-09-10; kept as a record.

**From:** the implementation session, 2026-09-10
**Against:** `Resource Explorer Survey and Dashboard.dc.html`
**Branch:** `re-experimental-ui`, pushed through `b584f33`

Everything in the pass is built, including all three of the "if you only do
three things". The symbol-count question has an answer, and it is not the one
either of us would have bet on.

---

## The symbol count: both numbers are right

`api_structure.symbol_count` is a **live `COUNT(*)`** of
`project_code_symbols` — what is stored and queryable right now.
`code_symbol_extraction.symbol_count` is **what the extractor recorded when it
ran**.

The table carries `UNIQUE(project_slug, file_path, qualified_name)`, so
symbols sharing a qualified name within one file collapse on insert.
Confirmed by measurement rather than by reading the schema: stored rows equal
distinct `(file_path, qualified_name)` **exactly**, on both repos.

| repo | stored | extractor reported | gap |
|---|---|---|---|
| `egeria_git` | 40,994 | 42,116 | 1,122 (2.7%) |
| `kafka` | 82,852 | 87,907 | 5,055 (5.8%) |

Your 40,994 / 42,116 reproduce precisely. Two questions under one name — *how
many distinct symbols are stored* versus *how many the extractor found* — and
the interface could not tell anyone that.

One thing worth flagging: on `kafka` **both were measured at the identical
timestamp**, so this is not a staleness artifact. That is what ruled out the
easy explanation and made me go looking at the unique key.

**The mechanism found three, not one**, on the first repo it ran against:

    symbol_count        40,994 / 42,116   api_structure / code_symbol_extraction
    relationship_count   2,568 /  2,513   api_structure / code_symbol_extraction
    total                   72 /     45   dependency_analysis / data_file_profiling

That third one is why the wording is careful — *"report this name with
different values; they may not be measuring the same thing"*. `total` is two
unqualified fields that were never the same measurement, which is a naming
defect of its own rather than a disagreement. The mark is right to catch it
and wrong to call it a contradiction.

It is now design rule 13, with your framing: a rule and then a mechanism, and
the durable fix is a declared definition per analysis — worth having whether
or not a given disagreement is legitimate.

---

## Survey — all five

**Tier on the row, grouped by tier.** Current stage open, the rest behind
"Other stages · N". As you said, that is what dissolves the four-line warning:
it is now a chip that names the scope, with a retry, and reads `Scope:
scouting` or `Scope: all tiers — stage filter unavailable · retry`.

**Last run and its status on every row**, reusing the matrix's staleness
treatment — the rule under the text, not a recolour. `✓ ran 3h ago` /
`never run`.

**One-line purpose up, changelog behind a link.** The naming history now sits
behind *definition history* on the qualified name, exactly where you put it —
beside where the same idea already goes for verdicts.

**The trap row is last, set apart, with a plan verb.** `Full Survey (all
steps)` carries a `40 steps · all tiers` chip and a *Plan a run…* button
instead of a Run button.

**Run goes through the same preview component** — literally the same one now;
`openDialog` is exported from the matrix module rather than reimplemented. It
names step count, what runs locally versus what Egeria coordinates, what the
survey writes, whether it auto-publishes, and that everything it covers will
be measured again.

**A finding of my own while building it:** all of this was already in the
payload — `survey_kind`, `last_run_at`, `last_run_status`, `steps`. The pane
was rendering `step_count`, which is `null` on every candidate, so the step
count never appeared at all. Same deferred-against-the-adapter mistake as the
panes themselves, one level down: I read the field I expected rather than the
field that was there.

---

## Dashboard — all five

**Three ranks.** Headline (the composed score, once, at size, with its
sub-scores) · findings (unresolved first) · counts (a table, at reference
weight). You were right that twelve bordered cards holding one number each is
a table drawn expensively.

**One state vocabulary.** The enums render in the matrix's own glyph set now:
`∅` established nothing, `⚠` needs a person, `◐` qualified, `✓` answered. This
is the change I would keep if I could only keep one.

**The group-header date is gone.** Dates are on measurements.

**The radar chart is cut**, on your reasoning. If it comes back it plots the
four published sub-scores on 0–100 in this app's palette.

**The summary tiles are cut too** — that is the `82/100` versus `82.2`
disagreement. Rather than pick which survives, the headline now appears once,
which removes the second rendering entirely.

---

## Two things I would flag back

1. **The dashboards read is slow enough to shape the design.** On Analysis it
   is 109s for one repo, because this aggregation re-runs the same results
   readers. It renders progressively now and no longer blocks the event loop,
   but a pane whose content takes two minutes may want the projection
   treatment the matrix got, rather than better loading copy.
2. **`relationship_count` agrees at 5,166 across both analyses on kafka but
   differs on egeria_git** (2,568 / 2,513). Agreement and disagreement on the
   same pair of analyses, repo to repo, suggests the collapse is
   content-dependent — which is consistent with the unique-key explanation and
   worth a look when the definitions get declared.

## Still open

- The digest threshold still wants a fortnight of real lists; the log is
  collecting.
- Survey and Dashboard now have a design pass. Understanding does not — it is
  the third pane that was deferred and turned out to be one GET away, and it
  has never been looked at.
