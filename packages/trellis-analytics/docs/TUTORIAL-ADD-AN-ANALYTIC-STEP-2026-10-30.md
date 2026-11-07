# Your first analysis: adding a measurement to Resource Explorer

> **Vocabulary note (2026-11-07):** the absence word `measured_nothing` was renamed `nothing_found` (trellis-analytics 0.3); this document now uses the new word. Results stored with the old word are still read.


*A tutorial for developers who are new to Resource Explorer. About an hour. Rewritten 2026-10-30.*

## What you will do

You will add a small measurement to Resource Explorer without changing Resource Explorer itself. When you are done:

- Resource Explorer shows your measurement on a database's page, with a button to run it.
- Your result appears as an answer to a question, with its unit and the time it was measured.
- When your measurement cannot be taken, the screen says why in your own words, instead of showing a zero.
- You can change your code, reinstall it, and see the change, with no edit anywhere in Resource Explorer.

Along the way you will learn the few rules that keep numbers in Resource Explorer honest. They are the same rules
every built-in measurement follows.

## The words you need

Only five.

| Word | What it means here |
|---|---|
| **step** | A piece of code that measures something about a database or file system during a survey. You are writing one. |
| **manifest** | A short description of your step that sits next to the code: its name, what it measures, what it needs, which question it answers. Resource Explorer reads the manifest; it never reads your code. |
| **state** | What happened when the step ran. One of: *measured* (you got a number), *nothing found* (you looked and there was nothing to count), *not measured* (you could not look), *not permitted* (you were refused), *not applicable* (the question does not apply to this kind of thing). |
| **as-of** | The moment the number was true. Every number carries one. |
| **entry point** | A line in your package's settings that says "here is a step". Installing the package is all the registration there is. |

## Before you start

You need:

- A checkout of the `trellis` repository and the `uv` tool. Check with:
  ```bash
  uv --version
  ```
- A Resource Explorer you can restart. Your own is best. If you use a shared one, ask its operator to restart it when
  you reach step 6. Do not restart a shared instance yourself.
- A database Resource Explorer already knows about. Any will do.

All commands run from the root of the `trellis` checkout.

## Step 1. Say what you are measuring, in plain words

Do this before any code. Write five sentences. Here they are for the step you will build, which measures how many
rows changed in each table since the last survey:

1. **The number and its unit:** "Rows changed per table since the previous survey."
2. **When it was true:** "As of the latest survey's time."
3. **If you could not look:** "Not measured: no survey has counted rows for this database yet."
4. **If you looked and found nothing to count:** "Measured: no table changed."
5. **If the question does not apply:** "Not applicable: row changes are measured for databases only."

These sentences go into your manifest in step 4, and Resource Explorer shows them to people. If you cannot write
them, you are not ready to write the code. That is the whole trick: decide what "nothing" means before you can be
tempted to print a 0.

## Step 2. Start from the example

A complete, working step ships in the repository. Copy it and rename it:

```bash
cp -r packages/trellis-analytics/examples/steps/database_staleness packages/trellis-analytics/examples/steps/my_row_changes
cd packages/trellis-analytics/examples/steps/my_row_changes
mv src/database_staleness src/my_row_changes
```

Open `pyproject.toml` and change three things: the package name, and the two places that say `database_staleness`:

```toml
[project]
name = "trellis-step-my-row-changes"

[project.entry-points."pyegeria.analytics"]
my_row_changes = "my_row_changes.manifest:MANIFEST"
```

That last block is the entry point. It tells Resource Explorer, and the Egeria Portal, where to find your manifest
once the package is installed. Nothing else announces your step: installing the package registers it. There is no
environment variable to set and no list of modules to edit.

(Older step packages say `trellis.analytics` here. It still works for now, as an alias, but write
`pyegeria.analytics`.)

## Step 3. Write the code

Open `src/my_row_changes/step.py`. A step is one function, `run`, that returns a list of results. Each result is a
plain dictionary. Here is the whole thing:

```python
def run(inputs):
    # Resource Explorer hands you what your manifest asked for. Here: `inputs.ods`,
    # a set of ready-made reads over data Resource Explorer already holds. No SQL, no passwords.
    # Each snapshot is one survey's row counts: {"surveyed_at", "source", "tables": [{schema_name,
    # table_name, table_type, row_count, state}, ...]}, newest first.
    snapshots = inputs.ods.row_count_snapshots(inputs.slug, limit=2)

    if len(snapshots) < 2:
        # You could not look. Say so, and say why. Return NO numbers.
        return [{"scope": inputs.slug, "state": "not_measured",
                 "reason": "no earlier survey has counted rows for this database yet"}]

    latest, previous = snapshots[0], snapshots[1]
    counts_before = {f"{t['schema_name']}.{t['table_name']}": t["row_count"] for t in previous["tables"]}
    results = []
    for t in latest["tables"]:
        name = f"{t['schema_name']}.{t['table_name']}"
        if t["row_count"] is None or name not in counts_before or counts_before[name] is None:
            continue                                    # no count on one side: nothing to compare
        changed = t["row_count"] - counts_before[name]
        if changed:
            results.append({"scope": name, "state": "measured",
                            "value": {"rowsChanged": changed}, "asOf": latest["surveyed_at"],
                            "summary": f"{changed:+,} rows"})

    if not results:
        # You looked, and nothing changed. That is a real answer, not an error.
        return [{"scope": inputs.slug, "state": "nothing_found", "asOf": latest["surveyed_at"],
                 "reason": "no table changed between the two surveys"}]
    return results
```

Three rules, and the wrapper around your code enforces all of them so you cannot forget:

- Every result names a **scope** (what it is about) and a **state**.
- A state other than *measured* or *nothing_found* needs a **reason** in words.
- A result that breaks these rules is rejected, and **nothing from that run is saved**. A half-saved run would look
  like a whole answer.

You never see a password, and you cannot send SQL. You get only what your manifest asks for.

## Step 4. Fill in the manifest

Open `src/my_row_changes/manifest.py`. Each line below has a note saying what it is for.

```python
from trellis_analytics.declaration import (AbsenceSentences, Declaration, MeasureDeclaration,
                                           ConfidenceDeclaration)
from trellis_analytics.manifest import StepManifest
from my_row_changes import __version__

MANIFEST = StepManifest(
    name="my_row_changes",                       # a name no built-in step uses
    display_name="Row changes",                  # what people see
    function="my_row_changes.step.run",          # where your code is
    description="Rows changed per table since the previous survey, from the row counts "
                "Resource Explorer already keeps. Reads no data from the database itself.",
    returns="one result per changed table",
    source="ods",                                # your data comes from Resource Explorer's own store
    version=__version__,                         # stamped on every result you produce
    resource_types=["database"],                 # what kind of thing you measure
    credential_tier="none",                      # you touch no credential; "read_only" is the most you may ask
    inputs=["ods"],                              # what run() receives
    stage="discovery",                           # the stage your question belongs to (Q-0041 is a Discovery question)
    questions=["Q-0041"],                        # the question in the catalogue you answer (see below)
    purposes=["Maintain"],                       # the kinds of investigation you serve
    request_type="re-my-row-changes",            # how Egeria may ask for you (optional, step 9)
    nothing_to_derive_guard="survey-invalid",    # what Egeria is told when you had nothing to measure
    declaration=Declaration(
        result_kind="measure",                   # one number per scope
        measures=[MeasureDeclaration(
            name="rows_changed",
            unit="rows changed since the previous survey",   # sentence 1
            as_of="asOf",                                     # sentence 2: the field holding the time
            value_field="rowsChanged")],
        absence=AbsenceSentences(                            # sentences 3 to 5, plus "not permitted"
            not_measured="Not measured: no earlier survey has counted rows for this database yet.",
            not_permitted="Not permitted: the row counts could not be read.",
            not_applicable="Not applicable: row changes are measured for databases only.",
            nothing_found="Measured: no table changed between the two surveys."),
        confidence=ConfidenceDeclaration(form="measured"),   # a count, not an estimate
        scope="member",                                      # each result is about one table
    ),
)
```

**Which question?** Resource Explorer keeps a catalogue of the questions people ask about a database, each with an
id. Find the one your measurement answers in `packages/resource-explorer/resource_explorer/configdata/question_catalog.yaml`
(search for "changing"). If none fits, you can bring your own question with the step; the reference README shows how.

**What if you leave something out?** Try it. Blank the `unit` and run:

```bash
uv run --with-editable . python -c "import my_row_changes.manifest"
```

You will get a sentence, not a stack trace: *measure 'rows_changed' declares no unit: a number never travels without
its unit and its as-of.* That sentence is what anyone installing your package would see. Put the unit back.

## Step 5. Test it without Resource Explorer

Make a `tests` folder in your package (the example ships without one) and write one small test per state you
promised in step 1, each feeding `run` a fake `inputs` with hand-made snapshots:

- two snapshots with a difference → *measured* results with the right numbers and the as-of;
- fewer than two snapshots → *not_measured*, your sentence, and no numbers anywhere in the result;
- two identical snapshots → *nothing_found*.

Run them:

```bash
uv run --with-editable . pytest tests
```

Then let the library check your manifest the way Resource Explorer will:

```bash
uv run --package trellis-analytics pytest packages/trellis-analytics/tests -k declaration
```

## Step 6. Install it and start Resource Explorer

Install your package into the environment Resource Explorer runs from:

```bash
uv pip install -e packages/trellis-analytics/examples/steps/my_row_changes
```

Start Resource Explorer (or ask the operator to restart the shared one) and look at the first lines of its log.
You are looking for a line like:

```
library steps 10: … my_row_changes …
```

If your step is named with a reason instead, for example *declares request type 're-schema-inventory', which belongs
to Resource Explorer's own step*, fix the reason and reinstall. A step is never half-loaded.

> **If your step disappears later:** the ten reference steps are members of the repository's uv workspace, so a plain
> `uv sync` keeps them. A step you copied into `examples/steps/` is picked up as a member too, but the root
> `pyproject.toml` lists the members `uv sync` installs (its `dev` dependency group), so add your step's name there to
> keep it across a sync, or `uv sync` will uninstall it. A step kept outside the repository is not a member at all:
> if the log one day says "1 missing", run the install command again.

## Step 7. See it

Open a database in Resource Explorer. The address looks like
`http://localhost:8813/?type=db&resource=<the database's slug>&stage=discovery` (your question is a Discovery one;
the Survey & analyses tab is the same on every stage).

1. **Survey & analyses tab.** Find the card "Row changes". It says *never run* with an empty circle. That is
   Resource Explorer's own way of saying nothing has happened yet; your sentences are for after a run. Above the
   Run button is a price line saying what the run will cost. Press Run (on a shared instance, ask the operator).
2. **Activity** (top bar). Your run appears with its duration and how many results it wrote.
3. **Survey & analyses tab again.** Your card now shows the result: the number, "rows changed since the previous
   survey", the as-of time, and a "measured" badge.
4. **Questions tab.** Find the question you chose. Under it is a line for your step: `my_row_changes · measured ·
   +30 rows · run 1 min ago · the numbers behind this ›`. Press the link to see one row per table.
5. **Try the absence.** Open a database that has had only one survey. Run your step there. The card and the
   question both show your sentence, *Not measured: no earlier survey has counted rows…*, with a dotted-circle cue,
   and no number.
6. **Chat.** Ask the question in your own words, about that database. The answer quotes your result and cites it.

## Step 8. Change it, and prove the change is yours

Edit the `description` in your manifest. Reinstall and restart:

```bash
uv pip install -e packages/trellis-analytics/examples/steps/my_row_changes
```

The card shows the new words. You changed nothing in Resource Explorer. That is the point of the whole design: the
application is the host, and your package is the guest it reads.

## Step 9 (optional). Let Egeria run it

Egeria can start your step as part of a governed survey. Two generators write what Egeria needs; a person then runs
the result, because it writes to Egeria:

```bash
uv run --with-editable packages/trellis-analytics/examples/steps/my_row_changes \
    python packages/resource-explorer/scripts/generate_external_step_entries.py
uv run --with-editable packages/trellis-analytics/examples/steps/my_row_changes \
    python packages/resource-explorer/scripts/generate_engine_host_declaration.py
```

The second writes `packages/resource-explorer/docs/dr-egeria/engine-host/library/my_row_changes.md`. Whoever
administers Egeria runs it with `resource-explorer dr-egeria run engine-host/library/my_row_changes.md`. From then on
Egeria knows your step by its request type, and when it asks, Resource Explorer runs it and files the results in a
survey report. Retiring a step later is a status change in Egeria, never a deletion; old results keep saying who
produced them and which version.

## When something goes wrong

| You see | It means | Do |
|---|---|---|
| *declares no unit* (or as-of, or a missing sentence) | the manifest is incomplete | add the field; the sentence names it |
| *takes a built-in step's name* / *request type … belongs to* | your name collides with Resource Explorer's own | pick another name |
| *declares `connection`* | you asked for a database connection; steps run unattended and get none | measure from `inputs.ods` instead |
| *nothing from that run is stored* | one result lacked a state or a reason | fix the result; run again |
| the card says *not installed · provided by trellis-step-my-row-changes* | Resource Explorer remembers your step but the package is gone from its environment | run the install command |
| the log says *N missing* after someone ran `uv sync` | the step is not in the root `pyproject.toml`'s `dev` group, or lives outside the workspace | add it to the group, or run the install command |

## What you did not have to do

Edit Resource Explorer. Write SQL. Hold a password. Draw a chart. Write the words for "nothing" more than once.
Explain to anyone what a 0 meant.

## Where to go next

- The reference: the [egeria-trellis-analytics README](https://github.com/odpi/egeria-trellis-analytics#readme), every field and rule.
- Putting your result on a Portal dashboard: the Portal's *Local Dashboards* tutorial. Your step is already listed in
  the Portal's Analytic Functions Browser, because the same registry serves both. A report spec names it by its
  registered name, `my_row_changes`, never by its Python path.
- How steps are versioned, retired and governed: the lifecycle design note.
