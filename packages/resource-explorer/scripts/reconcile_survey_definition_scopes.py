"""Reconcile repo Survey Definitions' `ScopedBy` links against what their
authored document actually names — the sibling of
`reconcile_survey_definition_links.py`, but for a different relationship.

That script diffs `NextGovernanceActionProcessStep` edges; this one diffs
`ScopedBy` links from a Survey Definition to the Question terms its document's
`## Link Element To Scope` blocks name. Neither incident this exists for
produces a "branching" symptom the step-edge reconciler can see:

  * 2026-08-19 — the questions batch ran after the survey-definitions batch
    once. Every `Link Element To Scope` command reported success against
    Question terms that did not exist yet, and created no relationship. The
    definition's own canary stayed present throughout; only the scoped
    candidate lookup silently returned empty.
  * 2026-09-13 — "What is its internal architecture...?" was split into four
    questions in the CSV (docs/Backlog.md, "Superseded Question term"). The
    old term's `ScopedBy` links from RepoFullSurvey and
    RepoArchitectureDiscovery were never removed when the replacement links
    were authored, so both definitions stayed scoped to a term that no
    longer existed on the platform (until removed and deleted by hand).

See `resource_explorer/surveyors/survey_definition_reconciler.py`'s
`expected_scopes_from_document()`/`diff_scopes()` for the diff logic and
`SurveyDefinitionReader.get_live_scopes()`/`.remove_scope()` for the live
fetch/write side.

DEFAULT IS REPORT-ONLY. This script never writes to Egeria unless
`--remove-extra` is explicitly given, and never both writes and emits a
document in one invocation (see `--emit-missing`) — each is a distinct,
deliberate step an operator asks for separately.

Usage:
    uv run python scripts/reconcile_survey_definition_scopes.py [--remove-extra] [--emit-missing PATH]

Exit code: 0 when every definition is fully reconciled (no missing, no extra);
1 when anything is missing, extra, or could not be determined at all
(unreachable platform, unresolvable definition) — so CI or a cron can notice.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_GENERATOR_PATH = _THIS_DIR / "generate_repo_survey_definition.py"
_SURVEY_DEFS_DIR = _THIS_DIR.parent / "docs" / "dr-egeria" / "survey-definitions"
_BATCH_MANIFEST = _SURVEY_DEFS_DIR / "_batch.json"


def _load_specs():
    """Loads generate_repo_survey_definition.py's SPECS by path (scripts/
    isn't a package) — same single source of truth
    reconcile_survey_definition_links.py already reads, so this reconciler
    never drifts from what was authored either. Gives survey_group and
    survey_display_name per output_filename, both of which the document
    text itself doesn't restate as one clean value (the qualified name in
    `## Create Governance Action Process` embeds survey_group, but parsing
    that back out here would be a second, divergeable copy of what SPECS
    already knows)."""
    spec = importlib.util.spec_from_file_location("generate_repo_survey_definition", _GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module.SPECS


def _load_batch_files() -> list[str]:
    import json

    data = json.loads(_BATCH_MANIFEST.read_text())
    return list(data.get("files", []))


_EMIT_HEADER = """# {title} — generated missing scope links, {date}

GENERATED SUBSET, not a source document. `## Link Element To Scope` blocks for the
Question(s) `scripts/reconcile_survey_definition_scopes.py --emit-missing` found authored
in `{doc_filename}` but not currently linked live. Run this document alone through
Dr.Egeria to add them — it contains no `Link First/Next Process Step` commands, so it
cannot duplicate a step edge.
"""


def _emit_missing_document(path: Path, entries: list[tuple], generated_date: str) -> None:
    """Writes one Dr.Egeria subset document containing only the missing
    `Link Element To Scope` blocks, matching the header shape of
    docs/archive/dr-egeria-batches/arch-discovery-scope-links-2026-09-13.md.
    `entries` is a list of (survey_display_name, doc_filename, [missing question names])."""
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


def _main() -> None:
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

    specs_by_output = {spec.output_filename: spec for spec in _load_specs()}
    batch_files = _load_batch_files()

    exit_code = 0
    emit_entries: list[tuple] = []

    for filename in batch_files:
        spec = specs_by_output.get(filename)
        if spec is None:
            print(f"[?] {filename}: not in generate_repo_survey_definition.py's SPECS — skip")
            exit_code = 1
            continue

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
            f"unresolvable {len(result.unresolvable)}, deprecated {len(result.deprecated)}"
        )
        if result.kept:
            print(f"    kept: {', '.join(result.kept)}")
        if result.missing:
            print(f"    missing: {', '.join(result.missing)}")
        if result.extra:
            print(f"    extra: {', '.join(e.display_name for e in result.extra)}")
        for d in result.deprecated:   # reported, never removed, never counted as drift
            print(f"    {d.label}: {d.display_name} (guid={d.guid})")
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


def main() -> None:
    """Run as RE's daemon: SurveyDefinitionReader resolves its principal from the calling context, and
    a CLI has no signed-in person (NoCallerIdentity otherwise)."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    with acting_as(Daemon(DaemonReason.BOOTSTRAP)):
        return _main()


if __name__ == "__main__":
    main()
