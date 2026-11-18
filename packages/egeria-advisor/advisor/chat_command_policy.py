"""Which Dr.Egeria commands a chat message may compose (EA's DrEgeriaActionAgent).

EA's action agent indexes pyegeria's packaged templates, advanced tier included. Some families must never be
composed from a chat message, however the confirm gate is answered: the MetadataExpert family changes Egeria's
type system and raw instances ("delete type def X", "delete instance <guid>"). Read-back
READBACK-metadata-expert-family-2026-11-13 §2.

Two sources, either one refuses:
  1. pyegeria's compact command specs: `chat_allowed: false`, on the command itself or at the top of its family's
     compact file. This is the long-term source (pyegeria is adding it to the MetadataExpert family).
  2. CHAT_DENIED_FAMILIES, by family name, until that flag ships in the pyegeria EA runs on. Keep a family here
     even after the flag ships: a pyegeria older than the flag must not reopen it.

Names compare without case, spaces, underscores or hyphens, so "MetadataExpert" (the template folder) and
"Metadata Expert" (a spec's family) are one family.
"""
from __future__ import annotations

from typing import Optional

from advisor import compact_specs

#: Families no chat message composes, by name. See the module docstring for why this stays after the flag.
CHAT_DENIED_FAMILIES = frozenset({"metadataexpert"})


def refusal_reason(command_name: str, family: Optional[str]) -> Optional[str]:
    """Why chat may not propose `command_name` (a clause), or None when it may."""
    fam = compact_specs.norm(family)
    if compact_specs.flag(command_name, "chat_allowed", family) is False:
        return "its Dr.Egeria specification marks it as not available from chat"
    if fam in CHAT_DENIED_FAMILIES:
        return (f"the {family} commands change Egeria's type system and raw instances, so they are kept out of "
                "chat actions")
    return None


def command_family(command_name: str) -> Optional[str]:
    """A command's family: pyegeria's compact spec when it has one, else the template folder EA indexed it from."""
    spec = compact_specs.command(command_name)
    if spec and spec.get("family"):
        return spec["family"]
    try:
        from advisor.command_keyword_index import get_command_keyword_index
        return get_command_keyword_index().family_of(command_name)
    except Exception:  # noqa: BLE001 -- no index: the spec (above) and the name are all there is
        return None


def chat_refusal(command_name: str, family: Optional[str]) -> Optional[str]:
    """The sentence that refuses composing `command_name` from chat, or None when chat may compose it."""
    why = refusal_reason(command_name, family)
    if why is None:
        return None
    return (f"**{command_name}** can't be composed from a chat message: {why}. Nothing was composed or run. "
            "Use a reviewed Dr.Egeria document for it instead.")


def plan_step_refusal(command_name: str) -> Optional[str]:
    """The sentence that keeps `command_name` out of an Advisor plan (composed or run), or None."""
    why = refusal_reason(command_name, command_family(command_name))
    if why is None:
        return None
    return f"**{command_name}** can't be part of an Advisor plan: {why}."
