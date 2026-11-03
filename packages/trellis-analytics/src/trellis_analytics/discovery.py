"""Find manifests through the `pyegeria.analytics` entry-point group (`trellis.analytics` read as its alias).

Q2(b), project owner 2026-10-31: installing a package is what registers its analytics, and a report spec names a
function by its registered name. pyegeria (6.2.17, #519) reads the same groups into its own registry, so this module
is how trellis reads them for itself (Resource Explorer's catalogue, the engine-host declaration), not how pyegeria
learns of them.
"""
from __future__ import annotations

import logging
import warnings
from importlib.metadata import entry_points

from trellis_analytics.manifest import _TrellisManifest, purpose_warnings, question_warnings

GROUP = "pyegeria.analytics"
#: Read during the library split so a step package built before Q2(b) is still found; nothing in trellis declares it.
ALIASES = ("trellis.analytics",)
GROUPS = (GROUP, *ALIASES)
log = logging.getLogger(__name__)


def known_purposes(registry=None) -> list[str] | None:
    """The investigation-purposes list as Resource Explorer holds it, or None when RE is not installed. With
    a `registry` (an RE ProjectRegistry the caller already holds): Egeria's list through RE's cache. Without
    one: RE's shipped seed. Never calls Egeria, and never opens a registry itself (constructing one connects
    to the shared registry and migrates it)."""
    try:
        from resource_explorer.investigation_purposes import known_purpose_names
    except ImportError:
        return None
    return known_purpose_names(registry)


def check_purposes(manifests: dict[str, _TrellisManifest], known: list[str] | None = None,
                   registry=None) -> list[str]:
    """Owner ruling 2026-10-13, "warn only - suggestive everywhere": every declared purpose outside the list is
    logged as a warning naming it; nothing is refused. Returns the warnings. With nothing declared, the list
    is not read at all."""
    if not any(m.purposes for m in manifests.values()):
        return []
    known = known if known is not None else known_purposes(registry)
    if known is None:
        log.warning("manifest purposes not checked: Resource Explorer (which holds the purposes list) is not "
                    "installed")
        return []
    out = [w for m in manifests.values() for w in purpose_warnings(m, known)]
    for w in out:
        log.warning(w)
    return out


def known_question_ids() -> set[str] | None:
    """Every question id in Resource Explorer's catalog, or None when RE is not installed. Read from the
    shipped YAML through the catalog reader; no registry is constructed, so nothing connects to the shared
    registry or migrates it."""
    try:
        from resource_explorer.surveyors.question_catalog_reader import all_question_ids
    except ImportError:
        return None
    return all_question_ids()


def check_questions(manifests: dict[str, _TrellisManifest], known: set[str] | None = None) -> list[str]:
    """Same rule as `check_purposes` (owner: suggestive, never reject): every declared question id the catalog
    does not hold is logged as a warning naming it; nothing is refused. Returns the warnings. With nothing
    declared, the catalog is not read at all."""
    if not any(m.questions for m in manifests.values()):
        return []
    known = known if known is not None else known_question_ids()
    if known is None:
        log.warning("manifest questions not checked: Resource Explorer (which holds the question catalog) is "
                    "not installed")
        return []
    out = [w for m in manifests.values() for w in question_warnings(m, known)]
    for w in out:
        log.warning(w)
    return out


def discover(known_purposes: list[str] | None = None, registry=None, *, check: bool = True) -> dict[str, _TrellisManifest]:
    """name -> manifest for every entry point. An entry point may name a manifest, or a callable returning
    one manifest or a list of them. A broken entry point raises, naming it: a silent skip would be absence
    shown as 'nothing installed'. Declared purposes are checked against the list and warned about, never
    refused (`check_purposes`). `check=False` skips both checks: for a caller that is itself what the checks read
    (Resource Explorer's question catalogue, which merges these manifests and so cannot ask itself whether they
    are known)."""
    found: dict[str, _TrellisManifest] = {}
    for group, ep in _entry_points():
        try:
            obj = ep.load()
            if callable(obj) and not isinstance(obj, _TrellisManifest):
                obj = obj()
            items = obj if isinstance(obj, (list, tuple)) else [obj]
            for m in items:
                if not isinstance(m, _TrellisManifest):
                    raise TypeError(f"{type(m).__name__} is not a RoutineManifest or StepManifest")
                found[m.name] = m
        except Exception as exc:  # noqa: BLE001 - re-raised with the entry point named
            raise RuntimeError(f"entry point {group}:{ep.name} ({ep.value}) failed to load: {exc}") from exc
    if check:
        check_purposes(found, known_purposes, registry)
        check_questions(found)
    return found


def _entry_points():
    """(group, entry point) for every entry point in the primary group, then in each alias."""
    for group in GROUPS:
        for ep in entry_points(group=group):
            yield group, ep


def origin(name: str) -> dict[str, str]:
    """Which installed package provides the manifest named `name`, and under which entry point:
    {"package": <distribution name>, "entry_point": <entry point name>, "group": <entry-point group>}. Raises
    LookupError when no installed entry point provides it (the sentence names it), since a declaration must name a
    real package."""
    for group, ep in _entry_points():
        try:
            obj = ep.load()
            if callable(obj) and not isinstance(obj, _TrellisManifest):
                obj = obj()
        except Exception:  # noqa: BLE001 - discover() is the place that reports a broken entry point
            continue
        items = obj if isinstance(obj, (list, tuple)) else [obj]
        if any(getattr(m, "name", None) == name for m in items):
            return {"package": ep.dist.name if ep.dist else "", "entry_point": ep.name, "group": group}
    raise LookupError(f"no installed package provides '{name}' through the {GROUP} entry point")


def register() -> list[str]:
    """Register discovered manifests into pyegeria's analytic registry; names already present are skipped
    (so calling it twice is harmless) and returned names are the ones added.

    Redundant since pyegeria 6.2.17 (#519): pyegeria reads these entry-point groups itself, so every manifest is
    already registered and this adds nothing. Kept, with a DeprecationWarning, so a caller is told rather than
    broken; to be removed with the `trellis.analytics` alias."""
    warnings.warn("trellis_analytics.discovery.register() is redundant: installing the package registers its "
                  "analytics (pyegeria reads the pyegeria.analytics entry points)", DeprecationWarning, stacklevel=2)
    from pyegeria.view.analytic_registry import get_analytic_registry, register_analytic_functions
    existing = set(get_analytic_registry().keys())
    new = {n: m for n, m in discover().items() if n not in existing}
    if new:
        register_analytic_functions(new, source=GROUP)
    return sorted(new)
