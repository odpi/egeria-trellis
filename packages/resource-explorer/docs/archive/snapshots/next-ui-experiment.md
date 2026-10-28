# The `/next` UI experiment

**Status:** implemented. Narrative of the /next experiment; Next has since become the default UI (2026-10-07).

A parallel web UI at `/next`, served alongside `/` by the same FastAPI app,
implementing the design handed off as *"skin 1c + answer-in-the-row"*: a
"dark chrome, paper content" skin behind a named token layer, and one
redesigned screen — the Questions checklist, changed from reporting **how** a
question would be answered to reporting **what the answer is**.

No endpoint, schema or migration changed. The whole UI is a consumer of
`/api/*`, so this is additive and reversible: if it is a dead end, delete
`static/next/`, `frontend-build/tailwind-next.config.js`, and the `/next`
route.

## What is here

| Path | What it is |
|---|---|
| `resource_explorer/web/static/next/index.html` | the shell — chrome, login overlay, responsive grid |
| `resource_explorer/web/static/next/app.js` | the app; rendering, state, the envelope reader |
| `resource_explorer/web/static/re-api.js` | the API client, as a shared ES module |
| `resource_explorer/web/static/next/fonts/` | self-hosted Cormorant Garamond + Lora |
| `frontend-build/tailwind-next.config.js` | the token layer; a SECOND Tailwind entry |
| `frontend-build/build-next-fonts.py` | regenerates the vendored fonts |
| `web/app.py` `GET /next` · `auth.py` `RE_PUBLIC_PATHS` | the two lines of wiring |

Build the stylesheet with `npm run build:css:next` (or `npm run build`, which
now runs both entries). The two Tailwind builds are independent: `build:css`
still content-scans `index.html` only and emits `static/tailwind.css`, so a
mistake in either stylesheet cannot reach the other UI.

## Scope

The frame, plus **one** pane: Questions, for a repo.

That pane is **parameterised by stage** rather than duplicated per stage —
the questions endpoint already takes `phase`, and this matters for more than
tidiness. Scouting's five questions are `analysis` and `direct` only; `gap`,
`human`, `mixed`, `partial` and `chart` all live in Analysis and Enrichment.
A Scouting-only build would have shipped a screen whose entire argument —
that six row states are scannable as glyph plus sentence — could not be
looked at.

Every other sub-tab (Search, Survey, Dashboard, Disposition) renders "not
built in /next" at full legibility. So do Investigation and Understanding.
Databases and filesystems are repo-only here.

## What the handoff got wrong, verified against the running app

The handoff document says its authors had read-only access and could not run
anything, and asks that claims about existing behaviour be checked. Two were
wrong, one materially:

**"The answer and caveat text do not exist as fields yet."** They do.
`GET /api/analyses/facts/{slug}/answer?question=…` returns a `facts.py`
`Envelope` carrying exactly what a row needs: a per-analysis `state` from
`surveyors/result_status.py`'s vocabulary, a written `headline`, a `note`
that is the caveat, `last_run_at`, `can_run`, and `blocked_reason` when the
question cannot be answered at all. The handoff's preferred option — "the
questions endpoint gains an answer projection" — is unnecessary; the
projection is a second endpoint that already exists. `/next` calls it once
per row, which is also what gives rows their independent arrival.

**"`GET /api/activity/{id}` — poll a run to completion".** Correct, but the
list endpoints it sits beside (`/api/activity/`, `/api/activity/rfas`) page,
with defaults of 200 and 500. A returned length equal to the limit is a full
page, not a total. `/next` renders those as `200+`.

## Three bugs this screen found in its own first hour

Recorded because each is the shape the project keeps rediscovering, and each
was invisible until the screen was pointed at real data.

1. **A tick beside "never run".** Most facts come back with `state:
   "measured"` and an EMPTY `last_run_at`. Rendering an absent timestamp as
   "never run" put a ✓ and a claim that nothing had ever run in the same row.
   Missing timestamp is a gap in the record, not evidence about the
   repository: the three cases are now "run 19d ago", "run time not
   recorded", and "never run".

2. **A tick with nothing under it.** Most analyses have no `headline_reader`
   wired up, so the answer line was empty while the row claimed to be
   answered. There is now a fallback ladder — headline, then the analysis's
   own `detail`/`summary` prose with its own `verdict` word, then the scalar
   measures with a caveat naming the analyses that have no written summary.
   Every rung relays something the analysis wrote; none composes a verdict.

3. **A perspective that matched nothing said nothing.** The "matches nothing
   here" chip was computed from the FILTERED question set, which inverts the
   answer in exactly the case the chip exists for: hold `Privacy`, every
   question is hidden, so the "matched" set is empty too and the chip renders
   as ordinary. It is computed from the stage's unfiltered set now.

There was also a rendering bug worth its own line: `esc()` emitted `&#39;`
for an apostrophe and `tnum()` then wrapped the `39` in a span, so the page
displayed `Egeria&#39;s catalog`. `esc()` uses named entities only now.

## Two decisions still open — they need the design owner

The handoff names both as cheap now and expensive to retrofit. Neither was
answered before building, so `/next` implements the design as designed and
flags them here.

**1. Does state stay hue-coded?** `/next` follows skin 1c: one gold accent,
state carried by glyph and weight. If green and amber must survive, they need
adding as named roles (`state-ok`, `state-warn` — never `green-500`) holding
at least 3:1 against *both* grounds, which the current amber does not manage
on paper.

**2. Which density?** `/next` uses the design system's airy 1.15× scale
(4.6 / 9.2 / 13.8 / 18.4 / 27.6 / 36.8px), which is looser than the current
UI. Spacing is the one thing a find-and-replace cannot reason about, so this
is worth settling before any of this reaches `index.html`.

## Exit criteria — not yet agreed

The handoff is right that "looks better" is not a bar, and that failing to
name one leaves a third thing to maintain. Its suggestion, unchanged and
still needing a yes or no:

> A tester answers the scouting questions for an unfamiliar repo faster in
> `/next`, and can correctly state which claims have not been run.

Meet it, then either codemod `index.html` or grow `/next` screen by screen
and retire the old one — but name which, before starting.

**What this cannot settle**, also from the handoff and worth repeating: it
says nothing about the work list (which needs a batch-enqueue endpoint that
does not exist), nothing about scale while it loads one resource at a time,
and it will flatter itself on performance because a new shell carries none of
the accumulated listeners the old one does.

## Round 2 — the regression review

The design owner reviewed the first build and raised nine losses, with a
correction to their own handoff: *"a parallel UI may defer any affordance,
but it may not silently omit one."* The first pass read "stub the chrome" as
licence to drop things that looked like frame and were function — a sub-tab
rail, a resize handle, a chat drawer, a state legend.

### The two readability fixes

**#1 — text too light.** The diagnosis offered was that the answer line had
picked up a muted token. It had not: the answer line measured `#201f1d` at
14.5px with Lora loading correctly. The real cause was next door — the
*question title* was muted for the unrun, no-surveyor and unclassified
states, and so was the whole body line for those rows. On the Analysis stage
that is most of the page. So de-emphasis had crept back in as fading text,
which is the one thing this palette forbids.

The correction was right even though the cause was not: no line that carries
content uses a muted token now (muted is labels, provenance and metadata
only), the glyph carries the state, and body and answer text went to 15px.

**#6 — no key for the symbols.** Restored as a counted legend under the
stage line — `✓ answered 3 · ✓ automatic 1 · ○ not run 1` — listing only the
states actually present, and re-rendering as rows arrive.

### The honesty pass

**#3** — the four unbuilt sub-tabs now carry the same dashed marker the
unbuilt intent uses, and clicking one opens a panel that says so and links
out. The same treatment reached Activity, RFAs and Admin in the chrome:
their counts stay live, the surfaces link to the current UI.

One thing that panel cannot do is preserve the resource. **The current UI has
no deep link** — its navigation state lives in JavaScript variables, not the
URL — so the panel says plainly that you will have to reselect. `/next` now
has one (`?resource=&stage=&tab=&perspectives=`), which the inventory lists
as a Keep and which neither UI had.

### The keeps

**#7, selection and grouping** — restored whole: the resource-type facet, the
text filter, the five lifecycle scope chips, disposition facets, Select mode
with bulk actions, the group tree, Show hidden, and per-resource status marks
(typographic, not emoji: `▣ ▤ ▢` for published/surveyed/new, `● ◎ ✚` for
using/investigating/recommended).

Two things worth knowing about that:

- Disposition zeros are **collapsed behind a "N more…" control, not
  removed**. The handoff's "show only non-zero counts" was advice about
  noise; as a spec it removed the controls, and a facet you cannot select is
  a filter you cannot undo.
- `GET /api/projects/` filters harder than its parameter names suggest:
  `include_ignored=false` drops **`abandoned` as well as `ignored`**, and
  `include_working_set_hidden=false` drops hidden repos. `/next` asks for the
  full list and filters client-side, or those facets could never be
  populated.

**#8, write paths** — remove, hide/unhide and disposition are on the resource
header, plus their bulk equivalents in Select mode. Notes on each:

- `DELETE /api/projects/{slug}` takes **no confirmation flag of any kind**
  and drops the repo's pgvector collections along with the registry row. The
  only confirmation that can exist is the client's, so it names what is
  going, says it cannot be undone, and says how it differs from marking a
  repo *ignored* — which leaves it registered.
- Disposition is keyed on **`github_url`, not the slug**. A repo with no
  GitHub URL cannot be dispositioned, and the header says that rather than
  appearing to work.
- There are **seven** legal dispositions, not four: `undecided`, `tracking`,
  `investigating`, `recommended`, `using`, `abandoned`, `ignored`.
- Every bulk write reports per-resource. A partial failure never renders as
  success.

**#9, external links** — GitHub and the project site, marked external and
visually distinct from internal navigation. There is **no separate docs
link**: `homepage` is already the derived best link (GitHub's declared
homepage, then the packaging manifest, then the README) and does duty as
both. It lives on the scouting overview, not the summary row, so the header
fetches it per selection and renders without it if that call fails.

**#2, resizable panes** — both seams drag, with keyboard support, and both
widths persist. Bounds are 150–520px for the sidebar and 220–620px for the
rail.

**#4 and #5, the chat drawer** — it toggles, remembers its state, overlays
rather than squeezing below 1100px, and keeps a transcript. Each turn is
labelled with the resource it was asked about and marked when that is not the
current selection. Each answer carries its source line, the three-state
feedback control, and an *Open as candidates* action when the answer looks
like a list.

The feedback vote is **three states, not a thumb pair**: `+1` / `0` /
`-1`, where `0` is "partly right". That middle value is the one that
separates a routing problem from a content problem, and folding it into
either neighbour would lose the signal the vote exists to collect. The
`query_hash` always comes off a server response — never computed here — so a
vote lands under the same key however the answer was produced.

**The current investigation** is in the chrome and switchable from the
sidebar. There is no server-side notion of a current investigation; the
existing UI keeps it in `localStorage` under `re_current_investigation`, and
`/next` reads and writes **the same key**, so switching shells does not
silently change what you are working on.

### A third instance of the same bug

The evidence panel was still saying "never run" for a fact whose state is
`measured` and whose `last_run_at` is empty — the same conflation fixed in
the row last round, in a second place. It now says "run time not recorded"
there too. Worth noting because it is the pattern: the fix went where the bug
was seen, not everywhere the mistake was made.

### Not verified

**The chat success path.** `POST /api/query/` requires a real session, and
the verification instance runs with `TRELLIS_ANONYMOUS_READ=true`, which
permits reads only. The turn's *error* rendering is verified against a live
401; the answer, source line, feedback control and candidates action are
written but have not been seen with a real response. Signing in is what would
settle it.

## Round 3 — the fix round

Working order: `FIX-ROUND.md` in the design bundle, with two reversals of the
original spec and a section on diagrams that the spec never covered.

### Hue is back

The first spec removed hue-coded state because the bound design system is
mono by construction. That was the system's constraint applied where it does
not belong: a list of rows scanned for exceptions is where hue earns its
keep. State is now **glyph *and* colour** — the glyph survives printing,
greyscale and colour blindness and is what the legend keys; the hue is what
makes the column scannable.

The instruction was for named roles "each holding at least 4.5:1 against both
grounds". **No single colour can do that**, and this is arithmetic rather
than opinion: passing on paper needs luminance ≤ 0.159, passing on chrome
needs ≥ 0.222, and that window is empty. So each role has two variants, the
same split that already forced `ink-muted` and `chrome-muted`:

| Role | On paper | On chrome | Used for |
|---|---|---|---|
| `state-ok` | `#1d6b3f` 5.82:1 | `#74c68d` 8.44:1 | answered, automatic |
| `state-warn` | `#9c4212` 5.87:1 | `#f2a26a` 8.40:1 | not run |
| `state-gap` | `#5b4a9c` 6.47:1 | `#a99ae0` 6.94:1 | no surveyor |
| `accent` (gold) | `#7d5411` 5.97:1 | `#e1ad66` 8.58:1 | needs your attention |

**Known risk, stated rather than hidden:** `state-warn` and gold are both
warm and within 1.02:1 of each other in luminance, so on the same ground they
separate by hue alone. Tolerable only because colour is never the sole
channel — the two states carry different glyphs (`○` vs `⚠`) and different
words in the legend. If they turn out to be confusable in use, warn is the
one to move.

Also raised: the chip border was `#d7d3d3` at **1.33:1**, well under the 3:1
a UI boundary needs. Now `#8e8a8a` at 3.05:1.

### Icons in, emoji out — metaphor kept

Two substitutions in the previous round went too far and are reverted to
pictograms, via a vendored Lucide sprite
(`frontend-build/build-next-icons.py` → `static/next/icons.svg`, injected so
`currentColor` inherits):

- **Feedback** — `yes / partly / no` became words, turning a one-glance
  control into reading. Back to `thumbs-up` / `minus` / `thumbs-down`.
- **Sidebar marks** — `▣ ▤ ▢` was abstract geometry needing a key where a
  metaphor had landed. Back to `cloud` / `bar-chart-2` / `sparkles`, plus
  `eye` / `microscope` / `badge-check` / `circle-check` for dispositions.

The rule now followed: nearest Lucide equivalent of the **same** metaphor, at
14–16px, inheriting `currentColor`. Only the six question states use an
abstract glyph, because no metaphor exists for them and a legend keys them.

### The five items from the side-by-side walk

- **Disposition history** restored, under the picker. The field is
  `decided_at` (verified against the endpoint, not guessed), and `decided_by`
  renders only when set rather than showing an empty attribution.
- **Facet count scoping** — `/next` counts every registered repo and now
  **says so** in the label; the current UI counts the investigation's working
  set. One was picked and named, as instructed.
- **GitHub link back on every sidebar row**, as well as on the header.
- **The deferred "Search" stub renamed** to what it is: *Repo discovery —
  find and import candidate repos*. Each deferred sub-tab now states its own
  job rather than inheriting a label that mis-describes it.
- **All perspectives per row**, wrapping. Seeing that a question carries four
  is how you learn the axis barely filters.

### The current UI gained one deep link

"Links out **preserving the current resource**" was not satisfiable: the
current UI had no URL state at all. `index.html` now reads `?resource=<slug>`
on boot and selects it — deliberately narrow and additive, it selects a
resource and nothing else, runs after the normal boot rather than replacing
any of it, does not write the URL back, and quietly ignores a slug that is
not registered. Verified both ways: `/?resource=egeria_workspaces_git` lands
on that repo, and a bare `/` still boots with nothing selected.

This is the first edit to `index.html` in this experiment. It adds no Tailwind
classes, so `tailwind.css` is unchanged.

### Diagrams and charts

Not in the original spec at all. They live in the **content pane, on paper** —
which is a real dividend of the chrome/paper split, since Mermaid, Plotly and
Kroki all default to a light ground and need no dark override there.

- **Token-bound, with the tokens read back off the live stylesheet** via a
  hidden probe element carrying the classes. Restating the palette in JS
  would put it in two places, which is what the token layer exists to
  prevent.
- **`font-diagram`** (the mono stack) for every node label, axis tick and
  legend — the one place the type system is deliberately overridden.
- **A diagram cannot live in the rail** at 290px, so a chart or diagram
  answer shows a marker there and an **"open in pane"** action that promotes
  it to full width, with `svg-pan-zoom` attached for SVG. Both libraries were
  already vendored.
- **Form follows answer shape**, not the model's preference: scalar inline,
  ranked list as a list, anything over time as a chart in the pane,
  relationships as Mermaid in the pane, cross-resource as the work-list grid
  (which does not exist yet).

Mermaid renders server-side through `POST /api/diagrams/mermaid`, which
returns **raw SVG as `image/svg+xml`, not JSON** — a first attempt parsed it
as JSON and would have failed on every diagram.

**Unverified end to end.** Reaching a chart or a diagram needs a real chat
answer, and `POST /api/query/` needs a session the anonymous-read verification
instance does not have. The rendering path, the theme binding and the
promotion mechanism are written and syntax-checked; none has been seen with a
real figure.

## Round 4 — why charts and diagrams did not work

Reported as "chart and diagram don't seem to work". They didn't, and the
cause was not the rendering code: **both were wired only to a chat answer.**
A chart needed the query router to classify the question as statistical,
health or comparison; a diagram needed the answer to contain a fenced
` ```mermaid ` block. Neither is reachable by anyone who has not asked the
right question first, and the chat path needs a session. So the features
existed and had no route to them.

Both now hang off data that is already there.

### Diagrams — on the question that has one

`architecture_diagram` writes its Mermaid source into the fact value, and
Discovery's *"How do its components relate to each other?"* routes to it. So
that row now carries a **`diagram`** action beside `evidence` and `re-run`,
and the evidence panel offers the same. Both promote into the content pane at
full width with `svg-pan-zoom`.

Verified on `egeria_workspaces_git`: the action appears on the right row, the
promotion renders the pane, and the render fails with a stated reason under
anonymous read — `POST /api/diagrams/mermaid` is a write-shaped route for a
read-shaped operation, so an anonymous-read deployment cannot render diagrams
at all. **That is the one part still unverified end to end**; with a session
it should complete, and Kroki is up on 6002.

### Charts — Understanding is where they live

`/api/stats/{slug}/charts/*` serves **seven working Plotly figures** for a
repo. Understanding was marked "not built" on the strength of having zero
rows in the analysis catalog and the activity log — both true, and neither of
them is what feeds a chart. The catalog is empty; the data is not.

So Understanding is now the charts pane: it probes every kind, offers the ones
with series, and keeps the three outcomes apart —

- a figure with data → rendered,
- a `200` with no series → *"nothing recorded yet"*, offered but not
  clickable, because an empty chart reads as a measured zero,
- a failed call → *"unavailable"* with the reason.

**This changes a marking the design made deliberately.** Understanding is no
longer dashed in the intent nav. The justification for the marker was
catalog-shaped and the charts are not; worth putting back to the designer
rather than assuming agreement.

Both chart paths — the Understanding pane and a promoted chat answer — share
one `chartLayout()`, so they cannot drift into two designs. Plotly's own
default template is dropped rather than fought property by property.

### The cache bug that wasted a loop

Editing `re-api.js` and reloading gave
`does not provide an export named 'REPO_CHARTS'` — an error that reads like a
code bug and is a stale module cache. `StaticFiles` sets no `Cache-Control`,
and an ES module already in a page's module map is stickier still.

`/static/next/*` and `/static/re-api.js` now carry `Cache-Control: no-cache`,
which means *revalidate*, not *do not store* — the browser still caches and
still gets a 304. Scoped by exact prefix, so the rest of `/static` is
untouched. If `/next` ever becomes the default, replace it with a content
hash in the filename rather than keeping revalidation forever.

## Round 5 — why the diagram was a blank box

Reported as "the diagram doesn't render — blank space where it should be".
Four separate defects, stacked, each hiding the next. Every one was found by
bisecting against the live Kroki on 6002 rather than reasoning about it, and
each is recorded at its call site because none of them is guessable.

**1. This Kroki rejects every `%%` line.** A plain comment and an
`%%{init: …}%%` directive alike, on a two-node diagram, return
`400 Internal Server Error`. The theming was delivered as an init directive,
so it broke every render it was supposed to style. Theming moved to
`themeSvgElement()`, which styles the returned SVG instead.

**2. A literal `%` in a node label fails the whole render.** `&#37;` and
`&percnt;` are fine. `architecture_diagram` writes confidence as `40% ⚠` into
every label, so the architecture diagram could never have rendered here.

**3. A `class` directive assigning more than 20 nodes fails.** 20 renders, 21
does not, deterministically, whether on one line or split across several. The
real diagram styles 53. The excess assignments are dropped and the pane says
how many were lost — losing the dashed "pending" styling beats losing the
diagram.

**4. The SVG is 14,102 × 193 — a 73:1 strip.** `fit: true` fits the limiting
dimension, so it fitted the width and left the diagram **8 pixels tall**.
That is the blank box. Wide diagrams now open fitted to HEIGHT with the left
edge in view.

### Two bugs introduced while fixing those

Worth recording because both measured as working:

- **An SVG `<style>` is not scoped.** The first theming attempt injected
  `text, span, p { … !important }` into the document, restyling the
  application's brand, nav and sidebar the moment a diagram opened.
- **Prefixing a selector list only scopes the first selector.**
  `#id a, b, c` leaves `b` and `c` global — so the "fixed" version still
  restyled the whole page, and the string *looked* correct. Every
  comma-separated part is prefixed now.

And one that measured as working and was not: after the framing fix, 53 nodes
reported bounding boxes of 96×39 while sitting at `y = -609`, entirely above
their container. "53 nodes rendered at 96×39" and "an empty white panel" were
both true. Hand-rolled pan arithmetic was replaced with the library's own
`center()`.

**Verified:** Kroki 200, 53 nodes, 5 visible in the opening viewport at
readable size, chrome fonts unaffected before and after opening a diagram.
The proxy call itself is still auth-gated, so the render was driven through a
stub that forwards the identical body to Kroki; everything after the fetch is
the app's own code path.

### Also in this round

- **The sidebar mark key.** Each row's disposition mark is now the exact
  glyph its facet chip uses — one `DISPOSITION_ICON` map read by both — so
  the filter row *is* the legend. Lifecycle keeps its metaphors, `hidden`
  moved from `circle-slash` to `eye-off` so it stops looking like `ignored`'s
  `ban`, and a `?` opens a compact key covering all three families.
- **Copy as evidence.** On every question row, on every chat turn, and on the
  transcript as a whole. Markdown, with the caveat as a blockquote and the
  provenance line always attached — an answer pasted without its source loses
  the point of the redesign. Named for what the artifact is for, not for the
  clipboard operation.

## Not started — Round 3, the vertical slice

The bundle's Round 3 asks for a slice through Scouting: search → select →
save as a work list → run one survey across the set concurrently → compare as
rows × questions → bulk disposition → promote the survivors.

**It is not started, and it needs decisions this experiment has not been
given.** It is the first round that is not API-free:

- a **batch-enqueue endpoint** (a set of resources plus an analysis, N rows
  in `runs`, a set id back) and a progress read by set id;
- persisting a work list as an Egeria `WorkList`-classified collection with
  `CollectionMembership` rationale and confidence — a **write to shared
  Egeria**, which is coordinated work on this machine, not a solo change.

The rest of the slice is client work on top of those two.

## Round 6 — the Scouting vertical slice

The bundle's Round 3, built: search → select → **save as a work list** → **run
one survey across the set** → **compare as rows × questions** → **bulk
disposition with reasons** → **promote the survivors**. It is the first
surface in `/next` that reads a SET rather than one resource at a time, which
is the constraint the whole review is about.

| Path | What it is |
|---|---|
| `resource_explorer/work_lists.py` | storage, batch enqueue, Egeria publish |
| `web/routes/work_lists.py` | `/api/work-lists/…` |
| `static/next/worklist.js` | the grid, the batch watcher, the bulk actions |

### The type is `WorkingSet`, not `WorkList`

`WorkList` **does not exist in Egeria** — no hit anywhere in the type archive
or `OpenMetadataType`. `WorkingSet` does, and its own definition is this
feature: *"a list of elements that are being worked on by a process or by a
group of people."*

`WorkItemList` is the neighbouring type and is the wrong one — *"a list of
activities such as ToDos, Tasks"*. A work list holds the **resources** being
worked on, not the activities. If the runs launched across a set ever want
cataloguing themselves, that is what `WorkItemList` would be for, as a second
collection beside this one.

RE already makes `WorkingSet` collections for an investigation's
per-disposition membership, so this is a second producer of the subtype; they
are told apart by qualifiedName (`WorkingSet::resource-explorer::<slug>`).

`membershipRationale` is real, and pyegeria's `add_to_collection` takes an
optional relationship body — so a member's reason travels to Egeria rather
than staying local. The shared creator sends a body **only** when a caller
supplied one, so the investigation path's already-queued rows keep meaning
what they meant.

### The one backend change

`POST /api/work-lists/runs/batch` — a set plus an analysis, one `runs` row per
resource, a `set_id` back — and `GET /api/work-lists/runs/sets/{set_id}` for
progress. The queue already did the hard half (`SKIP LOCKED`, per-user
fairness, dead-worker reconciliation); what was missing was a caller that
enqueues a set and a view that watches it finish.

Progress is **derived from the run rows on every read**, never stored: a
cached aggregate goes stale the moment a worker updates a row without
telling it. A run row that has vanished reports `unknown`, not `done` — an
unobserved run is not a successful one. The watcher shows queued / running /
done / failed *separately*, because "12 of 14 finished" hides that two failed.

### Every cell says which of the six states it is in

The grid's hardest requirement is the question pane's, at higher density: an
empty cell must not stand for "never ran", "ran and found nothing" and
"nothing can answer this" at once. Nine cell states, keyed by a legend under
the grid, including `∅ ran, found nothing` and a distinct `? could not read`
for a resource whose facts failed to load.

One call per resource, not one per cell — `GET /api/analyses/facts/{slug}`
returns every already-judged fact and the columns are looked up among them.
Fourteen calls, not seventy.

### Writes, and what was deliberately not run

Four interactive peer sessions were live and `SendMessage` is unavailable in
this tab, so the usual clearance could not be obtained. The work was split by
blast radius instead:

- **Verified on an isolated SQLite registry** (`REGISTRY_DATABASE_URL`
  pointing at a scratch file, no worker): create with dedupe, `set_member`
  with rationale and confidence, batch enqueue of three runs, progress
  derivation, a missing set returning `None`, and promote recording
  `derived_from` while silently dropping a non-member.
- **Verified with a stub client, contacting nothing**: the outbox payloads a
  publish would queue, and the exact body pyegeria would receive — with
  `membershipRationale`/`expectedConfidence` when supplied and `body: null`
  when not.
- **One work list created in the shared registry** to verify the grid against
  real facts. Safe by the coordination skill's own test: it writes only
  `work_lists`/`work_list_members`, three new tables nothing else reads, so a
  concurrent writer cannot be duplicated or diverged.
- **NOT run: the batch, and the publish.** The first writes `runs` rows that
  a live worker would execute as real surveys; the second reaches shared
  Egeria. Both need peer clearance rather than an assumption.

The seeded list is `Egeria family — scouting` (`wl-b37710b3b4`, two members).
Deleting a work list is local-only and never removes a published Collection —
that is a catalog decision with its own consequences.

## Round 7 — running the batch and the publish for real

Cleared to run both. Both worked, and running them found things the stubs
could not.

### The batch ran — and reported `0 of 2 complete` forever

Two runs were enqueued, a live worker claimed them, and **both succeeded**.
The progress endpoint reported `finished 0/2, complete: false` and would have
done so indefinitely, because the terminal states were guessed:

    invented:  done, failed, cancelled
    actual:    succeeded, failed, cancelled   (ProjectRegistry.RUN_STATES)

`claimed` was missing from the active set too. Nothing errored — the numbers
were simply always wrong, and since the UI polls on `complete`, a finished
batch would have polled until the tab closed and the grid would never have
refreshed. The vocabulary now comes from the registry's own `RUN_STATES`, and
a state in neither the terminal nor the active set is **named** in the
response as `unrecognised_states` rather than bucketed.

### The publish worked, after one bug a stub could not catch

`_default_clients()` returns a **tuple** — `(clients, find_element_guid)` —
not an `OutboxClients`. Unpacking it wrong raises
`'tuple' object has no attribute 'require'`, and that line is only reachable
with a live platform, so no amount of stub testing would have found it.

Then, end to end and verified **independently of the call that wrote it**:

- collection `d8323a5b-ad2a-4775-aadf-acee2df78441`, found by looking up its
  qualifiedName rather than trusting the publish's return value
- `typeName: WorkingSet`, `qualifiedName:
  WorkingSet::resource-explorer::wl-b37710b3b4`
- both memberships drained from the outbox and **confirmed present** —
  `egeria-python` and `egeria-workspaces` are attached. Presence confirmed,
  not "no failures reported"

Only those two outbox rows were pending, so the ordinary `drain_outbox()` was
safe; had there been a backlog it would have applied other sessions' queued
work, which is why the count was checked first.

### Two symptoms with one cause, and a real gap behind them

Reported mid-round: *"the batch could not be enqueued: Load failed"* and
*"switching the stage didn't change what was seen"*. The route was registered
and stage switching worked when tested (5 Scouting columns → 27 Analysis
columns). Both were the same event: **the server on 8811 was restarted
underneath the page**, and a fetch died.

The gap that made it confusing is real and is fixed: a pane whose load threw
left whatever was there before, so a dead page and a working one looked
identical. It now renders what failed, says a network-level failure usually
means a restart and that nothing was written, and offers a retry. `Load
failed` / `Failed to fetch` are distinguished from an application error,
because "the batch could not be enqueued" reads as a refusal and this is not
one.

## Known gaps

- **`re-api.js` is shared in location only.** `index.html` does not import it
  yet. Rewiring it is a separate change with its own verification, because
  the inlined copies there carry behaviour (toasts, view routing) this module
  deliberately does not. Until that lands, the drift the module exists to
  prevent is only half-prevented.
- **No deep link.** Neither UI has one, so "open the current UI" in the
  chrome is the app root, and the selection does not carry across. That
  weakens the side-by-side comparison the handoff asks for.
- **Enrichment rows cannot be answered inline.** The `⚠` row says so rather
  than offering a form.
- **Deferred, per the affordance inventory:** survey cards (run, schedule,
  notify, results, steps), sub-resources and scoped analysis, Curate
  verdicts, the Context form, Automate, the Activity log, the RFA drawer,
  Admin, and the stat tiles. Each is marked and linked out; none is silently
  absent.
- **Concurrency across a set is enqueued but not yet *proven* concurrent.**
  Two runs is not a test of throughput; the queue's `SKIP LOCKED` claim is
  existing, tested machinery, but the slice's own claim — fourteen repos
  running at once — has been exercised at two.
- **Lucide is not wired in.** The screen needs almost no icons: its state
  vocabulary is typographic (`✓ ○ ⚠ ·`), which is not emoji and not an icon
  set. Add the sprite when a surface actually needs one.
