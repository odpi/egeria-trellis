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
        jwt_ = sessions.mint(req.username, token, cfg, settings)
        return {"access_token": jwt_, "token_type": "bearer", "user_id": req.username}

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
        jwt_ = sessions.mint(user, egeria_token, cfg, settings, role=payload.get("role", "user"),
                             display_name=payload.get("display_name") or user)
        return {"access_token": jwt_, "token_type": "bearer", "user_id": user}

    @app.post("/api/auth/refresh")
    async def auth_refresh(request: Request) -> Dict[str, Any]:
        """A new session JWT for the session presented, only if everything still holds (sessions.py): it isn't
        signed out or past its maximum age, its Egeria token hasn't expired, and Egeria still accepts that
        token, asked as the user. Egeria refusing it ends the session here too."""
        from trellis_studio.egeria import SignInRefused, check_signin
        user = require_user(request)
        uid = user_id_of(user)
        refuse_service_account(uid, settings)
        ended = HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                              detail=f"Your Studio session has reached its {settings.session_max_hours}-hour limit. "
                                     "Sign in again.")
        if not user.get("auth_time") or time.time() >= user["auth_time"] + settings.session_max_hours * 3600:
            raise ended
        egeria_exp = trellis_auth.egeria_token_expiry(user.get("egeria_token") or "")
        if egeria_exp is not None and egeria_exp <= time.time():
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                                detail="Your Egeria sign-in has expired. Sign in again.")
        try:
            await asyncio.to_thread(check_signin, user, settings)
        except SignInRefused:
            sessions.revocations(app).revoke(user["sid"], sessions.revoke_until(user, settings))
            logger.info("studio: refresh refused for %r: Egeria no longer accepts the token; session ended", uid)
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                                detail="Egeria no longer accepts your sign-in. Sign in again.")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 -- couldn't ask is not refused: no new JWT, the session runs on
            logger.warning("studio: refresh for %r couldn't re-check Egeria: %s: %s", uid, type(exc).__name__, exc)
            raise HTTPException(status_code=503, detail="Studio couldn't re-check your sign-in with Egeria just "
                                                        "now. Your session carries on until it expires.")
        try:
            jwt_ = sessions.mint(uid, user["egeria_token"], cfg, settings, role=user.get("role", "user"),
                                 display_name=user.get("display_name"), sid=user["sid"],
                                 auth_time=user["auth_time"])
        except ValueError:
            raise ended
        return {"access_token": jwt_, "token_type": "bearer", "user_id": uid}

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request) -> Dict[str, str]:
        """Sign out: this session's JWTs are refused from now on and can't be refreshed (sessions.py). Always ok,
        so a page with an expired or missing JWT can still sign out."""
        user = trellis_auth.get_current_user(request, cfg)
        if user and user.get("sid"):
            sessions.revocations(app).revoke(user["sid"], sessions.revoke_until(user, settings))
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
