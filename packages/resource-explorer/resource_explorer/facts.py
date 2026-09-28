"""What is known about a resource, and how well it is known.

Every agent that answers a question about a resource needs the same two things:
the value, and whether the value means anything. Today each one goes to the
tables itself, so an empty table becomes "none found" in the answer — and
"none found" and "we never looked" read identically to whoever asked. That is
the failure this codebase keeps finding in new places, and an LLM narrating raw
rows is the most efficient way yet devised to produce it at scale.

So facts leave this layer already judged. A `Fact` carries a state from
result_status's vocabulary, never a bare value:

    measured         ran, and there is something here
    nothing_found    ran, and there is genuinely nothing — a real zero
    never_run        never ran, so nothing is known either way
    not_established  ran, but this analysis cannot be credited with the result
    partial          ran over part of what it covers

The distinction that matters most is `nothing_found` vs `never_run`. They are
the same number and opposite answers.

Two consumers, one layer. The catalogued questions (docs/dr-egeria/
resource_questions.csv) are a curated index into these facts — each declares
which analyses answer it. Free text reaches the same facts through the existing
agents. Neither should be able to assert something this layer did not.

`can_run` is what makes a follow-up honest: when a fact is `never_run`, the
step keys that would establish it are already known, so "shall I run X?" is
derived rather than guessed.

Deliberately NOT here: any judgement a human has to make. A component recovered
from a repo is a proposal, not a validated part of an architecture — see
`Fact.provenance`. Validation is a Curate-stage act, and when it exists it will
be a distinct provenance rather than a higher confidence, because "a person
agreed" and "the detector was sure" are different claims.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from resource_explorer.resource_types import DEFAULT_RESOURCE_TYPE
from resource_explorer.surveyors.result_status import (
    MEASURED,
    MEASURED_WITHIN_CREDENTIAL_SCOPE,
    NEVER_RUN,
    NO_READER,
    NOT_ESTABLISHED,
    NOTHING_FOUND,
)

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

#: A run happened but covered only part of what the analysis owns. Its own
#: state rather than a flag on `measured`: a partial result is usable, and
#: saying so is different from claiming the analysis completed.
PARTIAL = "partial"

#: Where a fact came from. `recovered` is the important one — a proposal a
#: detector made, which nobody has agreed with yet. Kept separate from
#: confidence on purpose: a high-confidence proposal is still a proposal.
PROVENANCE_MEASURED = "measured"      # a surveyor computed it
PROVENANCE_RECOVERED = "recovered"    # a detector proposed it
PROVENANCE_EGERIA = "egeria"          # materialised from an Egeria annotation
PROVENANCE_HUMAN = "human"            # someone stated or validated it


@dataclass
class Fact:
    """One analysis's contribution to what is known about one resource."""

    analysis_id: str
    state: str
    value: dict = field(default_factory=dict)
    provenance: str = PROVENANCE_MEASURED
    last_run_at: str = ""
    #: Step keys that would establish or improve this fact. Present even when
    #: the state is `measured` — re-running is how a stale fact is refreshed.
    can_run: list = field(default_factory=list)
    #: Why the state is what it is, in words, when there is something to say.
    note: str = ""
    #: The analysis's OWN one-sentence summary, from its headline_reader.
    #:
    #: All 33 analyses with a results reader define one, and until 2026-09-02
    #: nothing on this path called any of them — so the chat answered a
    #: question with a `·`-joined dump of field names while a written sentence
    #: sat one function away. Dan: "should be a summary sentence and maybe
    #: some bullet points with the facts."
    #:
    #: Not invented here: it is written per analysis by the same people who
    #: know what the fields mean, which is the reason it is allowed to be
    #: prose at all.
    headline: str = ""
    #: True when this informs a judgement rather than making one. Whether a
    #: resource REPLACES something you already have is a decision about intent;
    #: no query settles it, so the related-resources fact is offered as
    #: evidence and must not be phrased as the answer.
    evidence_only: bool = False

    @property
    def is_known(self) -> bool:
        """Did we actually look, and get an answer?

        `nothing_found` counts: a measured zero is knowledge. `never_run` and
        `not_established` do not.
        """
        return self.state in (
            MEASURED, NOTHING_FOUND, PARTIAL, MEASURED_WITHIN_CREDENTIAL_SCOPE,
        )

    def as_dict(self) -> dict:
        # destination/destination_basis (SPEC-ACTIONABLE-AND-HONEST.md §3,
        # destinations.py): read-time resolution, no stored field. `value`'s
        # own "checks" list — the {check_name, label, ...} shape most
        # REPO_ANALYSIS_RESULTS_MAP readers return — gets one per row,
        # carrying this Fact's own `state` so a whole-analysis
        # NOT_ESTABLISHED/disagreement (rule (1)) is never masked by a
        # per-row label that happens to look fine. The Fact itself also
        # carries a top-level destination for callers reading it as one
        # finding (a whole_analysis_only analysis, or before any per-check
        # rows are inspected) — check_name == analysis_id, matching how
        # destinations._check_declaration resolves a whole-analysis id.
        from resource_explorer.destinations import annotate_checks, resolve_destination

        value = dict(self.value or {})
        checks = value.get("checks")
        if isinstance(checks, list):
            value["checks"] = annotate_checks(
                self.analysis_id, list(checks), whole_state=self.state,
            )
        destination, destination_basis = resolve_destination(
            self.analysis_id, self.analysis_id, "", self.state,
        )
        return {
            "analysis_id": self.analysis_id, "state": self.state,
            "headline": self.headline,
            "value": value, "provenance": self.provenance,
            "last_run_at": self.last_run_at, "can_run": self.can_run,
            "note": self.note, "is_known": self.is_known,
            "evidence_only": self.evidence_only,
            "destination": destination, "destination_basis": destination_basis,
        }


def _query_hash(question: str) -> str:
    """The same key `/api/query/feedback` votes are recorded against.

    Deliberately identical to `web/routes/query.py`'s inline expression rather
    than a new scheme: a vote on "what is Egeria?" should land in one place
    whether the answer came from RAG or from measurements, otherwise the
    feedback corpus is split by an implementation detail the person voting
    cannot see.
    """
    import hashlib

    return hashlib.sha256((question or "").encode()).hexdigest()[:16]


@dataclass
class Envelope:
    """An answer, with everything needed to state it honestly.

    `answerable` is false when nothing was ever measured. A caller must not
    turn an unanswerable envelope into a negative answer -- that is the whole
    reason this type exists rather than returning the values directly.
    """

    subject: str                       # entity slug
    question: str = ""                 # the question text, when there was one
    question_id: str = ""
    kind: str = ""                     # catalog answering.kind, when catalogued
    facts: list = field(default_factory=list)
    #: Why no answer is possible, when none is. Empty when `answerable`.
    blocked_reason: str = ""
    #: Design §18.3 (docs/multi-resource-questions-design.md): true when this
    #: question is asked at a level BELOW `resource` (container/member/field)
    #: but every fact that answers it comes from an analysis whose own
    #: `target_shape` is `whole_resource_only` — a rollup with nothing
    #: per-member in it. `answerable` stays true (something real WAS
    #: measured, and that is not nothing), but a checkmark on this envelope
    #: would be exactly the "✓ means the mapped analysis ran, not that the
    #: question was answered" failure REVIEW-SURVEY-PANE-285.md's small-
    #: findings list names ("which schemas carry the data" ticked with a
    #: table/column COUNT and no schema named). Consumers gate the checkmark
    #: on `answerable and not level_mismatch`, not on `answerable` alone.
    level_mismatch: bool = False
    #: Why, in words, when `level_mismatch` is true. Empty otherwise.
    level_note: str = ""

    @property
    def answerable(self) -> bool:
        return any(f.is_known for f in self.facts)

    @property
    def can_run(self) -> list:
        """Every step that would improve this answer, deduped, order kept."""
        out: list = []
        for f in self.facts:
            for k in f.can_run:
                if k not in out:
                    out.append(k)
        return out

    def as_dict(self) -> dict:
        return {
            "subject": self.subject, "question": self.question,
            "question_id": self.question_id, "kind": self.kind,
            "answerable": self.answerable,
            "blocked_reason": self.blocked_reason,
            "level_mismatch": self.level_mismatch,
            "level_note": self.level_note,
            "facts": [f.as_dict() for f in self.facts],
            "can_run": self.can_run,
            # Counted here rather than left to each caller, so two agents
            # cannot disagree about whether the same envelope had an answer.
            "known_count": sum(1 for f in self.facts if f.is_known),
            "unknown_count": sum(1 for f in self.facts if not f.is_known),
            # The key `/api/query/feedback` records a vote against, so an
            # answer composed from measurements can be rated exactly like a
            # RAG answer. Computed HERE, not in the browser, so it cannot
            # drift from the query path's own
            # `hashlib.sha256(query.encode()).hexdigest()[:16]`
            # (web/routes/query.py) — two independent implementations of one
            # hash would agree until some question contained a character they
            # encoded differently, and then votes would silently land under a
            # key nothing else uses.
            "query_hash": _query_hash(self.question),
        }


@functools.lru_cache(maxsize=8)
def _analysis_target_shapes(resource_type: str) -> dict:
    """{analysis_id: target_shape} for one resource type, read straight from
    `analysis_catalog.yaml` — no new declaration; every entry already carries
    `target_shape` (default `"whole_resource_only"`). Used by `FactLayer.
    _check_level` to tell a whole-database rollup from a real per-member
    answer (design §18.3). Cached the same way `analysis_catalog_reader`'s
    own loader is — this reads a small, rarely-changing YAML file, not
    per-resource state, so caching by resource_type alone is safe; tests
    that mutate the catalog on disk call `clear_cache()` below."""
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

    return {
        a["id"]: a.get("target_shape", "whole_resource_only")
        for a in get_analyses(resource_type, include_egeria_live=False)
    }


def clear_target_shape_cache() -> None:
    """Testing hook — mirrors every other catalog loader's `clear_cache()`."""
    _analysis_target_shapes.cache_clear()


#: answering.kind values that no amount of surveying will satisfy. Each is a
#: real answer -- "nothing can answer this yet" is useful and true -- but never
#: an answer about the resource.
UNANSWERABLE_KINDS = {
    "gap": "No mechanism exists for this yet — no analysis produces it.",
    "human": "This comes from human-supplied context (the Enrichment form), "
             "not from any survey.",
    "unknown": "This question has not been classified, so how to answer it "
               "is not recorded.",
}

#: Questions answerable from the resource's own recorded state rather than from
#: an analysis. Declared here, keyed by the catalog's own question text.
#:
#: In code rather than as a CSV column because a field NAME is not enough for
#: most of them: "has this been catalogued in Egeria and when" is two fields
#: read together, "is this worth investigating" is a disposition looked up by
#: github_url in a different table, and "any known feedback" is a row count. A
#: column could name a field; it could not express any of those.
#:
#: Each resolver returns (value_dict, state) — and returns NOTHING_FOUND rather
#: than a fabricated negative when the state is genuinely absent, so "no
#: description" cannot become "this repo does nothing".
def _finding(reg, slug: str, kind: str, check: str) -> dict:
    """One named check from an analysis's persisted findings, or {}.

    The point of reading findings rather than the raw table underneath: the
    analysis has already turned numbers into a judgement, with its coverage and
    caveats attached. Re-deriving a verdict here would be a second opinion that
    can disagree with the card the user is looking at.
    """
    for row in (reg.query_findings(slug, kind) or []):
        if row.get("check_name") == check:
            return row
    return {}


def _r_maintained(reg, p) -> tuple:
    """Answered from the FOSS scorecard's own `maintained` check.

    The question was falling through to generic retrieval, which returned commit
    statistics and left the verdict to the reader. The verdict already exists:
    `foss_scorecard._c_maintained` reads archived state and 90-day commit
    counts and says pass/partial/fail with the number behind it.
    """
    row = _finding(reg, p.slug, "foss_scorecard", "maintained")
    if not row:
        return ({}, NEVER_RUN)
    label = (row.get("label") or "").lower()
    if label == "unknown":
        return ({"detail": row.get("summary", "")}, NOT_ESTABLISHED)
    return ({"maintained": label in ("pass", "partial"),
             "verdict": label, "detail": row.get("summary", "")}, MEASURED)


def _r_maintainers(reg, p) -> tuple:
    """Who actually writes the code, from recorded commits.

    Deliberately NOT `project_stats.contributors_count`. That is GitHub's
    all-time figure — everyone who ever landed a commit, including one-off PRs
    from years ago — and quoting it answers "how many people have ever touched
    this", which is not the question. Measured on egeria_git: 63 by that count,
    3 authors in the commits actually recorded, and one of them wrote over half.
    """
    with reg._conn() as conn:
        rows = conn.execute(
            "SELECT author_name, author_email, count(*) AS commits "
            "FROM project_commits WHERE project_slug = ? "
            "GROUP BY author_name, author_email ORDER BY commits DESC",
            (p.slug,),
        ).fetchall()
    if not rows:
        return ({}, NOTHING_FOUND)
    total = sum(r["commits"] for r in rows)

    # Merge identities that share a name. One person commonly commits under
    # two addresses — a GitHub noreply and a personal one — and counting those
    # separately splits them into two apparent maintainers, understating
    # exactly the concentration this question is asked to find. Measured on
    # egeria_git: "Mandy Chessell" under a noreply and a gmail address holds
    # 241 + 132 of 375 commits, which is 99.5% reported as two people at 64%
    # and 35%.
    #
    # Two people really can share a name, so the merge is reported rather than
    # hidden: `emails` says how many addresses were folded together, and a
    # reader who doubts it can look.
    merged: dict = {}
    for r in rows:
        key = (r["author_name"] or "").strip().lower() or (r["author_email"] or "")
        entry = merged.setdefault(key, {"name": r["author_name"], "commits": 0,
                                        "emails": 0})
        entry["commits"] += r["commits"]
        entry["emails"] += 1
    people = sorted(merged.values(), key=lambda e: -e["commits"])
    top = [{**e, "share": round(e["commits"] / total, 3)} for e in people[:5]]
    concentration = _finding(reg, p.slug, "chaoss_metrics", "elephant_factor")
    return ({"people": len(people),
             # Kept alongside, because the difference between them IS the
             # finding when one person commits under several addresses.
             "commit_identities": len(rows),
             "commits": total, "top_authors": top,
             # Named so an answer can say "three authors, one of whom wrote
             # 64%" instead of a bare list that reads as a team of three peers.
             "concentration": concentration.get("label", ""),
             "concentration_detail": concentration.get("summary", "")},
            MEASURED)


def _r_community(reg, p) -> tuple:
    """Community as the analyses report it, not as a contributor count.

    `community_support` reports the WEAKEST dimension rather than averaging,
    and `chaoss_metrics` reports how concentrated authorship is. Both exist
    because a raw count says a four-contributor project has a community of
    four, and a 63-contributor count says a project one person maintains has a
    community of 63. Neither is true in the sense the question means.
    """
    rows = {r["check_name"]: r for r in (reg.query_findings(p.slug, "community_support") or [])}
    elephant = _finding(reg, p.slug, "chaoss_metrics", "elephant_factor")
    if not rows and not elephant:
        return ({}, NEVER_RUN)
    stats = reg.get_latest_project_stats(p.slug) or {}
    known = {k: v.get("label") for k, v in rows.items()
             if v.get("label") and v.get("label") != "not_established"}
    # Each finding already carries a plain-English `summary` (e.g. "Open for
    # participation via ..."); keep it alongside the label so the evidence UI
    # can explain the value instead of showing a bare word like "open".
    summaries = {k: v.get("summary") for k, v in rows.items() if v.get("summary")}
    value = {
        "attention": known.get("attention", ""),
        "attention_detail": summaries.get("attention", ""),
        "participation": known.get("participation", ""),
        "participation_detail": summaries.get("participation", ""),
        "channels": known.get("channels", ""),
        "channels_detail": summaries.get("channels", ""),
        "widely_used_narrowly_maintained":
            known.get("attention_exceeds_participation") == "yes",
        "authorship_concentration": elephant.get("label", ""),
        "detail": elephant.get("summary", ""),
        # Included, but labelled for what it is, so an answer can cite it
        # without implying it measures the active community.
        "github_all_time_contributor_count": stats.get("contributors_count"),
        "stars": stats.get("stars"),
    }

    # The two dimensions can disagree, and when they do the disagreement IS the
    # answer. `participation` is derived from GitHub's all-time contributor
    # count, `authorship_concentration` from the commits actually recorded — so
    # a project can read "broad" and "sole" at once. On egeria_git that is
    # exactly what happens: 63 people have contributed at some point, and one
    # of them wrote over half of the 375 recorded commits.
    #
    # Surfaced rather than resolved here. Picking a winner would be this layer
    # overruling an analysis card the user can also see, and the honest answer
    # to "how active is the community" is that the two measures point different
    # ways for different reasons.
    if value["participation"] in ("broad", "team") and \
            value["authorship_concentration"] in ("sole", "narrow"):
        value["measures_disagree"] = (
            f"{stats.get('contributors_count')} people have contributed at some "
            "point, but authorship of the commits actually recorded is "
            f"{value['authorship_concentration']}. The first counts everyone who "
            "ever landed a commit; the second reflects who writes the code now."
        )
    return (value, MEASURED if known or elephant else NOT_ESTABLISHED)


def _survey_candidates(p) -> tuple:
    """(candidates, reachable) for this resource's technology type.

    The only reader here that leaves the database. Both questions below ask
    what RE itself can run, and that lives in Egeria's authored Survey
    Definitions — there is no local copy to consult. The reader is cached
    (SurveyDefinitionReader's own candidates cache), so this is usually not a
    round trip.

    Unreachable returns (…, False) rather than raising or returning an empty
    list: "no Survey Definition is authored for this" and "we could not ask"
    are opposite answers, and the second must never be reported as the first.
    """
    try:
        from resource_explorer.surveyors.survey_definition_reader import (
            SurveyDefinitionReader,
        )
        reader = SurveyDefinitionReader()
        return (reader.find_candidate_process_guids("Git Repository"), True)
    except Exception as exc:
        log.debug("survey definition candidates unavailable: %s", exc)
        return ([], False)


def _r_which_survey(reg, p) -> tuple:
    """Which Survey Definition to run — answered from what is actually authored.

    Was classified `direct` with nowhere declared to read it from, so it fell
    through to generic retrieval like the maintained/community questions did.
    The answer is the candidate list RE already builds for the Survey tab.
    """
    candidates, reachable = _survey_candidates(p)
    if not reachable:
        return ({"detail": "Egeria could not be reached, so what is authored "
                           "for this technology type is unknown."},
                NOT_ESTABLISHED)
    if not candidates:
        return ({"candidates": []}, NOTHING_FOUND)
    names = [c.get("qualified_name", "").split("::")[-1] for c in candidates]
    return ({"count": len(candidates), "candidates": names,
             # Named rather than ranked: which to run depends on what the
             # caller wants to learn, and a recommendation here would be this
             # layer deciding that on their behalf.
             "note": "Cheapest first is the usual order; the Survey tab shows "
                     "each one's step count and speed tag."},
            MEASURED)


def _r_survey_definition_exists(reg, p) -> tuple:
    """Whether ANY Survey Definition is authored for this technology type.

    The empty case is the whole point of the question — it asks whether the
    absence is a catalog gap — so NOTHING_FOUND here is a real answer, and is
    kept strictly apart from the unreachable case.
    """
    candidates, reachable = _survey_candidates(p)
    if not reachable:
        return ({"detail": "Egeria could not be reached, so whether anything "
                           "is authored is unknown — this is not a finding "
                           "that nothing is."}, NOT_ESTABLISHED)
    return ({"authored": bool(candidates), "count": len(candidates),
             "technology_type": "Git Repository"},
            MEASURED if candidates else NOTHING_FOUND)


def _r_description(reg, p) -> tuple:
    d = (getattr(p, "description", "") or "").strip()
    return ({"description": d}, MEASURED if d else NOTHING_FOUND)


def _r_catalogued(reg, p) -> tuple:
    guid = getattr(p, "egeria_asset_guid", "") or ""
    # The GUID alone says "published at some point"; the publish claims say
    # when. Both, or the answer is half of one.
    published = reg.get_last_published_annotation_types(p.slug) or {}
    when = max(published.values(), default="")
    return ({"catalogued": bool(guid), "egeria_asset_guid": guid,
             "last_published_at": when},
            MEASURED if guid else NOTHING_FOUND)


def _r_surveyed(reg, p) -> tuple:
    when = getattr(p, "last_surveyed_at", "") or ""
    return ({"last_surveyed_at": when, "surveyed": bool(when)},
            MEASURED if when else NOTHING_FOUND)


def _r_disposition(reg, p) -> tuple:
    d = reg.get_disposition(p.github_url) or {}
    verdict = d.get("disposition") or ""
    # `undecided` is the default a resource has before anyone judged it, so it
    # is the absence of an answer, not an answer.
    known = verdict and verdict != "undecided"
    return ({"disposition": verdict, "reason": d.get("reason", ""),
             "decided_at": d.get("decided_at", "")},
            MEASURED if known else NOTHING_FOUND)


def _r_feedback(reg, p) -> tuple:
    rows = reg.list_resource_feedback("repo", p.slug) or []
    return ({"feedback_count": len(rows)}, MEASURED if rows else NOTHING_FOUND)


def _r_license(reg, p) -> tuple:
    stats = reg.get_latest_project_stats(p.slug) or {}
    name = (stats.get("license") or "").strip()
    spdx = (stats.get("license_spdx_id") or "").strip()
    # "Other"/"NOASSERTION" is GitHub saying it could not identify a licence,
    # which is NOT the same as there being none — and for a question about
    # restrictions on use, reporting it as a licence would be the more
    # dangerous of the two errors.
    unidentified = spdx.upper() in ("NOASSERTION", "") or name.lower() == "other"
    return (
        {"license": name, "license_spdx_id": spdx,
         "identified": not unidentified},
        MEASURED if (name and not unidentified) else NOTHING_FOUND,
    )


def _r_existing_use(reg, p) -> tuple:
    """Is this already known to us, and does anything sit alongside it?

    "Existing use in our organization" is answered from what the catalog
    already holds: whether this resource is registered, and what shares its
    group. Anything beyond that would be a guess about people, not resources.
    """
    group = getattr(p, "group_slug", "") or ""
    siblings = []
    if group:
        siblings = [
            x.slug for x in (reg.list_projects_in_group(group) or [])
            if x.slug != p.slug
        ]
    value = {
        "registered": True,
        "group": group,
        "siblings_in_group": len(siblings),
        "sibling_slugs": siblings[:20],
        "surveyed": bool(getattr(p, "last_surveyed_at", "")),
    }
    # Registered at all IS existing use of a kind, so this is measured even
    # with no group -- the zero is about siblings, not about knowing it.
    return value, MEASURED


def _r_related(reg, p) -> tuple:
    """Resources that might overlap with this one.

    Evidence for the judgement, never the judgement. Whether something
    REPLACES what you already have is a decision about intent, and no query
    settles it -- so this returns candidates and says so in the note rather
    than answering a question it cannot.
    """
    lang = (reg.get_latest_project_stats(p.slug) or {}).get("primary_language") or ""
    same_language = []
    if lang:
        for other in reg.list_all():
            if other.slug == p.slug:
                continue
            other_lang = (reg.get_latest_project_stats(other.slug) or {}).get("primary_language")
            if other_lang == lang:
                same_language.append(other.slug)
    group = getattr(p, "group_slug", "") or ""
    siblings = [x.slug for x in (reg.list_projects_in_group(group) or [])
                if x.slug != p.slug] if group else []
    value = {
        "primary_language": lang,
        "same_language_count": len(same_language),
        "same_language": same_language[:15],
        "same_group_count": len(siblings),
        "same_group": siblings[:15],
    }
    return value, MEASURED if (same_language or siblings) else NOTHING_FOUND


def _r_changed_since_survey(reg, p) -> tuple:
    """Which analyses produced something different on their latest run.

    Runs the same comparison Automate's subscriptions use, across every
    analysis with results history, so the answer is the one the change
    detector would give rather than a second opinion about it.
    """
    from resource_explorer.notification_detector import detect_change
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_ANALYSIS_RESULTS_MAP,
    )

    changed, unchanged = [], 0
    for analysis_id in sorted(REPO_ANALYSIS_RESULTS_MAP):
        try:
            res = detect_change(reg, p.slug, analysis_id)
        except Exception:  # one unreadable kind must not lose the rest
            continue
        if res.changed:
            changed.append({"analysis_id": analysis_id, "summary": res.summary})
        else:
            unchanged += 1
    value = {"changed_count": len(changed), "changed": changed[:15],
             "unchanged_count": unchanged,
             "last_surveyed_at": getattr(p, "last_surveyed_at", "") or ""}
    # Nothing changed IS an answer, provided something was comparable at all.
    # With no comparable history, nothing is known either way.
    comparable = bool(changed) or unchanged
    return value, (MEASURED if changed else NOTHING_FOUND) if comparable else NOT_ESTABLISHED


#: question text -> (resolver, subject). The subject is what the fact is
#: ABOUT, used as the analysis_id slot so the envelope reads sensibly.
#:
#: **This is the REPOSITORY table**, and its keys say so — every one of them
#: is the exact text of a repo question ("Is this repository actively
#: maintained?"). It is registered on the repo `ResourceTypeAdapter`
#: (`analysis_results_map`/`state_sources`, 2026-09-20) and reached through
#: `get_adapter(resource_type).state_sources()`, never imported directly by
#: the fact layer any more: consulting it for a database would have matched
#: nothing and reported "nothing is recorded for this yet" about a question
#: it was never able to answer. Design §1.1 item 5 / §13 Phase 0 item 3.
#: Another resource type's table lives with that type's own adapter.
#:
#: **Four of these keys were reworded to cross-type wording 2026-09-21**, in
#: the same commit that reconciled `re/db-questions-csv` (the
#: question-authoring stream) against this table:
#:
#:   _r_description  "What does this repository do?"
#:                -> "What is this resource, and what is it for?"
#:   _r_maintainers  "Who maintains this repository?"
#:                -> "Who owns this resource (accountable owner), and who administers it?"
#:   _r_catalogued   "Has this repository already been catalogued in Egeria and when?"
#:                -> "Has this resource already been catalogued in Egeria, and when?"
#:   _r_license      "Are there any restrictions for use?"
#:                -> "Under what licence or agreement may this resource be used?"
#:
#: `_r_license`'s row SPLIT into two. It maps to the licence row above, which
#: is what the resolver actually does (it reads `stats["license"]`). The other
#: half — "Are there any restrictions for use beyond the licence —
#: classification, zone or terms of use?" — is a new, unbuilt question and
#: gets NO entry here. A question with no state source is a normal state, and
#: pointing a licence-field read at it would answer a governance question with
#: a licence string.
#:
#: `test_every_declared_question_is_in_the_real_catalog` fails loudly on each
#: of these, which is the point: a key matching nothing is a resolver that
#: never runs, and it looks exactly like a question we chose not to answer.
RESOURCE_STATE_SOURCES = {
    "What is this resource, and what is it for?": (_r_description, "description"),
    "Is this repository actively maintained?": (_r_maintained, "foss_scorecard"),
    "Who owns this resource (accountable owner), and who administers it?":
        (_r_maintainers, "project_commits"),
    "How widely adopted and active is the community around this repository?":
        (_r_community, "community_support"),
    "Which Survey Definition should I run — a quick coarse check or the full deep survey?":
        (_r_which_survey, "survey_definitions"),
    "Is there a Survey Definition authored for this resource's technology type at all, or is that a catalog gap?":
        (_r_survey_definition_exists, "survey_definitions"),
    "Has this resource already been catalogued in Egeria, and when?":
        (_r_catalogued, "egeria_catalogue_state"),
    "Has this resource already been surveyed at any tier, and what did earlier signals reveal?":
        (_r_surveyed, "survey_history"),
    "Based on what's already known, is this worth investigating further, or should it be deprioritized?":
        (_r_disposition, "disposition"),
    "Any known feedback?": (_r_feedback, "resource_feedback"),
    "Under what licence or agreement may this resource be used?": (_r_license, "license"),
    "Is there any existing use within our organization?":
        (_r_existing_use, "catalog_presence"),
    "Does it replace or extend something we already have?":
        (_r_related, "related_resources"),
    "How much has changed since the last time this was surveyed — is it worth re-running now?":
        (_r_changed_since_survey, "change_since_last_survey"),
}

#: Facts that are EVIDENCE for a judgement rather than the judgement. The
#: distinction earns its own table because the phrasing has to differ: a fact
#: that answers gets stated, one that informs gets offered.
EVIDENCE_ONLY = {"related_resources"}

#: Owner's gate follow-up (2026-09-27): `_resource_state_fact` never set a
#: `headline` at all — every resource-state-sourced question fell straight
#: to `readEnvelope`'s rung-3 scalar fallback ("no written summary — the
#: figures above are the raw measures"), the exact defect this whole area
#: exists to close for analysis-backed facts, just never extended to these.
#: `{subject: (value, state) -> str}` — a headline function per subject,
#: not per resolver, since `survey_definitions` is shared by two resolvers
#: with different value shapes (`_r_which_survey`'s `candidates` list vs
#: `_r_survey_definition_exists`'s bare `authored`/`count`) and must handle
#: both. Returning "" falls through to the scalar floor unchanged — the
#: same "never fail to report a fact" contract `_headline_for` keeps.
def _h_catalog_presence(value: dict, state: str) -> str:
    siblings = value.get("siblings_in_group") or 0
    group = value.get("group") or ""
    if not group:
        return "Registered, but not assigned to a group — no siblings to compare against."
    if siblings:
        return f"Registered in group {group!r}, alongside {siblings} other resource(s)."
    return f"Registered in group {group!r}, with no other resources alongside it yet."


def _h_related_resources(value: dict, state: str) -> str:
    lang = value.get("primary_language") or ""
    same_lang = value.get("same_language_count") or 0
    same_group = value.get("same_group_count") or 0
    if state == NOTHING_FOUND:
        return "No candidate overlap found — no other resource shares its group or primary language."
    parts = []
    if same_group:
        parts.append(f"{same_group} in the same group")
    if same_lang:
        parts.append(f"{same_lang} sharing its {lang or 'primary'} language")
    return ("Candidate overlap only, not a judgement of replacement: "
            + "; ".join(parts) + ".")


def _h_survey_history(value: dict, state: str) -> str:
    if state == NOTHING_FOUND:
        return "Never surveyed at any tier."
    return f"Last surveyed {value.get('last_surveyed_at', '')}."


def _h_survey_definitions(value: dict, state: str) -> str:
    if state == NOT_ESTABLISHED:
        return value.get("detail") or "Egeria could not be reached, so this is not established."
    if "candidates" in value:
        count = value.get("count") or 0
        if not count:
            return "No Survey Definition is authored for this technology type."
        names = ", ".join(value.get("candidates") or [])
        return f"{count} Survey Definition(s) authored: {names}."
    authored = value.get("authored")
    count = value.get("count") or 0
    tech = value.get("technology_type") or "this technology type"
    return (f"{count} Survey Definition(s) authored for {tech}." if authored
            else f"No Survey Definition is authored for {tech} — a catalog gap.")


def _h_disposition(value: dict, state: str) -> str:
    if state == NOTHING_FOUND:
        return "No disposition recorded yet — undecided."
    verdict = value.get("disposition") or ""
    reason = value.get("reason") or ""
    return f"{verdict.capitalize()}" + (f" — {reason}" if reason else ".")


def _h_change_since_last_survey(value: dict, state: str) -> str:
    if state == NOT_ESTABLISHED:
        return "Nothing comparable yet — no prior survey to measure change against."
    changed = value.get("changed_count") or 0
    unchanged = value.get("unchanged_count") or 0
    if not changed:
        return f"Nothing has changed since the last survey ({unchanged} analysis(es) compared)."
    names = ", ".join(c["analysis_id"] for c in (value.get("changed") or [])[:5])
    return f"{changed} of {changed + unchanged} analysis(es) changed since the last survey: {names}."


_RESOURCE_STATE_HEADLINES: dict[str, "Callable[[dict, str], str]"] = {
    "catalog_presence": _h_catalog_presence,
    "related_resources": _h_related_resources,
    "survey_history": _h_survey_history,
    "survey_definitions": _h_survey_definitions,
    "disposition": _h_disposition,
    "change_since_last_survey": _h_change_since_last_survey,
}

#: Kinds that ARE answerable, but not from analysis results — and not yet
#: readable here. `direct` questions come from a field on the resource
#: (Project.description and the like) and `chart` from a trend series. The
#: catalog names the field in prose ("N/A — direct field (Project.description)")
#: but does not declare it machine-readably, so this layer cannot fetch it
#: without parsing English.
#:
#: Kept apart from UNANSWERABLE_KINDS deliberately: reporting these as "nothing
#: can answer this" would be false, and would hide the largest and cheapest fix
#: in the catalog — 11 of 41 questions, needing a field name each.
UNDECLARED_KINDS = {
    "direct": "Answerable from a field on the resource, but the catalog records "
              "which field only in prose, so it cannot be read automatically yet.",
    "chart": "Answerable from a trend series, which this layer does not read yet.",
}


class FactLayer:
    """Reads what is known about a resource. Never writes, never runs anything."""

    def __init__(
        self,
        registry: "ProjectRegistry | None" = None,
        resource_type: str = DEFAULT_RESOURCE_TYPE,
    ) -> None:
        from resource_explorer.registry import ProjectRegistry

        self._registry = registry or ProjectRegistry()
        self._run_cache: dict = {}
        #: Which resource type's maps this layer reads. Defaults to "repo" —
        #: every caller before 2026-09-20 was implicitly repo-only, because
        #: the maps were imported rather than looked up (design §1.1 item 5).
        self.resource_type = resource_type
        self._maps_cache: dict = {}

    # ── per-resource-type maps, via the adapter ─────────────────────────────
    def _map(self, name: str) -> dict | None:
        """One of the adapter's declared fact maps, or None if this resource
        type does not declare it.

        None and `{}` are different answers and both reach callers: a type
        that declares no results map cannot have facts read from it at all,
        where a type that declares an empty one has a map and nothing in it.
        Conflating them is the bug this whole stream is about.
        """
        if name in self._maps_cache:
            return self._maps_cache[name]
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutorError,
            get_adapter,
        )

        try:
            provider = getattr(get_adapter(self.resource_type), name, None)
        except SurveyDefinitionExecutorError:
            # No adapter registered for this resource type at all. Recorded,
            # not swallowed — and reported to the caller as "not declared",
            # which is true, rather than as an empty map, which would claim
            # we looked.
            log.debug("no survey-definition adapter for resource type %r",
                      self.resource_type, exc_info=True)
            provider = None
        result = provider() if callable(provider) else None
        self._maps_cache[name] = result
        return result

    @property
    def _undeclared_note(self) -> str:
        return (
            f"No analysis results are registered for resource type "
            f"{self.resource_type!r}, so nothing can be read about it here yet "
            f"— a gap in this resource type's fact layer, not a measured "
            f"absence."
        )

    # ── one analysis ────────────────────────────────────────────────────────
    def fact(self, slug: str, analysis_id: str, level: str = "resource") -> Fact:
        results_map = self._map("analysis_results_map")
        source_steps = self._map("analysis_source_steps") or {}

        if results_map is None:
            return Fact(
                analysis_id=analysis_id, state=NOT_ESTABLISHED,
                note=self._undeclared_note,
            )

        # SOURCE steps, not owned ones. `can_run` answers "what would you run to
        # get this answered", which for a derives-from analysis is its source's
        # steps — `architecture_diagram` owns none, and naming none would tell a
        # user with no diagram that nothing can produce one.
        can_run = list(source_steps.get(analysis_id, []))
        run = self._last_run(slug).get(analysis_id, {})
        entry = results_map.get(analysis_id)

        if entry is None:
            # An id with no results reader cannot be read from, whatever it did.
            # egeria_publish is the live example: an action, not an analysis.
            return Fact(
                analysis_id=analysis_id, state=NOT_ESTABLISHED, can_run=can_run,
                note="No results are stored for this analysis — it produces an "
                     "action rather than findings.",
            )

        # A live-read analysis does not depend on a survey step having run:
        # its reader queries a table populated at ingestion. Gating it on run
        # attribution reports "cannot be determined" about data sitting in
        # the table — measured 2026-09-02, api_structure said not_established
        # for a repo with 8,654 symbols. Read first, and only fall through to
        # the run gate if there is genuinely nothing there.
        # NOT `getattr(entry, "live_read")` — the results map is a DERIVED
        # VIEW holding a (results_reader, trend_reader) tuple, not the
        # AnalysisKindResults object. Reading the flag off `entry` silently
        # returned False for everything, so the branch below never ran and the
        # first version of this fix did nothing at all. Read it from the
        # registry that actually carries it.
        _kind = (self._map("analysis_kinds") or {}).get(analysis_id)
        live = bool(getattr(getattr(_kind, "results", None), "live_read", False))
        if live and not run.get("last_run_at"):
            try:
                value = self._read_results(slug, analysis_id, entry)
            except Exception as exc:
                log.debug("live read failed for %s/%s: %s", slug, analysis_id, exc)
                value = {}
            if _has_content(value):
                # Slice 21b follow-up (2026-09-27, live gate on coco_pharma):
                # this used to hardcode state=MEASURED whenever there was any
                # content at all, bypassing `_state_for`'s own `_status`
                # override entirely -- a live-read analysis (every database
                # analysis is one; see DATABASE_ANALYSIS_KINDS's own comment)
                # that attaches `_status={"state": NOT_ESTABLISHED, ...}` for
                # thin coverage (db_relationship_graph/db_classification/
                # grain_determination's own coverage check) had that override
                # silently ignored here, reporting a confident "measured"
                # over data that was 95% unmeasured -- the exact "correct
                # number, wrong label" defect the owner's gate caught: the
                # headline text already said "insufficient signal", the state
                # field the UI actually gates a checkmark on did not agree.
                # `_state_for` falls back to MEASURED (given `_has_content`
                # is already true here) when no `_status` override exists, so
                # this is a strict widening, not a behavior change, for every
                # other live-read analysis that has never set one.
                return Fact(
                    analysis_id=analysis_id, state=self._state_for(value, run), value=value,
                    headline=self._headline_for(analysis_id, slug, level),
                    provenance=self._provenance_for(value), can_run=can_run,
                    note="Read live from data refreshed at ingestion, not from a "
                         "recorded survey run — current regardless of when a survey "
                         "last ran.",
                )

        if not run.get("last_run_at"):
            # The three-state distinction from the Analyses cards, carried
            # through: a repo surveyed by runs too old to record their steps
            # has NOT never-run, we simply cannot say.
            unattributed = run.get("basis") == NOT_ESTABLISHED
            return Fact(
                analysis_id=analysis_id,
                state=NOT_ESTABLISHED if unattributed else NEVER_RUN,
                can_run=can_run,
                note=("This resource was surveyed by runs that predate per-step "
                      "recording, so whether this analysis was among them cannot "
                      "be determined.") if unattributed else
                     "This analysis has never run against this resource.",
            )

        try:
            value = self._read_results(slug, analysis_id, entry)
        except Exception as exc:
            log.debug("results read failed for %s/%s: %s", slug, analysis_id, exc)
            return Fact(
                analysis_id=analysis_id, state=NOT_ESTABLISHED, can_run=can_run,
                last_run_at=run.get("last_run_at", ""),
                note=f"Results could not be read ({type(exc).__name__}).",
            )

        if value is None:
            # No results_reader registered for this analysis at all -- not to
            # be confused with a reader that ran and measured a real zero
            # (NOTHING_FOUND, below). See NO_READER's own docstring.
            return Fact(
                analysis_id=analysis_id, state=NO_READER, can_run=can_run,
                last_run_at=run.get("last_run_at", ""),
                note="This analysis ran; no summary reader exists yet for its "
                     "results.",
            )

        state = self._state_for(value, run)
        return Fact(
            analysis_id=analysis_id, state=state, value=value,
            headline=self._headline_for(analysis_id, slug, level),
            provenance=self._provenance_for(value),
            last_run_at=run.get("last_run_at", ""), can_run=can_run,
            note=self._note_for(state, value, run),
        )

    def _headline_for(self, analysis_id: str, slug: str, level: str = "resource") -> str:
        """The analysis's own summary sentence, or "" — level-aware (slice
        21a). Before this, the SAME resource-level sentence answered every
        question an analysis backs regardless of the question's own
        declared level — found live, owner's question 2026-09-26: "How big
        is this database" (`levels: [resource, container]`) and "Which
        schemas carry the data...?" (`levels: [container]` alone) both
        rendered schema_inventory's identical resource-level headline, so
        the second question ticked ✓ on a line that names schemas but
        classifies none.

        `level` at any value other than "resource" first tries the
        resource type's `analysis_container_headline_map` (a SEPARATE
        provider from `analysis_headline_map` — see that field's own
        docstring in `survey_definition_executor.py` for why the shape of
        the existing, level-agnostic map could not simply change). Only
        `schema_inventory` registers one today; every other analysis, and
        every level that isn't specifically registered, falls straight
        through to the ordinary resource-level reading below — "readers
        that don't declare level support return the resource headline for
        any level," the coordinator's own no-regression rule.

        Best-effort by design: this layer must never fail to report a fact
        because the sentence describing it could not be built.
        """
        if level != "resource":
            container_map = self._map("analysis_container_headline_map") or {}
            container_reader = container_map.get(analysis_id)
            if container_reader is not None:
                try:
                    head = container_reader(self._registry, slug) or {}
                except Exception as exc:
                    log.debug("container headline read failed for %s/%s/%s: %s",
                              slug, analysis_id, level, exc)
                    return ""
                return str(head.get("label") or "")

        kind = (self._map("analysis_kinds") or {}).get(analysis_id)
        reader = getattr(getattr(kind, "results", None), "headline_reader", None)
        if reader is None:
            return ""
        try:
            head = reader(self._registry, slug) or {}
        except Exception as exc:
            log.debug("headline read failed for %s/%s: %s", slug, analysis_id, exc)
            return ""
        return str(head.get("label") or "")

    def _level_specific_headline_exists(self, analysis_id: str, level: str) -> bool:
        """Would `_headline_for(analysis_id, ..., level)` return a GENUINE
        reading at `level`, rather than falling back to the resource
        headline? "Resource" always counts (it IS the base reading); any
        other level requires this exact analysis_id to have its own entry
        in `analysis_container_headline_map` — used by `_check_level` to
        tell "this fact's headline answers the question's own containment
        claim" from "this fact's headline is the resource-fallback text,
        rendered so something shows, but not an answer at this level."
        """
        if level == "resource":
            return True
        container_map = self._map("analysis_container_headline_map") or {}
        return analysis_id in container_map

    def _read_results(self, slug: str, analysis_id: str, entry) -> dict | None:
        """The reader's own result, or `None` when no reader is registered at
        all -- `{}` (falsy but not `None`) still means "a reader ran and
        returned nothing", which `_state_for` must tell apart from this."""
        reader = entry[0] if isinstance(entry, (tuple, list)) else entry
        if not callable(reader):
            reader = getattr(entry, "results_reader", None)
        if not callable(reader):
            return None
        return reader(self._registry, slug) or {}

    @staticmethod
    def _state_for(value: dict, run: dict) -> str:
        """Measured, nothing-found, or partial — decided from the results.

        A results dict that carries its own `_status` wins: the surveyor knew
        more about its own run than this layer can infer. Only when it says
        nothing do we fall back to looking at whether there is content.
        """
        status = (value or {}).get("_status") or {}
        if status.get("state"):
            return status["state"]
        if run.get("partial"):
            return PARTIAL
        if (value or {}).get("partial"):
            return PARTIAL
        return MEASURED if _has_content(value) else NOTHING_FOUND

    @staticmethod
    def _provenance_for(value: dict) -> str:
        detail = (value or {}).get("detail") or {}
        if isinstance(detail, dict) and detail.get("source") == "egeria":
            return PROVENANCE_EGERIA
        # Components are proposals a detector made. Saying so is what keeps a
        # recovered partition from being reported as an established one.
        #
        # `mermaid` is the same caveat under a second key: architecture_diagram
        # draws its picture from the exact same detector proposal
        # architecture_recovery's `components` describes — the project owner's
        # 2026-09-08 question ("are these accepted/published, or just
        # proposed?") landed on a real gap here, not a hypothetical one.
        # Without this, `_renderEnvelopeMarkdown` (index.html) rendered the
        # diagram with no "(proposed by a detector, not yet validated)"
        # caveat at all, because `f.provenance === 'recovered'` never matched
        # a value shaped {mermaid, caption, char_count, ...} with no
        # `components` key of its own.
        if (value or {}).get("components") is not None or \
                (value or {}).get("mermaid") is not None:
            return PROVENANCE_RECOVERED
        return PROVENANCE_MEASURED

    @staticmethod
    def _note_for(state: str, value: dict, run: dict) -> str:
        if state == NOTHING_FOUND:
            return "This analysis ran and found nothing — a measured zero, not a gap in coverage."
        if state == PARTIAL:
            return "This run covered only part of what the analysis reports on."
        if state == MEASURED_WITHIN_CREDENTIAL_SCOPE:
            status = (value or {}).get("_status") or {}
            fraction = status.get("fraction") or ""
            connected_as = status.get("connected_as") or ""
            who = f" as `{connected_as}`" if connected_as else ""
            # When the catalog-only fallback (connection.py's
            # `_catalog_only_fallback`, STATE_CATALOG_ESTIMATE) recovered
            # tables `information_schema` alone would have hidden entirely,
            # say so with the real total — "23 tables, of which `analyst_ro`
            # can read 3" is the honest answer this whole mechanism exists to
            # produce, not a bare "3 tables". Previously led with the
            # parenthetical instead of the headline, and coined "catalog-only
            # ones" in the same sentence it used the term
            # (REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §3) — the
            # reader had to work out that meant "the ones it can't read".
            catalog_only = (value or {}).get("catalog_only_table_count") or 0
            table_count = (value or {}).get("table_count")
            if catalog_only and table_count:
                select_count = table_count - catalog_only
                who_name = f"`{connected_as}`" if connected_as else "this credential"
                # Pluralisation computed here, not baked in as "(s)" — N is
                # always known at render time (§0's pluralisation complaint).
                tables_word = "table" if table_count == 1 else "tables"
                other_verb = "is" if catalog_only == 1 else "are"
                catalog_note = (
                    f"{table_count} {tables_word}, of which {who_name} can read "
                    f"{select_count}; row counts for the other {catalog_only} "
                    f"{other_verb} planner estimates, not exact."
                )
                return catalog_note
            if fraction:
                return (
                    f"Measured within this credential's visibility only — connected{who}, "
                    f"which can read {fraction}. Broader access may reveal more."
                )
            return f"Measured within this credential's visibility only{who} — broader access may reveal more."
        unverified = (value or {}).get("unverified") or []
        if unverified:
            item_word = "item" if len(unverified) == 1 else "items"
            verb = "is" if len(unverified) == 1 else "are"
            return f"{len(unverified)} {item_word} in this result {verb} unverified."
        return ""

    # ── many analyses ───────────────────────────────────────────────────────
    def facts(self, slug: str, analysis_ids: list, level: str = "resource") -> list:
        results = [self.fact(slug, a, level) for a in analysis_ids]
        # The one choke point every consumer of "what is known about this
        # resource" already goes through (resource_facts, bulk_resource_facts,
        # the Questions tab's has_data checks) — so the gaps collection
        # (gaps.py) stays current whenever the page is, with no separate
        # scheduler loop. Best-effort: a resource with no registered project
        # (or a fresh one this call raced with a deletion) must not turn an
        # otherwise-successful facts read into a 500.
        if results:
            from resource_explorer.gaps import record_gaps_for

            try:
                record_gaps_for(self._registry, slug, self.resource_type)
            except ValueError as exc:
                log.debug("gap recording skipped for %s: %s", slug, exc)
        return results

    def answer(self, slug: str, question: dict) -> Envelope:
        """An envelope for one catalogued question.

        The catalog already states how each question is answerable, so routing
        is a lookup rather than an inference — and for 18 of the 41 the correct
        behaviour is to produce no answer about the resource at all.
        """
        answering = question.get("answering") or {}
        kind = answering.get("kind") or "unknown"
        env = Envelope(
            subject=slug, question=question.get("question", ""),
            question_id=question.get("question", ""), kind=kind,
        )
        # A declared resource-state source answers before the kind is
        # consulted: `direct` describes where the answer lives, and once that
        # is declared the question is answerable regardless of the label.
        declared = (self._map("state_sources") or {}).get(question.get("question", ""))
        if declared:
            env.facts = [self._resource_state_fact(slug, *declared)]
            if not env.answerable:
                env.blocked_reason = (
                    "Nothing is recorded for this on this resource yet."
                )
            return env

        for table in (UNANSWERABLE_KINDS, UNDECLARED_KINDS):
            if kind in table:
                env.blocked_reason = table[kind]
                note = (answering.get("note") or "").strip()
                if note:
                    env.blocked_reason += f" ({note[:200]})"
                return env

        ids = answering.get("analysis_ids") or []
        if not ids:
            env.blocked_reason = (
                "This question declares no analysis that answers it, so there is "
                "nothing to read."
            )
            return env
        env.facts = self.facts(slug, ids, self._primary_level(question))
        if not env.answerable:
            env.blocked_reason = (
                "Nothing has been measured for this yet."
                + (f" Run: {', '.join(env.can_run)}." if env.can_run else "")
            )
        else:
            self._check_level(env, question)
        return env

    #: Levels named below "resource" (design §18.3's engine-neutral
    #: vocabulary: database=resource, schema=container, table=member,
    #: column=field). A question carrying only "resource" (the default for
    #: every entry generated before the `Level` column existed) is exempt —
    #: a whole-resource rollup genuinely IS the answer at that level.
    _SUB_RESOURCE_LEVELS = frozenset({"container", "member", "field"})

    def _primary_level(self, question: dict) -> str:
        """The single level to build this question's facts/headlines at
        (slice 21a). A question can declare MORE THAN ONE level — "How big
        is this database" is `[resource, container]`, "Which schemas carry
        the data...?" is `[container]` alone (design §18.3's CSV column).

        `resource` wins when it is one of the declared levels: until the
        scope model (slice 19) gives sub-resource levels their own
        navigable screen, every question is asked from the whole-resource
        page, so a question that declares BOTH is read as "the
        whole-resource summary, which also happens to be answerable
        per-container elsewhere" — "How big"'s own headline is unchanged by
        this slice, per the coordinator's own gate. A question with NO
        resource-level declaration has no resource reading to fall back to
        at all, so it gets its own (first) sub-resource level instead.
        """
        levels = question.get("levels") or ["resource"]
        if "resource" in levels:
            return "resource"
        for lv in levels:
            if lv in self._SUB_RESOURCE_LEVELS:
                return lv
        return "resource"

    #: Mirrors `app.js`'s `scalarMeasures()` closely enough to answer one
    #: question — would rung 3 of `readEnvelope` find anything to say at
    #: all — never a general-purpose renderer. Both must be kept in step by
    #: hand; there is no shared source between a Python backend and a
    #: browser-side fallback formatter.
    _SCALAR_FALLBACK_MAX_LEN = 60

    def _renders_text(self, fact: "Fact") -> bool:
        """Would ANYTHING on this fact reach the screen — `readEnvelope`'s
        rung 1 (`headline`), rung 2 (`value.detail`/`summary`/`description`
        prose), or rung 3 (`scalarMeasures()`'s last-resort `key value`
        dump)? Slice 17c's own trigger: `db_resilience` ties four nested
        dicts (`replication`/`wal_archiving`/`backup_tool_signals`/
        `clustering`) together with no top-level scalar anywhere, so rung 3
        — which explicitly skips every list/object field by design — can
        NEVER produce a line for it, structurally, on every run, not only
        the runs the gate happened to catch live. A known fact with nothing
        renderable is not an answer; the checkmark it sits under is the one
        the whole fact/envelope layer exists to keep honest.

        `NOTHING_FOUND` gets its own rung, ahead of headline/prose/scalar,
        matching `readEnvelope`'s own ordering (`app.js`): a fact whose
        state is `NOTHING_FOUND` and carries no headline/prose still
        renders a synthesized sentence there — "`<analysis_id>` ran and
        found nothing." Missing this case here produced a genuine
        contradiction live (`coco_pharma`, 2026-09-26): a card showed BOTH
        that synthesized sentence AND "This ran; no summary reader exists
        yet for its results.", because `_renders_text` (not knowing about
        `readEnvelope`'s special case) concluded nothing rendered and
        `_check_level` added its own note on top of an answer that, in
        fact, already rendered one. A measured zero is knowledge (see
        `Fact.is_known`'s own docstring) and must not ALSO be treated as
        "nothing to show."
        """
        if (fact.headline or "").strip():
            return True
        if fact.state == NOTHING_FOUND:
            return True
        value = fact.value or {}
        for key in ("detail", "summary", "description"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return True
        for k, v in value.items():
            if v is None or v == "" or k == "verdict":
                continue
            if isinstance(v, (dict, list)):
                continue
            if len(str(v)) > self._SCALAR_FALLBACK_MAX_LEN:
                continue
            return True
        return False

    def _check_level(self, env: "Envelope", question: dict) -> None:
        """Withhold the checkmark (design §18.3) when nothing that actually
        reaches the screen answers this question — either because nothing
        renders at ANY level, or because the question is asked below
        `resource` level and nothing names an item at that level.

        First cut of this gate (slice 17) bound the sub-resource-level check
        to `target_shape` — a static catalog declaration of what an
        analysis is CAPABLE of producing. Live gate on `coco_pharma`
        (2026-09-26) found that wrong: "Which schemas carry the data"
        stayed ticked with the rendered answer reading "table count 56 ·
        column count 427" — no schema named anywhere — because
        `schema_inventory` declares `target_shape: single_container` (it
        does store one row per table, schema_name and all) even though
        nothing renders that breakdown. Slice 17b's fix bound the
        sub-resource check to whether a known fact produced a `headline`.

        The SAME gate on the very next screen (still 2026-09-26) found the
        identical defect one level up: "Is this database a primary or a
        replica..." — a plain `resource`-level question, exempt from the
        sub-resource check entirely — showed a ✓ with NO answer text at
        all, because `db_resilience` has no `headline_reader` and its
        value is four nested dicts, which `scalarMeasures()`'s fallback
        (rung 3) skips by design. `target_shape`-vs-`headline` was never
        the right axis to generalize; "does the checkmark's own fact
        produce a line of text, at any level" is. `_renders_text` answers
        that directly, so this method now runs it FIRST and unconditionally
        (`levels` no longer gates whether the check happens at all — only
        whether the stricter member-naming rule on top of it applies).

        `target_shape` still decides what the NOTE says once a sub-resource
        question additionally fails the headline-specific check, because
        "nothing to show" (`whole_resource_only`) and "rows exist, no
        reader shows them" (anything else) point different people at
        different follow-ups — the second is a live pointer at slice 22's
        per-schema view.

        Deliberately conservative in the same way as before: only ids that
        actually CONTRIBUTED a known fact are checked, and one fact with
        renderable text (rung 1-3) is enough to clear the first check; one
        with a headline specifically (rung 1) is enough to clear the
        sub-resource one.
        """
        known = [f for f in env.facts if f.is_known]
        if not known:
            return
        if not any(self._renders_text(f) for f in known):
            env.level_mismatch = True
            env.level_note = (
                "This ran; no summary reader exists yet for its results."
            )
            return

        levels = question.get("levels") or ["resource"]
        sub_levels = [lv for lv in levels if lv in self._SUB_RESOURCE_LEVELS]
        if not sub_levels:
            return
        # Slice 21a: a question whose PRIMARY level (see `_primary_level`) is
        # still "resource" — because it also declares `resource` alongside a
        # sub-level, like "How big is this database" ([resource, container])
        # — keeps the pre-existing rule: any non-empty headline clears it,
        # since a whole-database rollup genuinely does answer that question.
        # A question with NO resource fallback at all — "Which schemas carry
        # the data...?" is `[container]` alone — has nothing to fall back to,
        # so its headline must have come from a GENUINE level-specific
        # reader, not `_headline_for`'s own resource-headline fallback (which
        # exists so a reader with no level support keeps rendering
        # something, not so that fallback text can satisfy a level-only
        # question's own checkmark). Otherwise an analysis that ships a
        # container reader for another question, but not this one's own
        # analysis_id, would tick on text that never answered this
        # question's own containment claim at all.
        primary_level = self._primary_level(question)
        if primary_level == "resource":
            if any((f.headline or "").strip() for f in known):
                return
        else:
            if any(
                (f.headline or "").strip()
                and self._level_specific_headline_exists(f.analysis_id, primary_level)
                for f in known
            ):
                return
        known_ids = [f.analysis_id for f in known]
        shapes = _analysis_target_shapes(self.resource_type)
        capable_ids = [
            aid for aid in known_ids
            if shapes.get(aid, "whole_resource_only") != "whole_resource_only"
        ]
        env.level_mismatch = True
        if capable_ids:
            env.level_note = (
                f"Answered, but not at {'/'.join(sub_levels)} level — "
                + (f"{capable_ids[0]} stores per-{sub_levels[0]} rows"
                   if len(capable_ids) == 1
                   else f"{', '.join(capable_ids)} store per-{sub_levels[0]} rows")
                + f", but no reader shows them yet."
            )
        else:
            env.level_note = (
                "Answered only as a whole-resource rollup — this question is "
                f"asked at {'/'.join(sub_levels)} level and "
                + (f"{known_ids[0]} names no {sub_levels[0]}."
                   if len(known_ids) == 1
                   else f"none of {', '.join(known_ids)} name one.")
            )

    # ── internals ───────────────────────────────────────────────────────────
    def _resource_state_fact(self, slug: str, resolver, subject: str) -> Fact:
        """A fact read from the resource's own state, not from an analysis.

        `can_run` is empty on purpose: no survey step establishes these. Saying
        "run X to find out" when nothing would change the answer is the same
        false offer the envelope exists to prevent, one step further on.
        """
        project = self._registry.get(slug)
        if not project:
            return Fact(subject, NOT_ESTABLISHED,
                        note=f"No resource named {slug!r} is registered.")
        try:
            value, state = resolver(self._registry, project)
        except Exception as exc:
            log.debug("resource-state resolver failed for %s/%s: %s", slug, subject, exc)
            return Fact(subject, NOT_ESTABLISHED,
                        note=f"Could not be read ({type(exc).__name__}).")
        headline = self._resource_state_headline(slug, subject, value, state)
        return Fact(
            subject, state, value=value, headline=headline, provenance=PROVENANCE_MEASURED,
            evidence_only=subject in EVIDENCE_ONLY,
            note=("Nothing is recorded for this — a real absence, not an "
                  "unrun analysis." if state == NOTHING_FOUND else ""),
        )

    @staticmethod
    def _resource_state_headline(slug: str, subject: str, value: dict, state: str) -> str:
        """The resource-state fact's own summary sentence, or "" — best
        effort, mirroring `_headline_for`'s own "must never fail to report a
        fact because the sentence could not be built" contract."""
        headline_fn = _RESOURCE_STATE_HEADLINES.get(subject)
        if headline_fn is None:
            return ""
        try:
            return headline_fn(value, state) or ""
        except Exception as exc:
            log.debug("resource-state headline failed for %s/%s: %s", slug, subject, exc)
            return ""

    def _last_run(self, slug: str) -> dict:
        """{analysis_id: {last_run_at, basis, partial}} — one query per resource.

        Reuses get_analysis_last_run, which credits an analysis for running as
        part of a SURVEY, not only via its own Run button. Reading only the
        latter is what made every analysis on every repo report never-run.
        """
        if slug in self._run_cache:
            return self._run_cache[slug]
        # `self.resource_type`, not a literal "repo". `get_analysis_last_run`
        # has always taken an entity_type; passing "repo" for a database
        # looked up activity rows for a repository with the database's slug
        # and found none, so every database analysis reported never-run.
        # Sixth `repo` hardcode, found by a test rather than by the design's
        # §1.1 sweep.
        raw = self._registry.get_analysis_last_run(self.resource_type, slug)
        unattributed = raw.pop("__unattributed_surveys__", {}).get("count", 0)
        out = {
            k: {
                "last_run_at": v.get("last_run_at", ""),
                "partial": bool(v.get("last_run_partial")),
                "basis": MEASURED if v.get("last_run_at") else NEVER_RUN,
            }
            for k, v in raw.items()
        }
        if unattributed:
            # Recorded per-analysis so `fact()` can tell "never ran" from
            # "cannot say", without every caller re-deriving it.
            out.setdefault("__unattributed__", {"count": unattributed})
        self._run_cache[slug] = _WithDefault(out, unattributed)
        return self._run_cache[slug]


class _WithDefault(dict):
    """Missing analyses report not_established when unattributed surveys exist.

    A repo whose only surveys predate step recording has no row for ANY
    analysis, and every one of them is "cannot say" rather than "never ran".
    Expressing that as a dict default keeps the distinction in one place.
    """

    def __init__(self, data: dict, unattributed: int) -> None:
        super().__init__(data)
        self._unattributed = unattributed

    def get(self, key, default=None):  # type: ignore[override]
        if key in self:
            return dict.__getitem__(self, key)
        if self._unattributed:
            return {"last_run_at": "", "partial": False, "basis": NOT_ESTABLISHED}
        return default if default is not None else {}


def _has_content(value) -> bool:
    """Is there anything here beyond the envelope keys?

    `_status`, `surveyed_at` and `detail` describe the run, not the finding. A
    results dict carrying only those has measured nothing, and counting them as
    content is how an empty result came to render as a populated one.
    """
    envelope = {"_status", "surveyed_at", "detail", "scoped_to", "run_outcomes"}
    if isinstance(value, dict):
        return any(
            _has_content(v) for k, v in value.items() if k not in envelope
        )
    if isinstance(value, (list, tuple, set)):
        return any(_has_content(v) for v in value)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return value is not None
