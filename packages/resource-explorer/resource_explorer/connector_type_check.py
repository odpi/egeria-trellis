"""Start-up check: is the ConnectorType RE writes secrets through still on the expected provider class?

RE's own secrets-store graph (`EgeriaDatabaseSurveyor._ensure_own_secrets_store`) holds a ConnectorType
named `<own secrets store qualifiedName>::ConnectorType`. An earlier build created it with the read-only
`YAMLSecretsStoreProvider`; Egeria then accepts RE's saves and does nothing (see the surveyor's comment).
The owner's ruling (2026-10-14): treat that as a one-time leftover and CHECK at start, never repair.

This module reads only (one by-name search as RE's daemon) and never writes. It returns a state dict and
`connector_type_line(state)` words it for the start-up log: "connector type: ok", or the mismatch, or
"could not check" (never "ok" when the read failed).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

__all__ = ["check_connector_type", "connector_type_line"]

OK = "ok"
MISMATCH = "mismatch"
ABSENT_STATE = "absent"
COULD_NOT_CHECK = "could not check"


def _expected() -> tuple[str, str]:
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        _OWN_SECRETS_STORE_QUALIFIED_NAME,
        _YAML_SECRETS_FILE_PROVIDER_CLASS,
    )

    return f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::ConnectorType", _YAML_SECRETS_FILE_PROVIDER_CLASS


def _maker() -> Any:
    from pyegeria import ConnectionMaker

    from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

    return egeria_client(Daemon(DaemonReason.BOOTSTRAP), purpose="connector type check",
                         shared=False).of(ConnectionMaker)


def check_connector_type(client: Optional[Any] = None) -> dict:
    """Read the ConnectorType and compare its `connectorProviderClassName`. Never raises, never writes."""
    from resource_explorer.egeria_absence import ABSENT, is_absent

    try:
        qn, expected = _expected()
    except Exception as exc:  # noqa: BLE001
        return {"status": COULD_NOT_CHECK, "reason": f"{type(exc).__name__}: {exc}"}
    state: dict = {"qualified_name": qn, "expected": expected}
    try:
        maker = client if client is not None else _maker()
        try:
            result = maker.get_connector_types_by_name(
                qn, body={"class": "FilterRequestBody", "filter": qn, "startFrom": 0, "pageSize": 20},
                output_format="JSON")
        except Exception as exc:  # noqa: BLE001 - classified below
            if is_absent(exc) == ABSENT:
                return {**state, "status": ABSENT_STATE}
            raise
        if is_absent(result) == ABSENT:
            return {**state, "status": ABSENT_STATE}
        items = result if isinstance(result, list) else [result]
        props = [e.get("properties") for e in items if isinstance(e, dict)]
        exact = [p for p in props if isinstance(p, dict) and p.get("qualifiedName") == qn]
        if len(exact) != 1:
            return {**state, "status": COULD_NOT_CHECK,
                    "reason": f"{len(exact)} ConnectorType elements named {qn!r}" if exact
                              else f"no readable ConnectorType among {len(items)} answer(s)"}
        found = exact[0].get("connectorProviderClassName")
        if not isinstance(found, str) or not found:
            return {**state, "status": COULD_NOT_CHECK, "reason": "the element carries no connectorProviderClassName"}
        return {**state, "status": OK if found == expected else MISMATCH, "found": found}
    except Exception as exc:  # noqa: BLE001 - every failure is "could not check", never "ok"
        return {**state, "status": COULD_NOT_CHECK, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}


def connector_type_line(state: dict) -> str:
    status = state.get("status")
    if status == OK:
        return "connector type: ok"
    if status == MISMATCH:
        return (f"connector type: MISMATCH {state.get('qualified_name')!r} has connectorProviderClassName "
                f"{state.get('found')!r}, expected {state.get('expected')!r} — not repaired; saves through it "
                "may silently do nothing until the element is corrected")
    if status == ABSENT_STATE:
        return "connector type: not created yet (RE creates it when it first stores a secret)"
    return f"connector type: could not check ({state.get('reason') or 'unknown reason'})"
