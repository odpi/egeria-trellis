"""Shared request-body handling for resource-registration routes.

Until 2026-10-09 this validated a per-resource `egeria_user` (a display name typed as a login ID
once broke a publish). The owner then ruled that per-resource Egeria credentials are no longer
accepted, stored or used at all (Brief I): Egeria calls run as the signed-in person or as RE's
daemon identity. What is left is the warning for a caller that still sends them.
"""
from __future__ import annotations

from fastapi import HTTPException

import logging

log = logging.getLogger(__name__)

#: Per-resource Egeria credentials (owner's ruling, 2026-10-09): no longer accepted, stored or used.
#: The request models keep the two fields so an older caller is not refused with a 422; a caller
#: that still sends a value gets it ignored, with one WARNING that names the fields, never the values.
IGNORED_EGERIA_CREDENTIAL_FIELDS = ("egeria_user", "egeria_password")


def ignore_egeria_credentials(body, *, where: str) -> None:
    """Log (never echo) that a request carried per-resource Egeria credentials, which are ignored."""
    sent = [f for f in IGNORED_EGERIA_CREDENTIAL_FIELDS
            if f in getattr(body, "model_fields_set", set()) and getattr(body, f, None)]
    if sent:
        log.warning("%s: %s ignored — RE no longer accepts per-resource Egeria credentials; Egeria "
                    "calls run as the signed-in person or RE's daemon identity", where, "/".join(sent))
