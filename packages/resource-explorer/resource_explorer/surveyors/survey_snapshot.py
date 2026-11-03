"""The survey a person decides on: the latest completed survey of a repository, kept per step.

Brief section 1 (project owner, 2026-10-07): "we shouldn't have to survey again before publishing; we
are asking the user to make decisions on what has already been surveyed, not what the current state
is." A publish therefore publishes the survey that already exists. Until now no such thing existed to
publish: `SurveyOrchestrator.run` returned a `SurveyResult` in memory, the findings tables kept lossy
rows, and the only way to get annotations in front of `EgeriaPublisher` was to run the survey again.

So the orchestrator keeps each step's annotations when the step completes, and this module reads them
back as the survey a publish sends. **No schema change:** the rows live in `app_settings` (key/value),
one per step, plus one small index per repository:

    repo_survey_step::<slug>::<step_key>   {"surveyed_at", "step", "annotations": [...]}
    repo_survey_index::<slug>              {"<step_key>": "<surveyed_at>", ...}

Per step, because surveys are run in parts (an Analyses-card "run", a definition, a scheduled step):
the latest completed survey is the newest result of EACH step, and `surveyed_at` of the whole is the
newest of them. A step that failed or was skipped keeps its previous result; it is never overwritten by
an absence. A step that ran and found nothing is recorded as an EMPTY result (ran, nothing found), which
is a different statement from "never ran" (no row).

Annotations are stored as the dataclass fields they were built with. JSON cannot carry everything a
surveyor might put in a `json_properties` dict; anything it cannot encode is stored as its `str()`, which
is what the publisher would send for it anyway (`to_string_map`).
"""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

log = logging.getLogger(__name__)

STEP_PREFIX = "repo_survey_step::"
INDEX_PREFIX = "repo_survey_index::"

#: What a publish or a commit says when nothing has been surveyed (brief section 1).
NO_SURVEY_SENTENCE = "no survey to publish yet · run the first survey"

#: What a publish or a commit says when the repository HAS been surveyed (the header's "surveyed 40d ago")
#: but no kept survey exists, because kept surveys only exist for surveys completed since 2026-10-07.
SURVEYED_BEFORE_KEPT_SENTENCE = "surveyed before 10-07 · nothing kept to publish · re-survey to publish"


def surveyed_before_kept(registry, slug: str) -> bool:
    """True when the repository has survey history (the SAME `projects.last_surveyed_at` the header's
    "surveyed <ago>" reads, so the two lines cannot disagree) while no kept survey exists."""
    try:
        project = registry.get(slug)
    except Exception:  # an unreadable row is "no history known", never a claim of one
        return False
    return bool(project is not None and (getattr(project, "last_surveyed_at", "") or ""))


def no_kept_survey_sentence(registry, slug: str) -> str:
    """The sentence for "no kept survey": the two states are told apart by persisted rows."""
    return SURVEYED_BEFORE_KEPT_SENTENCE if surveyed_before_kept(registry, slug) else NO_SURVEY_SENTENCE


def _step_key(slug: str, step: str) -> str:
    return f"{STEP_PREFIX}{slug}::{step}"


def _index_key(slug: str) -> str:
    return f"{INDEX_PREFIX}{slug}"


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def annotation_to_dict(annotation) -> dict:
    return _jsonable(annotation)


def _classes() -> dict:
    from resource_explorer.surveyors import survey_report as sr

    out = {}
    for name in dir(sr):
        obj = getattr(sr, name)
        if (isinstance(obj, type) and dataclasses.is_dataclass(obj) and issubclass(obj, sr.Annotation)
                and obj is not sr.Annotation):
            out[obj.__dataclass_fields__["annotation_type"].default.value] = obj
    return out


def annotation_from_dict(data: dict):
    """The annotation a stored dict describes; raises `ValueError` for a type it does not know
    (a stored survey a newer build wrote), never a guess."""
    classes = _classes()
    cls = classes.get(data.get("annotation_type"))
    if cls is None:
        raise ValueError(f"unknown annotation type {data.get('annotation_type')!r} in a stored survey")
    init_names = {f.name for f in dataclasses.fields(cls) if f.init}
    return cls(**{k: v for k, v in data.items() if k in init_names})


def record_step(registry, slug: str, step_key: str, surveyed_at: str, annotations) -> None:
    """Keep one completed step's annotations as the latest result of that step."""
    payload = {"surveyed_at": surveyed_at, "step": step_key,
               "annotations": [annotation_to_dict(a) for a in annotations]}
    registry.set_setting(_step_key(slug, step_key), json.dumps(payload))
    index = _read_index(registry, slug)
    index[step_key] = surveyed_at
    registry.set_setting(_index_key(slug), json.dumps(index))


def _read_index(registry, slug: str) -> dict:
    raw = registry.get_setting(_index_key(slug))
    if not raw:
        return {}
    try:
        index = json.loads(raw)
    except ValueError:
        log.warning("the stored survey index for %s is unreadable; reading it as no survey kept", slug)
        return {}
    return index if isinstance(index, dict) else {}


@dataclass
class Snapshot:
    """The latest completed survey of one repository."""
    slug: str
    #: newest `surveyed_at` among the steps (ISO text); what the band shows as "the survey of <date>"
    surveyed_at: str = ""
    #: {step_key: {"surveyed_at": iso, "annotations": [dict, ...]}}
    steps: dict = field(default_factory=dict)

    @property
    def annotation_count(self) -> int:
        return sum(len(s["annotations"]) for s in self.steps.values())

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def age_seconds(self, now: datetime | None = None) -> float | None:
        if not self.surveyed_at:
            return None
        then = datetime.fromisoformat(self.surveyed_at)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0.0, ((now or datetime.now(timezone.utc)) - then).total_seconds())


def latest(registry, slug: str) -> Snapshot | None:
    """The latest completed survey, or `None` when nothing has been surveyed. A step whose stored
    row cannot be read is left OUT (and logged), never counted as run."""
    index = _read_index(registry, slug)
    if not index:
        return None
    snap = Snapshot(slug=slug)
    for step, at in index.items():
        raw = registry.get_setting(_step_key(slug, step))
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("stored survey step %s/%s is unreadable; leaving it out", slug, step)
            continue
        snap.steps[step] = {"surveyed_at": data.get("surveyed_at") or at,
                            "annotations": data.get("annotations") or []}
    if not snap.steps:
        return None
    snap.surveyed_at = max(s["surveyed_at"] for s in snap.steps.values())
    return snap


def to_result(project, snapshot: Snapshot):
    """A `SurveyResult` the publisher can send, assembled from the stored steps in the step
    registry's own order (so a publish after a snapshot reads like a publish after a run)."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
    from resource_explorer.surveyors.survey_report import SurveyResult

    surveyed_at = datetime.fromisoformat(snapshot.surveyed_at)
    if surveyed_at.tzinfo is not None:
        surveyed_at = surveyed_at.astimezone(timezone.utc).replace(tzinfo=None)   # the orchestrator's utcnow()
    result = SurveyResult(resource_slug=project.slug, project_display_name=project.display_name,
                          github_url=project.github_url, surveyed_at=surveyed_at)
    order = [k for k in STEP_REGISTRY if k in snapshot.steps] + sorted(
        k for k in snapshot.steps if k not in STEP_REGISTRY)
    for step in order:
        for data in snapshot.steps[step]["annotations"]:
            result.add(annotation_from_dict(data))
    result.steps_run = sorted(snapshot.steps)
    return result
