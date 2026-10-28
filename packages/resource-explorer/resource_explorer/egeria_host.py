"""The Egeria-visible host of a registered server or database, defaulted in ONE place.

Egeria runs in Docker, so a database on this Mac's localhost is reached from Egeria as host.docker.internal.
Every registration path (server register, database register, add-database from a server, the CSV import)
calls `default_egeria_host`, and the web form's `egeriaHostDefault` mirrors it, so the UI and the server agree.
"""
from __future__ import annotations

DOCKER_HOST = "host.docker.internal"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
NOTE_IN_EGERIA = ("The elements Egeria holds keep the old address until the database is registered again. "
                  "Deleting the old elements is the owner's job.")


def default_egeria_host(host: str, egeria_host: str = "") -> str:
    """`egeria_host` as given (trimmed); empty on a local DB host becomes host.docker.internal; else empty."""
    given = (egeria_host or "").strip()
    if given:
        return given
    return DOCKER_HOST if (host or "").strip().lower() in LOCAL_HOSTS else ""


def check_egeria_host(value: str) -> str:
    """The trimmed value, or ValueError when it cannot be a host name (spaces, slashes)."""
    v = (value or "").strip()
    if any(c.isspace() or c == "/" for c in v):
        raise ValueError("The Egeria-visible host is a host name: no spaces or slashes.")
    return v
