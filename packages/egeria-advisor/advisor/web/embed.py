"""EA embedded in Trellis Studio (E1, BRIEF-TRELLIS-STUDIO-V1-2026-11-14; owner decisions A and B).

Embed mode is the same page at `/?embed=studio`: compact, and with no password form. The session arrives only by
postMessage: EA posts {type:'ea.ready'} to its parent; Studio answers {type:'trellis_auth', jwt} to EA's exact
origin; EA accepts it only from an origin in ADVISOR_EMBED_ORIGINS and keeps it as its own session JWT. EA
verifies it with the same check it applies to its own tokens (decision A: a Studio JWT, signed with the box's
shared TRELLIS_JWT_SECRET, is valid here), through `GET /api/embed/whoami`, which also refuses a service account.

Routes:
  GET  /api/embed/config        public: the allowed embed origins (not secret)
  GET  /api/embed/whoami        the signed-in user, or 401 / 403 (service account)
  POST /api/studio/open-draft   "Open in Studio" (decision B): EA's elicited spec saved as a pyegeria.authoring
                                SpecDraft, as the user, in their private draft zone; answers the draft reference
  POST /api/studio/draft-context  "Ask EA about this draft": the user's Studio draft, read as them, as text for the
                                conversation
Both Studio routes run as the signed-in user's own Egeria token; with no token they refuse ("Sign in ...") and
never fall back to EA's service account.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter()


def embed_origins() -> List[str]:
    """ADVISOR_EMBED_ORIGINS as exact origins (scheme://host[:port]); anything else is dropped with a warning."""
    from urllib.parse import urlsplit
    from advisor.config import settings
    out: List[str] = []
    for raw in (settings.advisor_embed_origins or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = urlsplit(raw)
        origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""
        if not origin or parts.path not in ("", "/") or parts.query or parts.fragment:
            logger.warning(f"embed: ADVISOR_EMBED_ORIGINS entry {raw!r} is not an origin (scheme://host:port); ignored")
            continue
        if origin not in out:
            out.append(origin)
    return out


def frame_ancestors_header() -> str:
    return "frame-ancestors " + " ".join(["'self'", *embed_origins()])


@router.get("/api/embed/config")
async def embed_config() -> Dict[str, Any]:
    return {"embed_origins": embed_origins()}


@router.get("/api/embed/whoami")
async def embed_whoami(request: Request) -> Dict[str, Any]:
    from advisor.auth import get_current_user, refuse_service_account
    user = get_current_user(request)
    if user is None or user.get("anonymous"):
        raise HTTPException(status_code=401, detail="Sign in to Studio to use EA here.",
                            headers={"WWW-Authenticate": "Bearer"})
    user_id = user.get("user_id") or user.get("sub", "")
    refuse_service_account(user_id)
    return {"user_id": user_id, "display_name": user.get("display_name") or user_id, "exp": user.get("exp")}


def _user_client(request: Request):
    """A pyegeria client signed in as the request's user, from their own bearer token. No token: 401, before any
    client is built (never EA's service account)."""
    from advisor.auth import apply_token, get_egeria_credentials, refuse_service_account
    from advisor.egeria_signin import SIGN_IN_TO_READ_EGERIA
    from advisor.mcp_config import get_pyegeria_platform_config
    creds = get_egeria_credentials(request)
    if not creds or not creds.get("token") or not creds.get("user_id"):
        raise HTTPException(status_code=401, detail=SIGN_IN_TO_READ_EGERIA, headers={"WWW-Authenticate": "Bearer"})
    refuse_service_account(creds["user_id"])
    conn = get_pyegeria_platform_config()
    from pyegeria.egeria_tech_client import EgeriaTech
    client = EgeriaTech(view_server=conn["view_server"], platform_url=conn["platform_url"], user_id=creds["user_id"])
    apply_token(client, creds["token"])
    return client


def _authoring_error(exc: BaseException, doing: str) -> HTTPException:
    from pyegeria.authoring import AuthoringRefused
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, AuthoringRefused):
        return HTTPException(status_code=409, detail=str(exc))
    logger.warning(f"embed: {doing} failed: {type(exc).__name__}: {str(exc)[:300]}")
    return HTTPException(status_code=502, detail=f"{doing} failed ({type(exc).__name__}); nothing was assumed.")


class OpenDraftRequest(BaseModel):
    draft_id: str          # EA's own report draft (the spec the conversation elicited)
    label: str             # the spec's label in Egeria: ReportSpec::<label>


def spec_draft_from_ea_draft(ea_draft: Dict[str, Any], label: str):
    """EA's elicited spec as a pyegeria.authoring SpecDraft: EA's own markdown for it, parsed by EA's parser into
    the FormatSet pyegeria uses. Nothing else is carried (Studio re-reads the draft from Egeria)."""
    from pyegeria.authoring import SpecDraft
    from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
    from advisor.report_spec_parser import parse_report_spec_markdown
    md = get_report_spec_elicitor()._generate_report_spec_md(ea_draft)
    fs = parse_report_spec_markdown(md)
    return SpecDraft(label=label, format_set=fs, derived_from=f"ea:report-draft:{ea_draft.get('draft_id') or ''}")


@router.post("/api/studio/open-draft")
async def open_in_studio(request: Request, body: OpenDraftRequest) -> Dict[str, Any]:
    """Owner decision B: save EA's elicited spec as an authoring-API SpecDraft, as the user (D16: Egeria, private to
    them, 30-day prune), and answer its reference. The page then posts {type:'studio.openDraft', draft_ref} to
    Studio's exact origin; Studio loads the draft by reference, as the user."""
    from advisor.auth import get_current_user
    from advisor.report_draft import resolve_report_draft
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Give the spec a name for Studio (its label).")
    user = get_current_user(request) or {}
    user_id = user.get("user_id") or user.get("sub")
    resolved = resolve_report_draft(body.draft_id, user_id=user_id, role=user.get("role"))
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Report draft {body.draft_id!r} was not found, or is not yours.")
    try:
        draft = spec_draft_from_ea_draft(resolved[1], label)
    except Exception as exc:  # noqa: BLE001 -- said
        raise HTTPException(status_code=422, detail=f"This draft can't be made into a report spec yet: {exc}") from exc

    def save():
        from pyegeria.authoring import EgeriaDraftStore, inprocess_runner
        from pyegeria.core.async_runner import run_sync
        client = _user_client(request)
        store = EgeriaDraftStore(client, inprocess_runner(client))
        return run_sync(store.async_save(client.user_id, draft))
    try:
        qn = await asyncio.to_thread(save)
    except Exception as exc:  # noqa: BLE001
        raise _authoring_error(exc, "Saving the draft for Studio") from exc
    return {"draft_ref": {"kind": "spec", "name": label}, "qualified_name": qn,
            "words": "Saved in Egeria in your private draft zone; Studio opens it from there."}


class DraftContextRequest(BaseModel):
    draft_ref: Dict[str, str]   # {kind, name}


@router.post("/api/studio/draft-context")
async def draft_context(request: Request, body: DraftContextRequest) -> Dict[str, Any]:
    """The user's Studio draft, read from Egeria as them, as Dr.Egeria markdown for the conversation. Only a
    reference crosses from Studio; the content is read here."""
    ref = body.draft_ref or {}
    kind, name = ref.get("kind"), ref.get("name")
    if kind not in ("spec", "report", "sheet") or not name:
        raise HTTPException(status_code=400, detail="A draft reference is {kind: spec|report|sheet, name}.")

    def load():
        from pyegeria.authoring import EgeriaDraftStore, serialize
        from pyegeria.core.async_runner import run_sync
        client = _user_client(request)
        d = run_sync(EgeriaDraftStore(client).async_load(client.user_id, kind, name))
        return None if d is None else serialize(d)
    try:
        md = await asyncio.to_thread(load)
    except Exception as exc:  # noqa: BLE001
        raise _authoring_error(exc, "Reading the Studio draft") from exc
    if md is None:
        raise HTTPException(status_code=404, detail=f"No {kind} draft named {name!r} is in your private draft zone.")
    return {"draft_ref": {"kind": kind, "name": name}, "markdown": md}
