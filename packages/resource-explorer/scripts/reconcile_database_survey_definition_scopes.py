"""Reconcile database Survey Definitions' `ScopedBy` links against what
their authored document actually names — the database equivalent of
`reconcile_survey_definition_scopes.py` (Phase 1b coordinator brief,
slice 12).

That script diffs `NextGovernanceActionProcessStep` edges (see
`reconcile_database_survey_definition_links.py`); this one diffs
`ScopedBy` links from a Survey Definition to the Question terms its
document's `## Link Element To Scope` blocks name. See
`reconcile_survey_definition_scopes.py`'s own docstring for the two real
incidents (an out-of-order batch run; a superseded Question term left
linked) that motivate this as its own check, independent of the step-edge
reconciler — the same mechanism applies here unchanged.

Unlike the repo version, this reads its file list directly from
`generate_database_survey_definition.py`'s own `SPECS` rather than a
separate `_batch.json` manifest — every database Survey Definition
document is one of `SPECS`'s own `output_filename`s, so there is no
batch-external file to additionally track. (The repo version's
`_batch.json` also drives bootstrap auto-healing after a platform
reset — that resilience wiring is not attempted here yet; see this
slice's own IMPLEMENTED doc for why.)

DEFAULT IS REPORT-ONLY. This script never writes to Egeria unless
`--remove-extra` is explicitly given, and never both writes and emits a
document in one invocation (see `--emit-missing`).

Usage:
    uv run python scripts/reconcile_database_survey_definition_scopes.py [--remove-extra] [--emit-missing PATH]

Exit code: 0 when every definition is fully reconciled (no missing, no extra);
1 when anything is missing, extra, or could not be determined at all.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_GENERATOR_PATH = _THIS_DIR / "generate_database_survey_definition.py"
_SURVEY_DEFS_DIR = _THIS_DIR.parent / "docs" / "dr-egeria" / "survey-definitions-database"


def _load_specs():
    """Loads generate_database_survey_definition.py's SPECS by path
    (scripts/ isn't a package) — same single source of truth
    reconcile_database_survey_definition_links.py already reads."""
    spec = importlib.util.spec_from_file_location("generate_database_survey_definition", _GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module.SPECS


_EMIT_HEADER = """# {title} — generated missing scope links, {date}

GENERATED SUBSET, not a source document. `## Link Element To Scope` blocks for the
Question(s) `scripts/reconcile_database_survey_definition_scopes.py --emit-missing` found authored
in `{doc_filename}` but not currently linked live. Run this document alone through
Dr.Egeria to add them — it contains no `Link First/Next Process Step` commands, so it
cannot duplicate a step edge.
"""


def _emit_missing_document(path: Path, entries: list[tuple], generated_date: str) -> None:
    from resource_explorer.surveyors.dr_egeria_survey_publisher import render_scope_link_block

    blocks: list[str] = []
    titles = []
    for survey_display_name, doc_filename, missing in entries:
        if not missing:
            continue
        titles.append(survey_display_name)
        for question in missing:
            blocks.append(render_scope_link_block(survey_display_name, question))

    header = _EMIT_HEADER.format(
        title=" / ".join(titles) if titles else "no missing links",
        date=generated_date,
        doc_filename=", ".join(f for _, f, m in entries if m) or "(none)",
    )
    path.write_text(header + "\n---\n\n" + "\n___\n\n".join(blocks) + ("\n" if blocks else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--remove-extra", action="store_true",
        help="Remove every live ScopedBy link the authored document does not name, via "
             "ClassificationExplorer.clear_scope_from_element. WRITES to Egeria.",
    )
    parser.add_argument(
        "--emit-missing", metavar="PATH",
        help="Write a Dr.Egeria subset document containing only the missing "
             "'Link Element To Scope' blocks, for a person to run by hand. Does not "
             "write to Egeria itself.",
    )
    parser.add_argument("--platform-url", default=None)
    parser.add_argument("--view-server", default=None)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--user-password", default=None)
    args = parser.parse_args()

    if args.remove_extra and args.emit_missing:
        parser.error(
            "--remove-extra and --emit-missing cannot both be given in one invocation — "
            "removing extra links and authoring new ones are two deliberate, separate "
            "operator decisions. Run this script twice."
        )

    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader
    from resource_explorer.surveyors.survey_definition_reconciler import (
        diff_scopes,
        expected_scopes_from_document,
    )

    reader = SurveyDefinitionReader(
        platform_url=args.platform_url, view_server=args.view_server,
        user_id=args.user_id, user_password=args.user_password,
    )

    specs = _load_specs()

    exit_code = 0
    emit_entries: list[tuple] = []

    for spec in specs:
        filename = spec.output_filename
        doc_path = _SURVEY_DEFS_DIR / filename
        try:
            doc_text = doc_path.read_text()
        except OSError as exc:
            print(f"[{spec.survey_kind}] {filename}: ERROR reading document — {exc}")
            exit_code = 1
            continue

        expected = expected_scopes_from_document(doc_text)
        process_qualified_name = f"GovActionProcess::{spec.survey_group}"

        try:
            guid = reader.find_process_guid_by_name(process_qualified_name)
        except Exception as exc:
            print(f"[{spec.survey_kind}] {process_qualified_name}: ERROR resolving process — {exc}")
            exit_code = 1
            continue

        if not guid:
            print(f"[{spec.survey_kind}] {process_qualified_name}: not found in Egeria — skip (authored yet?)")
            continue

        try:
            live = reader.get_live_scopes(guid)
        except Exception as exc:
            print(f"[{spec.survey_kind}] {process_qualified_name}: ERROR reading live scopes — {exc}")
            exit_code = 1
            continue

        result = diff_scopes(live, expected)
        result.process_qualified_name = process_qualified_name

        if result.missing or result.extra:
            exit_code = 1

        print(
            f"[{spec.survey_kind}] {process_qualified_name}: kept {len(result.kept)}, "
            f"missing {len(result.missing)}, extra {len(result.extra)}, "
            f"unresolvable {len(result.unresolvable)}"
        )
        if result.kept:
            print(f"    kept: {', '.join(result.kept)}")
        if result.missing:
            print(f"    missing: {', '.join(result.missing)}")
        if result.extra:
            print(f"    extra: {', '.join(e.display_name for e in result.extra)}")
        if result.unresolvable:
            for u in result.unresolvable:
                print(f"    unresolvable: guid={u.guid} type={u.type_name} qn={u.qualified_name} — {u.reason}")

        if args.remove_extra and result.extra:
            before = len(live)
            for extra in result.extra:
                print(f"    removing: {extra.display_name} (guid={extra.guid})")
                reader.remove_scope(extra.guid, guid)
            after_live = reader.get_live_scopes(guid)
            after = len(after_live)
            print(f"    before {before} scope(s), after {after} scope(s) (removed {len(result.extra)})")
            after_result = diff_scopes(after_live, expected)
            if after_result.extra:
                print(
                    f"    WARNING: {len(after_result.extra)} extra scope(s) still present "
                    f"after removal — re-run to check"
                )

        if args.emit_missing:
            emit_entries.append((spec.survey_display_name, filename, list(result.missing)))

    if args.emit_missing:
        from datetime import date

        out_path = Path(args.emit_missing)
        _emit_missing_document(out_path, emit_entries, date.today().isoformat())
        total_missing = sum(len(m) for _, _, m in emit_entries)
        print(f"\nWrote {total_missing} missing scope link(s) to {out_path}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
