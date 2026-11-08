"""Reconcile RE's executor links: exactly ONE `Link Action to Action Executor` to ResourceExplorerSurvey per
GovernanceActionType the Dr.Egeria documents declare (RE-HEAL-LEDGER-TRUST decision 5, 2026-11-07). The logic and its
rules are in `resource_explorer/executor_links.py`.

Wired in as the engine-host batch's `post_heal` (with --apply: the heal's own write, inside the heal lock, recorded as
an activity row) and as a report-only `post_heal_checks` entry of each survey-definition batch that requires it.

Usage:
    uv run python scripts/reconcile_executor_links.py                    # report (dry run); engine-host + dependents
    uv run python scripts/reconcile_executor_links.py --batch survey-definitions-database
    uv run python scripts/reconcile_executor_links.py --document path/to/links.md
    uv run python scripts/reconcile_executor_links.py --apply            # add missing, remove duplicates

Exit, report mode: 0 no drift, 1 drift. With --apply: 0 when every missing/duplicate link was repaired and every
type could be read (a type not yet in Egeria and a wrong request type are reported, not failed on), 1 otherwise.
2 either way when nothing could be checked (the engine is absent, or Egeria could not be read).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--batch", action="append", default=[], help="a bootstrap batch id (repeatable)")
    ap.add_argument("--document", action="append", default=[], help="a Dr.Egeria document (repeatable)")
    ap.add_argument("--apply", action="store_true", help="add missing links and remove duplicates")
    args = ap.parse_args(argv)

    from resource_explorer import executor_links as xl
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    paths = [Path(p) for p in args.document] or xl.batch_documents(args.batch)
    declared = xl.declared_links_of(paths)
    with acting_as(Daemon(DaemonReason.BOOTSTRAP)):
        from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

        reader = SurveyDefinitionReader()
        try:
            expert = reader._connect_metadata_expert()
            author = None
            if args.apply:
                from pyegeria.omvs.action_author import ActionAuthor

                author = reader._clients().of(ActionAuthor)
        except Exception as exc:  # noqa: BLE001 - nothing checked
            print(f"executor links not reconciled: Egeria could not be reached ({type(exc).__name__}: {exc})")
            return 2
        report = xl.reconcile(expert, declared, author=author, apply=args.apply)
    for st in report.states:
        if st.problem != xl.OK or st.action:
            print(f"  {st.problem:<19} {st.declared.governance_action_type} ({st.declared.document})"
                  + (f": {st.detail}" if st.detail else "") + (f" -> {st.action}" if st.action else ""))
    if args.apply and any(st.action for st in report.states):
        try:
            print(f"activity {xl.record(report)}")
        except Exception as exc:  # noqa: BLE001 - the write happened; say the row did not
            print(f"activity row NOT written: {type(exc).__name__}: {exc}")
    print(report.words())
    if report.refused:
        return 2
    if args.apply:
        # As a heal's post_heal, fail only on what the heal could and did not do: a repair that did not end ok, or a
        # read that could not answer. A type not yet in Egeria (a dependent batch that has not run yet in this pass)
        # and a wrong request type (a person's call) are reported in the words, not failed on.
        left = [s for s in report.states if s.problem == xl.UNREADABLE
                or (s.problem in xl.REPAIRABLE and not s.action.endswith("now ok"))]
        return 1 if left else 0
    return 1 if report.drift else 0


if __name__ == "__main__":
    sys.exit(main())
