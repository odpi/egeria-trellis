# DESIGN — Splitting the Analytic Library out of trellis: what moves, versions, loading, and RE's own analyses (2026-10-30)

> Superseded in part by Q2(b) (#956) and pyegeria 6.2.18 (#959): analytics register through `pyegeria.analytics`, report specs through `pyegeria.report_specs`; `PYEGERIA_REPORT_SPEC_MODULES` is deprecated in 6.2.18 and removed in 6.2.19; dotted function paths are refused in 6.2.19 (pyegeria side: [ISSUE-173](https://github.com/odpi/egeria-python/blob/main/PYEGERIA_ISSUES.md) in egeria-python's PYEGERIA_ISSUES.md, [odpi/egeria-python#523](https://github.com/odpi/egeria-python/pull/523)).

Owner's questions (coordinator relay, verbatim):
"* Does it make the most sense for all steps to move?
* Why do we need to pin a version? And do you mean pinning a Dr.E definition or do you mean the code? Perhaps the DrE
definition specifies the version of the code?
* Would it make sense to take a dynamic loading approach where we do a lazy load of the python modules needed?
* If its not too inefficient (and that may require exploration), yes it would make sense for RE's analyses to be
library steps"
Builds on DESIGN-ANALYTIC-LIFECYCLE-2026-10-30 (§5 packaging, §14 rulings 1–7, §16 Egeria homes) and the common-library
ruling of 2026-10-30. Design only.

## In one page

**What moves.** Everything that is a *declaration plus a reader of stored facts* moves to the library: the declaration
model and wrapper, the eight reference steps (four database, four file-system) and the routines. What does *not* move
is anything that needs RE's own tables, credentials or screens to exist: the surveyors that fill those tables, the
Dr.Egeria survey definitions that chain them, and the hosts that run them. The rule is "move it if a Portal user could
run it with only pyegeria and a registry read; keep it if it writes RE's tables or needs RE's session". Today's eight
reference steps already pass that rule, which is why they live under examples/.

**Versions.** Two things carry a version and they pin different things. The *code* version is the package's
`__version__`, stamped on every result today, so a number on a screen can always be traced to the code that made it.
The *definition* version is Egeria's: a GovernanceActionProcess and its steps are elements with their own
versions. Yes, the Dr.Egeria definition should name the code it needs: each step element gets a `requestParameter`
`implementationVersion` (a range like `>=1.2,<2`), and the host refuses to run a step whose installed version is
outside it, with the words "definition asks for fs_activity >=1.2, installed 1.0". We pin because a survey report is
evidence: "226 of 266 tables" must be reproducible by the version that measured it, and because the owner ruled
(lifecycle 6) that the latest runs by default and an earlier one only when named.

**Lazy loading.** Yes. Today every entry point is loaded at start-up (one `discover()` call per resource type, all
manifests imported, 2 s on 8813 with 9 steps). With a hundred steps that is a hundred imports before the first screen.
The design: read the manifests eagerly (cheap, they are data), import a step's code only on its first run (the entry
point's `load()` deferred), cache it, and say "not loaded yet · loads on first run" in Admin. A broken import then
fails one run with the step's own words, never the start-up.

**RE's own analyses as library steps.** Agreed in principle; the exploration is a two-day spike that converts two
of RE's native analyses (one cheap read, one heavy) to library steps and measures start-up time, per-call overhead
and result equivalence before and after. The expected answer is "cheap": the library wrapper is a function call and a
declaration, not a process boundary.

**Where things go.** pyegeria gets the small shared core (the declaration block on `AnalyticFunctionSpec`, the
entry-point group, the version rule, the registry-name resolver). The new library repository gets the model, the
readers' *interfaces*, the reference steps and the tutorial. trellis-analytics becomes a thin dependency and then
retires. RE keeps the hosts, the surveyors, the readers' *implementations* and the screens.

## 1. Which steps move — and the rule

| Thing | Where it is | Moves? | Why |
|---|---|---|---|
| Declaration model (`Declaration`, `MeasureDeclaration`, basis/capped/categories, absence sentences, confidence, extents) | trellis-analytics | **yes** | pure data model; the Portal and EA need it to show the same words |
| `StepManifest` / routine manifest (kind, source, version, inputs, request_type, guard, questions) | trellis-analytics | **yes**, the *declaration block* goes further into pyegeria's `AnalyticFunctionSpec` | one spec everywhere (eval §4.1) |
| Discovery (entry-point group `trellis.analytics`) | trellis-analytics | **yes**; the group name becomes pyegeria's with trellis's as an alias | one discovery for Portal, RE, EA |
| The eight reference steps (change_rates, database_staleness, hub_tables, statistics_freshness; fs_activity, fs_change_rates, fs_duplicates, fs_permissions) + fs_layout_signals | examples/steps | **yes** | they read stored facts through typed readers and write nothing; they are the tutorial's examples |
| Typed readers' *interfaces* (`inputs.ods.row_count_snapshots`, `filesystem_walk_files`, …): names and row shapes | RE `OdsReader` + `STEP_ODS_METHODS` | **interface yes, implementation no** | a step must know the shape; only RE knows the table |
| Surveyors that fill RE's tables (schema inventory, value profile, the walk, file profiles) | RE | **no** | they need RE's credentials, tables and session; they are what the library reads |
| Dr.Egeria survey definitions (DatabaseAnalysisSurvey, RepoAnalysisSurvey, the future FileSystemSurvey) | RE docs/dr-egeria | **no** (they name steps; they stay with the host that runs them) | a definition is deployment, not library |
| Hosts (`survey_step_host`, engine host, Files host), run queue, screens | RE | **no** | RE is one host of several |
| Routines (generate_engine_host_declaration etc.) and report-spec generator | trellis-analytics | **yes** | they are library tooling |

**Rule for a new step:** it moves to the library when (a) it declares its result, (b) it reads only through named
typed readers or `source_report`, (c) it writes nothing, (d) it needs no RE screen to be meaningful. A step that
fails (b) is a surveyor and stays in the host that owns the table.

## 2. Version pinning — what is pinned, and whether a Dr.Egeria definition can name the code

Three versions exist and each answers a different question:

| Version of | Carried where | Answers |
|---|---|---|
| the **code package** (`fs_activity 1.0.0`) | the step package's `__version__`; stamped on every result row and in provenance (built) | "which code produced this number?" |
| the **declaration** (manifest content hash, lifecycle §5) | provenance; `discovery.origin()` to compute it (design) | "did the words or measures change without a version bump?" |
| the **Egeria definition** (GovernanceActionProcess / step element) | Egeria's element version and `versionIdentifier` | "which chain of steps did this survey run?" |

Why pin at all: a published annotation is evidence a person acts on. Re-running "the same step" a month later with
different code and getting a different number is a silent change; the stamp lets the screen say "measured by
fs_activity 1.0.0 · 1.2.0 installed now · re-run to refresh". The owner's lifecycle ruling 6 (latest by default; an
earlier version only when named) is the policy; the stamp is how it is enforced.

**Can the Dr.Egeria definition specify the code version? Yes, and it should.** Each Create Governance Action Process
Step block gains two request parameters the host reads before dispatch:

```
| re_analysis_step        | fs_activity        |
| implementationVersion   | >=1.0,<2           |   # a PEP 440 range; absent = latest installed (ruling 6)
```

At run time the host compares the installed package version with the range. Inside: run and stamp. Outside: refuse the
step with "definition asks for fs_activity >=1.0,<2 · installed 2.1.0 · update the definition or install the older
package", a not_established result with that sentence, never a silent run. The definition's own element version
changes when the range changes, so Egeria's history answers "when did we start requiring 2.x". Egeria also lets a
GovernanceActionProcess step name an `ImplementedBy` target (lifecycle §16: AnalyticalActionProcess → EmbeddedProcess
/ GovernanceActionService); the version range lives on the step, the implementation link says where the code is.

Not pinned: the data a step reads (facts carry their own `as_of`), and RE's own package version (the host version is
in the activity row already).

## 3. Lazy / dynamic loading of step modules

**Today.** `discover_steps()` calls `trellis_analytics.discovery.discover()`, which iterates
`entry_points(group="trellis.analytics")` and `ep.load()`s every one — importing each step's module — at start-up
for each resource type, then `attach()` admits or refuses. 8813's log shows the engine-host line 2 s after the worker
starts with 9 steps. One broken package is caught and logged, so start-up survives, but every step's imports (pandas,
etc.) run before the first request.

**Design: manifests eager, code lazy.**
1. Split each step's entry point in two names or load the manifest from package metadata: the manifest (a small
   dataclass, no heavy imports) is read at start-up for every step — it is what the catalogue, Admin and the Questions
   rows need. The entry point's `load()` of the *runner* is deferred.
2. A step's code is imported on its first run (`importlib` through the entry point), timed, and cached for the
   process's life; the first run's activity row carries "first load N ms" so the cost is visible.
3. Failure words: an import error at first run is that run's result, state not_established, "fs_duplicates could not
   be loaded: No module named 'xxhash' · install the step's requirements", and Admin › Analytics shows the step as
   "installed · not loadable" with the error; the next press retries. Start-up never fails for a step.
4. Reload without restart (later): Admin "reload steps" re-reads entry points and clears the cache; a step mid-run
   finishes on the old code. Needed for T2 third-party steps in production; not for the first slice.
5. Cost: manifest read is microseconds per step; a lazy import costs what the step's imports cost, once. Risk: a step
   whose manifest import drags in its heavy dependencies defeats the split — the library's authoring rule becomes
   "manifest.py imports nothing but the declaration model", checked by a test that imports every manifest with the
   heavy modules blocked.

Same mechanism for the Portal: pyegeria's registry already resolves functions by dotted path at call time (lazy by
accident, with no allowlist); the entry-point group gives it the manifest list eagerly and the same first-use import.

## 4. RE's own analyses as library steps — the exploration and the spike

RE's analysis catalogue has 81 entries of which 9 are already library steps. The native ones are Python functions
registered per resource type (`re_analysis_steps` in the three survey_definition_adapters) with results readers in
the catalogue. Converting one means: a manifest with a declaration, the function wrapped as a library step, its reads
expressed as typed reader calls, and a results reader that is the generic declared one.

**What the exploration measures (before any mass move):**
| Measure | How | Pass |
|---|---|---|
| start-up cost | 8813 boot to "engine host: library steps" line, before/after, with lazy loading on | no worse than today's 2 s per 10 steps; linear in manifests, not in imports |
| per-call overhead | time a native run vs its library-step twin on the same database (statistics_freshness has both today: db_statistics_freshness native, statistics_freshness library) | overhead under 50 ms per run (the wrapper + declaration words) |
| result equivalence | the twin pair's numbers and words side by side on the Questions row | identical numbers; words from the declaration |
| reader cost | native code reads tables directly; the library twin goes through `OdsReader` typed reads | no extra round trips (the typed read is the same SQL) |
| screen behaviour | the Questions row, publish table, chat compile for the twin | the declared surfaces work with no per-analysis renderer |

**The spike (two days, one builder, no DDL):** convert `db_statistics_freshness` (cheap, already has a library twin
to compare) and `value_profile` (heavy, a container reader, per-field numbers) to library steps behind a flag; run both
forms on coco_pharma and the throwaway; record the five measures above; keep the native code until the owner reads the
numbers. If the numbers pass, the rule becomes "every new RE analysis is a library step; existing ones convert as
touched", which is also how the Portal's and EA's analytics arrive.

**Decision (project owner, 2026-10-31):** "An RE analysis becomes a library step when it is next touched for another reason, never as a batch. Before it converts, (1) the typed read must say every fact the native read uses (table scope: `table_kind`, A1, shipped 2026-11-02), and (2) its per-member detail must have a declarable shape (a per-column result kind, B1, not yet designed)."

The spike (`evidence/SPIKE-RESULT-RE-ANALYSES-AS-LIBRARY-STEPS-2026-10-31.md`) found the two prerequisites: a library twin of
`statistics_freshness` counted 266 tables where native counted 263 because the typed read did not say base table from
view (prerequisite 1, closed for table scope by `table_kind`), and `value_profile`'s ~40 measures x 1,916 columns cannot be
declared as a member result (prerequisite 2, open). Until both hold for an analysis, it stays native.

## 5. Slices

| Slice | Content | Where | Depends on |
|---|---|---|---|
| LS1 | pyegeria core: declaration block on `AnalyticFunctionSpec`, entry-point group + name-only resolver, `implementationVersion` rule | pyegeria (PYEGERIA_ISSUES / Mandy + owner) | — |
| LS2 | repository created (owner, ruling 1); move model, discovery, reference steps, routines, tutorial; trellis-analytics depends on it | new library repo + trellis | LS1 for the spec, else a shim |
| LS3 | lazy loading (manifests eager, code on first run, failure words, Admin state) | library + RE host | LS2 |
| LS4 | Dr.Egeria `implementationVersion` on step blocks; host check; refusal words | RE adapters + docs/dr-egeria | LS1 |
| LS5 | the spike of §4; then the conversion rule | RE | LS2, LS3 |
| LS6 | Portal + EA adopt the group and the declared renderer (eval G1–G8) | egeria-workspaces, EA | LS1–LS2 |

DDL: none in any slice; provenance already carries version; a content hash is a new provenance *field* inside the
existing JSON, not a column.

## 6. What goes to pyegeria versus the library

| pyegeria (small, shared, versioned with Egeria) | library repository (grows with every step) |
|---|---|
| `AnalyticFunctionSpec` + declaration block (result kinds, measures, basis/capped/categories, absence, confidence) | `Declaration` helpers, words builders (`declared_words`), wrapper, generators |
| entry-point group name and manifest loader | the reference steps and routines |
| registry-name-only resolver (no arbitrary dotted paths) | readers' interfaces (names, row shapes, docs) |
| `implementationVersion` comparison | the tutorial and authoring tests (manifest imports nothing heavy) |
| exec_report_spec state-aware execution (eval item 2) | report-spec generator from declarations |

## 7. Questions for Mandy

1. Egeria side of §2: is a request parameter the right carrier for `implementationVersion`, or should the step element's
   `versionIdentifier` / an `ImplementedBy` relationship property hold it so Egeria's lineage can show it?
2. Does the Portal accept an entry-point group as its discovery (replacing `PYEGERIA_REPORT_SPEC_MODULES`), and a
   name-only resolver that refuses arbitrary dotted paths?
3. GovernanceActionService vs EmbeddedProcess for a library step run by Egeria's engine host (lifecycle §16): which does
   the FileSystemSurvey's RE steps declare?
4. Repository home and name (odpi/egeria-analytics was recommended) and whether pyegeria's release train carries LS1 in
   6.2.x or 6.3.

## 8. Open for the owner

1. The library's name and whether the reference steps keep their `fs_`/database names or gain a namespace.
2. Spike scope: the two analyses named in §4, or others you care about more.
3. Whether the lazy-load slice (LS3) comes before or after the spike.
