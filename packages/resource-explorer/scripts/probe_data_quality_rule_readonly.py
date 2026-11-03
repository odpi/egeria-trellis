"""Read-only probe for Quality Q5: how the running Egeria platform holds a DataQualityRule.

DESIGN-QUALITY-Q5-DATA-QUALITY-RULE.md §2 records one run of this script. It makes NO create, update, delete, link
or classify call: only the bearer-token request, type-definition GETs, and find/get-by-name queries (which Egeria
serves as POSTs that read). Run as RE's daemon identity from RE's `.env` (`EGERIA_USER_ID`/`EGERIA_USER_PASSWORD`,
`EGERIA_PLATFORM_URL`, `EGERIA_VIEW_SERVER` or qs-view-server). Prints one JSON object per call: what was asked and
what came back, trimmed.

    python scripts/probe_data_quality_rule_readonly.py [path/to/.env]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pyegeria.omvs.governance_officer import GovernanceOfficer
from pyegeria.omvs.valid_metadata import ValidMetadataManager

TYPES = ("DataQualityRule", "GovernanceRule", "GovernanceControl", "GovernanceDefinition")
RELATIONSHIPS_OF = ("RelationalColumn", "RelationalTable", "Database", "QualityAnnotation", "DataQualityRule")
VALID_VALUE_PROPERTIES = ("qualityDimension", "checkType", "metric", "severity", "comparisonOperator")


def load_env(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            if value and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")


def show(call: str, answer) -> None:
    text = answer if isinstance(answer, str) else json.dumps(answer, default=str)
    print(json.dumps({"call": call, "answer": text[:4000]}))


def typedef_summary(td) -> dict | str:
    if not isinstance(td, dict):
        return td
    td = td.get("typeDef", td)
    return {
        "name": td.get("name"), "guid": td.get("guid"), "version": td.get("versionName"),
        "superType": (td.get("superType") or {}).get("name"),
        "status": td.get("status"),
        "attributes": [a.get("attributeName") for a in td.get("attributeDefinitions") or []],
        "end1": ((td.get("endDef1") or {}).get("entityType") or {}).get("name"),
        "end2": ((td.get("endDef2") or {}).get("entityType") or {}).get("name"),
    }


def typedef_names(answer) -> list | str:
    """The names in a typeDef list answer, which comes back as {"typeDefs": [...]} or as a bare list."""
    if isinstance(answer, dict):
        answer = answer.get("typeDefs", [])
    if not isinstance(answer, list):
        return answer
    return sorted({t.get("name") for t in answer if isinstance(t, dict)})


def main() -> int:
    load_env(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / ".env")
    view_server = os.environ.get("EGERIA_VIEW_SERVER", "qs-view-server")
    platform = os.environ["EGERIA_PLATFORM_URL"]
    user, password = os.environ["EGERIA_USER_ID"], os.environ["EGERIA_USER_PASSWORD"]
    show("identity", {"user": user, "platform": platform, "view_server": view_server})

    vm = ValidMetadataManager(view_server, platform, user_id=user, user_pwd=password)
    vm.create_egeria_bearer_token(user, password)
    for name in TYPES:
        try:
            show(f"get_typedef_by_name({name})", typedef_summary(vm.get_typedef_by_name(name)))
        except Exception as exc:  # noqa: BLE001 - a probe reports, it does not stop
            show(f"get_typedef_by_name({name})", f"ERROR {type(exc).__name__}: {exc}")
    try:
        show("get_sub_types(GovernanceRule)", typedef_names(vm.get_sub_types("GovernanceRule")))
    except Exception as exc:  # noqa: BLE001
        show("get_sub_types(GovernanceRule)", f"ERROR {type(exc).__name__}: {exc}")
    for name in RELATIONSHIPS_OF:
        try:
            names = typedef_names(vm.get_valid_relationship_types(name))
            show(f"get_valid_relationship_types({name}) [Governed*/Annotation* only]",
                 [n for n in names if "Govern" in n or "Annotation" in n] if isinstance(names, list) else names)
        except Exception as exc:  # noqa: BLE001
            show(f"get_valid_relationship_types({name})", f"ERROR {type(exc).__name__}: {exc}")
    try:
        show("get_typedef_by_name(GovernedBy)", typedef_summary(vm.get_typedef_by_name("GovernedBy")))
    except Exception as exc:  # noqa: BLE001
        show("get_typedef_by_name(GovernedBy)", f"ERROR {type(exc).__name__}: {exc}")
    for prop in VALID_VALUE_PROPERTIES:
        try:
            vals = vm.get_valid_metadata_values(prop, "DataQualityRule")
            show(f"get_valid_metadata_values({prop}, DataQualityRule)",
                 [v.get("preferredValue") for v in vals if isinstance(v, dict)] if isinstance(vals, list) else vals)
        except Exception as exc:  # noqa: BLE001
            show(f"get_valid_metadata_values({prop}, DataQualityRule)", f"ERROR {type(exc).__name__}: {exc}")

    go = GovernanceOfficer(view_server, platform, user_id=user, user_pwd=password)
    go.create_egeria_bearer_token(user, password)
    body = {"class": "SearchStringRequestBody", "searchString": "*", "startsWith": True,
            "metadataElementTypeName": "DataQualityRule", "startFrom": 0, "pageSize": 50}
    try:
        found = go.find_governance_definitions(body=body)
        if isinstance(found, list):
            show("find_governance_definitions(type=DataQualityRule)", {
                "count": len(found),
                "elements": [{"guid": (e.get("elementHeader") or {}).get("guid"),
                              "type": ((e.get("elementHeader") or {}).get("type") or {}).get("typeName"),
                              "qualifiedName": (e.get("properties") or {}).get("qualifiedName")}
                             for e in found[:20] if isinstance(e, dict)]})
        else:
            show("find_governance_definitions(type=DataQualityRule)", found)
    except Exception as exc:  # noqa: BLE001
        show("find_governance_definitions(type=DataQualityRule)", f"ERROR {type(exc).__name__}: {exc}")
    # Control: the same find with no type filter, so "No elements found" above is an empty type, not a dead query.
    try:
        found = go.find_governance_definitions(body={**body, "metadataElementTypeName": None})
        types: dict[str, int] = {}
        for e in found if isinstance(found, list) else []:
            name = ((e.get("elementHeader") or {}).get("type") or {}).get("typeName", "?")
            types[name] = types.get(name, 0) + 1
        show("find_governance_definitions('*', no type filter) [control, first page]",
             {"count": len(found), "byType": types} if isinstance(found, list) else found)
    except Exception as exc:  # noqa: BLE001
        show("find_governance_definitions('*', no type filter)", f"ERROR {type(exc).__name__}: {exc}")
    try:
        found = go.find_governance_definitions("::Quality::", starts_with=False)
        show("find_governance_definitions('::Quality::', any type)",
             {"count": len(found)} if isinstance(found, list) else found)
    except Exception as exc:  # noqa: BLE001
        show("find_governance_definitions('::Quality::')", f"ERROR {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
