# Suppressing an RFA: "not applicable" and "won't do"

**Status:** implemented. Not applicable and won't do dismissals for RFAs, built.

## Where this came from

Looking at the live RFA drawer on 2026-08-31, the project owner reported ten items they could
neither understand nor act on:

> "On the screen, I now see 10 RFAs - and I don't know what they mean or what
> do about them - and there seem to be duplicates - they all seem to be
> SecurityHygieneCheck 3 - there is little description and it seems that
> another option should be Ignore (or Not Applicable) because the user,
> generally can't do something about no SECURITY.md file found on repos they
> don't control?"

Four distinct problems, and only the fourth is this document:

1. **Mislabeling** — passing checks and measurements were being typed
   `RequestForAction`. One root cause in `survey_orchestrator.py`'s `by_step`
   grouping; fixed, with `tests/test_rfa_mislabeling_fix.py`.
2. **Thin descriptions** — `explanation` / `action_requested` /
   `action_target_name` existed on every annotation and were never forwarded
   past the activity log. Fixed.
3. **"Duplicates"** — not duplicates. Two genuine survey runs nine hours
   apart. The drawer now collapses repeat occurrences of one finding behind
   an "N earlier occurrence(s)" toggle.
4. **No way to say "this doesn't apply here"** — this document.

On how it should behave, the project owner was specific:

> "suppress with visibility - do both - there may be a future admin setting
> that allows you to reset or clear some of these decisions in the future
> (for example, you become a maintainer)"

Both halves of that shape the design: suppression is real (the item stops
being an outstanding action), and it is visible (the item is still there, and
still says who decided what).

## What it is not

**Not a fifth `rfa_status`.** `rfa_actions` already carries
open / deferred / reassigned / completed, and adding `dismissed` there would
have been the smaller diff. It is wrong for two independent reasons:

- **It is keyed by the wrong thing.** `rfa_actions.id` is
  `"{entry_id}::{annotation_index}"` — an identity for one occurrence in one
  activity-log entry. Every survey run writes a new entry, so a dismissal
  recorded against that key would silently stop suppressing the next time the
  survey ran. That is precisely the case the feature exists for: nobody is
  going to add a `SECURITY.md` to a repo they don't own, so the finding
  recurs on every single run, forever.
- **Those rows sync to Egeria.** `rfa_egeria_sync.sync_rfa_action` turns an
  `rfa_actions` row into a real Egeria ToDo. "We are not going to act on
  this" is a local judgement about our own view of someone else's repo, not a
  governance task for anyone to carry out. Putting it in that table would
  push a ToDo into the catalog for every dismissal.

**Not a delete.** Nothing is removed. Clearing a dismissal is an `UPDATE`
that stamps `cleared_at`/`cleared_by`, so "we decided this was not
applicable, then changed our mind" stays readable. That is what makes the project owner's
future-maintainer case a one-line state change rather than an undelete of
something that was thrown away.

## The content key

    key = sha256(entity_type ␟ entity_slug ␟ analysis_name ␟ summary)[:32]

Whitespace-normalised and case-folded. Derived in exactly one place —
`ProjectRegistry.rfa_dismissal_key()` — called by both the write path and the
read overlay, because a dismissal that hashed differently from the lookup
meant to find it would suppress nothing, with no error anywhere.

The tuple is deliberately the same one the drawer already groups
same-finding occurrences by (`index.html`'s `byFinding` map: `analysis_name`
+ `summary`, within an entity). One dismissal therefore suppresses exactly
one rendered group — including the occurrences a future run has not produced
yet.

### Known limit, disclosed

A summary carrying a changing measurement — `"Repository disk footprint:
209.7 MB across 699 files"` — produces a different key on every run, so a
dismissal against it stops matching. For a measurement that is arguably
correct: the number changed, so the judgement may deserve revisiting. For a
finding that is really the same one, it is wrong.

Measured against live data on 2026-09-01: every real RFA summary is stable
text (`"No SECURITY.md found"`), and the one measurement-bearing row
(`FileSizeAnalysis`) was itself a symptom of problem 1 above and is no longer
an RFA after that fix. So this bites nothing today. If a measurement-bearing
RFA appears later, the fix is a normalised key on that annotation type — not
a looser match here, which would trade a precise miss for a silent
over-suppression.

## Visibility

`GET /api/activity/rfas` returns dismissed items **like every other item**,
carrying `dismissed: bool`, `dismissal_key`, and the whole `dismissal` record
(reason, note, who, when). It never filters them out.

That is deliberate and load-bearing. A server-side filter would make a
suppressed finding indistinguishable from one that never occurred — the
absence-as-answer shape this codebase keeps paying for. The drawer decides
presentation: live findings render normally, dismissed ones collapse into an
"N suppressed ▸" toggle per resource, dimmed, showing the reason, the note,
and a Restore button.

The drawer badge counts open **and not dismissed**, so a suppressed finding
stops nagging — which is the "suppress" half doing its job.

## API

| | |
|---|---|
| `POST /api/activity/rfas/{rfa_id}/dismiss` | `{reason, note, dismissed_by}`. Takes an occurrence id because that is what the drawer has in hand; records against the finding's content, resolved from the activity entry. |
| `POST /api/activity/rfas/dismissals/{id}/clear` | `{cleared_by}`. Reverses it, keeping the row. |
| `GET /api/activity/rfas/dismissals?include_cleared=` | The review listing a future admin surface reads. |

`reason` is one of `not_applicable` / `wont_do`, validated once, in the
registry — an unrecognised value would otherwise be stored and then match no
filter, reporting success while suppressing nothing.

## Deliberately not built

- **No admin gate.** `PATCH /rfas/{id}` (defer / reassign / complete) is
  ungated today; gating only dismissal would recreate exactly the
  two-surface auth drift that was just removed from the feedback routes. The
  right fix is one consistent posture across the RFA surface, which belongs
  with the centralised-admin question already in the backlog — not a gate
  invented here on one endpoint.
- **`created_by` is not authenticated.** It is the Egeria service-account id
  from `/api/egeria/whoami`, not a logged-in person — the same caveat
  `repo_dispositions.decided_by` already carries. Read it as a note about who
  was at the keyboard, never as an authenticated fact.
- **No bulk "dismiss this finding everywhere".** Ten repos with no
  `SECURITY.md` need ten dismissals today. Whether one judgement should span
  resources is a real question (it is close to a policy, not a dismissal) and
  is worth answering deliberately rather than falling into.
