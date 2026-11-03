# Curate: a reload no longer forgets the commit it was watching (implemented)

A status-from-proof-rows gap: the commit stepper, its 2 s record poll and its 60 s survey watch lived only in the tab
(`commitUi` in `curate-scope.js`), so a reload or a second tab showed nothing of a commit that was still running.

## The route

`GET /api/catalogue-scope/{slug}/commits/latest` (new, read-only). The existing routes were `GET .../commits` (every record, heavy)
and `GET .../commits/{id}` (one record, needs the id the page no longer has); this returns the NEWEST catalog commit for the database:
`{commit, terminal, age_hours, stale_unfinished, states}` where `commit` is the curation record (its steps and run state, which the
run writes from proof rows and the read-back settles), `terminal` is `state in (done, failed)`, `stale_unfinished` is a non-terminal
commit requested more than 6 h ago (it did not finish), and `states` is `derive_commit_state(...)["schemas"]`, the proof-derived state
of every schema. `commit` is null when nothing was ever committed. It writes nothing and never contacts Egeria.
Declared before `/commits/{curation_id}` so `latest` is not read as an id.

## The page

On load (`renderCatalogueScope`), once per database per page load, the pane reads that route. No tab-local state is involved, so a
reload and a second tab read the same registry rows.

| Newest commit | What the page draws |
|---|---|
| not finished, requested within 6 h | the section opens by itself; Panel B (the steps) is drawn; the record is followed (2 s) to its end and, while its survey is open, the 60 s read-back watch resumes |
| finished within 24 h | Panel B drawn (and the watch resumes only if its survey step is still open) |
| finished earlier | one line, `last commit <id> · <when> · <outcome>`, with a `show steps` disclosure; never the list by default |
| not finished and requested over 6 h ago | the same one line, outcome `did not finish`; not watched |
| none | nothing |

Guards: no write on load (the first read-back is the 60 s tick, not the load); never more than one watch on a page (a new watch
stops the previous); the watch stops at a terminal state and does not read while the page is hidden. The 2 s record poll is the page's existing one (a cheap registry read that ends at the terminal state); a failed read of the route leaves the page working and draws nothing. A commit this tab starts itself keeps
its own state and is never replaced by the read.

**Deviation from the brief, noted:** the 60 s watch resumes for any commit (non-terminal or finished) whose survey step is still
open, because that is the page's existing rule; a finished commit with a finished survey starts no watch.

## What the owner will see

Start a commit, reload the page (or open a second tab on the same database): the Catalog section is open on its own, the
numbered steps are there as they stood (`step 6 of 9 · running: Egeria survey`, elapsed time, annotations so far), the survey
keeps being read every 60 s, and the rows follow. A commit from days ago appears as one quiet line with `show steps`.

## Tests

`tests/test_catalogue_latest_commit.py` (route, temp SQLite: none, newest wins, each run state, stale, proof-derived states, two
readers, other record kinds ignored, route order) and `frontend-build/test-harness/curate-resume-commit.test.mjs` (fake fetch, fake
clock: running, finished within 24 h, finished yesterday, failed, stuck, none, route failing, a second tab, one watch, terminal and
hidden).
