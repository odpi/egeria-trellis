#!/usr/bin/env python3
"""Do the 12 Perspectives from foundations.md exist in live Egeria?

`docs/open-stack-checklist.md` §7 gives three post-redeploy checks — questions
resolve, no duplicate/stale links, both surveys load — and **none of them looks
at Perspectives**. They are authored by `docs/dr-egeria/foundations/foundations.md`
in the same batch as the glossary and funnel stages, so a heal that reports
success can leave them missing exactly the way 8 of 49 questions went missing on
2026-08-31.

**Compared against the generative source, not a snapshot.**
`docs/egeria-redeploy-baseline-2026-08-26.json` records 12 perspectives and was
verified identical to foundations.md on 2026-08-31 — so the snapshot adds
nothing the repo does not already hold, and a generator says what SHOULD be
there where a snapshot only says what once was.

    uv run python scripts/verify_perspectives.py

Exit codes: 0 all present · 1 some missing · 2 could not determine.

**UNREACHABLE IS NOT MISSING, and that distinction is the point of this file.**
Written while the platform was down for a redeploy, so it has never been
exercised against a live server and its first real run is the post-redeploy
verification itself. A check that cannot tell "the platform is not answering"
from "the perspectives are gone" would report 0/12 at precisely the moment that
reads as catastrophe — so a connection failure exits 2 and says so, and only a
definite not-found counts as missing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_FOUNDATIONS = (Path(__file__).resolve().parents[1]
                / "docs" / "dr-egeria" / "foundations" / "foundations.md")

#: `## Create Perspective` followed by `### Display Name` and its value. Reading
#: the authored document rather than a list in this file: a hard-coded list here
#: would be a second copy to keep in step, which is the duplication this
#: codebase keeps removing.
_PERSPECTIVE_RE = re.compile(
    r"##\s*Create Perspective\s*\n+###\s*Display Name\s*\n+([^\n]+)")


def expected_perspectives() -> list[str]:
    if not _FOUNDATIONS.is_file():
        raise FileNotFoundError(f"foundations document not found: {_FOUNDATIONS}")
    names = [m.strip() for m in _PERSPECTIVE_RE.findall(_FOUNDATIONS.read_text())]
    return sorted(n for n in names if n)


def _main() -> int:
    expected = expected_perspectives()
    if not expected:
        print("No perspectives found in foundations.md — the parse, not the platform.")
        print(f"  looked in {_FOUNDATIONS}")
        return 2
    print(f"{len(expected)} perspective(s) declared in foundations.md\n")

    try:
        from resource_explorer.surveyors.survey_definition_reader import (
            SurveyDefinitionReader,
        )
        client = SurveyDefinitionReader()._connect_classification_explorer()
    except Exception as exc:
        print(f"Could not reach Egeria ({type(exc).__name__}). "
              "NOT reporting the perspectives as missing — this says nothing "
              "about whether they exist.")
        return 2

    missing: list[str] = []
    undetermined: list[str] = []
    for name in expected:
        # qualifiedName ONLY. `Perspective::<name>` is what foundations.md
        # authors for all 12, and it is unique by construction.
        #
        # The first version also fell back to displayName, and on its first
        # live run (2026-08-31, straight after the redeploy) that fallback
        # produced three FALSE OKs: `Community`, `Privacy` and `Steward`
        # resolved to elements of some other type that merely share a name,
        # while no perspective existed at all. It reported "9 of 12 missing"
        # when the answer was 12 — under-reporting an absence is the one
        # direction this check must never fail in, since it is read as
        # permission to stop looking.
        #
        # displayName is not unique across types, so it can only ever add false
        # positives here. Removed rather than narrowed.
        try:
            got = client.get_guid_for_name(f"Perspective::{name}",
                                           property_name=["qualifiedName"])
        except Exception as exc:
            # An exception is "could not ask", never "not there" — `Security`
            # raised CLIENT_ERROR_400 on the displayName probe, and the first
            # version turned that into MISSING because only one of its two
            # probes had raised.
            undetermined.append(name)
            print(f"  ?        {name}  ({type(exc).__name__} — not counted as missing)")
            continue
        text = str(got or "")
        if text and "No elements" not in text and "not found" not in text.lower():
            print(f"  ok       {name}")
        else:
            missing.append(name)
            print(f"  MISSING  {name}")

    print()
    if undetermined:
        print(f"{len(undetermined)} could not be determined and are NOT counted as missing.")
    if missing:
        print(f"{len(missing)} of {len(expected)} perspective(s) are absent from Egeria.")
        print("Re-run the foundations document, then "
              "scripts/reconcile_survey_definition_links.py.")
        return 1
    if undetermined:
        return 2
    print(f"All {len(expected)} perspectives resolve.")
    return 0


def main() -> int:
    """Run as RE's daemon: SurveyDefinitionReader resolves its principal from the calling context, and
    a CLI has no signed-in person (NoCallerIdentity otherwise)."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    with acting_as(Daemon(DaemonReason.BOOTSTRAP)):
        return _main()


if __name__ == "__main__":
    sys.exit(main())
