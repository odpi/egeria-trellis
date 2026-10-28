"""Generate the Dr.Egeria document that declares RE's engine host to Egeria (E1 of
docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md §5.1).

Written to `docs/dr-egeria/engine-host/resource-explorer-survey-engine.md`, generated from
DATABASE_STEP_REGISTRY through `resource_explorer.engine_host` (the same names the claim sweep dispatches on),
together with its sibling `resource-explorer-survey-engine.pyegeria-steps.json`: the three elements pyegeria
6.2.3's Dr.Egeria has no command for (the SurveyActionEngine, one SurveyActionService per step, and their
SupportedGovernanceService links), which `resource_explorer.dr_egeria_runner` runs as find-or-create pyegeria
steps BEFORE the document. This script never talks to Egeria, and the folder has no `_batch.json`, so RE's
bootstrap never runs it: an admin runs it with `resource-explorer dr-egeria run
engine-host/resource-explorer-survey-engine.md` (or the admin route).

TODO (engine host, step 2 live gate): add this folder to the initialization batches (a `_batch.json` here and
an entry in `_folder_order.json`) only AFTER the live gate of engine-host step 2 has passed against Egeria.

**Analytic Library steps (engine host, step 3).** Each installed library step the engine host admits
(`survey_step_host.library_steps()`, the same function the claim sweep dispatches through) gets its OWN document,
`docs/dr-egeria/engine-host/library/<step>.md`, with its own `.pyegeria-steps.json`: the engine (find-or-create),
one SurveyActionService, one SupportedGovernanceService link carrying the request type the manifest declares, then
the GovernanceActionType `ResourceExplorerSurvey::<request type>` with the manifest's fields as Additional Properties
(`trellis_analytics.declare.manifest_properties`) and its GovernanceActionExecutor. One document per step, so
declaring a new step never re-runs another step's "Link Action to Action Executor", which is not safe to repeat.
The manifests are read from the `trellis.analytics` entry point, so the steps' packages must be installed where
this runs:

    uv run --with-editable packages/trellis-analytics/examples/steps/database_staleness \
        python packages/resource-explorer/scripts/generate_engine_host_declaration.py [--check]

A step that the analysis catalog names (generated from its manifest, `provided_by`) but that is not installed
here is refused with its package's name, never silently left out.

    uv run python packages/resource-explorer/scripts/generate_engine_host_declaration.py [--check]

**Mixed processes (engine host, step 4).** `--mixed-process` generates (or, with `--check`, checks) only the documents
of `resource_explorer.mixed_process.MIXED_PROCESSES`, one per process under `docs/dr-egeria/engine-host/processes/`:
the GovernanceActionProcess, its GovernanceActionProcessSteps, each step's GovernanceActionExecutor (the executor of the
GovernanceActionType the step stands for: a step cannot point at a type, so the type is named in its Additional
Properties), the first-step link and each next-step link with its guard. pyegeria 6.2.3's Dr.Egeria has a command for
every one of these (`commands_action_author.json`), so no pyegeria steps file is written. Without the flag every
document is generated, these included.

**Registry steps (engine host, case 3).** `docs/dr-egeria/engine-host/steps/<kind>-steps.md` (and its pyegeria steps
file), one per step registry (database, repo): every registry step not already in the engine's own document, as a
SurveyActionService, a SupportedGovernanceService `re-<step key>` and a GovernanceActionType with its executor and the
producedGuards of `step_outcome.produced_guards`, so the executor each Survey Definition step carries (C2) names a
request type the engine advertises. Generated with every document (no flag).

`--check` writes nothing and exits 1 when the committed document or steps file differs from what would be generated.
tests/test_engine_host_declaration.py holds the committed document to this generator and to the registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
DOC = PKG / "docs" / "dr-egeria" / "engine-host" / "resource-explorer-survey-engine.md"
STEPS = DOC.with_name(DOC.stem + ".pyegeria-steps.json")      # dr_egeria_runner.STEPS_SUFFIX

ENGINE_DESCRIPTION = (
    "Resource Explorer's survey engine. It is hosted by Resource Explorer's worker, outside any Egeria engine "
    "host (Q-M4): no Java engine host configuration may list it, or a JVM host races Resource Explorer for "
    "every claim.")


def _header() -> str:
    from resource_explorer import engine_host as eh

    steps = eh.declared_steps()
    rows = "\n".join(
        f"  - {eh.SERVICE_TYPE_NAME} `{eh.service_qualified_name(k)}`, linked to the engine by\n"
        f"    SupportedGovernanceService {{requestType: \"{eh.request_type_for_step(k)}\", "
        f"serviceRequestType: \"{eh.request_type_for_step(k)}\"}}"
        for k in steps)
    return f"""<!--
GENERATED from DATABASE_STEP_REGISTRY by packages/resource-explorer/scripts/generate_engine_host_declaration.py.
Do not hand-edit; regenerate. Design: docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md §5.1 (E1).

RE's bootstrap never runs this document (this folder is not a bootstrap batch); an admin runs it with
`resource-explorer dr-egeria run engine-host/{DOC.name}`.

NOT EXPRESSIBLE IN Dr.Egeria (pyegeria 6.2.3 has no command for these). They are pyegeria steps in
{STEPS.name}, which `resource-explorer dr-egeria run` runs
FIRST, in this order, each finding its element by qualifiedName and creating it only when absent:
  - {eh.ENGINE_TYPE_NAME} `{eh.ENGINE_QUALIFIED_NAME}` (display name "{eh.ENGINE_DISPLAY_NAME}"). Hosted by
    Resource Explorer's worker, outside any Egeria engine host (Q-M4). No Java engine host configuration may
    list it, or a JVM host races RE for every claim.
{rows}
The commands below are the ones Dr.Egeria has: the GovernanceActionType a person or process initiates, and its
GovernanceActionExecutor link to the engine. "Link Action to Action Executor" is NOT safe to repeat; the runner
refuses a second run of the same content unless forced.

TODO (engine host, step 2 live gate): this folder joins the initialization batches (_batch.json +
_folder_order.json) only after the live gate of engine-host step 2.
-->
"""


def render() -> str:
    from resource_explorer import engine_host as eh

    parts = [_header()]
    for step_key, info in eh.declared_steps().items():
        request_type = eh.request_type_for_step(step_key)
        gat = eh.governance_action_type_qn(step_key)
        parts.append(f"""
## Create Governance Action Type
### Display Name
Resource Explorer Survey — {step_key}

### Qualified Name
{gat}

### Description
{info.description}

### Produced Guards
{", ".join(eh.SURVEY_GUARDS)}

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | {step_key} |
| engine_host | resource-explorer-worker |

___

## Link Action to Action Executor
### Governance Action Type
{gat}

### Governance Engine
{eh.ENGINE_QUALIFIED_NAME}

### Request Type
{request_type}

### Description
Egeria dispatches {gat} to Resource Explorer's engine, which claims it and runs RE step {step_key}.

___
""")
    return "".join(parts)


def render_steps() -> str:
    """The pyegeria steps file: engine, then one service and one SupportedGovernanceService link per step, in the
    order dr_egeria_runner requires (engine -> services -> links)."""
    from resource_explorer import engine_host as eh

    steps = [{"command": "Create Survey Action Engine", "qualified_name": eh.ENGINE_QUALIFIED_NAME,
              "display_name": eh.ENGINE_DISPLAY_NAME, "description": ENGINE_DESCRIPTION}]
    declared = eh.declared_steps()
    for step_key, info in declared.items():
        steps.append({"command": "Create Survey Action Service",
                      "qualified_name": eh.service_qualified_name(step_key),
                      "display_name": f"Resource Explorer Survey Service — {step_key}",
                      "description": info.description})
    for step_key in declared:
        steps.append({"command": "Link Supported Governance Service",
                      "governance_engine": eh.ENGINE_QUALIFIED_NAME,
                      "governance_service": eh.service_qualified_name(step_key),
                      "request_type": eh.request_type_for_step(step_key),
                      "service_request_type": eh.request_type_for_step(step_key)})
    doc = {"$comment": [
        f"GENERATED with {DOC.name} by packages/resource-explorer/scripts/generate_engine_host_declaration.py "
        "from DATABASE_STEP_REGISTRY. Do not hand-edit; regenerate.",
        "pyegeria steps for what pyegeria 6.2.3's Dr.Egeria cannot express (docs/dr-egeria-engine-host-gaps.md). "
        "resource_explorer.dr_egeria_runner runs them, in this order, before the document: each finds its "
        "element by qualifiedName (the link: by engine, service and requestType) and creates it only when absent.",
        "TODO (engine host, step 2 live gate): add engine-host to the initialization batches only after that gate.",
    ], "steps": steps}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


LIBRARY = DOC.parent / "library"


def _catalogued_library_steps() -> dict[str, str]:
    """{step: package} for every user step the analysis catalog names (its entries are generated from manifests)."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    out = {}
    for entity_type in ("database",):
        for entry in _load().get(entity_type, []):
            if entry.provided_by:
                out[entry.provided_by.get("step") or entry.id] = entry.provided_by.get("package") or "(unnamed)"
    return out


def _library_header(step, gat: str) -> str:
    from trellis_analytics.discovery import origin

    from resource_explorer import engine_host as eh

    # The catalog entry's package when the step has one, else the installed distribution's (a step with no card).
    package = step.provided_by.get("package") or origin(step.step_key).get("package", "")
    return f"""<!--
GENERATED from the manifest of '{step.step_key}' ({package} {step.manifest.version}) by
packages/resource-explorer/scripts/generate_engine_host_declaration.py. Do not hand-edit; regenerate.
Design: docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md §4.3 (engine host, step 3).

Declares the Analytic Library step '{step.step_key}' as a service of Resource Explorer's engine, so Egeria can
dispatch it: initiating {gat} creates an engine action with request type {step.request_type}, which RE's engine host
claims, runs through its step host and completes with one of {", ".join(eh.SURVEY_GUARDS)}
({step.nothing_to_derive_guard} when there is nothing to derive).

Run it with `resource-explorer dr-egeria run engine-host/library/{step.step_key}.md`. RE's bootstrap never runs it.
Its pyegeria steps ({step.step_key}{STEPS.name[len(DOC.stem):]}) run first, each find-or-create:
  - {eh.ENGINE_TYPE_NAME} `{eh.ENGINE_QUALIFIED_NAME}` (normally already there, from the engine's own document);
  - {eh.SERVICE_TYPE_NAME} `{eh.service_qualified_name(step.step_key)}`, linked to the engine by
    SupportedGovernanceService {{requestType: "{step.request_type}", serviceRequestType: "{step.request_type}"}}.
"Link Action to Action Executor" below is NOT safe to repeat; the runner refuses a second run of the same content
unless forced. Retire the step by setting Content Status to DEPRECATED, never by deleting the element.
-->
"""


def render_library() -> dict[Path, str]:
    """{path: text} for every admitted library step: its document and its pyegeria steps file. Exits naming the
    package when a step the analysis catalog names is not installed here (it would otherwise be dropped)."""
    from trellis_analytics.declare import manifest_properties
    from trellis_analytics.discovery import origin

    from resource_explorer import engine_host as eh
    from resource_explorer.surveyors import survey_step_host

    steps = eh.library_steps()
    installed = {s.step_key for s in steps.values()} | set(survey_step_host.library_step_refusals())
    missing = {k: pkg for k, pkg in _catalogued_library_steps().items() if k not in installed}
    if missing:
        raise SystemExit("cannot generate the engine-host declaration: the analysis catalog names "
                         + ", ".join(f"'{k}' (package {pkg})" for k, pkg in sorted(missing.items()))
                         + ", not installed here. Install it (uv run --with-editable <its package>) and run again.")
    out: dict[Path, str] = {}
    for request_type, step in sorted(steps.items(), key=lambda kv: kv[1].step_key):
        gat = eh.governance_action_type_qn_for(request_type)
        rows = [("executes_at", "resource-explorer"), ("re_analysis_step", step.step_key),
                ("engine_host", "resource-explorer-worker"), ("request_type", request_type),
                ("nothing_to_derive_guard", step.nothing_to_derive_guard),
                *manifest_properties(step.manifest, origin(step.step_key))]
        table = "\n".join(["| Parameter Name | Parameter Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows])
        name = getattr(step.manifest, "display_name", "") or step.step_key
        doc = _library_header(step, gat) + f"""
## Create Governance Action Type
### Display Name
Resource Explorer Survey — {name}

### Qualified Name
{gat}

### Description
{step.description}

### Produced Guards
{", ".join(eh.SURVEY_GUARDS)}

### Content Status
ACTIVE

### Additional Properties
{table}

___

## Link Action to Action Executor
### Governance Action Type
{gat}

### Governance Engine
{eh.ENGINE_QUALIFIED_NAME}

### Request Type
{request_type}

### Description
Egeria dispatches {gat} to Resource Explorer's engine, which claims it and runs Analytic Library step {step.step_key}.

___
"""
        path = LIBRARY / f"{step.step_key}.md"
        steps_doc = {"$comment": [
            f"GENERATED with {path.name} by packages/resource-explorer/scripts/generate_engine_host_declaration.py "
            f"from the manifest of '{step.step_key}'. Do not hand-edit; regenerate.",
            "resource_explorer.dr_egeria_runner runs these find-or-create pyegeria steps, in this order, before the "
            "document (docs/dr-egeria-engine-host-gaps.md).",
        ], "steps": [
            {"command": "Create Survey Action Engine", "qualified_name": eh.ENGINE_QUALIFIED_NAME,
             "display_name": eh.ENGINE_DISPLAY_NAME, "description": ENGINE_DESCRIPTION},
            {"command": "Create Survey Action Service", "qualified_name": eh.service_qualified_name(step.step_key),
             "display_name": f"Resource Explorer Survey Service — {step.step_key}",
             "description": step.description},
            {"command": "Link Supported Governance Service", "governance_engine": eh.ENGINE_QUALIFIED_NAME,
             "governance_service": eh.service_qualified_name(step.step_key), "request_type": request_type,
             "service_request_type": request_type},
        ]}
        out[path] = doc
        out[path.with_name(path.stem + STEPS.name[len(DOC.stem):])] = (
            json.dumps(steps_doc, indent=2, ensure_ascii=False) + "\n")
    return out


REGISTRY_STEPS = DOC.parent / "steps"


def registry_steps_path(entity_type: str) -> Path:
    return REGISTRY_STEPS / f"{entity_type}-steps.md"


def _registry_steps_for(entity_type: str) -> list:
    """The registry steps this kind's document declares: every key of its step registry except those the engine's
    own document already declares (`engine_host.ENGINE_HOST_STEPS`), so no step is declared twice."""
    from resource_explorer import engine_host as eh

    return [s for s in eh.registry_steps().values()
            if s.entity_type == entity_type and s.step_key not in eh.ENGINE_HOST_STEPS]


def _registry_step_header(entity_type: str, steps: list) -> str:
    from resource_explorer import engine_host as eh

    declared_elsewhere = [k for k in eh.ENGINE_HOST_STEPS if k in eh.registry_steps()
                          and eh.registry_steps()[k].entity_type == entity_type]
    return f"""<!--
GENERATED from the {entity_type} step registry by packages/resource-explorer/scripts/generate_engine_host_declaration.py.
Do not hand-edit; regenerate. Design: docs/design-notes/SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md
(C1, decision 1: one SurveyActionService per step key, never per definition).

Declares {len(steps)} {entity_type} step(s) as services of Resource Explorer's engine, so the GovernanceActionExecutor
each Survey Definition step carries (C2, dr_egeria_survey_publisher) names a request type the engine advertises. Each:
one {eh.SERVICE_TYPE_NAME}, one SupportedGovernanceService {{requestType: re-<step key>}} (both in
{registry_steps_path(entity_type).stem}{STEPS.name[len(DOC.stem):]}, run first by `resource-explorer dr-egeria run`, find-or-create), then
the GovernanceActionType ResourceExplorerSurvey::re-<step key> with producedGuards from step_outcome.py's mapping, and
its GovernanceActionExecutor below.{f'''
Not here, declared by the engine's own document: {", ".join(declared_elsewhere)}.''' if declared_elsewhere else ""}

Run it with `resource-explorer dr-egeria run engine-host/steps/{registry_steps_path(entity_type).name}` AFTER the
engine's own document. RE's bootstrap never runs it. "Link Action to Action Executor" is NOT safe to repeat; the runner
refuses a second run of the same content unless forced. Retire a step by Content Status DEPRECATED, never by deleting.
-->
"""


def render_registry_steps() -> dict[Path, str]:
    """{path: text}: per step registry, one Dr.Egeria document declaring every registry step as a request type of RE's
    engine (case 3, C1) and its pyegeria steps file. Out-of-scope steps are declared too, saying so in their
    Additional Properties: the engine host claims them and completes them survey-invalid with the reason."""
    from resource_explorer import engine_host as eh
    from resource_explorer import step_outcome

    out: dict[Path, str] = {}
    for entity_type in eh.REGISTRY_ENTITY_TYPES:
        steps = _registry_steps_for(entity_type)
        path = registry_steps_path(entity_type)
        parts = [_registry_step_header(entity_type, steps)]
        for step in steps:
            info = step.info
            gat = eh.governance_action_type_qn(step.step_key)
            scope = eh.out_of_scope_reason(step.step_key)
            rows = [("executes_at", "resource-explorer"), ("re_analysis_step", step.step_key),
                    ("engine_host", "resource-explorer-worker"), ("request_type", step.request_type),
                    ("entity_type", entity_type), ("fetch_cost", getattr(info, "fetch_cost", "") or "none"),
                    ("compute_cost", getattr(info, "compute_cost", "") or "low"),
                    ("requires_capability", getattr(info, "requires_capability", "") or "undeclared"),
                    ("engine_host_dispatch", "survey-invalid: " + scope.split(";")[0] if scope else "runs")]
            parts.append(f"""
## Create Governance Action Type
### Display Name
Resource Explorer Survey — {step.step_key}

### Qualified Name
{gat}

### Description
{info.description}

### Produced Guards
{", ".join(step_outcome.produced_guards(step.step_key))}

### Content Status
ACTIVE

### Additional Properties
{_table(rows)}

___

## Link Action to Action Executor
### Governance Action Type
{gat}

### Governance Engine
{eh.ENGINE_QUALIFIED_NAME}

### Request Type
{step.request_type}

### Description
Egeria dispatches {gat} to Resource Explorer's engine, which claims it and runs RE step {step.step_key}.

___
""")
        out[path] = "".join(parts)
        steps_doc = {"$comment": [
            (f"GENERATED with {path.name} by packages/resource-explorer/scripts/generate_engine_host_declaration.py "
             f"from the {entity_type} step registry. Do not hand-edit; regenerate."),
            ("resource_explorer.dr_egeria_runner runs these find-or-create pyegeria steps, in this order, before the "
             "document (docs/dr-egeria-engine-host-gaps.md): the engine, one service per step, one link per step."),
        ], "steps": [
            {"command": "Create Survey Action Engine", "qualified_name": eh.ENGINE_QUALIFIED_NAME,
             "display_name": eh.ENGINE_DISPLAY_NAME, "description": ENGINE_DESCRIPTION},
            *({"command": "Create Survey Action Service", "qualified_name": eh.service_qualified_name(s.step_key),
               "display_name": f"Resource Explorer Survey Service — {s.step_key}",
               "description": s.info.description} for s in steps),
            *({"command": "Link Supported Governance Service", "governance_engine": eh.ENGINE_QUALIFIED_NAME,
               "governance_service": eh.service_qualified_name(s.step_key), "request_type": s.request_type,
               "service_request_type": s.request_type} for s in steps),
        ]}
        out[path.with_name(path.stem + STEPS.name[len(DOC.stem):])] = (
            json.dumps(steps_doc, indent=2, ensure_ascii=False) + "\n")
    return out


PROCESSES = DOC.parent / "processes"


def mixed_process_path(key: str) -> Path:
    return PROCESSES / f"{key.replace('_', '-')}.md"


def _table(rows: list[tuple[str, str]]) -> str:
    return "\n".join(["| Parameter Name | Parameter Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows])


def render_mixed_process(process) -> str:
    """One mixed process's Dr.Egeria document, from its `MIXED_PROCESSES` entry and nothing else."""
    from resource_explorer import engine_host as eh

    steps = sorted(process.steps, key=lambda s: s.number)
    re_steps = ", ".join(f"{s.request_type} (step {s.number})" for s in process.re_steps)
    parts = [f"""<!--
GENERATED from MIXED_PROCESSES["{process.key}"] (resource_explorer/mixed_process.py) by
packages/resource-explorer/scripts/generate_engine_host_declaration.py --mixed-process. Do not hand-edit; regenerate.
Design: docs/design-notes/SLICE-ENGINE-HOST-STEP4-MIXED-PROCESS.md (M1).

Declares {process.qualified_name}: {len(steps)} steps, each run by the engine its executor names, chained by
NextGovernanceActionProcessStep links whose guards Egeria evaluates. A plain GovernanceActionProcess: Dr.Egeria cannot
create the SurveyingActionProcess subtype yet (egeria-python ISSUE-150); one line changes when it can.

Needs, already declared: RE's engine {eh.ENGINE_QUALIFIED_NAME} serving {re_steps}
(engine-host/library/), and the content pack's engines. Every element here has a Dr.Egeria command, so there is no
pyegeria steps file. Run it with `resource-explorer dr-egeria run engine-host/processes/{mixed_process_path(process.key).name}`;
RE's bootstrap never runs it. The Link commands are NOT safe to repeat; the runner refuses a second run of the same
content unless forced. Start a run with Dr.Egeria's Initiate Governance Action Process, or RE's Run on the database.
-->

## Create Governance Action Process
### Display Name
{process.display_name}

### Qualified Name
{process.qualified_name}

### Description
{process.description}

### Content Status
ACTIVE

### Additional Properties
{_table([("declared_by", "resource-explorer"), ("mixed_process", process.key),
         ("entity_type", process.entity_type)])}

___
"""]
    for step in steps:
        parts.append(f"""
## Create Governance Action Process Step
### Display Name
{step.display_name}

### Qualified Name
{step.qualified_name}

### Description
{step.description}

### Produced Guards
{", ".join(step.produced_guards)}

### Content Status
ACTIVE

### Additional Properties
{_table([("governance_action_type", step.governance_action_type), ("runs_on", step.runs_on),
         ("request_type", step.request_type), ("process", process.qualified_name),
         ("step_number", str(step.number))])}

___
""")
    for step in steps:
        parts.append(f"""
## Link Action to Action Executor
### Governance Action Type
{step.qualified_name}

### Governance Engine
{step.governance_engine}

### Request Type
{step.request_type}

### Description
Step {step.number} of {process.qualified_name} runs as {step.governance_action_type} does: engine
{step.governance_engine}, request type {step.request_type}.

___
""")
    parts.append(f"""
## Link First Process Step
### Governance Action Process
{process.qualified_name}

### Governance Action Process Step
{process.first_step.qualified_name}

___
""")
    for link in process.links:
        parts.append(f"""
## Link Next Process Step
### Governance Action Process Step
{process.step(link.from_step).qualified_name}

### Next Governance Action Process Step
{process.step(link.to_step).qualified_name}

### Guard
{link.guard}

### Mandatory Guard
{"true" if link.mandatory else "false"}

___
""")
    return "".join(parts)


def render_mixed_processes() -> dict[Path, str]:
    """{path: text} for every MIXED_PROCESSES entry. Exits naming the step when an RE step of a process is not one
    RE's engine host can run here (its library package is not installed): the process would be declared with a step
    nobody claims."""
    from resource_explorer import engine_host as eh
    from resource_explorer.mixed_process import MIXED_PROCESSES

    supported = eh.supported_request_types()
    out: dict[Path, str] = {}
    for key, process in sorted(MIXED_PROCESSES.items()):
        missing = [s.request_type for s in process.re_steps if s.request_type not in supported]
        if missing:
            raise SystemExit(f"cannot generate {process.qualified_name}: RE's engine host does not run "
                             f"{', '.join(missing)} here. Install its Analytic Library package "
                             "(uv run --with-editable <its package>) and run again.")
        out[mixed_process_path(key)] = render_mixed_process(process)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 when the document is stale")
    ap.add_argument("--mixed-process", action="store_true",
                    help="only the mixed-process documents (docs/dr-egeria/engine-host/processes/)")
    args = ap.parse_args(argv)
    stale = 0
    if args.mixed_process:
        want = render_mixed_processes()
        orphans = sorted(PROCESSES.glob("*")) if PROCESSES.is_dir() else []
    else:
        want = {DOC: render(), STEPS: render_steps(), **render_library(), **render_mixed_processes(),
                **render_registry_steps()}
        orphans = [*(sorted(LIBRARY.glob("*")) if LIBRARY.is_dir() else []),
                   *(sorted(PROCESSES.glob("*")) if PROCESSES.is_dir() else []),
                   *(sorted(REGISTRY_STEPS.glob("*")) if REGISTRY_STEPS.is_dir() else [])]
    # A document no admitted step or declared process generates any more is stale too (retire it by status, in Egeria).
    for orphan in orphans:
        if orphan not in want:
            print(f"STALE: {orphan} (nothing generates it any more; remove it)")
            stale = 1
    for path, text in want.items():
        current = path.read_text() if path.is_file() else None
        if args.check:
            if current != text:
                print(f"STALE: {path} (run {Path(__file__).name})")
                stale = 1
            else:
                print(f"up to date: {path}")
            continue
        if current == text:
            print(f"unchanged: {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"wrote {path}")
    return stale


if __name__ == "__main__":
    sys.exit(main())
