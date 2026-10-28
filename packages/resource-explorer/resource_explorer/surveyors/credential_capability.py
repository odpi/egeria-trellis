"""What the connected credential can actually see and do — read back, not re-probed.

The second axis of the launcher gate, beside cost tier. `REPLY-DATABASE-
CREDENTIAL-CAPABILITY-VISIBILITY.md` §7.1, which is the approved design:

    Build it as one axis beside cost tier in the same gate, not as a separate
    flow: a step declares `fetch_cost`, `compute_cost` and
    `requires_capability`, and the launcher shows one combined reason.

This module is the "read the probe" half. It runs nothing and opens no
connection: the `credential_capability` step (built in `#253`, wired in
`#257`) is the only thing that talks to Postgres, and everything here reads
back what that step already stored. Two reasons that split matters:

  * the gate is consulted *before* a step runs, on a resolver path that is
    supposed to be pure (`prerequisite_resolver` "decides; it does not
    execute"). A gate that opened a connection to decide whether to open a
    connection would be the cost it exists to avoid;
  * the probe's own numbers are what the banner and the envelope state
    already show the user (`routes/databases.py::_to_summary`,
    `result_status.MEASURED_WITHIN_CREDENTIAL_SCOPE`). A gate deriving its
    own second opinion would let the launcher and the banner disagree about
    the same credential, which is worse than either being wrong alone.

**"Never probed" is a third answer, and it does not block.** `assess` returns
`known=False` when no probe result has ever been stored for this resource,
and the resolver raises no consent reason for it. That direction is chosen
deliberately and is the same one `step_preconditions._needs_rows` and
`prerequisite_resolver._found_nothing_on_this_snapshot` already take: on OUR
failure to establish something, run the step. Gating on an unmeasured probe
would turn "we have not looked" into "you may not ask", for every database
registered before the probe existed — which is the absence-as-answer failure
in a new place, pointing the other way.

**There is no ordering.** A four-value vocabulary invites a ladder and the
ladder would be wrong: `stats` needs `pg_monitor` membership, `read` needs
`SELECT` grants, and neither implies the other — a monitoring role with no
table grants satisfies `stats` and fails `read`; the `egeria_user` of the
incident that started all this is the reverse on three tables. So each value
is its own predicate over the probe's own fields, and `assess` evaluates
exactly one of them.

**Correction (live-verified by the coordinating session, 2026-09-24/25):**
`stats` does NOT mean "needs `pg_monitor` to see per-table activity/row-count
counters" — that premise, baked into this module (and into
`DATABASE-STEP-CAPABILITY-AUDIT.md`) since `#262`, is wrong. `pg_stat_user_
tables`, `pg_stat_user_indexes` (and, checked while fixing this,
`pg_stat_database`/`pg_stat_bgwriter`/`pg_stat_archiver`/`pg_stat_wal`) are
plain views over catalog state with no ACL predicate at all — confirmed
live against `coco_pharma` as `egeria_user` (not a `pg_monitor` member):
`pg_stat_user_tables` returned all 58 rows, matching an independent
`pg_class`/`pg_namespace` count exactly, with real non-null `n_tup_ins`/
`last_vacuum` values even for schemas `egeria_user` has no `USAGE` grant on.
Those views are `catalog`-tier, the same as `pg_namespace`/`pg_class`
themselves — see `DATABASE-STEP-CAPABILITY-AUDIT.md`'s "Correction" section
for the full per-view table.

What `pg_monitor`/`pg_read_all_stats` actually gate, confirmed the same way
(a second session's query masked to `<insufficient privilege>` in
`pg_stat_activity` for a non-`pg_monitor` role): visibility into OTHER
sessions/connections — `pg_stat_activity`'s query text and state for rows
that are not your own, and (per Postgres's own view definitions, same
mechanism, not independently reproducible in this environment because no
standby was attached) `pg_stat_replication`. `stats` keeps that meaning; the
probe (`stats_role`, driven by `pg_has_role(current_user, 'pg_monitor',
'MEMBER')`) is still the right check for it, and `db_resilience` still
correctly declares `stats` because it reads `pg_stat_replication`. What
changed is which steps' OUTPUT actually depends on that boundary —
`postgres_schema_and_stats`'s row-count/activity display does not, and is
now declared `read` instead (see `survey_definition_adapter.py`).
"""
from __future__ import annotations

import json
import logging

from dataclasses import dataclass

log = logging.getLogger(__name__)

CATALOG = "catalog"
READ = "read"
STATS = "stats"
WRITE = "write"


@dataclass(frozen=True)
class CapabilityAssessment:
    """One step's `requires_capability`, answered against one stored probe."""

    #: What the step declared. `""` when it declared nothing.
    requirement: str = ""
    #: Whether a probe result was available to answer with at all. `False`
    #: means the question is OPEN, not that the answer is "no" — see the
    #: module docstring. `satisfied` is `True` in that case so nothing is
    #: blocked, and `known` is what a caller must check before quoting it.
    known: bool = False
    satisfied: bool = True
    #: The identity the probe connected as, for the sentence the user reads.
    connected_as: str = ""
    #: The fraction behind a `read` verdict — `have` of `of` tables. Both `0`
    #: for the other three values, which are booleans with no denominator.
    have: int = 0
    of: int = 0
    #: One clause, phrased to sit inside a combined proposal sentence beside
    #: a cost-tier clause. Empty when there is nothing to say.
    detail: str = ""

    @property
    def blocks(self) -> bool:
        """Whether this is a real, measured shortfall worth asking about."""
        return bool(self.requirement) and self.known and not self.satisfied

    def as_dict(self) -> dict:
        return {
            "requirement": self.requirement,
            "known": self.known,
            "satisfied": self.satisfied,
            "connected_as": self.connected_as,
            "have": self.have,
            "of": self.of,
            "detail": self.detail,
        }


def stored_probe(registry, entity) -> dict | None:
    """The last `credential_capability` result stored for this resource.

    `None` when the probe has never run, when the resource is not a database,
    or when the registry cannot answer — all three are "not known", which
    `assess` then reports as such rather than as an absence of capability.

    Reads the survey blob rather than a dedicated table, and scans EVERY
    stored survey most-recent-first rather than only the latest, because that
    is exactly what `routes/databases.py::_to_summary` already does to feed
    the visibility banner: a plain schema-only run after the probe ran must
    not hide a still-current capability reading. One behaviour, two readers.
    """
    slug = getattr(entity, "slug", "") or ""
    if not slug:
        return None
    getter = getattr(registry, "get_database_surveys", None)
    if getter is None:
        # A repo/filesystem registry, or a stand-in. Not an error: those
        # resource types declare no `requires_capability` either.
        return None
    try:
        surveys = getter(slug) or []
    except Exception as exc:  # pragma: no cover - registry guard
        log.debug("cannot read surveys for %s: %s", slug, exc)
        return None
    for row in surveys:
        try:
            data = json.loads(row.get("survey_data") or "{}")
        except (ValueError, TypeError):
            continue
        cap = data.get("credential_capability")
        if cap:
            return cap
    return None


def assess(requirement: str, probe: dict | None) -> CapabilityAssessment:
    """Does `probe`'s credential satisfy `requirement`?

    `requirement` `""` (undeclared) and `probe` `None` (never measured) both
    return a non-blocking assessment, for the two different reasons the
    module docstring gives. They are distinguishable by `requirement`/`known`
    rather than folded into one "fine" — a caller that wants to say "this
    step makes no claim" and one that wants to say "nobody has looked yet"
    are asking different questions.
    """
    if not requirement:
        return CapabilityAssessment()
    if not probe:
        return CapabilityAssessment(requirement=requirement, known=False)

    who = probe.get("connected_as") or "(unknown)"

    if requirement == CATALOG:
        # Always true for a role that connected at all — the system catalogs
        # (`pg_namespace`, `pg_class`, `pg_attribute`) are readable regardless
        # of grants, which is precisely the property the probe itself relies
        # on to have a denominator. A stored probe IS the evidence: it could
        # only have produced one by making catalog reads.
        return CapabilityAssessment(
            requirement=CATALOG, known=True, satisfied=True, connected_as=who)

    if requirement == READ:
        # `relation_total`/`relation_select` (renamed from `table_total`/
        # `table_select`, REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.3 —
        # the whole-database figure counts base tables + views + matviews,
        # not tables alone). `probe` can be a survey blob stored before this
        # rename, so the old key is read as a fallback rather than silently
        # reading 0 and reporting "no shortfall" for a perfectly good,
        # merely old-shaped, stored probe.
        total = int(probe.get("relation_total", probe.get("table_total")) or 0)
        got = int(probe.get("relation_select", probe.get("table_select")) or 0)
        if total <= 0:
            # No denominator, so no fraction can be stated. Not a failure:
            # a database whose catalog shows no tables cannot have a SELECT
            # shortfall, and reporting 0-of-0 as a shortfall would raise a
            # proposal about nothing.
            return CapabilityAssessment(
                requirement=READ, known=True, satisfied=True, connected_as=who)
        ok = got >= total
        return CapabilityAssessment(
            requirement=READ, known=True, satisfied=ok, connected_as=who,
            have=got, of=total,
            detail="" if ok else (
                f"needs `read`: connected as {who}, which has SELECT on "
                f"{got} of {total} relation(s) — the rest are in this "
                f"database's catalog but out of this credential's reach"),
        )

    if requirement == STATS:
        # `pg_stat_user_tables`/`pg_stat_user_indexes` are NOT gated by
        # `pg_monitor` — live-verified (module docstring's "Correction").
        # What `pg_monitor` actually restricts is visibility into OTHER
        # sessions' activity (`pg_stat_activity` query text, `pg_stat_
        # replication`), so the wording here describes that, not per-table
        # counters.
        ok = bool(probe.get("stats_role"))
        return CapabilityAssessment(
            requirement=STATS, known=True, satisfied=ok, connected_as=who,
            detail="" if ok else (
                f"needs `stats`: connected as {who}, which is not a member of "
                "`pg_monitor`, so replication status and other sessions' "
                "activity are not fully visible (this does not affect "
                "per-table row-count/activity counters — `pg_stat_user_tables`"
                "/`pg_stat_user_indexes` are visible to any connected role)"),
        )

    if requirement == WRITE:
        ok = bool(probe.get("write_capable"))
        return CapabilityAssessment(
            requirement=WRITE, known=True, satisfied=ok, connected_as=who,
            detail="" if ok else (
                f"needs `write`: connected as {who}, which cannot INSERT "
                "(checked without writing anything)"),
        )

    # An unrecognised value. Say so rather than treating it as satisfied:
    # a typo in a declaration should surface as a question, not disappear.
    return CapabilityAssessment(
        requirement=requirement, known=True, satisfied=False, connected_as=who,
        detail=(f"declares an unrecognised capability requirement "
                f"({requirement!r}) — expected one of catalog/read/stats/write"),
    )


def capability_rfa(slug: str, display_name: str, step_key: str,
                   assessment: CapabilityAssessment) -> dict:
    """The launcher's own RFA payload — §7.1's third choice, "raise the RFA".

    **This is deliberately a DIFFERENT RFA from the one the
    `credential_capability` probe itself raises** (`database_surveyor.
    _create_credential_capability_annotations`), and the difference is the
    whole reason it exists. The probe's RFA is resource-shaped and standing:
    "connected as X: SELECT on 3 of 26 tables — grant SELECT on `coco_ods.*`".
    It is raised once per probe run, names no step, and is true whether or not
    anybody ever asked a question.

    The launcher's is question-shaped and occasional: somebody tried to run
    `postgres_column_profile`, right now, and *that* is what the missing grant
    costs. It names the step, the requirement, and the shortfall that blocked
    it. A database owner reading the probe's RFA learns their grants are
    narrow; reading this one they learn which analysis somebody could not run
    because of it, which is the part that makes it worth acting on.

    Both can be live at once and that is correct — they are not duplicates of
    each other any more than "this door is locked" duplicates "I tried to go
    through this door".

    Returns the payload; raising it is `routes/prerequisites.py`'s job, so
    this module stays pure and testable without a registry.
    """
    return {
        "entity_type": "database",
        "entity_slug": slug,
        "entity_name": display_name or slug,
        "status": "open",
        "summary": (
            f"`{step_key}` needs `{assessment.requirement}` — the credential "
            f"this database is registered with does not have it"),
        "analysis_name": step_key,
        "detail": (
            (assessment.detail or
             f"`{step_key}` declares requires_capability="
             f"{assessment.requirement!r}, unsatisfied by the connected "
             "credential.")
            + " Grant the missing privilege to this credential, or register a "
              "connection on this asset whose credential already has it. Until "
              "then this step can only run within the credential's scope, and "
              "whatever it reports is bounded by that rather than by the "
              "database."
        ),
    }
