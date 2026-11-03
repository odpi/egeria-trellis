"""Who may publish a private investigation's member, and what is recorded when nobody may yet (2026-10-13 review).

Design §3.6 (`docs/investigation-classification-and-zoning-design.md`): a private investigation's PRODUCED
artifacts (its survey reports and their findings) are private to its owner. Egeria's security connector judges a
create by the zones the new element is born with, and an annotation by its anchor's zones, so only the OWNER can
write them (`validateUserForElementCreate`, `validateUserForAnchorMemberCreate`). Three consequences live here:

* `resolve_private_owner` fails CLOSED: "could not tell" is never "not private".
* `PrivateAwaitsOwner`: a publish attempted by anyone but the signed-in owner (a daemon, a queued run, another
  person) is refused before any write, and the unattended callers record it as WAITING, not failed — the same
  stance as `egeria_outbox.OutboxAwaitsOwner` (#640).
* `PrivateKindNotSupported`: databases and file systems have no private (born-private) publish yet, so a private
  member of those kinds is refused before anything is written. Interim; born-private support is a follow-up.

`record_once` writes the activity entry for either, idempotently: one row per (resource, run) for a wait, one row
per state for a refusal — never one per scheduler cycle.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: The activity status a wait is recorded with: an existing value the UI already treats as not-finished.
WAITING_STATUS = "pending"
#: How a kind is said in the words a person reads.
_KIND_WORDS = {"repo": "repository", "database": "database", "filesystem": "file system"}


class PrivateOwnerUnknown(RuntimeError):
    """Whether a resource is in a private investigation could not be read. Nothing was written."""

    def __init__(self, entity_type: str, slug: str, cause: BaseException):
        self.entity_type, self.slug = entity_type, slug
        super().__init__(
            f"not published · could not tell whether {_KIND_WORDS.get(entity_type, entity_type)} '{slug}' is in a "
            f"private investigation ({type(cause).__name__}: {str(cause)[:200]}); nothing was written. "
            "Publishing anyway could put private work where everyone can read it."
        )


class PrivateAwaitsOwner(RuntimeError):
    """A private investigation's member, published by someone other than its signed-in owner. Nothing was written.

    Not a failure: the owner's own publish writes it. Unattended callers record it as waiting (`record_waiting`)."""

    def __init__(self, entity_type: str, slug: str, owner: str, acting_as: str):
        self.entity_type, self.slug, self.owner, self.acting_as = entity_type, slug, owner, acting_as
        super().__init__(
            f"waits for {owner} to publish · {_KIND_WORDS.get(entity_type, entity_type)} '{slug}' is in {owner}'s "
            f"private investigation, and only {owner} can write its private survey report "
            f"(this ran as {acting_as or 'nobody signed in'}); nothing was written"
        )


class PrivateKindNotSupported(RuntimeError):
    """A database or file system in a private investigation: no private publish exists for the kind yet."""

    def __init__(self, entity_type: str, slug: str, owner: str):
        self.entity_type, self.slug, self.owner = entity_type, slug, owner
        kind = _KIND_WORDS.get(entity_type, entity_type)
        super().__init__(
            f"not published · this {kind} ('{slug}') is in {owner}'s private investigation, and private "
            f"publishing for {kind}s is not built yet; nothing was written"
        )


def resolve_private_owner(registry, entity_type: str, slug: str) -> str:
    """The owner whose private investigation holds this resource, `""` for none. RAISES `PrivateOwnerUnknown` when
    the registry cannot answer (fail closed). No registry at all means no investigation can exist: `""`."""
    if registry is None:
        return ""
    try:
        owner = registry.private_owner_for_entity(entity_type, slug)
    except Exception as exc:
        raise PrivateOwnerUnknown(entity_type, slug, exc) from exc
    # The value becomes a zone name, so only a real userId string counts (see materializer._privacy_for).
    return owner.strip() if isinstance(owner, str) else ""


def is_owner(identity, owner: str) -> bool:
    """`identity` is the signed-in `owner` themself (a Caller), never a daemon acting for them."""
    return (getattr(identity, "kind", "") == "caller"
            and (getattr(identity, "user_id", "") or "").strip() == owner)


def record_once(registry, *, entity_type: str, slug: str, status: str, summary: str, marker: str,
                per: str = "run", name: str = "", location: str = "") -> bool:
    """Write one `catalog` activity entry carrying `marker` (in `detail`), unless it is already recorded.

    `per="run"`: skipped when ANY entry for the resource carries the marker (the marker names the run).
    `per="state"`: skipped when the resource's LATEST catalog entry carries it, so a state that persists across
    scheduler cycles is one row, and a new row appears only after something else happened in between.
    Returns True when a row was written. Never raises: the caller's own refusal or wait is the signal."""
    if registry is None:
        return False
    try:
        if per == "state":
            latest = registry.list_activity(entity_slug=slug, operation="catalog", limit=1)
            seen = bool(latest) and (latest[0].get("detail") or "") == marker
        else:
            seen = any((e.get("detail") or "") == marker
                       for e in registry.list_activity(entity_slug=slug, operation="catalog", status=status,
                                                       limit=500))
        if seen:
            return False
        from resource_explorer.activity_logger import log_catalog

        log_catalog(registry, entity_type=entity_type, entity_slug=slug, entity_name=name or slug,
                    entity_location=location, status=status, summary=summary, detail=marker)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record %r for %s/%s: %s", marker, entity_type, slug, exc)
        return False


def record_waiting(registry, exc: PrivateAwaitsOwner, *, run_key: str, source: str, name: str = "",
                   location: str = "") -> bool:
    """The waiting row for one (resource, run): `pending`, the wait's own words, once however often it is retried."""
    return record_once(registry, entity_type=exc.entity_type, slug=exc.slug, status=WAITING_STATUS,
                       summary=str(exc), name=name, location=location,
                       marker=f"awaits-owner · {exc.owner} · {source} · {run_key}", per="run")


def refuse_unsupported_private(registry, entity_type: str, slug: str, *, name: str = "",
                               location: str = "") -> None:
    """Databases and file systems (interim): raise `PrivateKindNotSupported` before any write when the resource is
    in a private investigation, recording the refusal once per state. Raises `PrivateOwnerUnknown` when that cannot
    be told."""
    try:
        owner = resolve_private_owner(registry, entity_type, slug)
    except PrivateOwnerUnknown as exc:
        record_once(registry, entity_type=entity_type, slug=slug, status="error", summary=str(exc),
                    marker=f"private-owner-unknown · {entity_type}", per="state", name=name, location=location)
        raise
    if not owner:
        return
    refusal = PrivateKindNotSupported(entity_type, slug, owner)
    record_once(registry, entity_type=entity_type, slug=slug, status="error", summary=str(refusal),
                marker=f"private-kind-unsupported · {entity_type} · {owner}", per="state", name=name,
                location=location)
    raise refusal


def acting_user(identity: object | None) -> str:
    return (getattr(identity, "client_user", "") or getattr(identity, "user_id", "") or "").strip()
