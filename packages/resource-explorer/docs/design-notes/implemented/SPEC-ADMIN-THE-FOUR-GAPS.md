# Admin: reconcile, groups, discovery sources, and the two registries

**From:** the project owner's four gaps, 2026-09-20 — the first of the deferred
register's category C to be taken up.
**Read against:** `main` after `#130`
**Verified:** every claim below cites what I read.

---

## 0 · What these four have in common, and the rule it earns

Everything in the resource-facing UI acts on **one resource**. These four act on
**the corpus**: a question catalog edit changes every resource's questions, a
discovery source changes what gets found at all, a group changes how the sidebar
organises everything, and a reconcile action changes or deletes records across
the whole store.

So rule 4 — *an action must name what it would do, before it does it* — needs
its admin form:

> **An admin action names its blast radius before it acts:** how many things it
> affects, and whether the change can be undone.

That is the spine of all four sections. It is also why Admin is the right place
for them and the resource panes are not.

**Classic already does this, and one of its lines is better than mine.** Its
confirmations name the radius — *"Clear all cached Egeria GUIDs and survey
history for X?"*, *"Apply N repair step(s)"* — and one names the **opposite**:

> *"Remove filesystem X from the registry? This does not delete your files on
> disk."*

**A destructive-sounding action naming what it does not touch** is the sharper
half of the rule, and it is already written. Port that sentence's shape wherever
a name frightens more than the act warrants — §2's group deletion is exactly
that case.

**One asymmetry, checked rather than assumed:** annotation types have write
routes and a read-only pane; the question catalog has **no write route at all**.
The first is a UI gap. The second is a backend gap as well, and sizing them the
same would be wrong.

---

## 1 · Reconcile is **two** panels in classic, not one

**Correction.** I wrote this section as one surface. Classic has two, and they
do different jobs:

- **Resync** (`loadAdminResyncPanel`, `_applyResync`) — global drift, the
  `egeria_resync.py` machinery below.
- **Repair** (`loadAdminRepairPanel`) — **per-repository correction**, which is
  not drift at all: `submitRepairRename`, `submitRepairRepoint`,
  `submitRepairGithubUrl`, `submitRepairEnableCollection`,
  `submitRepairDropMembership`, with `selectRepairRepo` and
  `renderRepairRepoDetail` for picking one. Fixing a repository that was
  registered with the wrong URL or name is a different act from reconciling the
  store against Egeria, and folding them into one screen would put a data-entry
  correction beside an irreversible bulk clear.

**Build them as two.** Everything below is Resync. Repair is a port of classic's
per-repo detail view and needs no design from me beyond §0's blast-radius rule.

### Resync — the backend is complete and already classifies

`egeria_resync.py` has **twelve scanners** (`_scan_assets` … `_scan_specification_gap`,
`:226-237`) and **nine repair actions** (`_do_reauthor_survey_definitions` …
`_do_relink_investigation_members`). Nothing about this needs designing from
scratch. The `Finding` dataclass already carries what the UI needs:

| field | what the UI does with it |
|---|---|
| `title` / `detail` | the sentence |
| `count`, `items` (capped 50), `truncated` | **counts open what they counted** — the list opens in the rail |
| `repair_step` | `""` means *no automatic repair is correct* |
| `needs_decision` | this one wants a person, not a button |

**The whole design is: do not flatten those last two into "a row with a button."**
Three shapes, not one:

- **Already scheduled** — `clear_stale_assets`, `clear_orphan_publish_claims`,
  `flag_vanished_publishes` are in `SAFE_SCHEDULED_STEPS` and run unattended
  every 600s. The row reports **state**, and offers *run now* rather than
  implying nothing is happening.
- **Repairable, not scheduled** — a button, and it **names its blast radius**:
  what it touches, how many, and whether it is reversible.
- **`repair_step: ""`** — **no button.** It is a report. And `needs_decision`
  makes it a question, routed the way `SPEC-ACTIONABLE-AND-HONEST.md`'s
  destinations route a finding. A reconcile screen that offers a button for
  everything teaches that every drift is the app's to fix, which is false.

**The safety case, and it is not hypothetical.** `_do_clear_stale_investigations`
clears `investigations.egeria_project_guid`, which can hold a GUID **a person
deliberately bound** to a real Egeria Project — the defect found during the
publish-state round. Offering that as an unlabelled button would make it the most
dangerous control in the product. Its confirmation says what it unbinds and how
many, or it does not ship.

The comment above `SAFE_SCHEDULED_STEPS` is the model for the copy: it explains
that flagging is safe to automate *"because it never deletes anything — it only
WRITES a flag… A false positive here costs a reader one wrong badge state until
the next pass corrects it, not a decision silently unmade."* That distinction —
**a wrong badge versus a decision silently unmade** — is what each row must let a
reader make for themselves.

## 2 · Groups — CRUD exists, and so do suggestions nobody sees

`projects.py` has `GET /groups` (`:170`), `POST /groups` (`:190`),
`DELETE /groups/{slug}` (`:203`), `POST /{slug}/group` to assign (`:213`) — and
**`GET /groups/suggestions`** returning `GroupSuggestion` (`:135`), which nothing
in `/next` surfaces.

**Build:** create, rename, delete, assign, and surface the suggestions — **all
four are ports.** Classic has `createAdminGroup`, `deleteAdminGroup`,
`openAssignGroupModal`/`closeAssignGroupModal`, and **`applyGroupSuggestion`**.

*Correction: I first wrote that an app which can propose groupings and does not
offer them is hiding work it already did. That is true of `/next` and false of
classic, which surfaces them. It is a port, not an idea of mine.*

**Blast radius:** deleting a group does not delete resources, and the
confirmation must say so plainly — *N resources return to Ungrouped* — because
"delete group" reads as destructive and here is not.

**And this closes a split feature.** `SPEC-PARITY-INVENTORY-AND-GROUPS.md` §3
specifies collapsible groups in the sidebar with the force-expand-on-filter rule.
Group *authoring* and group *display* are the same capability across two
surfaces; neither is much use alone. Worth doing in one round.

## 3 · Discovery sources — the honest pattern is already built and unexposed

`discovery.py` has `POST /sources` (`:562`), `DELETE /sources/{slug}` (`:588`),
`POST /sources/{slug}/run` (`:598`), and — the notable one —
**`POST /sources/{slug}/refresh` returning `SourceRefreshPreview`** (`:659`).

**A preview endpoint is rule 4 already implemented on the backend.** Refresh can
say what would change before changing it, and no UI asks. Build the preview as
the default path: *refresh would add 4, drop 1, leave 22 unchanged ›*, then
confirm.

`run` imports repositories, so its confirmation names the count and where they
land. `delete` says whether already-imported repositories are affected — from the
route's behaviour, not from a guess; **check it before writing the copy.**

**And classic has three ways to add a source, one of which I would not have
thought of:** `_saveGithubSource`, `_quickAddListSource`, and
**`_saveCurrentSearchAsSource`** — you search for repositories, like what comes
back, and keep the query as a recurring source. That turns a one-off search into
a standing one at the moment the user already knows it is good, which is the
cheapest possible moment to ask. Port all three; that one first.

## 4 · The two registries, which are not the same size

### Annotation types — a UI gap

`POST /annotation-types` (`analyses.py:72`), `PUT …/{type_name}` (`:90`),
`DELETE …/{type_name}` (`:108`) all exist. `next/admin/annotation_types.js` makes
**zero write calls**. So: add create, edit, delete against routes that are
already there.

**Blast radius:** an annotation type is referenced by recorded annotations.
Deleting or renaming one changes what existing records mean. The confirmation
says how many annotations use it — and if that count is not cheaply available,
**say that it is unknown rather than implying zero.**

### Question catalog — a backend gap as well

`GET /question-catalog` (`analyses.py:135`) is the only route.
`next/admin/question_catalog.js` makes zero write calls **because there is
nothing to call.**

**So this one needs a decision before it needs a UI**, and it is a real one:

> **Editing a question changes the meaning of answers already recorded against
> it.** A survey result that answered *"does it have a published spec?"* did not
> answer the reworded question. Past answers are evidence about the old wording.

Three ways out, and this is the owner's call, not mine:

1. **Questions are versioned** — an edit makes a new version; old answers stay
   attached to the version they answered. Most honest, most work.
2. **Questions are append-only** — you may add and retire, never reword. Cheap,
   and retirement is a state the UI already knows how to show.
3. **Edits are allowed and answers are marked** — *answered under an earlier
   wording ›*. Cheapest, and it puts the caveat where a reader meets it.

I would take (2) and treat (1) as the upgrade if rewording turns out to be
common — but the shape of the write route follows from the answer, so **the
decision comes first.** Building a plain edit form would quietly make this
project's own provenance rules untrue, which is the one thing it does not do.

---

## 5 · Done when

- Reconcile shows all twelve findings in their three shapes; no finding with
  `repair_step: ""` carries a button; every repair names its blast radius and
  whether it is reversible; `clear_stale_investigations` names what it unbinds.
- Groups can be created, renamed, deleted and assigned; suggestions are offered;
  deleting a group says where its resources go.
- A discovery source can be added, run and deleted, and **refresh previews before
  it acts**.
- Annotation types can be created, edited and deleted, and a destructive change
  says how many annotations are affected — or says the count is unknown.
- The question catalog has a ruling recorded, and a write path that matches it.

**Out of scope:** the reconcile *scheduler* (which steps are safe to automate is
settled and in `SAFE_SCHEDULED_STEPS`); the classic admin views, which stay as
they are under `RULING-CLASSIC-AND-NEXT.md`; and bulk group assignment, which
wants the sidebar's selection and belongs with §2's collapse round.


---

## 6 · How this spec was wrong, and the rule it earns

The project owner had to tell me to look at the classic interface. I had read
the **routes** and the **dataclasses** — the layers that grep cleanly — and
inferred the UI from them. Classic has all four of these built and in daily use,
and reading it changed three sections:

- Reconcile is two panels, not one, and the second is per-repository data
  correction rather than drift.
- Group suggestions are already surfaced in classic; my line about an app hiding
  work it had already done was true of `/next` and false of the product.
- Discovery sources have three add paths, including saving a live search as a
  recurring source — an affordance I would not have invented.

And classic's *"This does not delete your files on disk"* is a better instance of
this spec's own rule than the one I wrote.

**Fourth instance of the same method failure**, after the resolver, the
stale-flag state, and `members.py`: I specified against the layer that was easy
to read instead of the one that had the answer. The standing rule was *search for
the existing implementation before designing the mechanism.* It needs its
missing half:

> **The classic interface is an existing implementation.** For anything `/next`
> is porting rather than inventing, read classic's version before speccing it —
> not the routes it calls, the surface itself.

This is cheap to follow and I did not follow it. Recorded here rather than in the
deferred register, because it is not a deferral — it is a spec that was written
with half the evidence available.
