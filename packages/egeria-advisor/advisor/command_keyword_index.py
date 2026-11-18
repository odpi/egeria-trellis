"""
CommandKeywordIndex — maps natural-language phrases to Dr.Egeria command names.

Sources (in decreasing confidence):
  1. Action catalog aliases → confidence 0.95 (explicitly curated)
  2. Catalog command names (normalised) → confidence 0.90
  3. Template filesystem names (uncatalogued) → confidence 0.75
  4. Partial substring match against any of the above → confidence 0.55

Usage:
    idx = get_command_keyword_index()
    match = idx.lookup("blueprint")
    if match and match.confidence >= 0.7:
        command_name = match.command   # "Create Solution Blueprint"
    elif match:
        suggestion = f"Did you mean {match.command}?"
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from loguru import logger


@dataclass
class CommandMatch:
    command: str        # Canonical Dr.Egeria command name
    family: str         # Template family (e.g. "Solution Architect")
    confidence: float   # 0.0–1.0
    source: str         # "alias" | "catalog_name" | "template_name" | "partial"
    # Set when chat may never propose this command (advisor.chat_command_policy). Matched anyway, so a request
    # for it is refused in words rather than silently mapped to some other command.
    refusal: Optional[str] = None


class CommandKeywordIndex:
    """
    Searchable index of all known Dr.Egeria commands and their natural-language aliases.
    Built lazily from the action catalog and the template filesystem.
    """

    def __init__(self) -> None:
        self._entries: List[Dict] = []   # [{command, family, terms: [str], source_tier}]
        self._built = False

    # ── Build ──────────────────────────────────────────────────────────────────

    def _ensure_built(self) -> None:
        if self._built:
            return
        self._entries = []
        self._load_catalog()
        self._load_templates()
        from advisor.chat_command_policy import refusal_reason
        for entry in self._entries:
            entry["refusal"] = refusal_reason(entry["command"], entry["family"])
        self._built = True
        logger.debug(f"CommandKeywordIndex: {len(self._entries)} entries loaded")

    def invalidate(self) -> None:
        """Clear the built index so the next lookup rescans the catalog YAML +
        template filesystem. Call after new Dr.Egeria template files have been
        added/synced so a running process picks them up without a restart.
        Wired to POST /api/admin/maintenance/refresh_templates."""
        self._built = False
        self._entries = []

    def _load_catalog(self) -> None:
        catalog_path = Path(__file__).parent / "configdata" / "dr_egeria_actions.yaml"
        try:
            with open(catalog_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(f"CommandKeywordIndex: could not load catalog: {exc}")
            return

        for action in data.get("actions", []):
            name = action.get("name", "")
            family = action.get("family", "")
            if not name:
                continue
            aliases = action.get("aliases", [])
            # Include normalized command name as an alias too
            self._entries.append({
                "command": name,
                "family": family,
                "terms": [a.lower() for a in aliases],
                "name_normalized": self._normalize(name),
                "source_tier": "catalog",
            })

    def _load_templates(self) -> None:
        """Scan template filesystem for command names not already in the catalog."""
        catalogued = {e["command"].lower() for e in self._entries}

        template_root = self._find_template_root()
        if not template_root:
            return

        for md_file in template_root.rglob("*.md"):
            # Decode filename: "Create_Solution_Blueprint.md" → "Create Solution Blueprint"
            command_name = md_file.stem.replace("_", " ")
            if command_name.lower() in catalogued:
                continue
            # Family is the parent folder name
            family = md_file.parent.name
            # Skip the basic/advanced level folder
            if family in ("basic", "advanced"):
                family = md_file.parent.parent.name

            self._entries.append({
                "command": command_name,
                "family": family,
                "terms": [],
                "name_normalized": self._normalize(command_name),
                "source_tier": "template",
            })
            catalogued.add(command_name.lower())

    def _find_template_root(self) -> Optional[Path]:
        """Find the template directory to index (the basic/ tree).

        The installed pyegeria's packaged templates come first (BRIEF
        EA-CATCHUP-AND-DASHBOARDS-2026-11-10 B2), so the index names exactly
        the commands the installed dispatcher runs. Older pyegeria: fall back,
        with a logged note, to ``_find_legacy_template_root``.
        """
        from advisor.dre_templates import installed_templates_root, note_fallback

        root = installed_templates_root()
        if root is not None:
            return root / "basic"
        legacy = self._find_legacy_template_root()
        note_fallback("CommandKeywordIndex", legacy)
        return legacy

    def _find_legacy_template_root(self) -> Optional[Path]:
        """Pre-B2: find the Dr-Egeria-Templates directory.

        Checked in decreasing preference. This repo's own bundled copy
        (<repo>/config/dr-egeria-templates/) comes first — it's version-controlled as part
        of the trellis repo and kept in sync by the dr-egeria-command-sync
        skill's explicit sync step, so it doesn't depend on any sibling
        checkout existing at a hardcoded path on this machine.

        Moved 2026-09-06 from packages/egeria-advisor/examples/templates to
        the repo-level config/. These templates are a runtime input to this
        index and to agents/tools.py, not an example library, and the sync
        script's destination moved with them rather than gaining a copy.

        The old `data/repos/egeria-workspaces/exchange-quickstart/...` and
        `egeria-workspaces-fs/exchange-quickstart/...` paths were dropped —
        confirmed live (2026-07-09) that path is a stale, orphaned copy
        missing entire families (Action Author, Actor Manager) that exist
        in every other copy. `egeria-workspaces-fs/templates/` (no
        `exchange-quickstart/Templates/Dr-Egeria-Templates` prefix) is the
        one actually served to the Portal app at runtime and kept current —
        see the dr-egeria-command-sync skill's references/repo_paths.md.
        """
        candidates = [
            Path(__file__).parent.parent.parent.parent / "config" / "dr-egeria-templates",
        ]
        # Sibling egeria-workspaces-fs checkout, either alongside this repo
        # (<parent>/egeria-workspaces-fs/templates) or alongside this repo's
        # parent (<grandparent>/egeria-workspaces-fs/templates) — covers both
        # the flat "trellis and egeria-workspaces-fs are siblings" layout and
        # dwolfson's own "trellis is one level deeper" layout. EGERIA_ROOT_PATH
        # / PYEGERIA_ROOT_PATH (same env vars advisor/agents/tools.py's
        # _templates_root() honours) take priority over both when set.
        env_root = os.environ.get("EGERIA_ROOT_PATH") or os.environ.get("PYEGERIA_ROOT_PATH")
        if env_root:
            candidates.append(Path(env_root).expanduser() / "templates")
        # __file__ = <repo>/packages/egeria-advisor/advisor/command_keyword_index.py
        repo_root = Path(__file__).parent.parent.parent.parent
        # Both checkout names, because they are both real. This machine
        # calls it `egeria-workspaces-fs`; the Linux box ("hedwig") calls it
        # `egeria-workspaces`, which is what EA BACKLOG DP-1 records — an
        # earlier version of this list tried only the `-fs` form and so
        # would have missed the very machine that motivated the fix.
        for parent in (repo_root.parent, repo_root.parent.parent):
            for ws in ("egeria-workspaces-fs", "egeria-workspaces"):
                candidates.append(parent / ws / "templates")
        # Try pyegeria config
        try:
            import pyegeria.core.config as cfg
            root = Path(cfg.get_app_config().Environment.pyegeria_root)
            candidates.append(root / "Templates" / "Dr-Egeria-Templates")
            candidates.append(root / "templates")
        except Exception:
            pass

        for path in candidates:
            if path.exists():
                # Prefer the basic/ subtree to avoid duplicates
                basic = path / "basic"
                return basic if basic.exists() else path
        return None

    # ── Lookup ─────────────────────────────────────────────────────────────────

    def lookup(self, phrase: str) -> Optional[CommandMatch]:
        """
        Return the best CommandMatch for the given phrase, or None if nothing
        scores above 0.50.

        Scoring tiers:
          0.95 — exact alias match (catalog entry)
          0.90 — exact normalized command name match
          0.75 — exact normalized match against template-only entry
          0.55 — partial: phrase is a substring of an alias or command name,
                          or an alias is a substring of phrase
        """
        self._ensure_built()
        phrase_n = self._normalize(phrase)
        if not phrase_n:
            return None

        best: Optional[CommandMatch] = None
        best_score = 0.0

        for entry in self._entries:
            is_catalog = entry["source_tier"] == "catalog"

            # Tier 1: exact alias match
            for term in entry["terms"]:
                if phrase_n == term:
                    score = 0.95
                    if score > best_score:
                        best_score = score
                        best = CommandMatch(entry["command"], entry["family"], score, "alias", entry.get("refusal"))

            # Tier 2/3: exact normalized command name
            if phrase_n == entry["name_normalized"]:
                score = 0.90 if is_catalog else 0.75
                if score > best_score:
                    best_score = score
                    src = "catalog_name" if is_catalog else "template_name"
                    best = CommandMatch(entry["command"], entry["family"], score, src, entry.get("refusal"))

            # Tier 4: partial match (only if nothing better found yet). Guarded to a
            # minimum length -- _normalize() strips leading verbs like "create ", so a
            # short leftover like "a" or "an" would otherwise trivially substring-match
            # almost any multi-word command name (e.g. "a" in "campaign") and produce a
            # confident-looking but meaningless match.
            if best_score < 0.60 and len(phrase_n) >= 3:
                # phrase is contained in alias or name
                for term in entry["terms"] + [entry["name_normalized"]]:
                    if len(term) >= 3 and (phrase_n in term or term in phrase_n):
                        score = 0.55
                        if score > best_score:
                            best_score = score
                            best = CommandMatch(entry["command"], entry["family"], score, "partial", entry.get("refusal"))

        return best if best_score >= 0.50 else None

    def search_by_keyword(self, keyword: str) -> Dict[str, List[Dict]]:
        """Return all commands whose name, family, or aliases contain *keyword*.

        Returns a dict grouped by family (same shape as all_commands()).
        """
        self._ensure_built()
        kw = self._normalize(keyword)
        seen: Dict[str, Dict] = {}
        for entry in self._entries:
            if entry.get("refusal"):
                continue   # never offered to chat or the Plan Editor's picker (advisor.chat_command_policy)
            name_n = entry["name_normalized"]
            family_n = self._normalize(entry["family"] or "")
            terms = entry["terms"]
            if (kw in name_n or kw in family_n
                    or any(kw in t for t in terms)):
                key = entry["command"]
                if key not in seen:
                    seen[key] = {
                        "name": entry["command"],
                        "family": entry["family"],
                        "aliases": terms,
                        "in_catalog": entry["source_tier"] == "catalog",
                    }
        groups: Dict[str, List] = {}
        for cmd in seen.values():
            fam = cmd["family"] or "Other"
            groups.setdefault(fam, []).append(cmd)
        return {fam: sorted(cmds, key=lambda c: c["name"])
                for fam, cmds in sorted(groups.items())}

    def all_commands(self) -> List[Dict]:
        """Return all commands grouped for the /api/actions endpoint. Commands chat may never propose are left
        out (cosmetic: plan execute refuses them server-side whatever the picker showed)."""
        self._ensure_built()
        seen: Dict[str, Dict] = {}
        for entry in self._entries:
            if entry.get("refusal"):
                continue
            key = entry["command"]
            if key not in seen:
                seen[key] = {
                    "name": entry["command"],
                    "family": entry["family"],
                    "aliases": entry["terms"],
                    "in_catalog": entry["source_tier"] == "catalog",
                }
        # Group by family
        groups: Dict[str, List] = {}
        for cmd in seen.values():
            fam = cmd["family"] or "Other"
            groups.setdefault(fam, []).append(cmd)
        # Sort families and commands within each family
        return {fam: sorted(cmds, key=lambda c: c["name"])
                for fam, cmds in sorted(groups.items())}

    def family_of(self, command: str) -> Optional[str]:
        """The family EA indexed `command` under (catalog entry, else template folder), or None."""
        self._ensure_built()
        n = self._normalize(command)
        for entry in self._entries:
            if entry["name_normalized"] == n:
                return entry["family"] or None
        return None

    # ── Utilities ──────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(s: str) -> str:
        """Lowercase, collapse spaces, strip punctuation for comparison."""
        s = s.lower().strip()
        s = re.sub(r'[_\-\.]', ' ', s)
        s = re.sub(r'\s+', ' ', s)
        # Remove common leading articles/verbs for matching purposes
        s = re.sub(r'^(?:create|link|add|attach|classify|set)\s+', '', s)
        return s.strip()


# ── Singleton ──────────────────────────────────────────────────────────────────

_index: Optional[CommandKeywordIndex] = None


def get_command_keyword_index() -> CommandKeywordIndex:
    global _index
    if _index is None:
        _index = CommandKeywordIndex()
    return _index
