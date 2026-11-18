"""pyegeria's compact Dr.Egeria command specs, as packaged with the installed md_processing: read-only flags.

EA reads flags pyegeria declares per command or per family, without loading pyegeria's dispatcher
(md_processing's COMMAND_DEFINITIONS is empty until a dispatcher is set up):
  - `chat_allowed` (advisor.chat_command_policy): may a chat message compose this command;
  - `idempotent` (GovernancePlanAgent's re-run acknowledgement): is running it twice the same as once.

A flag on a command wins over one at the top of its family's file. Names compare without case, spaces,
underscores or hyphens, and a command's alternate_names are indexed too, so a plan's "## Create Term" finds
the spec that pyegeria calls "Create Glossary Term" when it lists that alternate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


def norm(name: Optional[str]) -> str:
    return re.sub(r"[\s_\-]+", "", name or "").lower()


def compact_dir() -> Optional[Path]:
    try:
        import md_processing
    except ImportError:
        return None
    d = Path(md_processing.__file__).parent / "data" / "compact_commands"
    return d if d.is_dir() else None


@dataclass(frozen=True)
class Specs:
    families: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # norm(family) -> the file's top-level flags
    commands: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # norm(name or alternate) -> the command


def _names(c: Dict[str, Any], key: Optional[str] = None):
    yield c.get("display_name")
    yield key           # a dict-shaped file keys commands by their header name ("Initiate Survey": initiate_survey)
    alts = c.get("alternate_names") or []
    if isinstance(alts, str):
        alts = re.split(r"[;,]", alts)
    for a in alts:
        if isinstance(a, str):
            yield a


@lru_cache(maxsize=1)
def load() -> Specs:
    """Every packaged compact spec file. A file that cannot be read contributes nothing (and is logged)."""
    specs = Specs()
    d = compact_dir()
    if d is None:
        return specs
    for f in sorted(d.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"compact_specs: could not read {f.name}: {exc}")
            continue
        if not isinstance(doc, dict):
            continue
        family = doc.get("family") or ""
        specs.families[norm(family)] = {k: v for k, v in doc.items() if k not in ("commands", "attribute_definitions",
                                                                                 "bundles")}
        cmds = doc.get("commands") or []
        for key, c in (cmds.items() if isinstance(cmds, dict) else ((None, c) for c in cmds)):
            if not isinstance(c, dict):
                continue
            entry = {**c, "family": c.get("family") or family}
            for n in _names(c, key):
                if n and norm(n) not in specs.commands:
                    specs.commands[norm(n)] = entry
    return specs


def command(name: str) -> Optional[Dict[str, Any]]:
    """The spec of a command (by display name or an alternate name), or None when pyegeria has none."""
    return load().commands.get(norm(name))


def flag(name: str, key: str, family: Optional[str] = None) -> Any:
    """`key` for a command: its own value, else its family file's, else None (pyegeria does not say)."""
    c = command(name)
    if c is not None and key in c:
        return c[key]
    fam = (c or {}).get("family") or family
    return load().families.get(norm(fam), {}).get(key) if fam else None
