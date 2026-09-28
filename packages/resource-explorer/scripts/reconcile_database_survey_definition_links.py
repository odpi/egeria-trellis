"""Reconcile database Survey Definitions' step-links against their intended
chain — the database equivalent of `reconcile_survey_definition_links.py`
(Phase 1b coordinator brief, slice 12).

Run this any time
docs/dr-egeria/survey-definitions-database/database-survey-definition-*.md is
executed via Dr.Egeria against an *already-linked* process — e.g. after
scripts/generate_database_survey_definition.py regenerates a doc, or after
any DATABASE_STEP_REGISTRY reordering. Dr.Egeria's "Link First/Next Process
Step" commands are NOT idempotent — see
`reconcile_survey_definition_links.py`'s own docstring for the full
incident history this reconciler exists to prevent; the same mechanism
applies here unchanged, since `survey_definition_reconciler.py`/
`survey_definition_reader.py` are already resource-type-agnostic.

For a Survey Definition's `ScopedBy` links to the Question terms its
document names, see the sibling script
`scripts/reconcile_database_survey_definition_scopes.py`.

Usage:
    uv run python scripts/reconcile_database_survey_definition_links.py [--dry-run]

Safe to run repeatedly — a fully-reconciled Survey Definition is a no-op.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_GENERATOR_PATH = Path(__file__).resolve().parent / "generate_database_survey_definition.py"


def _load_specs():
    """Loads generate_database_survey_definition.py's SPECS by path
    (scripts/ isn't a package) — same single source of truth the doc
    generator itself uses, so this reconciler never drifts from what was
    authored."""
    spec = importlib.util.spec_from_file_location("generate_database_survey_definition", _GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module.SPECS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without deleting anything.")
    parser.add_argument("--platform-url", default=None)
    parser.add_argument("--view-server", default=None)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--user-password", default=None)
    args = parser.parse_args()

    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

    reader = SurveyDefinitionReader(
        platform_url=args.platform_url, view_server=args.view_server,
        user_id=args.user_id, user_password=args.user_password,
    )

    any_changes = False
    for spec in _load_specs():
        process_qualified_name = f"GovActionProcess::{spec.survey_group}"
        guid = reader.find_process_guid_by_name(process_qualified_name)
        if not guid:
            print(f"[{spec.survey_kind}] {process_qualified_name}: not found in Egeria — skip (authored yet?)")
            continue

        result = reader.reconcile_step_links(guid, spec.survey_group, spec.step_keys, dry_run=args.dry_run)
        if result.error:
            print(f"[{spec.survey_kind}] {process_qualified_name}: ERROR — {result.error}")
            continue

        verb = "would remove" if args.dry_run else "removed"
        if result.removed_total:
            any_changes = True
        print(
            f"[{spec.survey_kind}] {process_qualified_name}: kept {result.kept} edge(s), "
            f"{verb} {result.removed_duplicate} duplicate, {result.removed_stale} stale"
        )
        for entry in result.to_remove:
            print(f"    {entry.reason}: {entry.prev_qualified_name} -> {entry.next_qualified_name}")

    if not any_changes:
        print("\nAll Survey Definitions already reconciled — nothing to do.")


if __name__ == "__main__":
    main()
