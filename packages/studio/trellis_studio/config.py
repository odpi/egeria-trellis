"""Studio's settings, read from the environment like RE's and EA's (pydantic BaseSettings).

Names are the box-wide ones, so a deploy sets each value once for every trellis app:
  EGERIA_PLATFORM_URL (EGERIA_VIEW_SERVER_URL accepted, EA's name)   EGERIA_VIEW_SERVER
  TRELLIS_JWT_SECRET    signs Studio's session JWT; the SAME secret EA and RE verify with (owner decision A). There
                        is deliberately no Studio-only override: a per-app secret would silently break one sign-in
                        per box.
  TRELLIS_SERVICE_ACCOUNTS, RE_DAEMON_USER_ID  ids no person may sign in to Studio as (EA refuses the same ones)
  TRELLIS_PORTAL_SECRET verifies the Portal's short-lived SSO token (the Portal's EGERIA_ADVISOR_SSO_SECRET)
  EGERIA_ADVISOR_URL    where the embedded EA panel loads from (default, as the Portal's: http://localhost:8880/)
  STUDIO_PUBLIC_URL     Studio's own public origin (for EA's frame-ancestors / embed origins)
  STUDIO_PORTAL_ORIGINS comma-separated origins allowed to hand a Portal token over by postMessage

Deliberately absent: EGERIA_USER / EGERIA_PASSWORD. Studio has no service account; with no signed-in user there
is no Egeria call at all.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Tuple
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PORT = 8840          # laz (D2); the freshstart twin is 7840
FRESHSTART_PORT = 7840


def origin_of(url: str) -> str:
    """scheme://host[:port] of a URL, or "" if it has no scheme or host. Used for postMessage target origins
    and origin checks, which must be exact."""
    parts = urlsplit((url or "").strip())
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


class StudioSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    egeria_platform_url: str = Field(
        default="https://localhost:9443",
        validation_alias=AliasChoices("EGERIA_PLATFORM_URL", "EGERIA_VIEW_SERVER_URL"),
    )
    egeria_view_server: str = Field(default="qs-view-server", validation_alias="EGERIA_VIEW_SERVER")
    jwt_secret: str = Field(default="", validation_alias="TRELLIS_JWT_SECRET")
    portal_secret: str = Field(default="", validation_alias=AliasChoices("STUDIO_PORTAL_SECRET",
                                                                        "TRELLIS_PORTAL_SECRET"))
    jwt_ttl_hours: int = Field(default=8, validation_alias="STUDIO_JWT_TTL_HOURS")
    advisor_url: str = Field(default="http://localhost:8880/", validation_alias="EGERIA_ADVISOR_URL")
    public_url: str = Field(default=f"http://localhost:{DEFAULT_PORT}", validation_alias="STUDIO_PUBLIC_URL")
    portal_origins_raw: str = Field(default="", validation_alias="STUDIO_PORTAL_ORIGINS")
    service_accounts_raw: str = Field(default="", validation_alias="TRELLIS_SERVICE_ACCOUNTS")
    re_daemon_user_id: str = Field(default="", validation_alias="RE_DAEMON_USER_ID")

    @property
    def service_accounts(self) -> frozenset:
        ids = {p.strip().casefold() for p in self.service_accounts_raw.split(",") if p.strip()}
        if self.re_daemon_user_id.strip():
            ids.add(self.re_daemon_user_id.strip().casefold())
        return frozenset(ids)

    @property
    def advisor_origin(self) -> str:
        return origin_of(self.advisor_url)

    @property
    def public_origin(self) -> str:
        return origin_of(self.public_url)

    @property
    def portal_origins(self) -> Tuple[str, ...]:
        out = []
        for raw in self.portal_origins_raw.split(","):
            o = origin_of(raw)
            if o and o not in out:
                out.append(o)
        return tuple(out)


@lru_cache(maxsize=1)
def get_settings() -> StudioSettings:
    return StudioSettings()
