"""Sub-surveyor: is this supported by a community, or merely popular?

Answers "is it supported by a community?" from `project_stats` alone — no
fetch, the same read-only relationship CiQualitySurveyor has with its data.

**Why this is not repository_health's community_score.** That score is

    stars/100*20 + forks/20*20 + min(contributors,50)*1.2

which is dominated by attention. Measured across the real catalog on
2026-08-26: `docling_graph` scores **100/100** on it with **four
contributors** — 683 stars and 71 forks max out the popularity terms on their
own. 22 of 59 registered repos have six contributors or fewer. A number that
says "excellent community" about a project one person could strand is the
failure this codebase keeps finding, in the one place it most affects a
decision to depend on something.

So this reports DIMENSIONS and never a single number:

    attention      how many noticed        stars, forks, watchers
    participation  how many contribute     contributor count, bus factor
    channels       where you would ask     discussions, issues, wiki
    responsiveness whether anyone answers  — not measurable from what we hold

Rolling them together is precisely what produced the misleading score, so the
headline names the WEAKEST dimension rather than averaging them. High attention
with low participation is the specific shape worth surfacing, and it gets its
own finding rather than being averaged away.

`responsiveness` is honest about being absent: issue and PR response times are
not collected, and the number of open issues says nothing about whether anyone
replies to them.
"""
from __future__ import annotations

import logging

from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation
from resource_explorer.survey_time import now_utc_iso

log = logging.getLogger(__name__)

STEP = "CommunitySupport"

#: Said once, so the annotation and the persisted finding cannot drift apart.
_NOTHING_TO_ASSESS = ("No repository statistics are recorded, so nothing about "
                             "community support could be assessed — this is not a "
                             "finding that it has none.")

#: Contributor counts below which a project depends on very few people. Not a
#: quality judgement — a four-person project can be excellent — but it is the
#: fact someone deciding whether to depend on it most needs, and the one the
#: popularity-weighted score hides.
_SOLE_MAINTAINER = 1
_SMALL_TEAM = 5

#: Attention high enough that a low contributor count is worth calling out
#: explicitly rather than merely reporting. Chosen from the catalog: the three
#: repos this fires for all score >= 53 on health's community_score.
_NOTABLE_ATTENTION = 200


def _dimension(name: str, label: str, summary: str, *, known: bool = True) -> dict:
    return {
        "check_name": name,
        "label": label if known else "not_established",
        "summary": summary,
        "confidence": 100 if known else 0,
        "detail": {"dimension": name, "known": known},
    }


def assess(stats: dict) -> list:
    """The four dimensions, each judged on its own terms."""
    contributors = stats.get("contributors_count") or 0
    stars = stats.get("stars") or 0
    forks = stats.get("forks") or 0
    watchers = stats.get("watchers") or 0
    out = []

    # ── attention ────────────────────────────────────────────────────────────
    if stars or forks:
        out.append(_dimension(
            "attention",
            "high" if stars >= _NOTABLE_ATTENTION else "moderate" if stars >= 20 else "low",
            f"{stars} star(s), {forks} fork(s), {watchers} watcher(s).",
        ))
    else:
        out.append(_dimension("attention", "", "No stars or forks recorded.", known=False))

    # ── participation ────────────────────────────────────────────────────────
    if contributors:
        if contributors <= _SOLE_MAINTAINER:
            label, note = "sole_maintainer", "One contributor — the project stops if they do."
        elif contributors <= _SMALL_TEAM:
            label, note = "small_team", f"{contributors} contributors — a small group."
        elif contributors <= 20:
            label, note = "team", f"{contributors} contributors."
        else:
            label, note = "broad", f"{contributors} contributors."
        out.append(_dimension("participation", label, note))
    else:
        # Not zero contributors — uncounted. A repo always has at least one.
        out.append(_dimension("participation", "",
                              "Contributor count was not collected.", known=False))

    # ── the divergence, as its own finding ───────────────────────────────────
    # Reported separately because averaging it into a score is exactly what
    # made a four-contributor project read as an excellent community.
    if contributors and stars >= _NOTABLE_ATTENTION and contributors <= _SMALL_TEAM:
        out.append(_dimension(
            "attention_exceeds_participation", "yes",
            f"{stars} stars but {contributors} contributor(s) — widely used, "
            "narrowly maintained. Popularity is not support.",
        ))

    # ── channels ─────────────────────────────────────────────────────────────
    channel_keys = ("has_discussions", "has_issues", "has_wiki")
    collected = [k for k in channel_keys if stats.get(k) is not None]
    channels = [n for n, k in (("discussions", "has_discussions"),
                               ("issues", "has_issues"),
                               ("wiki", "has_wiki")) if stats.get(k)]
    if not collected:
        # None of the flags were collected. "All off" and "never looked" are
        # the same absence in a dict, and only one of them means there is
        # nowhere to ask a question.
        out.append(_dimension("channels", "",
                              "Whether discussions, issues or a wiki are enabled "
                              "was not collected.", known=False))
    else:
        out.append(_dimension(
            "channels",
            "open" if channels else "none",
            (f"Open for participation via {', '.join(channels)}."
             if channels else
             "No discussions, issues or wiki — nowhere obvious to ask a question."),
        ))

    # ── responsiveness ───────────────────────────────────────────────────────
    # Deliberately unmeasured. open_issues is a COUNT, not a latency: a large
    # number can mean an engaged community or an abandoned one, and picking
    # either reading would invent the answer someone actually wants.
    out.append(_dimension(
        "responsiveness", "",
        "Not measurable from what is collected — issue and pull-request "
        "response times are not gathered, and an open-issue count says nothing "
        "about whether anyone replies.",
        known=False,
    ))
    return out


def headline(dimensions: list) -> str:
    """The weakest known dimension, not an average.

    Averaging is what let attention mask participation. A reader deciding
    whether to depend on something needs the weakest link named.
    """
    order = {"sole_maintainer": 0, "none": 1, "small_team": 2, "low": 3,
             "team": 4, "moderate": 5, "open": 6, "broad": 7, "high": 8}
    known = [d for d in dimensions
             if d["detail"]["known"] and d["check_name"] != "attention_exceeds_participation"]
    if not known:
        return "Nothing about community support could be established."
    weakest = min(known, key=lambda d: order.get(d["label"], 99))
    unknown = [d["check_name"] for d in dimensions if not d["detail"]["known"]]
    tail = f"; {', '.join(unknown)} not established" if unknown else ""
    return f"{weakest['check_name']}: {weakest['label']}{tail}"


class CommunitySupportSurveyor(BaseSurveyor):
    """Community support as dimensions, never as one number."""

    def __init__(self, project, registry, surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or now_utc_iso()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        out: list[Annotation] = []
        try:
            slug = self.project.slug
            stats = self.registry.get_latest_project_stats(slug) or {}
            if not stats:
                # Persist the reason, not just annotate it. The annotation says
                # "not established"; the results card reads FINDINGS, so returning
                # without writing one made the card render an absence where a stated
                # reason exists — "we have nothing" instead of "we could not tell, and
                # here is why". Found 2026-08-27 via kedro_kubeflow, which has neither
                # commits nor stats and showed a blank card.
                self.registry.upsert_finding(
                    slug, "community_support",
                    [{"check_name": "community_support", "label": "not_established",
                      "summary": _NOTHING_TO_ASSESS, "confidence": 0,
                      "detail": {"known": False}}],
                    surveyed_at=self._surveyed_at,
                )
                out.append(ClassificationAnnotation(
                    check_name="community_support",
                    summary=_NOTHING_TO_ASSESS,
                    analysis_step=STEP,
                    candidate_classifications=["not_established"],
                    confidence=0,
                    json_properties=StepOutcome(
                        "unverified", cause="no repository statistics to assess").as_row(),
                ))
                return out

            dimensions = assess(stats)
            self.registry.upsert_finding(slug, "community_support", dimensions,
                                         surveyed_at=self._surveyed_at)
            out.append(ClassificationAnnotation(
                check_name="community_support",
                summary=headline(dimensions),
                analysis_step=STEP,
                candidate_classifications=[
                    d["label"] for d in dimensions if d["detail"]["known"] and d["label"]
                ],
                confidence=80,
                json_properties={
                    **{d["check_name"]: d["label"] for d in dimensions},
                    # Each dimension carries its own `known`. Stats were read,
                    # so a dimension nothing established is a real absence in
                    # this repo rather than a gap in what we looked at.
                    **(StepOutcome("recovered", detail={"dimensions": len(dimensions)})
                       if any(d["detail"]["known"] for d in dimensions)
                       else no_signal("no community-support dimension could be established "
                                      "from the statistics read",
                                      known_positive=True,
                                      dimensions=len(dimensions))).as_row(),
                },
            ))
        except Exception as exc:
            log.exception("CommunitySupportSurveyor failed for %s", self.project.slug)
            self._warn(out, str(exc))
        return out
