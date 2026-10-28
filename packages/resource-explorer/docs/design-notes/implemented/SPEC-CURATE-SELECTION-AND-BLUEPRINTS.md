# Curate: selection and blueprints — build-ready

**Item 3.** Drawn on canvas page eight (`CurateSelection.dc.html`).
**Read against:** `main` after `#130`
**Nothing is blocking this.** The one question the drawing left open is answered
by the code — see §0.

---

## 0 · The open question closes, and it was already answered

The drawing said I would not invent an Egeria act for accepting a cluster,
because naming a type before anyone verified it is the `SoftwareLibrary`
mistake. **It does not need inventing.** `blueprint_materializer.py` already
exists:

- `materialize_blueprint_element` finds-or-creates a real Egeria
  **`SolutionBlueprint`** (`:14`)
- qualified as
  `SolutionBlueprint::{entity_type}::{entity_slug}::{perspective}::{cluster_name}`
  (`:200-208`) — **the reading is in the identity**, all the way into Egeria,
  which confirms §3 below rather than merely asserting it
- `record_materialized_blueprint` / `get_materialized_blueprints`
  (`registry.py:3277`, `:3264`) persist the result
- `SolutionBlueprint` is a `Collection`, and `CollectionMembership` admits
  nesting (`clustering.py:641`)

So the type is pinned, by someone who verified it. **This is the fourth open
question of mine this week that the source had already answered** — after the
resolver, the stale-flag state, and `ClassificationExplorer`. The rule I wrote
after the third one holds: grep for the verb before designing the mechanism.

**What is genuinely not built** is the wiring between them.
`materializer.py:21-22`: *"no candidate blueprint to attach to yet. Wiring a
materialized component into a blueprint is future work once that exists, not
faked here."* That is §4.

---

## 1 · Selection on the branch tree

`stages/curate.js` already has the scaffolding — `curateRowHtml(r, selected,
pick)` takes a selection state, and `recordVerdicts(slug, scopes, verdict, …)`
already accepts a **list** of scopes. So this is wiring a control to a function
that is already plural.

**Build:**

- A checkbox per branch row, and one in the header for select-all-shown.
- `selectAllMatching` for the full set: *select all 69 branches ›*. It must say
  the total, not just act — the tree shows 8 of 69 by default.
- The footer reads *N of M shown selected*, and the actions read
  **accept N selected** / **reject N**.
- **Each selected branch contributes its scope count, and the confirmation says
  the total before acting.** Accepting two branches can be twenty scopes; a
  curator should be told that before, not discover it after. This is rule 4 —
  *an action must name what it would do, before it does it.*

**The confirmation wording is already ruled** (`REPLY-CATALOGUE-IN-LAYERS.md`):
catalogues them as software components under `resource-explorer`, the exact
Egeria type is not yet pinned, and the price carries its measured basis
(`1.5s` median per element). Do not name `DeployedSoftwareComponent` here —
that type is still unverified, unlike the blueprint's.

**Do not** add a select-mode toggle. The sidebar's `selectMode` is a mode
because the sidebar's rows are navigation; the tree's rows are already a work
queue, so the checkboxes are simply present.

---

## 2 · The blueprint list — the count that opens nothing

Live today: the coverage sentence says *"0 of 12 clusters in the logical reading
reviewed"* and **blueprints have no UI in `/next`**. A count that opens nothing
is the one thing this app does not do.

**Build a blueprint list under Curate**, beside the component tree:

- One row per cluster in the **current reading**: name, why it is cohesive
  (imports, co-change), and how many of its members already carry component
  verdicts — *3 of its 9 members are accepted components*.
- `N members ›` opens the member list in the rail. Counts open what they
  counted.
- `accept` / `reject` per row.
- Foot: *N of M clusters shown · all M in the {reading} reading › · the
  {other} reading has K ›*.

**Verdict plumbing already exists.** Blueprint verdicts live in the same table
as component verdicts, distinguished by `verdict_target='blueprint'`, with
`scope_locator` repurposed to hold the blueprint key (`registry.py:1579-1600`);
`list_component_verdicts` filters those rows out (`curate.py:312`). So this
needs a sibling reader, not a schema change.

---

## 3 · Say that a blueprint verdict is reading-scoped

A component verdict is keyed by path, so it holds whatever reading you were in.
A cluster is keyed `perspective::cluster_name` and **only exists inside one
reading** — and the materializer puts the perspective in the Egeria qualified
name, so this is not a UI convention, it is the identity.

One table, two identity regimes. **The screen must say which**, or a curator
will expect a blueprint verdict to carry across readings the way a component's
does:

> A verdict here is recorded against `logical::metadata access services` and
> applies in this reading only — switching readings shows a different set, not
> the same set re-judged.

That sentence, or one like it, at the head of the blueprint list. And when the
reading selector changes, the list is **replaced**, never diffed against the
previous one.

---

## 4 · Membership wiring — scoped out, and say so

Accepting a blueprint materialises the `SolutionBlueprint`. It does **not**
attach its accepted components as members, because that wiring does not exist
(`materializer.py:21-22`).

**So the blueprint row must not imply it does.** After accepting:

> **accepted** · catalogued as a Solution Blueprint · *its members are not yet
> linked — `3` accepted components stand apart ›*

Absence is a state. A blueprint that says nothing about its members reads as a
blueprint with none, which is false — it has three and they are unlinked. This
is the honest version of a real limitation, not a placeholder, and it names the
number so the gap is measurable.

---

## 5 · One rule from `#130`, because it will recur here

`#130` fixed two header buttons that kept the dashed *"not built"* underline
after they were built. The cause is structural: the deferred styling is written
inline at each site (`app.js:644`, `:755`, `:798` — `border-bottom:1px dashed
currentColor`), so it is set independently of the flag that gates the behaviour.

**This is the `unbuilt` defect in a second costume** — state declared in one
place, appearance derived in another, and nothing makes them agree.

**Rule: the deferred styling must come from the same flag that gates the
behaviour.** One helper — `deferredAttrs(isBuilt)` or equivalent — used at every
site, so a thing that becomes built stops looking deferred without anyone
remembering. Item 3 adds several new controls with built and unbuilt states
(select-all, blueprint accept, the member link in §4), and every one of them is
a chance to repeat this.

---

## 6 · Done when

- A curator can select several branches and accept or reject them in one act,
  having been told the scope total first.
- The twelve clusters the coverage sentence names are on screen, with member
  counts that open.
- A blueprint can be accepted, materialises as a `SolutionBlueprint`, and says
  that its members are not yet linked.
- Switching readings replaces the blueprint list, and the screen says a
  blueprint verdict applies to one reading only.
- No new control carries deferred styling it does not derive from a flag.

**Out of scope:** membership wiring (§4), the classic Curate panel beyond the
honesty clause already ruled in `RULING-CLASSIC-AND-NEXT.md` §3, and
`repo_survey_definition_adapter.py:2951`'s primary pick — that one is ruled
(best-evidenced, not most-recent) and is its own small change.
