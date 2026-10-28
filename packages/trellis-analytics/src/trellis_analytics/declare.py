"""What a step's Egeria declaration says about the step, generated from its manifest (brief 2c, 3a step 4).

Generated only: nothing here talks to Egeria. Retirement is a status (`Content Status` DEPRECATED), never a delete.

The declaration itself is the engine host's (Mandy, 2026-10-14, Q-M2; egeria-python ISSUE-150): Resource
Explorer's `SurveyActionEngine` `ResourceExplorerSurvey`, and per step one `SurveyActionService`, one
`SupportedGovernanceService` link carrying the step's request type, and one `GovernanceActionType`
(`ResourceExplorerSurvey::<request type>`) with a `GovernanceActionExecutor` to the engine. Resource Explorer's
`scripts/generate_engine_host_declaration.py` writes it, one document per step under
`docs/dr-egeria/engine-host/library/`.

This module gives that document the manifest's own fields, as the type's `additionalProperties` rows, keyed exactly
like the manifest fields (ISSUE-150 (c)): `manifest_properties`. It replaced `declaration_markdown`, the provisional
2c document (`GovActionType::trellis-analytics::<step>`, a GovernanceActionType with no executor that nothing could
dispatch), which is retired.
"""
from __future__ import annotations

from trellis_analytics.manifest import StepManifest


def manifest_properties(manifest: StepManifest, origin: dict[str, str]) -> list[tuple[str, str]]:
    """(key, value) rows for the declaration's Additional Properties. `origin` is `discovery.origin(name)`: the
    package and entry point that provide the step."""
    m = manifest
    return [
        ("kind", m.kind), ("source", m.source), ("version", m.version),
        ("resource_types", ", ".join(m.resource_types)), ("annotation_type", m.annotation_type),
        ("questions", ", ".join(m.questions)), ("purposes", ", ".join(m.purposes)),
        ("perspectives", ", ".join(m.perspectives)), ("credential_tier", m.credential_tier),
        ("inputs", ", ".join(m.inputs)), ("measures", ", ".join(m.measure_names())), ("family", m.family),
        ("function", m.function),
        ("package", origin.get("package", "")), ("entry_point", f"trellis.analytics:{origin.get('entry_point', '')}"),
        # Only when a measure is published as another Egeria type than the step's own (a step with none says nothing).
        # Only when the step declares parameters: the values its code reads (`inputs.parameters`).
        *([("parameters", ", ".join(f"{k}={v}" for k, v in m.parameters.items()))] if m.parameters else []),
        *([("measure_annotation_types", ", ".join(f"{k}: {v}" for k, v in m.measure_annotation_types.items()))]
          if m.measure_annotation_types else []),
    ]
