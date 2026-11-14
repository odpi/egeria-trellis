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
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import trellis_auth
import trellis_ui
from trellis_auth import LoginRequiredMiddleware, resolve_policy

from trellis_studio import __version__
from trellis_studio.auth import STUDIO_PUBLIC_PATHS, auth_config, current_user, require_user, user_id_of
from trellis_studio.config import StudioSettings, get_settings

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
    app.add_middleware(LoginRequiredMiddleware, config=cfg, policy=policy)
    logger.info("studio: %s", policy.describe())

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
        # The password is used once, here, for an Egeria bearer token, and never stored or signed into the JWT.
        token = await asyncio.to_thread(trellis_auth.login_with_password, req.username, req.password, cfg)
        if not token:
            raise HTTPException(status_code=401, detail="Egeria did not sign you in: wrong user id or password, "
                                                        "or Egeria could not be reached.")
        jwt_ = trellis_auth.create_access_token(req.username, token, cfg)
        return {"access_token": jwt_, "token_type": "bearer", "user_id": req.username}

    @app.post("/api/auth/portal")
    async def auth_portal(req: PortalTokenRequest) -> Dict[str, Any]:
        payload = trellis_auth.exchange_portal_token(req.portal_token, cfg)  # 401/400/503 in words
        user = payload.get("sub", "")
        egeria_token = payload.get("egeria_token", "")
        # The token was minted by the Portal: Egeria checks it before Studio wraps a session round it.
        ok = await asyncio.to_thread(trellis_auth.validate_egeria_token, egeria_token, cfg)
        if not ok:
            logger.warning("studio: Portal sign-in for %r not accepted (Egeria refused the token or could not "
                           "be reached)", user)
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                                detail="Egeria did not accept the Portal's sign-in (or could not be reached). "
                                       "Sign in again from the Portal, or with your user id and password.")
        jwt_ = trellis_auth.create_access_token(user, egeria_token, cfg, role=payload.get("role", "user"),
                                                display_name=payload.get("display_name") or user)
        return {"access_token": jwt_, "token_type": "bearer", "user_id": user}

    @app.post("/api/auth/logout")
    async def auth_logout() -> Dict[str, str]:
        return {"status": "ok"}  # nothing server-side: the page drops its JWT

    @app.get("/api/auth/me")
    async def auth_me(request: Request) -> Dict[str, Any]:
        user = require_user(request)
        return {"authenticated": True, "user_id": user_id_of(user), "role": user.get("role", "user"),
                "display_name": user.get("display_name") or user_id_of(user), "exp": user.get("exp")}

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
