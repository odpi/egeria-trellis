"""Quality-check review for a database (Curate, Structure tab).

GET  /api/quality-checks/settings           the value profile's and quality checks' settings in force, with what each
                                            means (the Admin page's read-only pane)
GET  /api/quality-checks/{slug}/proposals   groups by table with counts; `?schema=&table=` adds one group's rows
POST /api/quality-checks/{slug}/verdict     one accept or reject of one check, as the signed-in person
GET  /api/quality-checks/{slug}/rules       each check's DataQualityRule state line (Quality Q5)
POST /api/quality-checks/{slug}/publish-rules
                                            Publish: accepted checks to Egeria DataQualityRules, rejected ones that
                                            were published to DEPRECATED, as the signed-in person; read back
POST /api/quality-checks/{slug}/table-comparison
                                            opt one table into (or out of) Table Comparison, the baseline-match
                                            checks, as the signed-in person; the next propose run reads it

The write resolves the person from the session first (401 when nobody is signed in) and never takes the author, the
parameters or the proposal id from the body. It writes RE's own verdict row only; Publish is what reaches Egeria
(`publish-rules`, DESIGN-QUALITY-Q5-DATA-QUALITY-RULE.md).
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from resource_explorer import quality_review as review
from resource_explorer.auth import get_current_user
from resource_explorer.registry import ProjectRegistry

router = APIRouter()


def _require_author(request: Request, action: str) -> str:
    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail=f"Sign in to {action}: a verdict needs a person to be recorded as.")
    return author


def _registry_for(slug: str) -> ProjectRegistry:
    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    return registry


class VerdictBody(BaseModel):
    check_key: str
    verdict: Literal["accepted", "rejected"]
    note: str = ""


class TableComparisonBody(BaseModel):
    schema_name: str
    table_name: str
    watch: bool
    note: str = ""


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except review.ReviewError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


def describe_settings() -> dict:
    """The value profile's and quality checks' settings in force, each with its value, unit, environment name, whether
    it was set or is the default, and what it means, in a person's words (architect, 2026-10-24: a setting a person
    must be able to read on the Admin page, not only as an env name)."""
    from resource_explorer.config import get_config

    config = get_config()
    rows = []
    for section, name, setting, unit, means in (
        (config.value_profile, "live_write_table_share", "VALUE_PROFILE_LIVE_WRITE_TABLE_SHARE", "share of tables",
         ("When a repeat value-profile publish finds counts moved on more than this share of the tables it measured, "
          "the run card adds “this database is written live · see change rates”.")),
        (config.value_profile, "table_budget_seconds", "VALUE_PROFILE_TABLE_BUDGET_SECONDS", "seconds",
         ("The most time one table's value profile may take, across its chunks. Past it the table is "
          "“profile incomplete”, never partly counted.")),
        (config.value_profile, "statement_timeout_seconds", "VALUE_PROFILE_STATEMENT_TIMEOUT_SECONDS", "seconds",
         ("The time limit on each quality check's statement. A check that runs past it is an error, with "
          "Postgres's words.")),
        (config.quality_checks, "row_ct_pct_default_threshold", "QUALITY_CHECKS_ROW_CT_PCT_DEFAULT_THRESHOLD",
         "percent",
         ("The threshold a proposed Row_Ct_Pct check starts with: the row count within this percent of the "
          "baseline. A person can change it on any one check before accepting it.")),
    ):
        rows.append({"name": name, "setting": setting, "value": getattr(section, name), "unit": unit,
                     "source": "set in the environment" if name in section.model_fields_set else "default",
                     "means": means})
    return {"settings": rows}


@router.get("/settings")
def read_settings(request: Request) -> dict:
    """Read-only, Admin only like the Admin page's other routes."""
    from resource_explorer.web import admin_auth

    admin_auth.require_admin(request)
    return describe_settings()


@router.get("/{slug}/proposals")
async def read_proposals(slug: str, test_type: str = "", dimension: str = "", state: str = "",
                         schema: str = "", table: str = "") -> dict:
    registry = _registry_for(slug)
    return await _run(review.build_review, registry, slug, test_type=test_type, dimension=dimension, state=state,
                      schema=schema, table=table)


@router.post("/{slug}/verdict")
async def post_verdict(slug: str, body: VerdictBody, request: Request) -> dict:
    author = _require_author(request, "accept or reject a quality check")
    registry = _registry_for(slug)
    from resource_explorer.web.routes.curate import authorize_resource_write

    await authorize_resource_write(registry, "database", slug)
    return await _run(review.record_verdict, registry, slug, body.check_key, body.verdict, decided_by=author,
                      note=body.note)


@router.post("/{slug}/table-comparison")
async def post_table_comparison(slug: str, body: TableComparisonBody, request: Request) -> dict:
    author = _require_author(request, "opt a table into Table Comparison")
    registry = _registry_for(slug)
    from resource_explorer.web.routes.curate import authorize_resource_write

    await authorize_resource_write(registry, "database", slug)
    return await _run(review.set_table_comparison, registry, slug, body.schema_name, body.table_name, body.watch,
                      decided_by=author, note=body.note)


@router.get("/{slug}/rules")
async def read_rules(slug: str) -> dict:
    from resource_explorer.surveyors.database.quality.egeria_rules import rule_states

    registry = _registry_for(slug)
    return await _run(rule_states, registry, slug)


def _publish_rules(registry, slug: str, author: str) -> dict:
    import uuid
    from datetime import UTC, datetime

    from resource_explorer.registry import ActivityEntry
    from resource_explorer.surveyors.database.quality.egeria_rules import (
        egeria_gateway,
        publish_database_rules,
    )

    db_entity = registry.get_database(slug)
    result = publish_database_rules(registry, db_entity, egeria_gateway(), recorded_by=author)
    result["read_at"] = datetime.now(UTC).isoformat()
    registry.write_activity(ActivityEntry(
        id=str(uuid.uuid4()), ts=datetime.now(UTC).isoformat(),
        operation="quality_rules_published", intent="curate", entity_type="database", entity_slug=slug,
        summary=f"{result.get('state_words') or result['line']} · {result['line']} (by {author})",
        annotations=[{"counts": result.get("counts", {}), "state": result.get("state", "")}],
    ))
    return result


@router.post("/{slug}/publish-rules")
async def publish_rules(slug: str, request: Request) -> dict:
    """Publish the database's accepted checks as DataQualityRules, as the signed-in person (their own token)."""
    author = _require_author(request, "publish quality rules")
    registry = _registry_for(slug)
    from resource_explorer.web.routes.curate import authorize_resource_write

    await authorize_resource_write(registry, "database", slug)
    return await _run(_publish_rules, registry, slug, author)
