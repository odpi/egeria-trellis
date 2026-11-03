"""Compile a context for a question — the packer's HTTP surface.

NOT to be confused with routes/context.py, which is the Resource context API
(human-provided metadata and RFA generation, backing the Enrichment intent).
Different concern, and the similar names are a hazard: this file exists under
this name because `context.py` was already taken.

Returns the manifest and the derivation alongside the text, deliberately. A
caller that only gets text cannot say why a section is there, what was dropped,
or what is missing — and those are the three things a person actually asks.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class CompileRequest(BaseModel):
    resource_slug: str
    question: str
    #: 'repo' | 'database' | 'filesystem'. Defaults to "repo" for backward
    #: compatibility with callers that don't pass it (same default as
    #: `entity_type` on routes/analyses.py's `answer_question` and
    #: routes/context.py's `get_context`). Trusted as given, same as
    #: `answer_question()` already trusts its own `entity_type` query param —
    #: this route's registry.get() 404 check does not (and does not need to)
    #: verify the slug actually belongs to this type.
    entity_type: str = "repo"
    purposes: list[str] = []
    perspectives: list[str] = []
    #: Characters, not tokens. The caller owns the conversion, because only it
    #: knows which model the context is for. Omitted, it is the tier's
    #: compile_evidence_budget_chars -- the budget the Ask path compiles with,
    #: so a preview names the evidence the answer will get. (It was a fixed
    #: 8000 here while Ask used 6000, and the two could list different sections.)
    budget: int | None = None
    target_model: str = ""


class CompileResponse(BaseModel):
    text: str
    manifest: dict
    derivation: list[dict]
    #: Stable id of this compile (also manifest["compile_id"]); send it back
    #: with feedback so the rating attaches to the context, not just the answer.
    compile_id: str = ""


@router.post("/compile", response_model=CompileResponse)
async def compile_endpoint(request: CompileRequest) -> CompileResponse:
    from resource_explorer.config import get_llm_tier_config
    from resource_explorer.context_compile import compile_context
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    # Dispatch the existence check through the SAME per-type adapter used
    # everywhere else (survey_definition_executor.get_adapter) rather than
    # always calling the repo-only `registry.get()` -- which is exactly what
    # `_get_project_entity` (repo's own `get_entity`) already is, so this is
    # unchanged for "repo" and newly correct for "database"/"filesystem",
    # which `registry.get()` can never find (it only ever returns a
    # `Project`). This trusts the caller's stated `entity_type` to pick which
    # table to look in -- it does not verify the type against the registry,
    # which is deliberately out of scope (see answer_question() in
    # analyses.py, which trusts its own `entity_type` the same way).
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        get_adapter,
    )

    try:
        entity = get_adapter(request.entity_type).get_entity(registry, request.resource_slug)
    except SurveyDefinitionExecutorError:
        entity = registry.get(request.resource_slug)
    if entity is None:
        raise HTTPException(
            status_code=404, detail=f"Resource '{request.resource_slug}' not found"
        )
    try:
        compiled = compile_context(
            registry, request.resource_slug, request.question,
            resource_type=request.entity_type,
            purposes=request.purposes, perspectives=request.perspectives,
            budget=request.budget if request.budget is not None else get_llm_tier_config().compile_evidence_budget_chars,
            target_model=request.target_model,
        )
    except Exception as exc:
        # A compile that cannot satisfy its own spec is a 422, not a 500: the
        # request was well-formed and the answer is "not within this budget".
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CompileResponse(
        text=compiled.text, manifest=compiled.manifest, derivation=compiled.derivation,
        compile_id=compiled.compile_id,
    )
