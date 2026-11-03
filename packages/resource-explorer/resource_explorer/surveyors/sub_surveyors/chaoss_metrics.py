"""Sub-surveyor: CHAOSS community-health metrics, over commit authorship.

CHAOSS (Community Health Analytics in Open Source Software) is a set of named,
defined metrics rather than a score, which suits this codebase: each one is
reported on its own terms and none are averaged together.

**The metric that justifies the whole module is the Elephant Factor** — the
smallest number of contributors who between them account for half the commits.
`project_contributor_stats` has held the per-author commit counts needed to
compute it for thirty repositories and nothing has ever read them. Measured
across the catalogue on 2026-08-26, it separates projects that every other
signal here calls the same:

    deep_causality       5 authors, 2791 commits, elephant factor 1  (98% one person)
    opea_project…        1 author,   493 commits, elephant factor 1  (100%)
    egeria_git           3 authors, 1761 commits, elephant factor 1  (64%)
    polaris             49 authors, 1488 commits, elephant factor 2
    sqlglot             79 authors, 1429 commits, elephant factor 3
    openmetadata        72 authors, 2318 commits, elephant factor 8
    kafka              113 authors,  758 commits, elephant factor 15

repository_health's community_score rates deep_causality 100/100. CommunitySupport
already improves on that by reporting the contributor COUNT, but a count still
calls deep_causality a five-person project. The distribution is what says one
person wrote 98% of it, and that is the fact someone deciding whether to depend
on it most needs.

**Why this reads project_commits and not project_contributor_stats.** The
latter looks like the natural source — it holds per-author commit counts by
period — but its "periods" are nested trailing windows (30d, 90d, 365d, all
ending today), not sequential ones. Built on it, this module double-counted
every recent commit once per window, and its turnover metric compared the 30-day
window against the 90-day window that CONTAINS it: first-time contributors came
out as zero for every repository in the catalogue, and kafka, sqlglot, milvus
and openmetadata were all labelled "shrinking" for purely arithmetic reasons.
project_commits holds the individual commits with their timestamps, so periods
can be cut disjointly and each commit counted once.

The window is roughly 90 days deep, which is what is collected — shorter than
CHAOSS intends for authorship concentration, so every finding states the span
and the commit count it was computed over rather than presenting itself as an
all-time figure.

**Organizational diversity is reported only when it can be.** 72% of the
author emails in the catalogue are `users.noreply.github.com` or `gmail.com` —
no employer is recoverable from either. Where most authorship is unattributable
the metric says so rather than reporting the diversity of the minority that
happens to use a corporate address, which would systematically overstate it.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation

log = logging.getLogger(__name__)

STEP = "ChaossMetrics"

#: Said once, so the annotation and the persisted finding cannot drift apart.
_NOTHING_TO_ASSESS = ("Neither commit history nor repository stats "
                             "are recorded, so no CHAOSS metric could be computed — "
                             "this is not a finding about the community.")

#: Email domains that identify a person's host, not their employer. A metric
#: built on the rest would describe only the minority who commit from a
#: corporate address.
_PERSONAL_DOMAINS = {
    "users.noreply.github.com", "gmail.com", "googlemail.com", "outlook.com",
    "hotmail.com", "yahoo.com", "protonmail.com", "proton.me", "icloud.com",
    "me.com", "qq.com", "163.com", "126.com", "mail.ru", "yandex.ru",
    "web.de", "gmx.de", "gmx.net", "fastmail.com", "hey.com", "pm.me",
}

#: Below this share of attributable authorship, organizational diversity is
#: not reported as a number. Set from the catalogue: 72% of author emails are
#: personal, so a naive reading would describe barely a quarter of the work.
_MIN_ATTRIBUTABLE = 0.40

#: Elephant-factor bands. 1 means one person could take half the project's
#: history with them; the top of the measured catalogue is 15.
_SOLE = 1
_NARROW = 3
_BROAD = 8


def elephant_factor(commits_by_author: dict) -> int | None:
    """Fewest contributors accounting for at least half of all commits.

    CHAOSS also calls the same quantity the Contributor Absence Factor, from
    the other direction: how many would have to leave before half the project's
    history has no author left behind it.

    None when there are no commits — not 0, which would read as "nobody holds
    any of it" rather than "we counted nothing".
    """
    total = sum(commits_by_author.values())
    if total <= 0:
        return None
    running = 0
    for i, (_, n) in enumerate(Counter(commits_by_author).most_common(), 1):
        running += n
        if running * 2 >= total:
            return i
    return len(commits_by_author)


def _band(factor: int) -> tuple:
    if factor <= _SOLE:
        return "sole", "One contributor accounts for half of all commits."
    if factor <= _NARROW:
        return "narrow", f"{factor} contributors account for half of all commits."
    if factor <= _BROAD:
        return "moderate", f"{factor} contributors account for half of all commits."
    return "broad", f"{factor} contributors account for half of all commits."


def _metric(name: str, label: str, summary: str, *, known: bool = True,
            value=None, detail: dict | None = None) -> dict:
    return {
        "check_name": name,
        "label": label if known else "not_established",
        "summary": summary,
        "confidence": 100 if known else 0,
        "detail": {"metric": name, "known": known, "value": value,
                   **(detail or {})},
    }


def _day(ts) -> str:
    return str(ts or "")[:10]


def assess(commits: list, stats: dict, all_time=None) -> list:
    """CHAOSS metrics from individual commits, each on its own terms.

    `commits` are rows of (author_email, author_name, committed_at) — one per
    commit, so each is counted exactly once and periods can be cut disjointly.

    `all_time` is the stored all-time contributor list (`contributors.contributors(..., "all_time")`). When it
    has rows, authorship concentration (the elephant factor) is computed from it: GitHub's contributions per
    linked account, bots left out. The old derivation from `commits` stays only as the fallback when no list is
    stored, and says so ("from git authors, 90-day fetch").
    """
    from resource_explorer import contributors as contrib

    out: list = []

    commits_by_author: Counter = Counter()
    dated: list = []
    for r in commits:
        who = (r.get("author_email") or r.get("author_name") or "").strip().lower()
        if not who:
            continue
        commits_by_author[who] += 1
        day = _day(r.get("committed_at"))
        if day:
            dated.append((day, who))

    # ── elephant factor ──────────────────────────────────────────────────────
    listed = all_time is not None and bool(getattr(all_time, "rows", None))
    humans = {f"{r.id}:{r.name}": r.commits for r in all_time.rows} if listed else {}
    factor = elephant_factor(humans) if listed else elephant_factor(commits_by_author)
    if listed and factor is not None:
        label, note = _band(factor)
        # The stored non-bot total (the same denominator every contributor surface uses), so "the single largest
        # holds" here and Q-0003's top share are one number.
        total = int(all_time.total or 0) or sum(humans.values())
        top = all_time.rows[0].commits / total
        out.append(_metric(
            "elephant_factor", label,
            f"{note} Measured over GitHub's {total} all-time contribution(s) by {len(humans)} "
            f"contributor(s) (fetched {all_time.fetched_at[:10]}"
            + (f", {contrib.bots_words(all_time.bots_excluded)}" if all_time.bots_excluded else "")
            + f"); the single largest holds {top:.0%}.",
            value=factor,
            detail={"authors": len(humans), "commits": total, "top_author_share": round(top, 3),
                    "basis": contrib.SHARE_BASIS[contrib.WINDOW_ALL], "fetched_at": all_time.fetched_at,
                    "bots_excluded": all_time.bots_excluded, "window_start": "", "window_end": ""}))
    elif factor is None:
        out.append(_metric("elephant_factor", "",
                           "No per-author commit counts are recorded, so the "
                           "concentration of authorship is unknown — this is not "
                           "a finding that it is evenly spread.", known=False))
    else:
        label, note = _band(factor)
        total = sum(commits_by_author.values())
        top_share = commits_by_author.most_common(1)[0][1] / total
        out.append(_metric(
            "elephant_factor", label,
            f"{note} Measured over the {total} commit(s) recorded"
            + (f" between {dated[0][0] if dated else ''} and "
               f"{dated[-1][0] if dated else ''}" if dated else "")
            + f", by {len(commits_by_author)} author(s); the single largest wrote "
              f"{top_share:.0%} ({contrib.FALLBACK_WORDS}).",
            value=factor,
            detail={"authors": len(commits_by_author), "commits": total,
                    "top_author_share": round(top_share, 3), "basis": contrib.FALLBACK_WORDS,
                    "window_start": dated[0][0] if dated else "",
                    "window_end": dated[-1][0] if dated else ""}))

    # ── organizational diversity ─────────────────────────────────────────────
    attributable, personal = Counter(), 0
    for who, n in commits_by_author.items():
        domain = who.rpartition("@")[2]
        if not domain or domain in _PERSONAL_DOMAINS:
            personal += n
        else:
            attributable[domain] += n
    total_commits = sum(commits_by_author.values())
    share = (sum(attributable.values()) / total_commits) if total_commits else 0.0
    if not total_commits or share < _MIN_ATTRIBUTABLE:
        out.append(_metric(
            "organizational_diversity", "",
            (f"Only {share:.0%} of commits carry an employer-identifying email "
             "address, so the spread across organizations cannot be measured "
             "without describing a minority of the work."),
            known=False, value=None,
            detail={"attributable_share": round(share, 3),
                    "organizations_seen": len(attributable)}))
    else:
        n_orgs = len(attributable)
        label = "single" if n_orgs <= 1 else "narrow" if n_orgs <= 3 else "diverse"
        out.append(_metric(
            "organizational_diversity", label,
            f"{n_orgs} organization(s) identifiable across {share:.0%} of "
            f"commits: {', '.join(d for d, _ in attributable.most_common(3))}.",
            value=n_orgs,
            detail={"attributable_share": round(share, 3),
                    "organizations": dict(attributable.most_common(10))}))

    # ── contributor turnover ─────────────────────────────────────────────────
    # Cut the recorded window into two disjoint halves by date. Disjoint is the
    # whole point: the previous version compared nested windows and reported an
    # artifact of their nesting as a finding about the community.
    dated.sort()
    if len(dated) < 2 or dated[0][0] == dated[-1][0]:
        out.append(_metric(
            "contributor_turnover", "",
            "Commits span less than two distinct days, which is no basis for a "
            "comparison between periods.",
            known=False, detail={"commits_dated": len(dated)}))
    else:
        midpoint = dated[len(dated) // 2][0]
        first = {who for day, who in dated if day < midpoint}
        second = {who for day, who in dated if day >= midpoint}
        if not first or not second:
            out.append(_metric(
                "contributor_turnover", "",
                "Every recorded commit falls on one side of the window, so the "
                "two halves cannot be compared.",
                known=False, detail={"commits_dated": len(dated)}))
        else:
            new = second - first
            retained = second & first
            lost = first - second
            label = ("growing" if len(new) > len(lost)
                     else "shrinking" if len(lost) > len(new) else "steady")
            out.append(_metric(
                "contributor_turnover", label,
                f"Comparing {dated[0][0]}–{midpoint} with {midpoint}–{dated[-1][0]}: "
                f"{len(new)} contributor(s) appear only in the later half, "
                f"{len(retained)} in both, {len(lost)} only in the earlier. "
                "Over this window only — someone absent for three months has not "
                "necessarily left.",
                value=len(new) - len(lost),
                detail={"new": len(new), "retained": len(retained),
                        "lost": len(lost), "midpoint": midpoint,
                        "window_start": dated[0][0], "window_end": dated[-1][0]}))

    # ── release frequency ────────────────────────────────────────────────────
    interval = stats.get("avg_release_interval_days")
    count = stats.get("releases_count") or 0
    if not count:
        out.append(_metric("release_frequency", "",
                           "No releases are recorded.", known=False))
    elif not interval:
        out.append(_metric("release_frequency", "unmeasured",
                           f"{count} release(s), but no interval between them is "
                           "recorded."))
    else:
        label = ("frequent" if interval <= 45 else
                 "periodic" if interval <= 180 else "infrequent")
        out.append(_metric("release_frequency", label,
                           f"{count} release(s), roughly every {interval} day(s).",
                           value=interval))

    # ── time to first response ───────────────────────────────────────────────
    # Deliberately unmeasured, and named anyway: it is one of CHAOSS's most
    # cited metrics, and omitting it silently would leave a reader assuming it
    # was considered and found unremarkable.
    out.append(_metric(
        "time_to_first_response", "",
        "Issue and pull-request response times are not collected, so CHAOSS's "
        "responsiveness metric cannot be computed from what is held.",
        known=False))
    return out


def headline(metrics: list) -> str:
    """The concentration of authorship, because it is the one that decides things.

    Not an average: averaging is what let a 98%-one-person project score
    100/100 on community health.
    """
    ef = next((m for m in metrics if m["check_name"] == "elephant_factor"), None)
    unknown = [m["check_name"] for m in metrics if not m["detail"]["known"]]
    tail = f"; {len(unknown)} metric(s) not established" if unknown else ""
    if not ef or not ef["detail"]["known"]:
        return "Authorship concentration could not be established" + tail
    return f"elephant factor {ef['detail']['value']} ({ef['label']}){tail}"


class ChaossMetricsSurveyor(BaseSurveyor):
    """CHAOSS metrics, reported individually and never rolled into a score."""

    def __init__(self, project, registry, surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or datetime.now(timezone.utc).replace(
            tzinfo=None).isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        out: list[Annotation] = []
        try:
            slug = self.project.slug
            with self.registry._conn() as conn:
                rows = [dict(r) for r in conn.execute(
                    "SELECT author_email, author_name, committed_at "
                    "FROM project_commits WHERE project_slug = ? "
                    "ORDER BY committed_at", (slug,)).fetchall()]
            stats = self.registry.get_latest_project_stats(slug) or {}
            from resource_explorer import contributors as contrib
            all_time = contrib.contributors(self.registry, slug, contrib.WINDOW_ALL, top=10_000)

            if not rows and not stats and not all_time.rows:
                # Persist the reason, not just annotate it. The annotation says
                # "not established"; the results card reads FINDINGS, so returning
                # without writing one made the card render an absence where a stated
                # reason exists — "we have nothing" instead of "we could not tell, and
                # here is why". Found 2026-08-27 via kedro_kubeflow, which has neither
                # commits nor stats and showed a blank card.
                self.registry.upsert_finding(
                    slug, "chaoss_metrics",
                    [{"check_name": "chaoss_metrics", "label": "not_established",
                      "summary": _NOTHING_TO_ASSESS, "confidence": 0,
                      "detail": {"known": False}}],
                    surveyed_at=self._surveyed_at,
                )
                out.append(ClassificationAnnotation(
                    check_name="chaoss_metrics",
                    summary=_NOTHING_TO_ASSESS,
                    analysis_step=STEP,
                    candidate_classifications=["not_established"],
                    confidence=0,
                    # The reasoning here was already right — confidence 0 and a
                    # `not_established` label. What it could not do was reach
                    # the "which tools suit which repos" query, which reads the
                    # step-outcome label and nothing else.
                    json_properties=StepOutcome(
                        "unverified", cause="no commit history to assess").as_row(),
                ))
                return out

            metrics = assess(rows, stats, all_time=all_time)
            self.registry.upsert_finding(slug, "chaoss_metrics", metrics,
                                         surveyed_at=self._surveyed_at)
            ef = next((m for m in metrics if m["check_name"] == "elephant_factor"), None)
            if ef and ef["detail"]["known"]:
                self.registry.upsert_metric(
                    slug, "chaoss_metrics",
                    {"elephant_factor": float(ef["detail"]["value"]),
                     "top_author_share": float(ef["detail"]["top_author_share"])},
                    detail=ef["detail"], surveyed_at=self._surveyed_at)

            out.append(ClassificationAnnotation(
                check_name="chaoss_metrics",
                summary=headline(metrics),
                analysis_step=STEP,
                candidate_classifications=[
                    m["label"] for m in metrics if m["detail"]["known"] and m["label"]],
                confidence=80,
                json_properties={
                    **{m["check_name"]: m["label"] for m in metrics},
                    # `known` is the per-metric known-positive the assessor
                    # already computes: a metric it could establish is one it
                    # had the data for. None established over real rows is a
                    # provable zero; none established over nothing is not.
                    **(StepOutcome("recovered", detail={"metrics": len(metrics)})
                       if any(m["detail"]["known"] for m in metrics)
                       else no_signal("no CHAOSS metric could be established from the "
                                      "commit history read",
                                      known_positive=bool(rows),
                                      rows_read=len(rows))).as_row(),
                },
            ))
        except Exception as exc:
            log.exception("ChaossMetricsSurveyor failed for %s", self.project.slug)
            self._warn(out, str(exc))
        return out
