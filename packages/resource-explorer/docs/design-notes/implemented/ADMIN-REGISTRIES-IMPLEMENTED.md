# Admin registries — implemented

**Replies to:** `SPEC-ADMIN-THE-FOUR-GAPS.md` §4, "The two registries, which
are not the same size" — one of four deferred `/next` Admin panels (Reconcile,
Groups, Discovery Sources, and this one), built concurrently in separate
worktrees.

**Branch:** `re/admin-registries`, worktree `.claude/worktrees/wt-admin-registries`.
Nothing was written in the main checkout.

---

## Half 1 — Annotation types (pure UI gap)

The backend routes (`POST/PUT/DELETE /api/analyses/annotation-types(/{type})`)
already existed in `analyses.py`; `next/admin/annotation_types.js` made zero
write calls. Added:

- **Register** (`+ Register annotation type`), **Edit** and **Delete**
  against the existing routes, ported from classic's
  `showRegisterAnnotationModal`/`showEditAnnotationModal`/`saveAnnotationType`/
  `deleteAnnotationType` (`index.html`) — same field set (type key, display
  name, description, properties, Egeria class, Python class), type key
  immutable once registered (disabled on edit, matching classic).
- **A real blast-radius number for delete**, going further than classic
  (which only asked a bare `confirm("Are you sure...")`). RE keeps no
  durable local table of individual annotation instances by type — the real
  `AnnotationType` value only ever lives in Egeria itself or transiently in
  `egeria_outbox.payload_json` (purged on completion). The best cheap, real,
  indexed number is `project_published_annotation_types` — one row per
  (project, type) per publish — so a new route,
  `GET /api/analyses/annotation-types/{type}/usage`, returns
  `{"projects_published": N, "exact": false, "note": "..."}` via a new
  registry method, `count_projects_published_annotation_type`. The delete
  confirmation shows that `note` verbatim: *"Locally recorded as published
  for N project(s) — a lower bound, not a full annotation count... 0 here
  means 'no local publish record', not 'unused'."* A failed usage lookup
  (network error) is shown as **unknown**, not silently treated as zero —
  per the spec's §4/§0 instruction directly.

Existing coverage (`tests/test_web.py::TestAnnotationTypesRouter`) already
covered register/get/update/delete; added two tests for the new `/usage`
route (404 on an unknown type, and the honest-lower-bound assertions on a
freshly-registered, never-published type).

## Half 2 — Question catalog (backend gap too)

`GET /question-catalog` was the only route; there was genuinely nothing to
call. This needed real new backend surface, not just UI.

**Decision (project owner, 2026-09-20):** the catalog is **append-only**. You
may add a new question and retire an existing one. You may never reword an
existing question's text — editing a question changes the meaning of answers
already recorded against it, and append-only avoids that by making a wording
change require a genuinely new question rather than a silent edit.

**What was built:**

- `resource_explorer/surveyors/question_catalog_writer.py` — `add_question()`
  and `retire_question()`, writing `docs/dr-egeria/resource_questions.csv`
  (the real source of truth, confirmed by reading `question_catalog_reader.py`
  and the two generator scripts that consume the CSV) and regenerating
  `configdata/question_catalog.yaml` from it via the *same* `generate()`
  function `scripts/csv_to_question_catalog_yaml.py` already uses — so a
  write can never emit a shape that script's own byte-identical guard test
  would reject. Concurrency: a `FileLock` (already a transitive dependency)
  serializes read-modify-write, and both the CSV and the regenerated YAML
  are swapped in via temp-file-then-`os.replace()`, so a reader never
  observes a half-written file. **This is the minimal viable fix for a
  low-traffic admin form, not a migration** — a CSV cannot give the
  transactional guarantees a shared Postgres table would under real
  concurrent write load; if this pane ever sees that kind of traffic, that
  migration is the next step, not a bigger lock.
- A new `Status` column on the CSV (empty = active, `"Retired"` = retired),
  added to both generator scripts' `NON_PERSPECTIVE_COLUMNS`/
  `OPTIONAL_LEAD_COLUMNS` lists (the same by-elimination mechanism that
  already burned this CSV once — `Catalog History` became a phantom
  Perspective for two days in 2026-09-12 before that list caught up).
  Regenerated both the committed YAML and
  `docs/dr-egeria/questions/scouting-questions.md` from the updated CSV —
  the `.md` diff is empty (Status is never emitted to Egeria, exactly like
  `Purposes`), confirming the addition is inert for existing rows.
- Two new routes on `analyses.py`'s existing router:
  `POST /api/analyses/question-catalog/questions` (add) and
  `POST /api/analyses/question-catalog/questions/retire` (retire). Add 400s
  if the question text already exists — active or retired — rather than
  upserting: **the backend itself refuses an edit-in-place**, not just the
  UI declining to offer one. Retire 404s on an unknown question, 400s if
  already retired.
- `question_catalog_reader.py`'s `QuestionCatalogEntry` gained a `retired`
  bool (from the Status column). `get_questions()` does not filter it out.
- `next/admin/question_catalog.js`: an "+ Add question" form (question text,
  stage, why-important, rationale, answering mechanism, perspective
  checkboxes sourced from `GET /api/analyses/perspectives?scope=all`) and a
  "Retire" button per active row. Retired questions stay listed, dimmed,
  with a lifecycle chip (reusing the same icon+tone+label pattern as the
  existing `answering.kind` STATUS chips) — never hidden, since a past
  survey answer still refers to a retired question exactly as it was asked.

**Tests** (new backend logic, given real coverage rather than a note):
`tests/test_question_catalog_writer.py` — append adds a row and regenerates
the YAML correctly; add refuses a duplicate of an active question; add
refuses reusing a *retired* question's text (closing the "retire then
re-add" loophole around the append-only rule); add rejects an unknown
perspective column or an unknown Purpose *before* writing anything (both
files stay untouched on refusal); retire flips only the Status field and
appends to Catalog History without touching anything else; retire is not a
delete (row count unchanged); retire 404s on an unknown question and 400s on
a double-retire; and a structural check that the module exposes no
`update_question`/`edit_question`-shaped function at all. `tests/test_web.py`
adds route-level tests (isolated from the real committed CSV/YAML via the
same monkeypatch technique) proving the routes wire to the writer and
translate its errors to the right status codes, including the 400-on-duplicate
case as the route-level proof that editing is refused even hit directly.

## Verification

`uv sync --all-packages --extra dev` then `uv run pytest tests/ -q` — full
suite, 0 failed (see PR for the exact count). The shared dev server at
`localhost:8810` serves from the main checkout, which this worktree's changes
haven't reached yet (per this repo's own `CLAUDE.md` on worktrees), so the
new UI could not be click-tested live; reviewed statically instead
(`node --check` on all four touched/new JS files) and via the backend test
suite exercising every route and writer function these panes call.

## Out of scope / left as found

- Reconcile, Groups, and Discovery Sources — built by concurrent sessions in
  their own worktrees, not touched here.
- No versioned-questions system, no edit-with-a-marker: the append-only
  decision is retire-and-add, full stop, on both the UI and the backend.
- `question_catalog_writer.py`'s CSV-lock approach is explicitly named as
  the minimal fix, not a migration — see its module docstring for when a
  database-backed catalog would become the right call instead.
