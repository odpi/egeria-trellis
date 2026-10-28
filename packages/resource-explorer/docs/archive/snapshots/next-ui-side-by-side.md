# `/` vs `/next` — a side-by-side walk on one resource

**Status:** superseded. Walk written when / was Classic; Next is now the default (2026-10-07). Kept as a record.

> **Routing note (2026-10-07):** Next is now the default at `/`; Classic is at `/classic`. This walk was written when `/` was Classic, so read its `/` column as Classic (`/classic`) and `/next` as Next (`/`). Cross-links are now `Classic` (in Next) and `New UI` (in Classic).

**Resource:** `egeria_workspaces_git` (display name `egeria-workspaces`)
**Date:** 2026-09-09 · **Both UIs served by one process on port 8811**, same
session, same registry, same investigation (`q3-health-review`).

This is the inventory the design review asked for: *"walk the old UI and the
new side by side on one resource and list every difference, however small,
without judging them."* Nothing below is a recommendation. Where a difference
looks deliberate it is still listed, because the point is the list.

`index.html` is byte-identical to `main` on this branch, so `/` here is the
current UI exactly as it ships. Both were driven anonymously
(`TRELLIS_ANONYMOUS_READ=true`), which is why both report "not signed in".

---

## 1. Top bar

| | `/` | `/next` |
|---|---|---|
| Brand | "Resource Explorer" | same |
| Resource badge | `egeria_workspaces_git` | same |
| Activity | `📋 Activity` — **no count** | `Activity 200+ ↗`, live count, links out, dashed |
| Admin | `⚙ Admin` | `Admin ↗`, links out, dashed |
| Investigation | `🧭 Corpus baseline — republished after the Egeria reseed` | same text, no glyph |
| Identity | `⚠ Not signed in` | `not signed in` |
| Docs | `Multi-agent RAG · API docs` → `/docs` | **absent** |
| Sign out | not in the top bar | `Sign out` |
| Cross-link | — | `/next · open current UI` |

- The current UI's Activity control carries no number; `/next`'s does, and
  saturates at `200+` because the endpoint pages at 200.
- The link to `/docs` (FastAPI's own API docs) exists only in `/`.

## 2. Intent nav

| | `/` | `/next` |
|---|---|---|
| Items | 9, each with an emoji | 9, no emoji |
| Understanding | rendered as an ordinary peer | `Understanding · not built`, dashed rule |
| RFAs | `📝 RFAs44` (no space before the count) | `RFAs 44 ↗`, dashed, links out |
| Chat | `💬 Chat` | `Chat ×` — a toggle that reflects drawer state |

## 3. Perspective row

Both show the row on Scouting — the assumption that `/` hides it outside
Assessment/Analysis is wrong; it was visible on every intent checked.

| | `/` | `/next` |
|---|---|---|
| Label | `Perspective:` | `PERSPECTIVE · 0 OF 12` |
| Chips | 12, no separators, no counts | 12, count of held shown |
| Residue | none | `N of M questions shown · K hidden` when any held |
| Matched-nothing | not indicated | dashed chip, `· no questions here` |

## 4. Sidebar

### Controls

| | `/` | `/next` |
|---|---|---|
| Type facet | `📁 Repos 🗄 DBs 💾 FS` | `Repos DBs FS` |
| Investigation | **not in the sidebar** (header, read-only) | a `<select>` switcher |
| Text filter | present | present |
| Scope chips | `🎯 In scope · All · 🆕 New · 📊 Surveyed · ☁ Published` | same five, no emoji |
| Disposition facets | 7 chips **including `🚫 ignored 0`**, plus `◌ unjudged 14` | present dispositions with counts; empty ones behind `2 more…` |
| Select | `☑ Select` | `☐ Select` |
| Group control | a dropdown, `All projects` | group **headings** in the list, no dropdown |
| Show hidden | not seen in this walk | `Show hidden 1` |
| Count line | `17 of 59 match` | `18 shown` |

**The disposition counts mean different things.** `/` totals 19
(1+1+1+1+1+0+14) — the current investigation's working set. `/next` totals 59
— every registered repo. `/` also names the empty bucket `◌ unjudged`;
`/next` calls it `undecided`, which is the registry's own value.

`/` renders `🚫 ignored 0` — a zero — so the claim that it shows "six
permanent zeros" is at least partly out of date; here it showed one.

### Rows

| | `/` | `/next` |
|---|---|---|
| Lifecycle mark | `☁ / 📊 / 🆕` | `▣ / ▤ / ▢` |
| Disposition mark | `🔬 / 👍 / ✅` | `◎ / ✚ / ●` |
| In-scope mark | `🎯` | **absent** |
| Group mark | `🗂` | **absent** (groups are headings instead) |
| GitHub link | `↗` **on every row** | **absent from rows** (on the header instead) |
| Status line | `active · 1 collections` | **absent** |
| Hidden mark | — | `⌀` |

## 5. Resource header

`/`'s lives on a Scouting overview view; `/next`'s is at the top of the
Questions pane.

Present in `/` and **absent** in `/next`:

- the repo description (`Docker compose starter configurations for work,
  samples, and demos`)
- eight stat tiles — `WEBSITE`, `LANGUAGE` (HTML), `STARS` (★ 6), `FORKS` (4),
  `CONTRIBUTORS` (5), `LAST PUSHED` (6d ago), `SIZE` (1251.0 MB),
  `SECURITY & ANALYSIS` (1/4 enabled), `DEPLOYMENTS` (—)
- `❓ Questions checklist →`, `🔄 Refresh chat search index (Analysis) →`,
  `View full report →`
- a `SCOUTING SIGNAL` block: `50,517 lines of code across 288 source files,
  6,385 files in total` and `Health 53/100`
- `▶ Run all Scouting surveys`
- `Manage disposition →` (links to the Disposition sub-tab)

Present in `/next` and **absent** in `/`'s header:

- `hide` / `remove` as header actions (in `/` these are Select-mode bulk
  actions only)
- an inline disposition picker (in `/` it is a link to another sub-tab)
- the slug rendered in mono beside the display name

Same in both: display name, GitHub URL, homepage, surveyed/published state.
`/` renders the GitHub URL as its full text; `/next` labels it `GitHub ↗`.

## 6. Questions pane

| | `/` | `/next` |
|---|---|---|
| Sub-tabs | 5, emoji, all live | 5, four dashed and deferred |
| Stage selector | **inside the pane**, 7 stages | **not in the pane** — the intent nav |
| Legend | static, all six states, always | counted, only states present |
| Counter | `5 / 5 answered or automatic` | `5 of 5 answered · Scouting` |
| Survey panel above | always visible | absent |

### One row, both ways

`/`:

```
✓  Is this repository actively maintained?
   Scouting
   Steward · Consumer · Data Expert
   repository_health + foss_scorecard [checks: foss_scorecard:maintained]
   why?
```

`/next`:

```
✓  Is this repository actively maintained?                        Steward
   Pass — 1012 commit(s) in the last 90 days.
   foss_scorecard · run time not recorded · evidence · re-run
```

Per-row differences:

- `/` names the **stage** on every row; `/next` names it once, in the header.
- `/` lists **every** perspective; `/next` shows only the **first**.
- `/` shows the **answering mechanism** (`repository_health + foss_scorecard
  [checks: …]`); `/next` shows the **analysis ids that actually produced the
  answer** (`foss_scorecard`) — a shorter and different list, since the
  mechanism names what *could* answer and the provenance names what *did*.
- `/` has `why?`; `/next` has `evidence` and, where a run is possible,
  `re-run`.
- `/next` adds the answer, the caveat, and a run time. `/` has none of these.
- `/next` says `run time not recorded` on four of five rows, because the
  facts carry no `last_run_at`. `/` never claims a per-row run time at all,
  so the gap is invisible there.

## 7. Sub-tabs that `/next` defers

- **Search** — in `/` this is **repo discovery**: saved sources (Apache, CNCF,
  LF AI & Data, Eclipse), a GitHub search form, and a from-a-list importer.
  It is *not* a search of the selected resource. `/next`'s deferred panel says
  "Search is live in the current UI", which reads as though it were.
- **Survey** — survey definitions with their full step lists, `Run →`,
  `⏱ Schedule`, `🔔 Notify me`, `📊 Results (3)`. In `/` this panel is visible
  **above every Scouting sub-tab**, not only its own.
- **Dashboard** — `REPOSITORY HEALTH` (overall 52.8, activity 100, community
  11, release cadence 0, freshness 100), `MATURITY`, `COMMUNITY SUPPORT`,
  `CHAOSS METRICS`, and a star-growth chart.
- **Disposition** — six buttons plus a dated **HISTORY** list
  (`👁 Tracking — 2026-08-11T00:40:55`). `/next` had the picker but no
  history **when this walk was made**; it was built in `ee3a2d5`, committed
  one second after this document. `/next` now renders the same dated trail
  under the picker.

Worth noting: the caveat sentences the redesign puts in the row already exist
verbatim in `/`'s Dashboard — *"Issue and pull-request response times are not
collected, so CHAOSS's responsiveness metric cannot be computed from what is
held."* In `/` they are four sub-tabs away from the question they answer.

## 8. Chat

| | `/` | `/next` |
|---|---|---|
| Placement | a panel in the layout, width toggled | a drawer, `rail-closed` class |
| Opening state | persisted (`pe_chat_panel_open`) | persisted (`re-next.railOpen`) |
| Welcome | "Welcome to Resource Explorer! Select a project…" | none |
| Transcript | yes | yes |
| Turn labelling | role only (`You` / `Assistant`) | role **plus the resource**, marked when it is not the current one |
| Source line | on fact answers | on every answer |
| Feedback | `👍 😐 👎` | `yes / partly / no` |
| Candidates | — | `Open as candidates` on list-shaped answers |
| Narrow viewport | squeezes the content | overlays it |

## 9. Cross-cutting

| | `/` | `/next` |
|---|---|---|
| URL state | none | `?resource=&stage=&tab=&perspectives=` |
| Emoji | throughout | none |
| State encoding | hue (emerald ✓, cyan ✓, amber ⚠) | glyph plus weight, one gold accent |
| Resize | sidebar, chat, RFA drawer (`pe_*_w`) | sidebar and rail (`re-next.*Width`) |
| Storage keys | `pe_*` | `re-next.*`, except the shared `re_current_investigation` |
| Fonts | system stack | Cormorant Garamond + Lora, self-hosted |

The two UIs share exactly one storage key on purpose:
`re_current_investigation`. Both showed the same investigation during this
walk, which is the behaviour that was wanted.

---

## Things this walk found that were not on anyone's list

1. **The disposition facet counts are scoped differently** — investigation
   working set in `/`, all registered repos in `/next`. Both are defensible;
   they are not the same number and nothing says which is meant.
2. **`/` puts the GitHub link on every sidebar row**, so it is reachable
   without selecting anything. `/next` moved it to the header, which makes it
   one selection away.
3. **`/`'s "Search" is repo discovery, not resource search.** `/next`'s
   deferred message inherits the label and therefore mis-describes it.
4. **Disposition history existed in `/` and nowhere in `/next`** — the only
   place the *sequence* of verdicts is visible, and not among the nine.
   **Fixed:** `ee3a2d5` built it, one second after this walk was committed.
   This entry is kept because the walk is what found it; the gap it names is
   closed. Nothing else in this document has been re-verified since
   2026-09-09 — treat the rest as of that date.
5. **The per-row stage label is gone from `/next`.** Fine while the pane is
   one stage at a time; it is the thing that would be missing first if rows
   from several stages were ever shown together.
6. **`/next` shows only the first perspective per row.** `/` shows all of
   them, which is also how you can see that a question carries four.
7. **`/`'s survey panel is visible above every Scouting sub-tab**, not only
   under "Survey" — so in `/` you are never more than a scroll from `Run →`.
   In `/next` there is no run control outside a question row's `re-run`.
8. **`run time not recorded` appears on four of five rows.** `/next` surfaces
   a gap in the data that `/` never had to confront, because `/` shows no
   per-row run time. The gap is in the facts, not in either UI.
