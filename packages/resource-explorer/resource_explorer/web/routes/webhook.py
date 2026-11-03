"""GitHub webhook receiver — triggers incremental re-index on push events."""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from resource_explorer.config import get_config

router = APIRouter()
logger = logging.getLogger(__name__)


def _verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Return True if the X-Hub-Signature-256 header matches the payload HMAC."""
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def _do_refresh(resource_slug: str) -> None:
    from resource_explorer.ingestion.incremental import IncrementalIndexer
    from resource_explorer.query_cache import QueryCache
    from resource_explorer.registry import ProjectRegistry

    project = ProjectRegistry().get(resource_slug)
    if not project:
        logger.warning("Webhook refresh: project '%s' not found in registry", resource_slug)
        return
    logger.info("Webhook: starting incremental refresh for '%s'", resource_slug)
    try:
        result = IncrementalIndexer().refresh(project)
        QueryCache().invalidate_project(resource_slug)
        logger.info("Webhook: refresh complete for '%s': %s", resource_slug, result.value)
    except Exception as exc:
        logger.error("Webhook: refresh failed for '%s': %s", resource_slug, exc)


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
) -> dict:
    """Receive GitHub push events and trigger incremental re-indexing."""
    body = await request.body()
    secret = get_config().github.webhook_secret

    if secret:
        if not _verify_signature(body, x_hub_signature_256, secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Only act on push events; silently ack everything else
    if x_github_event != "push":
        return {"status": "ignored", "event": x_github_event}

    try:
        import json
        data = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    repo_url: str = data.get("repository", {}).get("html_url", "")
    if not repo_url:
        return {"status": "no_url"}

    from resource_explorer.registry import ProjectRegistry
    project = ProjectRegistry().get_by_github_url(repo_url)
    if not project:
        logger.info("Webhook push for unregistered repo: %s", repo_url)
        return {"status": "unregistered", "url": repo_url}

    logger.info("Webhook push → scheduling refresh for '%s'", project.slug)
    background_tasks.add_task(_do_refresh, project.slug)
    return {"status": "refresh_scheduled", "slug": project.slug}
