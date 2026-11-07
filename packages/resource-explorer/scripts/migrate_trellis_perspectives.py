#!/usr/bin/env python3
"""Move RE's two self-created Perspectives onto the content pack's, then retire them.

**Decision (project owner, 2026-11-07):** "So Trellis needs to stop creating Data Owner and Admin and use the
ones from the content pack."

RE created `Perspective::Data Owner` and `Perspective::Admin` itself (createdBy `resourceexplorernpa`,
2026-10-24, in no folder). Egeria's core content pack ships `Perspective::Owner` and
`Perspective::Administration` (folder "Product Perspectives"), which mean the same thing. The other ten RE
perspectives already are content-pack elements. This script does the Egeria half of the change. The repo
half (foundations.md, the CSV and everything generated from it) stops RE authoring the two again.

For each pair it:

  1. reads every element ScopedBy the RE perspective, and every element ScopedBy the content-pack one;
  2. links onto the content-pack perspective each element that is not already linked to it
     (`ClassificationExplorer.add_scope_to_element`), and skips the ones that already are;
  3. re-reads the content-pack perspective and confirms that every element is now present;
  4. only then retires the RE perspective (`ActorManager.delete_perspective`). Deleting it removes its own
     ScopedBy links with it.

**DRY RUN IS THE DEFAULT.** Without `--apply` it reads and reports, and writes nothing to Egeria. `--plan-out`
writes what it found to a JSON file. That file is the only record of the old links once the elements are gone.

**There is no undo, and the writes must run exactly once.** ScopedBy links between Questions and Perspectives
have no reconciler (unlike survey-definition step links), so a link written twice is a permanent duplicate.
That is why step 2 re-reads the target immediately before writing and skips anything it already holds. A
`--apply` that was interrupted can therefore be re-run safely: it re-reads, finds the links it already made,
and makes only the rest. A retired perspective cannot be brought back with its GUID. Re-authoring the old
foundations.md block would create a NEW element with a new GUID and no links. The plan file is what you would
rebuild the links from.

**Order matters.** Run `--apply` only once the repo change is deployed (RE's web server serves from the main
checkout, so that checkout must be fast-forwarded and the server running the new foundations batch). Under
the old code the foundations canary IS `Perspective::Admin`: retiring it would make the bootstrap heal re-run
the old foundations.md within ten minutes and re-create both perspectives, empty.

Usage (from packages/resource-explorer, with RE's .env present):

    uv run --package resource-explorer python scripts/migrate_trellis_perspectives.py [--plan-out PATH]
    uv run --package resource-explorer python scripts/migrate_trellis_perspectives.py --apply --plan-out PATH

Exit codes: 0 done or nothing to do · 1 a check refused (nothing written past that point) · 2 could not read.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

#: (RE's element, the content pack's element). GUIDs verified live 2026-11-07 against
#: http://127.0.0.1:8800/api/perspectives. Checked against the qualifiedName lookup before anything else,
#: because a different GUID means a different platform or a re-created element, and either one invalidates
#: the counts in the plan.
MIGRATIONS = (
    {"source_qn": "Perspective::Data Owner", "source_guid": "7dadd9e5-5a5e-4002-8fff-d7caaef24b6f",
     "target_qn": "Perspective::Owner", "target_guid": "8e7e8e7b-1a40-48d8-b687-6b96303d53c8"},
    {"source_qn": "Perspective::Admin", "source_guid": "aa3865b6-63b0-406c-b58e-4d925a246e16",
     "target_qn": "Perspective::Administration", "target_guid": "cab3343b-8731-4b83-86ff-f988bf2143a5"},
)

_PAGE = 100

#: An explicit body for the ScopedBy link. Without one, pyegeria (through 6.2.23) builds its default body with the
#: properties class "ScopedBy", which Egeria rejects (HTTP 400, "Could not resolve type id 'ScopedBy' as a subtype
#: of RelationshipProperties"; seen live 2026-11-07 on the first --apply, which therefore wrote nothing). Egeria's
#: class is "ScopedByProperties". Passing the body here avoids the default; keep it after pyegeria is fixed.
_SCOPED_BY_BODY = {"class": "NewRelationshipRequestBody", "properties": {"class": "ScopedByProperties"}}


class Refused(Exception):
    """A check failed. Nothing past this point is written."""


def _guid(element: dict) -> str:
    header = element.get("elementHeader") or {}
    return (header.get("guid") or element.get("guid") or
            ((element.get("relatedElement") or {}).get("elementHeader") or {}).get("guid") or "")


def _name(element: dict) -> str:
    props = element.get("properties") or {}
    related = (element.get("relatedElement") or {}).get("properties") or {}
    return (props.get("displayName") or element.get("displayName") or related.get("displayName")
            or props.get("qualifiedName") or element.get("qualifiedName") or "?")


def scoped_elements(explorer, scope_guid: str) -> dict[str, str]:
    """{guid: display name} for every element ScopedBy `scope_guid`.

    Paged until an EMPTY page. A short page is not the last page on this platform (the paging contract in
    docs, and the reason a single big page is not trusted here)."""
    out: dict[str, str] = {}
    start = 0
    while True:
        # graphQueryDepth 0: only the scoped elements, not each one's own graph. With pyegeria's default body
        # the 38-link Admin read took over 30 s and timed out, and the 16-link one took 13 s (measured
        # 2026-11-07). With this body each read takes about a second.
        body = {"class": "ResultsRequestBody", "graphQueryDepth": 0, "relationshipsPageSize": 0,
                "startFrom": start, "pageSize": _PAGE}
        page = explorer.get_scoped_elements(scope_guid, body=body, output_format="JSON")
        if isinstance(page, str) or not page:
            break  # pyegeria answers "No elements found" as a string
        if not isinstance(page, list):
            raise Refused(f"unexpected answer reading the scope of {scope_guid}: {type(page).__name__}")
        new = 0
        for element in page:
            g = _guid(element)
            if not g:
                raise Refused(f"an element ScopedBy {scope_guid} carries no GUID: {element!r:.160}")
            if g not in out:
                new += 1
            out[g] = _name(element)
        if new == 0:
            break  # the server repeated a page: stop rather than loop
        start += len(page)
    return out


def build_plan(expert, explorer) -> list[dict]:
    from resource_explorer.egeria_lookup import guid_by_qualified_name

    plan = []
    for m in MIGRATIONS:
        source = guid_by_qualified_name(expert, m["source_qn"])
        target = guid_by_qualified_name(expert, m["target_qn"])
        if target is None:
            raise Refused(f"{m['target_qn']} does not resolve: the content-pack element is missing, so there "
                          "is nothing to move the links onto")
        if target != m["target_guid"]:
            raise Refused(f"{m['target_qn']} resolves to {target}, not the verified {m['target_guid']}")
        entry = {**m, "source_present": source is not None}
        if source is None:
            entry.update(to_move=[], already_on_target=[], source_links=0,
                         target_links_before=len(scoped_elements(explorer, target)))
            entry["target_links_after"] = entry["target_links_before"]
            plan.append(entry)
            continue
        if source != m["source_guid"]:
            raise Refused(f"{m['source_qn']} resolves to {source}, not the verified {m['source_guid']}")
        src = scoped_elements(explorer, source)
        tgt = scoped_elements(explorer, target)
        entry.update(
            source_links=len(src),
            target_links_before=len(tgt),
            to_move=sorted(({"guid": g, "name": n} for g, n in src.items() if g not in tgt),
                           key=lambda e: e["name"]),
            already_on_target=sorted(({"guid": g, "name": n} for g, n in src.items() if g in tgt),
                                     key=lambda e: e["name"]),
            target_links_after=len(set(src) | set(tgt)),
        )
        plan.append(entry)
    return plan


def print_plan(plan: list[dict]) -> None:
    for e in plan:
        print(f"{e['source_qn']}  ->  {e['target_qn']}")
        if not e["source_present"]:
            print(f"  {e['source_qn']} does not resolve: already retired. Nothing to do.")
            print(f"  {e['target_qn']}: {e['target_links_before']} link(s)\n")
            continue
        print(f"  source {e['source_guid']}: {e['source_links']} ScopedBy link(s)")
        print(f"  target {e['target_guid']}: {e['target_links_before']} ScopedBy link(s) now")
        print(f"  to move:            {len(e['to_move'])}")
        for x in e["to_move"]:
            print(f"      + {x['name']}  [{x['guid']}]")
        print(f"  already on target:  {len(e['already_on_target'])} (skipped, never written twice)")
        for x in e["already_on_target"]:
            print(f"      = {x['name']}  [{x['guid']}]")
        print(f"  target after:       {e['target_links_after']} "
              f"(= {e['target_links_before']} + {len(e['to_move'])})")
        print(f"  then retire {e['source_qn']} [{e['source_guid']}]\n")


def apply_plan(plan: list[dict], explorer, actor) -> None:
    for e in plan:
        if not e["source_present"]:
            continue
        target = e["target_guid"]
        # Re-read immediately before writing: what is on the target NOW, not what the plan saw.
        held = set(scoped_elements(explorer, target))
        for x in e["to_move"]:
            if x["guid"] in held:
                print(f"  = already linked, skipped: {x['name']}")
                continue
            explorer.add_scope_to_element(target, x["guid"], body=_SCOPED_BY_BODY)
            print(f"  + linked: {x['name']}")
        after = set(scoped_elements(explorer, target))
        wanted = {x["guid"] for x in e["to_move"]} | {x["guid"] for x in e["already_on_target"]}
        missing = wanted - after
        if missing:
            raise Refused(f"{len(missing)} element(s) are still not ScopedBy {e['target_qn']} after linking; "
                          f"{e['source_qn']} NOT retired: {sorted(missing)}")
        print(f"  {e['target_qn']}: {len(after)} link(s), every link of {e['source_qn']} present")
        actor.delete_perspective(e["source_guid"])
        print(f"  retired {e['source_qn']} [{e['source_guid']}]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="Write to Egeria. Without it, read and report only.")
    parser.add_argument("--plan-out", type=Path, default=None,
                        help="Write the plan (every link, by GUID and name) to this JSON file")
    args = parser.parse_args()

    from pyegeria.omvs.actor_manager import ActorManager

    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

    with acting_as(Daemon(DaemonReason.BOOTSTRAP)):
        reader = SurveyDefinitionReader()
        try:
            expert = reader._connect_metadata_expert()
            explorer = reader._connect_classification_explorer()
            plan = build_plan(expert, explorer)
        except Refused as exc:
            print(f"REFUSED: {exc}")
            return 1
        except Exception as exc:  # could not ask is never "nothing there"
            print(f"Could not read Egeria ({type(exc).__name__}: {exc}). Nothing written.")
            return 2

        print(("APPLY" if args.apply else "DRY RUN — nothing is written") +
              f" · {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
        print_plan(plan)
        if args.plan_out:
            args.plan_out.write_text(json.dumps(
                {"generated_at": datetime.now(timezone.utc).isoformat(), "apply": args.apply, "plan": plan},
                indent=2, ensure_ascii=False) + "\n")
            print(f"plan written to {args.plan_out}")
        if not args.apply:
            return 0
        try:
            apply_plan(plan, explorer, reader._clients(shared=False).of(ActorManager))
        except Refused as exc:
            print(f"REFUSED: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
