"""Which technologies does a repository's dependency list indicate — and does
Egeria already know about them?

Answers the question catalog's *"Do we already support these dependencies?"*
as a **starting point for people**, not as an answer. Decision (project owner,
2026-09-11): Egeria may hold a starting point that "would still need
corroboration and augmentation by people"; and (2026-09-12) "A now, shaped to
seed B" — the curated mapping lives in RE (`configdata/dependency_support.yaml`)
today, with each entry linked to the Egeria technology type it will later be
promoted into.

**Why a curated mapping and not string matching** — measured 2026-09-12 before
building: against 2,894 distinct dependency names, Egeria's 213 technology
types matched TWO by exact name, and token overlap matched 373 that were nearly
all wrong (`apache_atlas` → Apache Airflow via "apache"). `psycopg2` ↔
PostgreSQL is knowledge, not similarity; the YAML is that knowledge. See the
file's own header.

**Three things a reader must be able to tell apart**, because collapsing them
is this codebase's dominant bug class:

* a dependency that MATCHED a curated technology — "this repo indicates X";
* a dependency that matched NOTHING — "no curated technology corresponds to
  this name", which is almost always "nobody has classified it yet" and is
  never "unsupported";
* a technology whose Egeria type could NOT BE CHECKED — Egeria unreachable —
  which is not the same as the type being absent.

The matcher here is pure and needs no Egeria; the Egeria check is a separate
function so the two failure modes cannot be confused for one another.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_MAPPING_PATH = Path(__file__).resolve().parent.parent / "configdata" / "dependency_support.yaml"


class DependencySupportMappingError(ValueError):
    """The mapping file is malformed. Raised loudly: a mapping that half-loads
    would silently turn every dependency into 'unmatched'."""


@dataclass(frozen=True)
class Technology:
    name: str
    #: Egeria technology-type displayName this promotes into, or None when no
    #: such type exists yet (a promotion candidate for the B step).
    egeria_technology_type: str | None
    patterns: tuple[str, ...]
    ecosystems: frozenset[str] = frozenset()   # empty = all

    def matches(self, dep_name: str, ecosystem: str = "") -> bool:
        if self.ecosystems and ecosystem and ecosystem.lower() not in self.ecosystems:
            return False
        name = (dep_name or "").strip().lower()
        if not name:
            return False
        # Maven `group:artifact` also matches on the bare artifact, so a pattern
        # written as `postgresql` catches `org.postgresql:postgresql`.
        candidates = {name}
        if ":" in name:
            candidates.add(name.rsplit(":", 1)[-1])
        for pat in self.patterns:
            p = pat.lower()
            if p.endswith("*"):
                if any(c.startswith(p[:-1]) for c in candidates):
                    return True
            elif p in candidates:
                return True
        return False


@dataclass
class TechnologyMatch:
    technology: Technology
    #: The dependency names (from this repo) that indicated it.
    dependencies: list[str] = field(default_factory=list)
    #: "present" / "absent" / "unchecked" — whether Egeria has the linked type.
    #: `unchecked` when Egeria could not be reached; `absent` when it was
    #: reached and the type is not there; and for a technology with no
    #: `egeria_technology_type` at all, "no-type" — a promotion candidate.
    egeria_state: str = "unchecked"


@dataclass
class SupportAssessment:
    """One repository's result."""

    matches: list[TechnologyMatch]
    unmatched: list[str]
    total_dependencies: int
    #: "checked" / "unreachable" / "skipped" — did we ask Egeria at all, and
    #: could it answer? Carried at the top so a reader never has to infer it
    #: from the per-match states.
    egeria_check: str
    egeria_check_detail: str = ""

    @property
    def matched_dependency_count(self) -> int:
        return sum(len(m.dependencies) for m in self.matches)

    def as_findings(self) -> list[dict]:
        """Rows for `project_analysis_findings`, kind `dependency_support`.

        One row per matched technology, plus ONE coverage row. Unmatched names
        are NOT persisted one-per-row — a repo with 800 dependencies would
        write 800 rows saying "unclassified" — they are re-derived at read time
        from `project_dependencies` minus the mapping, which is also what lets
        the cross-repo ranked unmatched list exist without a table of its own.
        """
        rows = []
        for m in self.matches:
            rows.append({
                "check_name": "technology",
                "label": m.technology.name,
                "summary": (f"{m.technology.name} — indicated by "
                            f"{', '.join(sorted(m.dependencies)[:6])}"
                            + (f" (+{len(m.dependencies)-6} more)" if len(m.dependencies) > 6 else "")),
                "confidence": 85 if m.egeria_state == "present" else 70,
                "detail": {
                    "dependencies": sorted(m.dependencies),
                    "egeria_technology_type": m.technology.egeria_technology_type,
                    "egeria_state": m.egeria_state,
                },
            })
        rows.append({
            "check_name": "coverage",
            "label": self.egeria_check,
            "summary": (f"{self.matched_dependency_count} of {self.total_dependencies} dependencies "
                        f"indicate {len(self.matches)} curated technolog{'y' if len(self.matches)==1 else 'ies'}; "
                        f"{len(self.unmatched)} have no curated technology yet"),
            "confidence": 100 if self.egeria_check == "checked" else 0,
            "detail": {
                "total_dependencies": self.total_dependencies,
                "matched_dependencies": self.matched_dependency_count,
                "unmatched_dependencies": len(self.unmatched),
                "technologies": len(self.matches),
                "egeria_check": self.egeria_check,
                "egeria_check_detail": self.egeria_check_detail,
            },
        })
        return rows


@lru_cache(maxsize=4)
def load_mapping(path: Path | str | None = None) -> tuple[Technology, ...]:
    """The curated technologies, validated. Cached per path."""
    import yaml

    p = Path(path) if path else _DEFAULT_MAPPING_PATH
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise DependencySupportMappingError(f"mapping file not found: {p}") from exc
    techs = raw.get("technologies")
    if not isinstance(techs, list) or not techs:
        raise DependencySupportMappingError(f"{p}: 'technologies' must be a non-empty list")

    out: list[Technology] = []
    seen: set[str] = set()
    allowed = {"name", "egeria_technology_type", "patterns", "ecosystems"}
    for i, t in enumerate(techs):
        if not isinstance(t, dict):
            raise DependencySupportMappingError(f"{p}: technologies[{i}] is not a mapping")
        unknown = set(t) - allowed
        if unknown:
            raise DependencySupportMappingError(
                f"{p}: technologies[{i}] has unknown keys {sorted(unknown)} — "
                f"a typo here silently drops patterns")
        name = str(t.get("name") or "").strip()
        if not name:
            raise DependencySupportMappingError(f"{p}: technologies[{i}] has no name")
        if name.lower() in seen:
            raise DependencySupportMappingError(f"{p}: technology {name!r} listed twice")
        seen.add(name.lower())
        pats = t.get("patterns")
        if not isinstance(pats, list) or not pats or not all(isinstance(x, str) and x.strip() for x in pats):
            raise DependencySupportMappingError(f"{p}: {name!r} needs a non-empty list of string patterns")
        for x in pats:
            if len(x.strip().rstrip("*")) < 3:
                raise DependencySupportMappingError(
                    f"{p}: {name!r} pattern {x!r} is shorter than 3 characters — "
                    f"that is how `apache` matched Airflow for 105 unrelated packages")
        eco = t.get("ecosystems") or []
        et = t.get("egeria_technology_type")
        out.append(Technology(
            name=name,
            egeria_technology_type=str(et).strip() if et else None,
            patterns=tuple(x.strip() for x in pats),
            ecosystems=frozenset(str(e).lower() for e in eco),
        ))
    return tuple(out)


def clear_cache() -> None:
    load_mapping.cache_clear()


def assess(dependencies: list[dict], *, technologies: tuple[Technology, ...] | None = None,
           egeria_types: dict[str, bool] | None = None,
           egeria_check: str = "skipped", egeria_check_detail: str = "") -> SupportAssessment:
    """Pure: match a repo's dependency rows against the mapping.

    `dependencies` are `project_dependencies` rows (need `dep_name`, optionally
    `ecosystem`). `egeria_types` is {displayName: True} for the types Egeria
    was found to hold — pass None when Egeria was not consulted, and set
    `egeria_check` accordingly so the assessment says which it was.
    """
    techs = technologies or load_mapping()
    by_tech: dict[str, TechnologyMatch] = {}
    unmatched: list[str] = []
    names_seen: set[str] = set()

    for row in dependencies:
        dep = str(row.get("dep_name") or "").strip()
        eco = str(row.get("ecosystem") or "")
        if not dep or dep.lower() in names_seen:
            continue
        names_seen.add(dep.lower())
        hit = False
        for t in techs:
            if t.matches(dep, eco):
                hit = True
                m = by_tech.setdefault(t.name, TechnologyMatch(t))
                m.dependencies.append(dep)
        if not hit:
            unmatched.append(dep)

    for m in by_tech.values():
        t = m.technology
        if not t.egeria_technology_type:
            m.egeria_state = "no-type"
        elif egeria_types is None:
            m.egeria_state = "unchecked"
        else:
            m.egeria_state = "present" if egeria_types.get(t.egeria_technology_type) else "absent"

    return SupportAssessment(
        matches=sorted(by_tech.values(), key=lambda m: (-len(m.dependencies), m.technology.name)),
        unmatched=sorted(unmatched, key=str.lower),
        total_dependencies=len(names_seen),
        egeria_check=egeria_check,
        egeria_check_detail=egeria_check_detail,
    )


def egeria_technology_types_present(names: list[str]) -> tuple[dict[str, bool] | None, str, str]:
    """({displayName: present}, check_state, detail) — asks Egeria once.

    Returns (None, "unreachable", why) when Egeria cannot be reached, so the
    caller records "could not check" rather than "absent". Never raises: a
    survey step must not fail because the catalog was down, and the three-state
    result IS the error handling.
    """
    if not names:
        return {}, "checked", "no linked types to verify"
    try:
        from resource_explorer.config import get_config
        from resource_explorer.surveyors.egeria_tech_type_catalog import EgeriaTechTypeCatalog

        e = get_config().egeria
        # Who asks is the factory's business (Brief I): no credentials passed from here.
        cat = EgeriaTechTypeCatalog(e.platform_url, e.view_server)
        cat.connect()
        have = {t.get("displayName") for t in cat.list_technology_types()}
    except PermissionError:
        raise      # no caller / expired sign-in is a 401, never "catalog unreachable"
    except Exception as exc:  # noqa: BLE001 - the whole point is to report, not raise
        log.warning("dependency_support: Egeria technology-type catalog unreachable: %s", exc)
        return None, "unreachable", f"{type(exc).__name__}: {exc}"
    return {n: (n in have) for n in names}, "checked", f"{len(have)} technology types in catalog"
