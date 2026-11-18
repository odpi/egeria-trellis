"""
Dr.Egeria command-template API routes for Egeria Advisor.

Extracted from `app.py` (TC-5, BACKLOG.md — router-per-domain refactor).
`GET /api/templates/{command_name}/fields` moved in slice 3, fully
self-contained. `GET /api/templates/Column/fields` was deliberately left
behind at the time — it calls `discover_draft_schema_internal()`, reports-
domain business logic that still lived in app.py's not-yet-extracted
reports section — and moved here in slice 6 once `advisor/web/reports.py`
existed to import that helper from.

Endpoints:
  GET /api/templates/Column/fields         → field config for a Report
                                              Column card in the canvas
  GET /api/templates/{command_name}/fields → template field metadata for a
                                              Dr.Egeria command
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from loguru import logger

from advisor.web.reports import discover_draft_schema_internal

router = APIRouter()


@router.get("/api/templates/Column/fields")
async def get_column_fields(draft_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the fields configuration for a Report Column card in the canvas."""
    valid_keys = []
    if draft_id:
        try:
            schema_data = await discover_draft_schema_internal(draft_id)
            valid_keys = [item["attribute_path"] for item in schema_data if "attribute_path" in item]
        except Exception as e:
            logger.warning(f"Failed to auto-populate Column Key valid_values: {e}")

    return {
        "fields": [
            {
                "name": "Name",
                "required": True,
                "description": "Column display name (e.g. Display Name)",
                "valid_values": []
            },
            {
                "name": "Key",
                "required": True,
                "description": "Egeria attribute property key (e.g. display_name)",
                "valid_values": valid_keys
            },
            {
                "name": "Apply formatting",
                "required": False,
                "description": "How to style this column's value (leave blank for plain text)",
                "valid_values": ["", "bulleted-list", "code", "date", "True"]
            },
            {
                "name": "Detail Spec",
                "required": False,
                "description": "Associated detail spec name for nested/drill-down reporting",
                "valid_values": []
            },
            {
                "name": "Output types",
                "required": False,
                "description": "Which output modes include this column",
                "valid_values": ["ALL", "REPORT", "LIST", "TABLE", "MERMAID", "DICT", "FORM", "JSON"],
                "multi_select": True
            }
        ]
    }


@router.get("/api/templates/{command_name}/fields")
async def get_template_fields(request: Request, command_name: str, level: str = "basic") -> Dict[str, Any]:
    """Return template field metadata for a Dr.Egeria command at the given template level.

    Requires login — the valid-values enrichment below performs live Egeria reads
    and must be attributable to the signed-in user, not a shared service account.
    """
    from advisor.auth import require_egeria_user, get_egeria_credentials
    require_egeria_user(request)
    egeria_credentials = get_egeria_credentials(request)
    from urllib.parse import unquote
    from advisor.agents.tools import _templates_root, _normalise
    from advisor.agents.dr_egeria_agent import parse_template

    action = unquote(command_name)
    root   = _templates_root()
    if root is None:
        return {"fields": [], "level": level, "state": "no_templates",
                "message": "No Dr.Egeria templates are available to this advisor."}

    level_dir = root / level
    if not level_dir.is_dir():
        level_dir = root / "basic"

    query_norm = _normalise(action)
    words      = [_normalise(w) for w in action.split() if len(w) > 3]

    best_score = 0
    best_file  = None
    for md_file in sorted(level_dir.rglob("*.md")):
        stem_norm = _normalise(md_file.stem)
        score = 0
        if query_norm == stem_norm:           score = 50
        elif query_norm in stem_norm:         score = 40
        elif stem_norm in query_norm:         score = 35
        elif words:
            hits = sum(1 for w in words if w in stem_norm)
            if hits == len(words):            score = 30
            elif hits > 0:                    score = 20 + hits
        if score > best_score:
            best_score = score
            best_file  = md_file

    if best_file is None or best_score == 0:
        return {"fields": [], "level": level, "state": "not_found",
                "message": f"No {level} Dr.Egeria template matches '{action}'."}

    try:
        template = parse_template(str(best_file))
    except Exception as exc:
        logger.info(f"get_template_fields: could not parse {best_file.name}: {exc}")
        return {"fields": [], "level": level, "state": "read_failed",
                "message": f"The {level} template for '{action}' ({best_file.name}) could not be read: {exc}"}

    # Enrich valid_values for known field patterns with live Egeria data. A field whose live values
    # could not be read, or of which Egeria holds none, says so (valid_values_state/_message) rather
    # than looking like a free-text field (A-P2).
    ctx = None
    for a in template["attributes"]:
        name_low = a["name"].lower()
        if a.get("valid_values"):
            continue
        if "zone" in name_low:
            values_of, kind = "zones", "governance_zones_answer"
        elif "deployed implementation type" in name_low:
            values_of, kind = "technology_types", "technology_types_answer"
        else:
            continue
        if ctx is None:
            from advisor.egeria_context import EgeriaContext
            ctx = EgeriaContext(egeria_credentials=egeria_credentials)
        answer = getattr(ctx, kind)()  # cached per instance, so each list is read once
        a["valid_values"] = answer[values_of]
        a["valid_values_state"] = answer["state"]
        a["valid_values_message"] = answer["message"]

    return {
        "level": level,
        "fields": [
            {
                "name":               a["name"],
                "required":           a["required"],
                "type":               a["type"],
                "description":        a.get("description", ""),
                "valid_values":       a.get("valid_values", []),
                "default_value":      a.get("default_value", ""),
                "alternative_labels": a.get("alternative_labels", []),
                **({"valid_values_state": a["valid_values_state"],
                    "valid_values_message": a["valid_values_message"]}
                   if "valid_values_state" in a else {}),
            }
            for a in template["attributes"]
        ],
        "state": "ok",
        "message": None,
    }
