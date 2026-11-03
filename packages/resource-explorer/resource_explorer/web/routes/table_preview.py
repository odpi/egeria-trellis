"""Table preview routes: a signed-in person's own credential, and the first n rows of one table.

Design: docs/design-notes/REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19.md. See `resource_explorer/table_preview.py` for
everything that is not HTTP.

* ``GET    /api/databases/{slug}/preview/credential``: is one held for THIS person, and the database user it names
  (never the password);
* ``PUT    /api/databases/{slug}/preview/credential``: test-before-hold, then keep it for this session only;
* ``DELETE /api/databases/{slug}/preview/credential``: forget it ("use another credential");
* ``POST   /api/databases/{slug}/preview``: run one preview (or reveal), answer with rows that are shown and never
  stored.

Any signed-in user may preview (owner, 2026-10-19: "if they know the database credentials then they have access
already"); a signed-out request is a 401. The password lives in a request body only (never a URL), reaches the holder
and nothing else, and the app-wide 422 handler withholds password-like fields.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter()


class PreviewCredentialBody(BaseModel):
    # Bounded: the user lands in the throttle's keys and (when plausible) the audit row, the password in a connect.
    # A longer value is a 422, and the app-wide 422 handler never echoes the value sent.
    user: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=1024)


class PreviewBody(BaseModel):
    schema_name: str = Field(alias="schema")
    table: str
    limit: int = 20
    #: Columns the person revealed for THIS preview (each one re-runs the query and is recorded).
    reveal: list[str] = []

    model_config = {"populate_by_name": True}


def _signed_in(request: Request) -> tuple[str, str, float | None]:
    """(person, session key, session expiry). Looked up through the module at call time so tests can swap
    `get_current_user`. The session key names THIS sign-in (the token's `jti`, else a digest of the bearer token), so
    a held credential belongs to one sign-in, not to the user id."""
    from resource_explorer import auth
    from resource_explorer import preview_credential_holder as holder

    claims = auth.get_current_user(request)
    person = ((claims or {}).get("user_id") or (claims or {}).get("sub") or (claims or {}).get("username") or "")
    if not person:
        raise HTTPException(status_code=401, detail="Sign in to preview a table: a preview is recorded with your name.")
    header = request.headers.get("Authorization", "")
    token = header[len("Bearer "):] if header.startswith("Bearer ") else ""
    exp = (claims or {}).get("exp")
    return person, holder.session_key(claims, token), float(exp) if isinstance(exp, (int, float)) else None


def _database(slug: str):
    """The registry and the database for the URL's slug. Every route resolves it and keys on `db.slug` (the stored,
    normalised slug), never on the raw URL text."""
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    # The stored credential is never used here, so an unreadable one does not matter.
    db = registry.get_database(slug, allow_unreadable=True)
    if not db:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    return registry, db


@router.get("/{slug}/preview/credential")
async def get_preview_credential(slug: str, request: Request) -> dict:
    from resource_explorer import preview_credential_holder as holder
    from resource_explorer import table_preview as tp

    person, session, _ = _signed_in(request)
    _, db = _database(slug)
    user = holder.held_user(person, session, db.slug)
    return {"held": user is not None, "user": user, "engine_supported": tp.engine_supported(db.db_type),
            "db_type": db.db_type, "unsupported": "" if tp.engine_supported(db.db_type)
            else tp.unsupported_sentence(db.db_type)}


@router.put("/{slug}/preview/credential")
async def hold_preview_credential(slug: str, body: PreviewCredentialBody, request: Request) -> dict:
    """Test the credential against the database, THEN hold it for this sign-in. A refusal holds nothing (the
    sentence is the reason, password scrubbed; the same one for a wrong password and an unknown user). Every attempt
    is an RE activity row, and five refusals in ten minutes lock the person out of this database for ten minutes
    (429). Nothing is written to the registry's credentials, the secrets store or a log."""
    from resource_explorer import table_preview as tp

    person, session, not_after = _signed_in(request)
    registry, db = _database(slug)
    if not tp.engine_supported(db.db_type):
        raise HTTPException(status_code=409, detail=tp.unsupported_sentence(db.db_type))
    try:
        user = await asyncio.to_thread(
            tp.hold_credential, registry, db, person, session, body.user.strip(), body.password,
            not_after=not_after)
    except tp.PreviewRefusal as exc:
        raise HTTPException(status_code=exc.status, detail=exc.sentence) from None
    except tp.NotRecorded as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {"held": True, "user": user}


@router.delete("/{slug}/preview/credential")
async def forget_preview_credential(slug: str, request: Request) -> dict:
    from resource_explorer import preview_credential_holder as holder

    person, session, _ = _signed_in(request)
    _, db = _database(slug)                      # an unknown database is a 404, not a quiet success
    holder.forget(person, session, db.slug)
    return {"held": False, "user": None}


@router.post("/{slug}/preview")
async def preview_table(slug: str, body: PreviewBody, request: Request) -> dict:
    """One preview, or (with `reveal`) one reveal of columns. The answer carries rows for the browser only: nothing
    here stores, logs or traces a value. The RE audit row is written before the rows are returned."""
    from resource_explorer import table_preview as tp

    person, session, _ = _signed_in(request)
    registry, db = _database(slug)
    req = tp.PreviewRequest(slug=db.slug, schema=body.schema_name, table=body.table, limit=body.limit,
                            reveal=tuple(body.reveal))
    try:
        return await asyncio.to_thread(tp.run_preview, registry, db, person, session, req)
    except tp.PreviewRefusal as exc:
        raise HTTPException(status_code=exc.status, detail=exc.sentence) from None
    except tp.NotRecorded as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
