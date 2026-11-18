"""Where Egeria Advisor reads Dr.Egeria command templates from.

The templates EA composes commands from must match the Dr.Egeria dispatcher
that runs them, and that dispatcher is the **installed** pyegeria. From the
pyegeria release that carries ``md_processing.templates`` (BRIEF
EA-CATCHUP-AND-DASHBOARDS-2026-11-10, B2 / D2), the rendered templates ship in
the wheel; this module points every EA template loader at them.

``installed_templates_root()`` returns the directory holding ``basic/`` and
``advanced/`` (the same layout every EA loader already walks), or ``None``
when the installed pyegeria predates the accessor. In that case the callers
keep their old paths (the 2026-08-17 egeria-python clone under
ADVISOR_DATA_PATH, ``config/dr-egeria-templates``, a workspace ``templates/``)
and this module logs once that they are doing so. Once trellis' pyegeria floor
includes the accessor, those fallbacks can be removed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

LEVELS = ("basic", "advanced")

_fallback_logged: set[str] = set()


def installed_templates_root() -> Optional[Path]:
    """The installed pyegeria's template root (contains ``basic/`` and
    ``advanced/``), or ``None`` if pyegeria has no ``md_processing.templates``
    or it reports no templates.

    Accepts ``templates_source`` of ``"package"`` (installed wheel) and
    ``"checkout"`` (editable pyegeria install): both are the templates
    belonging to the dispatcher that is actually imported.
    """
    try:
        from md_processing import templates as pyeg_templates
    except Exception:
        return None
    try:
        roots = []
        for level in LEVELS:
            if pyeg_templates.templates_source(level) == "missing":
                return None
            # A wheel installed into site-packages is a plain directory, so the
            # Traversable is a filesystem path. A zipped install would not be;
            # treat that as "not available" rather than half-reading it.
            p = Path(str(pyeg_templates.templates_root(level)))
            if not p.is_dir():
                return None
            roots.append(p.parent)
        if len({str(r.resolve()) for r in roots}) != 1:
            return None
        return roots[0]
    except Exception as exc:  # never let template discovery break a loader
        logger.warning(f"pyegeria md_processing.templates present but unusable: {exc}")
        return None


def installed_templates_source() -> str:
    """``"package"``, ``"checkout"`` or ``"unavailable"`` — for status/admin
    display and logs."""
    try:
        from md_processing import templates as pyeg_templates
        if installed_templates_root() is None:
            return "unavailable"
        return pyeg_templates.templates_source("basic")
    except Exception:
        return "unavailable"


def note_fallback(loader: str, path: Optional[Path]) -> None:
    """Log, once per loader, that it is using a non-pyegeria template copy."""
    if loader in _fallback_logged:
        return
    _fallback_logged.add(loader)
    where = str(path) if path is not None else "no template directory"
    logger.info(
        f"{loader}: installed pyegeria has no md_processing.templates; "
        f"falling back to {where}. These templates may not match the installed "
        f"Dr.Egeria dispatcher until trellis' pyegeria floor includes the accessor."
    )


def templates_status() -> dict:
    """What the admin status page shows about Dr.Egeria templates, so a
    fallback to a copy that may not match the dispatcher is visible, not only
    logged.

    ``matches_executor`` is True only when the templates come from the
    installed pyegeria (``source`` "package" or "checkout"). Otherwise
    ``source`` is "fallback", ``root`` is the first legacy directory in use
    (or None when there is none at all) and ``fallback_roots`` names each
    loader's own legacy directory, since they differ (the 2026-08-17 clone,
    config/dr-egeria-templates, a workspace templates/).
    """
    try:
        from importlib.metadata import version
        pyeg_version = version("pyegeria")
    except Exception:
        pyeg_version = None

    root = installed_templates_root()
    if root is not None:
        return {
            "source": installed_templates_source(),
            "root": str(root),
            "matches_executor": True,
            "pyegeria_version": pyeg_version,
        }
    fallback_roots: dict = {}

    def _probe(name, fn):
        try:
            p = fn()
            fallback_roots[name] = str(p) if p is not None else None
        except Exception:
            fallback_roots[name] = None

    def _template_index():
        from advisor.agents.dr_egeria_agent import TemplateIndex
        paths = TemplateIndex()._legacy_base_paths()
        return paths[0] if paths else None

    def _tools():
        from advisor.agents.tools import _legacy_templates_root
        return _legacy_templates_root()

    def _keyword_index():
        from advisor.command_keyword_index import CommandKeywordIndex
        return CommandKeywordIndex()._find_legacy_template_root()

    _probe("TemplateIndex", _template_index)
    _probe("agents.tools", _tools)
    _probe("CommandKeywordIndex", _keyword_index)
    first = next((r for r in fallback_roots.values() if r), None)
    return {
        "source": "fallback",
        "root": first,
        "matches_executor": False,
        "pyegeria_version": pyeg_version,
        "fallback_roots": fallback_roots,
    }
