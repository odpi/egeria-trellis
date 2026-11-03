"""The kind of a blueprint, in its name (project owner, 2026-10-07; brief section 4).

A repository can be read as several different blueprints, and they are not interchangeable: a
deployment blueprint and a logical blueprint over the same repository are both correct and share
components (`docs/curation-lenses-design.md` section 1). So the KIND rides in the name of what RE
writes: "Egeria Deployment Blueprint", "Egeria Build Blueprint", "Egeria Logical Blueprint"; the
generic form is "<repository> <Kind> Blueprint".

Pure functions: nothing here reads or writes Egeria. `blueprint_kind_rows` is the selector at the top
of "what it's made of": one row per kind RE can offer, with its source and its state, said plainly
about the ones not yet drawn.
"""
from __future__ import annotations

import re

#: kind key -> the word in the name. A key is an architecture perspective of the recovery
#: (`clustering.Cluster.perspective`) read as a kind of blueprint.
KIND_WORDS = {"deployment": "Deployment", "build": "Build", "logical": "Logical",
              # Not a recovery perspective: the services a repository RUNS but does not build, as other
              # projects' deployment units (DESIGN-BLUEPRINT-NODE-ADMISSION.md).
              "environment": "Environment Deployment"}
#: Perspective -> kind key. The recovery's "dev" (dev/devops) reading is the BUILD reading.
PERSPECTIVE_KIND = {"deployment": "deployment", "dev": "build", "logical": "logical"}
#: The kinds that are drawn today. Build and Logical are listed, honestly, as not yet drawn.
DRAWN_KINDS = ("deployment",)

_SLUG_SUFFIX = re.compile(r"[_\-.]git$", re.I)


def kind_key(perspective: str) -> str:
    """The kind of blueprint a recovery perspective is. An unknown perspective keeps its own word, so
    a new reading is named, never silently called a deployment blueprint."""
    return PERSPECTIVE_KIND.get((perspective or "").lower(), (perspective or "").lower())


def kind_word(perspective: str) -> str:
    key = kind_key(perspective)
    return KIND_WORDS.get(key, key.capitalize() if key else "Unclassified")


def repo_label(slug: str, display_name: str = "") -> str:
    """How the repository reads inside a blueprint name. A display name that is a real name (not
    the slug repeated) is used as it stands; otherwise the slug, minus a trailing "_git", is split on
    separators and capitalised: `egeria_git` -> "Egeria"."""
    name = (display_name or "").strip()
    if name and name != slug and not re.fullmatch(r"[a-z0-9_.\-]+", name):
        return name
    base = _SLUG_SUFFIX.sub("", name or slug)
    return " ".join(w.capitalize() for w in re.split(r"[_\-.\s]+", base) if w) or slug


def blueprint_kind_name(label: str, perspective: str) -> str:
    """"<repository> <Kind> Blueprint": the name of the blueprint of that kind over the repository."""
    return f"{label} {kind_word(perspective)} Blueprint"


def blueprint_display_name(label: str, perspective: str, cluster_name: str, *, sole_root: bool,
                           identifier: str = "") -> str:
    """The displayName RE writes for one candidate blueprint. The top blueprint of a reading, when it
    is the only one, IS the repository's blueprint of that kind and takes the plain name; any other
    cluster keeps its own name beneath the kind, so two clusters never share a display name."""
    base = blueprint_kind_name(label, perspective)
    if identifier:                  # a person's identifier names the second one, not the cluster's root
        return f"{base} \u00b7 {identifier}"
    return base if sole_root else f"{base} · {cluster_name}"


def qualified_name_slot(perspective: str) -> str:
    """The `<slot>` of the LEGACY (#556) form `SolutionBlueprint::<type>::<slug>::<slot>::<cluster>`:
    the kind, spelled as in the display name ("Deployment Blueprint"). Kept ONLY to recognise and adopt
    a blueprint written in that form; nothing is created under it any more."""
    return f"{kind_word(perspective)} Blueprint"


# ── the Egeria identity of a blueprint: KIND + REPOSITORY (architect's ruling, 2026-10-08) ──────────────────
#
# `SolutionBlueprint::<entity_type>::<slug>::<kind>` (kind lower-case, last segment), plus an optional
# `::<identifier>` a PERSON gives a second blueprint of the same kind. The root cluster's name is never in it.

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._\-]{0,63}")


def validate_identifier(raw: str | None) -> str:
    """The identifier as it will be stored: trimmed. None or "" means none given. A given identifier must
    start with a letter or digit and hold only letters, digits, space, '.', '_' and '-' (never a '::'
    separator). Raises ValueError, with the reason, otherwise."""
    if not raw:
        return ""
    text = raw.strip()
    if not text:
        raise ValueError("the identifier is empty")
    if "::" in text:
        raise ValueError("the identifier must not contain '::'")
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError("the identifier may hold letters, digits, space, '.', '_' and '-' only "
                         "(up to 64, starting with a letter or digit)")
    return text


def identity_qualified_name(entity_type: str, slug: str, perspective: str, identifier: str = "") -> str:
    """`SolutionBlueprint::<type>::<slug>::<kind>[::<identifier>]`: no cluster name anywhere in it."""
    base = f"SolutionBlueprint::{entity_type}::{slug}::{kind_key(perspective)}"
    return f"{base}::{identifier}" if identifier else base


def legacy_qualified_names(entity_type: str, slug: str, perspective: str, cluster_name: str) -> list[str]:
    """The two OLDER forms, newest first, that carry the cluster's name: the #556 form (kind slot) and the
    pre-#556 form (perspective slot). Adopt-only: a blueprint found under one is used, never duplicated and
    never created again."""
    return [f"SolutionBlueprint::{entity_type}::{slug}::{qualified_name_slot(perspective)}::{cluster_name}",
            f"SolutionBlueprint::{entity_type}::{slug}::{perspective}::{cluster_name}"]


def identity_property(slug: str, perspective: str, identifier: str = "") -> str:
    """The Egeria `identifier` the way the owner's own blueprints carry it: `<SLUG>-<KIND>`, upper-case with
    hyphens ("EGERIA-GIT-DEPLOYMENT"); a person's identifier, when there is one, follows the kind."""
    def up(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").upper()
    return "-".join(p for p in (up(slug), up(kind_key(perspective)), up(identifier)) if p)


def identifier_needed_sentence(perspective: str, slug: str, holder: str = "") -> str:
    """Said when a second blueprint of a kind is accepted for a repository without an identifier. `holder` is
    the cluster whose CACHE ROW owns the element, read at refusal time; with none the sentence is exactly the
    ruled one (no guess)."""
    held = f" (held by {holder})" if holder else ""
    return f"a {kind_word(perspective)} Blueprint already exists for {slug}{held} \u00b7 give this one an identifier"


def identifier_planned_sentence(perspective: str, slug: str, holder: str) -> str:
    """Said when another blueprint of the kind is accepted and not yet in Egeria: it takes the repository's
    plain identity at the next Publish, so this one needs a person's identifier. Never says "already exists"."""
    return (f"{holder} is accepted as the {kind_word(perspective)} Blueprint for {slug} and takes that name at "
            f"Publish \u00b7 give this one an identifier")


def blueprint_kind_rows(*, label: str, blueprints: list[dict], artifact_count: int,
                        build_files: list[str], logical_unconfirmed: int | None,
                        environment_services: int = 0, environment_linked: int = 0) -> list[dict]:
    """The selector's rows, one per kind. Each says what it is read from and where it stands:

    * Deployment: drawn when the recovery proposed any deployment-reading blueprint
      ("recovered from N artifacts · M units · proposed"), else "not yet drawn".
    * Build: "from <files> · M modules · not yet drawn". Listed, not drawn (a later slice).
    * Logical: "needs your confirmation of K components · not yet drawn" (K is the count of logical
      components with no verdict; `None` when RE cannot say).

    * Environment Deployment: offered ONLY when referenced-only services exist (the repository runs
      other projects' images): "N services run other projects' images · M linked to the repository that
      builds them". Its services are listed under Dependencies · runtime; it is not drawn as a diagram.

    A count RE does not have is not given as zero: the sentence omits it."""
    by_kind: dict[str, list[dict]] = {}
    for bp in blueprints:
        by_kind.setdefault(kind_key(bp.get("perspective", "")), []).append(bp)

    def units(key: str) -> int:
        seen: set[str] = set()
        for bp in by_kind.get(key, []):
            seen.update(bp.get("members") or [])
        return len(seen)

    rows = []
    dep = by_kind.get("deployment", [])
    accepted = sum(1 for bp in dep if (bp.get("verdict") or {}).get("verdict") == "accepted")
    dep_state = ("accepted" if accepted else "proposed") if dep else "not yet drawn"
    rows.append({
        "kind": "deployment", "name": blueprint_kind_name(label, "deployment"), "drawn": bool(dep),
        "state": dep_state, "perspective": "deployment", "units": units("deployment"),
        "artifacts": artifact_count, "blueprints": len(dep), "accepted": accepted,
        "source": (f"recovered from {artifact_count} artifact{'' if artifact_count == 1 else 's'} · "
                   f"{units('deployment')} unit{'' if units('deployment') == 1 else 's'}") if dep
                  else "no deployment reading has been recovered yet"})
    mods = units("build")
    rows.append({
        "kind": "build", "name": blueprint_kind_name(label, "dev"), "drawn": False,
        "state": "not yet drawn", "perspective": "dev", "units": mods, "artifacts": len(build_files),
        "blueprints": len(by_kind.get("build", [])), "accepted": 0,
        "source": (f"from {', '.join(build_files)}" if build_files else "no build file found")
                  + (f" · {mods} module{'' if mods == 1 else 's'}" if mods else "")})
    rows.append({
        "kind": "logical", "name": blueprint_kind_name(label, "logical"), "drawn": False,
        "state": "not yet drawn", "perspective": "logical", "units": units("logical"), "artifacts": 0,
        "blueprints": len(by_kind.get("logical", [])), "accepted": 0,
        "source": (f"needs your confirmation of {logical_unconfirmed} component"
                   f"{'' if logical_unconfirmed == 1 else 's'}") if logical_unconfirmed is not None
                  else "needs your confirmation of its components"})
    if environment_services > 0:
        n = environment_services
        rows.append({
            "kind": "environment", "name": blueprint_kind_name(label, "environment"), "drawn": False,
            "state": "proposed", "perspective": "environment", "units": n, "artifacts": 0,
            "blueprints": 0, "accepted": 0,
            "source": (f"{n} service{'' if n == 1 else 's'} run{'s' if n == 1 else ''} other projects' images · "
                       f"{environment_linked} linked to the repository that builds "
                       f"{'it' if n == 1 else 'them'} · listed under Dependencies · runtime")})
    return rows
