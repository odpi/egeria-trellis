"""Studio's FastAPI app.

    from trellis_studio.app import create_app
    app = create_app()                 # settings from the environment
    app = create_app(StudioSettings(...))   # tests

Mount order matters: trellis-ui's /static/trellis-ui before Studio's own /static (Starlette matches in order).
The login policy is trellis-auth's shared one (`resolve_policy("STUDIO")`: TRELLIS_* then STUDIO_*), so every
non-public path answers 401 without a valid session JWT before a route runs.
"""
from __future__ import annotations

import asyncio
import logging
import time
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import trellis_auth
import trellis_ui
from trellis_auth import resolve_policy

from trellis_studio import __version__
from trellis_studio.auth import (STUDIO_PUBLIC_PATHS, auth_config, current_user, refuse_service_account,
                                 require_user, user_id_of)
from trellis_studio.config import StudioSettings, get_settings
from trellis_studio import sessions

logger = logging.getLogger("trellis_studio")


def static_dir() -> Path:
    return Path(str(resources.files("trellis_studio") / "static"))


class LoginRequest(BaseModel):
    username: str
    password: str


class PortalTokenRequest(BaseModel):
    portal_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


def _claims_even_if_expired(request: Request) -> Optional[Dict[str, Any]]:
    """The claims of the request's Studio access token, signature checked, expiry NOT: for matching a refresh to
    its session and for signing out. Never used to let a request in."""
    import jwt as pyjwt
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    cfg = request.app.state.auth_config
    try:
        return pyjwt.decode(header[len("Bearer "):].strip(), cfg.jwt_secret, algorithms=[cfg.jwt_algorithm],
                            options={"verify_exp": False})
    except pyjwt.PyJWTError:
        return None


def create_app(settings: Optional[StudioSettings] = None) -> FastAPI:
    settings = settings or get_settings()
    cfg = auth_config(settings)
    policy = resolve_policy("STUDIO", extra_public_paths=STUDIO_PUBLIC_PATHS)

    app = FastAPI(title="Trellis Studio", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.auth_config = cfg
    app.state.policy = policy
    # trellis-auth's gate, plus: a signed-out session's JWT is refused like no JWT (TR-10, sessions.py).
    app.add_middleware(sessions.StudioLoginMiddleware, config=cfg, policy=policy,
                       revoked=sessions.revocations(app))
    logger.info("studio: %s", policy.describe())

    # Studio serves many users: say so to pyegeria's spec cache at startup (spec-cache R-A).
    @app.on_event("startup")
    async def _declare_multi_user() -> None:
        from trellis_studio.egeria import declare_multi_user_process
        declare_multi_user_process()

    # A token Egeria rejects is a 401 wherever it surfaces, never a retry and never a 500 (S0 R1).
    try:
        from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    except Exception:  # pragma: no cover -- pyegeria is a dependency
        PyegeriaUnauthorizedException = None
    if PyegeriaUnauthorizedException is not None:
        from fastapi.responses import JSONResponse

        @app.exception_handler(PyegeriaUnauthorizedException)
        async def _egeria_refused_token(request: Request, exc: Exception):
            return JSONResponse(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                                content={"detail": "Egeria no longer accepts your sign-in. Sign in again."})

    # ---- health (public) -------------------------------------------------------------------------------
    @app.get("/health")
    async def health() -> Dict[str, Any]:
        # Says only that Studio's own process answers. It never claims Egeria or EA are up: it doesn't ask them.
        return {"status": "ok", "app": "trellis-studio", "version": __version__}

    # ---- sign-in (public routes; trellis-auth does the work) -------------------------------------------
    @app.get("/api/auth/policy")
    async def auth_policy() -> Dict[str, Any]:
        return {
            "login_required": policy.require_login and not policy.anonymous_read,
            # Not secrets: the page needs them before sign-in to accept a Portal hand-over by postMessage.
            "portal_origins": list(settings.portal_origins),
        }

    @app.post("/api/auth/login")
    async def auth_login(req: LoginRequest) -> Dict[str, Any]:
        if not req.username or not req.password:
            raise HTTPException(status_code=400, detail="username and password required")
        refuse_service_account(req.username, settings)   # before any Egeria call
        # The password is used once, here, for an Egeria bearer token, and never stored or signed into the JWT.
        token = await asyncio.to_thread(trellis_auth.login_with_password, req.username, req.password, cfg)
        if not token:
            raise HTTPException(status_code=401, detail="Egeria did not sign you in: wrong user id or password, "
                                                        "or Egeria could not be reached.")
        return sessions.start_session(app, req.username, token, cfg, settings)

    @app.post("/api/auth/portal")
    async def auth_portal(req: PortalTokenRequest) -> Dict[str, Any]:
        payload = trellis_auth.exchange_portal_token(req.portal_token, cfg)  # 401/400/503 in words
        user = payload.get("sub", "")
        egeria_token = payload.get("egeria_token", "")
        refuse_service_account(user, settings)           # before the Egeria check
        # The token was minted by the Portal: Egeria checks it before Studio wraps a session round it.
        ok = await asyncio.to_thread(trellis_auth.validate_egeria_token, egeria_token, cfg)
        if not ok:
            logger.warning("studio: Portal sign-in for %r not accepted (Egeria refused the token or could not "
                           "be reached)", user)
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                                detail="Egeria did not accept the Portal's sign-in (or could not be reached). "
                                       "Sign in again from the Portal, or with your user id and password.")
        return sessions.start_session(app, user, egeria_token, cfg, settings, role=payload.get("role", "user"),
                                      display_name=payload.get("display_name") or user)

    @app.post("/api/auth/refresh")
    async def auth_refresh(req: RefreshRequest, request: Request) -> Dict[str, Any]:
        """Trade a refresh token for a new access token and a new refresh token (rotation, sessions.py). The
        identity is the refresh token's own; a used refresh token, or an access token for someone else sent
        with it, revokes the whole session. Egeria must still accept the session's token, asked as the user."""
        from trellis_studio.egeria import SignInRefused, check_signin
        store = sessions.refresh_store(app)
        again = "Sign in again."
        refused = lambda detail: HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                                               detail=detail)
        rec = store.peek(req.refresh_token)
        if rec is None or sessions.revocations(app).is_revoked(rec.sid):
            raise refused(f"Your Studio session has ended. {again}")
        if rec.used:
            sessions.end_session(app, rec.sid, settings, auth_time=rec.auth_time)
            logger.warning("studio: a used refresh token was presented again for %r; session revoked", rec.sub)
            raise refused(f"That sign-in was already renewed, so Studio ended the session to be safe. {again}")
        presented = _claims_even_if_expired(request)
        if presented is not None and (presented.get("sub") != rec.sub or presented.get("sid") != rec.sid):
            sessions.end_session(app, rec.sid, settings, auth_time=rec.auth_time)
            logger.warning("studio: refresh for %r presented an access token for %r; session revoked",
                           rec.sub, presented.get("sub"))
            raise refused(f"The sign-in didn't match. {again}")
        refuse_service_account(rec.sub, settings)
        if time.time() >= rec.auth_time + settings.session_max_hours * 3600:
            raise refused(f"Your Studio session has reached its {settings.session_max_hours}-hour limit. {again}")
        egeria_exp = trellis_auth.egeria_token_expiry(rec.egeria_token)
        if egeria_exp is not None and egeria_exp <= time.time():
            sessions.end_session(app, rec.sid, settings, auth_time=rec.auth_time)
            raise refused(f"Your Egeria sign-in has expired. {again}")
        user = {"sub": rec.sub, "user_id": rec.sub, "egeria_token": rec.egeria_token}
        try:
            await asyncio.to_thread(check_signin, user, settings)
        except SignInRefused:
            sessions.end_session(app, rec.sid, settings, auth_time=rec.auth_time)
            logger.info("studio: refresh refused for %r: Egeria no longer accepts the token; session ended", rec.sub)
            raise refused(f"Egeria no longer accepts your sign-in. {again}")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 -- couldn't ask is not refused: the refresh token stays unused
            logger.warning("studio: refresh for %r couldn't re-check Egeria: %s: %s", rec.sub, type(exc).__name__, exc)
            raise HTTPException(status_code=503, detail="Studio couldn't re-check your sign-in with Egeria just "
                                                        "now. Your session carries on until it expires.")
        if not store.consume(req.refresh_token):        # used meanwhile (a concurrent use): a replay
            sessions.end_session(app, rec.sid, settings, auth_time=rec.auth_time)
            raise refused(f"That sign-in was already renewed, so Studio ended the session to be safe. {again}")
        try:
            access = sessions.mint(rec.sub, rec.egeria_token, cfg, settings, role=rec.role,
                                   display_name=rec.display_name, sid=rec.sid, auth_time=rec.auth_time)
        except ValueError:
            raise refused(f"Your Studio session has reached its {settings.session_max_hours}-hour limit. {again}")
        refresh = store.issue(sessions.RefreshRecord(
            sid=rec.sid, sub=rec.sub, egeria_token=rec.egeria_token, role=rec.role,
            display_name=rec.display_name, auth_time=rec.auth_time, expires=rec.expires))
        return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "user_id": rec.sub}

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request, req: Optional[LogoutRequest] = None) -> Dict[str, str]:
        """Sign out: the session's access tokens are refused from now on and its refresh tokens are dropped
        (sessions.py). Takes the access token, the refresh token, or both, so a page whose access token has
        expired can still sign out. Always ok."""
        claims = _claims_even_if_expired(request)
        if claims and claims.get("sid"):
            sessions.end_session(app, claims["sid"], settings, auth_time=claims.get("auth_time"),
                                 exp=claims.get("exp"))
        rec = sessions.refresh_store(app).peek(req.refresh_token) if req and req.refresh_token else None
        if rec is not None:
            sessions.end_session(app, rec.sid, settings, auth_time=rec.auth_time)
        return {"status": "ok"}

    @app.get("/api/auth/me")
    async def auth_me(request: Request) -> Dict[str, Any]:
        user = require_user(request)
        return {"authenticated": True, "user_id": user_id_of(user), "role": user.get("role", "user"),
                "display_name": user.get("display_name") or user_id_of(user), "exp": user.get("exp"),
                "session_ends_by": (user.get("auth_time") or 0) + settings.session_max_hours * 3600}

    # ---- what the page needs to know (signed in) --------------------------------------------------------
    @app.get("/api/config")
    async def config(request: Request) -> Dict[str, Any]:
        require_user(request)
        return {
            "advisor_url": settings.advisor_url,
            "advisor_origin": settings.advisor_origin,
            "public_origin": settings.public_origin,
            "version": __version__,
        }

    # ---- authoring (S1+) --------------------------------------------------------------------------------
    from trellis_studio.routes_drafts import router as drafts_router
    app.include_router(drafts_router)
    from trellis_studio.routes_lifecycle import router as lifecycle_router
    app.include_router(lifecycle_router)
    from trellis_studio.routes_rights import router as rights_router
    app.include_router(rights_router)

    @app.get("/api/renderer")
    async def renderer(request: Request) -> Dict[str, Any]:
        require_user(request)
        src = trellis_ui.egeria_table_source()
        return {"source": src["source"], "version": src.get("version"), "commit": src.get("commit")}

    # ---- the page ---------------------------------------------------------------------------------------
    trellis_ui.mount(app)  # before /static
    app.mount("/static", StaticFiles(directory=static_dir()), name="studio-static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir() / "index.html", headers={"Cache-Control": "no-store"})

    return app
