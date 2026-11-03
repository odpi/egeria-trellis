"""Reconcile repo Survey Definitions' step-links against their intended chain.

Run this any time docs/dr-egeria/repo-survey-definition-*.md is executed via
Dr.Egeria against an *already-linked* process — e.g. after
scripts/generate_repo_survey_definition.py regenerates a doc to add new
docs/survey-question-context-plan.md D1 ScopedBy Question links, or after
any STEP_REGISTRY reordering. Dr.Egeria's "Link First/Next Process Step"
commands are NOT idempotent (confirmed live 2026-08-13, the incident that
motivated this script) — re-running them against steps that are already
linked creates duplicate relationships rather than merging, and a chain
reorder leaves the old edge stale rather than replacing it. Either one
makes a step look "branching" to SurveyDefinitionReader, which correctly
refuses to guess and raises UnsupportedSurveyDefinitionError.

See resource_explorer/surveyors/survey_definition_reconciler.py for the
diff logic and resource_explorer/surveyors/survey_definition_reader.py's
reconcile_step_links() for the live fetch+delete side.

This reconciler compares step edges only. For a Survey Definition's
`ScopedBy` links to the Question terms its document names (docs/Backlog.md,
"Superseded Question term — unlinked and deleted 2026-09-13"), see the
sibling script scripts/reconcile_survey_definition_scopes.py.

Usage:
    uv run python scripts/reconcile_survey_definition_links.py [--dry-run]

Safe to run repeatedly — a fully-reconciled Survey Definition is a no-op.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_GENERATOR_PATH = Path(__file__).resolve().parent / "generate_repo_survey_definition.py"


def _load_specs():
    """Loads generate_repo_survey_definition.py's SPECS by path (scripts/
    isn't a package) — same single source of truth the doc generator
    itself uses, so this reconciler never drifts from what was authored."""
    spec = importlib.util.spec_from_file_location("generate_repo_survey_definition", _GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module.SPECS


def _main() -> None:
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


def main() -> None:
    """Run as RE's daemon: SurveyDefinitionReader resolves its principal from the calling context, and
    a CLI has no signed-in person (NoCallerIdentity otherwise)."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    with acting_as(Daemon(DaemonReason.BOOTSTRAP)):
        return _main()


if __name__ == "__main__":
    main()
