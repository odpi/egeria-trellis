"""The resource-type vocabulary, in one place.

Before this module the tuple `("repo", "database", "filesystem")` was
re-declared in at least six modules under five different names
(`RESOURCE_TYPES`, `RESOURCE_KINDS`, `_KNOWN_RESOURCE_TYPES`,
`_ALL_RESOURCE_TYPES`, and two bare literals). Six copies did not give six
chances to be right; adding a fourth resource type meant finding all of them,
and nothing said where they were. `docs/multi-resource-questions-design.md`
§1.3 records the count and §13 Phase 0 item 4 asks for this module.

**Two constants, not one, and the difference is load-bearing.**

`RESOURCE_TYPES` is the *vocabulary* — every resource type the project has
decided to talk about, including the two that nothing surveys yet. The design
doc (§7, §8) names `dataset` (a published dataset descriptor on a portal) and
`model` (an AI model repository) as real resource types with authored question
sets ahead of any surveyor, so a question row tagged `dataset` must generate a
`dataset_questions` catalog key rather than being silently dropped as a typo.
That is what this constant is for.

`SURVEYED_RESOURCE_TYPES` is the *subset that has a registry table, a
`ResourceTypeAdapter` and an analysis catalog today*. Call sites that validate
an `entity_type` coming in off the wire, iterate registry-backed inventories,
or search per-type analysis catalogs want this one — widening them to the full
vocabulary would turn a clean 422 into a lookup against a table that does not
exist. It is derived from `RESOURCE_TYPES` rather than re-declared, so the two
cannot disagree about spelling or order.

Promoting a type from vocabulary to surveyed is a one-line move here, plus the
adapter and catalog section that earn it.
"""
from __future__ import annotations

#: Every resource type this project's question catalog, design documents and
#: Egeria mappings recognise. Ordered oldest-first; `dataset` and `model` were
#: added 2026-09-20 with no surveyor behind them, deliberately (design §13
#: Phase 0 item 4: "added now so nothing else hardcodes three").
RESOURCE_TYPES: tuple[str, ...] = (
    "repo",
    "database",
    "filesystem",
    "dataset",
    "model",
    # Open data OD1 (DESIGN-OPEN-DATA-SEARCH-AND-SURVEY §9, §11): the dataset, the portal it is found on, and the
    # directory of portals. Registered, not surveyed (see REGISTERED_RESOURCE_TYPES).
    "open_data",
    "open_data_portal",
    "portal_directory",
)

#: The types with a registered `ResourceTypeAdapter`, a registry table and an
#: `analysis_catalog.yaml` section. Use this for input validation, inventory
#: iteration and catalog lookups — see the module docstring for why it is not
#: the same list.
_SURVEYED = {
    "repo",
    "database",
    "filesystem",
}
SURVEYED_RESOURCE_TYPES: tuple[str, ...] = tuple(
    t for t in RESOURCE_TYPES if t in _SURVEYED
)

#: The kinds a registry table holds that nothing surveys yet (OD1: registration, search and import only). They are
#: NOT in SURVEYED_RESOURCE_TYPES, which claims a `ResourceTypeAdapter` and an analysis catalog section: adding
#: them there would make `*` in the question catalog expand to kinds with no questions and would fail
#: `test_every_surveyed_type_has_a_registered_adapter`. OD2 promotes `open_data` when its scout earns it.
REGISTERED_ONLY_RESOURCE_TYPES: tuple[str, ...] = ("open_data", "open_data_portal", "portal_directory")

#: The kinds a work list's members may be: the surveyed ones plus `open_data` (a portal and a directory are
#: sources, not things a person works through, so they are not members).
MEMBER_RESOURCE_TYPES: tuple[str, ...] = SURVEYED_RESOURCE_TYPES + ("open_data",)

#: The value every pre-multi-resource call site assumed. Kept as a named
#: constant so "this defaults to repo" is greppable rather than a bare string
#: literal in a dozen signatures.
DEFAULT_RESOURCE_TYPE = "repo"

#: The CSV cell meaning "this question applies to every resource type", per
#: **Decision (project owner, 2026-09-20)** in design §1.1: the `Resource
#: Types` column is `;`-separated, `*` for all.
ALL_RESOURCE_TYPES_MARKER = "*"

#: The separator inside a `Resource Types` CSV cell. Same as the `Purposes`
#: column's, and for the same reason: one validated column fails loudly where
#: one column per type would give every typo'd header a chance to become a
#: phantom Perspective (see `scripts/csv_to_question_catalog_yaml.py`'s
#: `NON_PERSPECTIVE_COLUMNS`).
RESOURCE_TYPES_SEPARATOR = ";"


def parse_resource_types(raw: str) -> list[str]:
    """Parse one `Resource Types` CSV cell into resource-type names.

    `*` expands to the kinds with an adapter (`SURVEYED_RESOURCE_TYPES`), not to every name in the vocabulary
    (`dataset` and `model` stay valid NAMES but nothing surveys them, and the question-catalog generator refuses a
    row that names one). An empty cell yields
    `[DEFAULT_RESOURCE_TYPE]` — every row authored before the column existed
    is a repository question, and the backfill says so explicitly, but a row
    added later with the cell left blank must not vanish from every catalog.

    Raises ValueError on an unknown type rather than dropping it: a typo'd
    `databse` that silently produced no catalog key would look exactly like a
    resource type nobody has authored questions for, which is the distinction
    this whole stream exists to preserve.
    """
    text = (raw or "").strip()
    if not text:
        return [DEFAULT_RESOURCE_TYPE]
    values = [
        v.strip() for v in text.split(RESOURCE_TYPES_SEPARATOR) if v.strip()
    ]
    if ALL_RESOURCE_TYPES_MARKER in values:
        # `*` is the kinds that have an adapter, not the whole vocabulary (owner ruling 2026-10-22,
        # REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22.md §1.1): a section for a kind nothing can survey would be
        # a list of questions nobody can answer.
        return list(SURVEYED_RESOURCE_TYPES)
    unknown = [v for v in values if v not in RESOURCE_TYPES]
    if unknown:
        raise ValueError(
            f"unknown Resource Type(s) {unknown}; valid values are "
            f"{list(RESOURCE_TYPES)} or {ALL_RESOURCE_TYPES_MARKER!r} for all. "
            f"Fix the CSV, or add the type to RESOURCE_TYPES in "
            f"resource_explorer/resource_types.py if the project genuinely "
            f"gained one."
        )
    return list(dict.fromkeys(values))
