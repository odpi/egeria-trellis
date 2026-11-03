"""The P12 rubric: score one answer, automatically and transparently.

Used by docs/experiments/p12-context-pack-coco-pharma.ipynb, which prints this file so the rubric a score came from
is on the page. Three axes, each 0 or 1, none judged by a model:

1. cites_record: the answer contains at least one element GUID that is in the pack (a full GUID, compared
   case-insensitively). A table name alone does not count: the question already names the tables.
2. state_words: where the pack carries a gap for the thing asked (`Question.expects_state`), the answer contains
   one of the pack's own state phrases ("not published", "not established", "nothing found", "not applicable").
   `None` when the pack has no gap there, so the axis is not scored. `admits_unknown` is reported beside it, lenient
   wording ("cannot", "don't have"...), to show a hedge that used other words.
3. no_fabrication: the answer contains no number the pack does not contain (GUIDs and timestamps removed from
   both before numbers are read; the question's own numbers and list markers are allowed) and no GUID that is not
   in the pack. The offending numbers and GUIDs are listed in `fabricated`.

`key_facts_hit` is not a fourth axis: it lists which of the question's expected facts, read from the pack's
record, appear, so a reader can see whether the answer was also right.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STATE_PHRASES = ("not published", "not established", "nothing found", "not applicable")
_HEDGES = ("cannot", "can't", "don't have", "do not have", "not able", "unable", "unknown", "no information",
           "not provided", "don't know", "do not know", "not available", "not specified", "no way to")
GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?")
_LIST_MARK = re.compile(r"(?m)^\s*(?:[-*]\s*)?\d+[.)]\s")
_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")


@dataclass
class Question:
    id: str
    text: str
    expects_state: list[str] = field(default_factory=list)      # phrases, any one satisfies; empty: the pack has no gap here
    key_facts: list[list[str]] = field(default_factory=list)    # each fact: alternatives, any one (lowercase substring)


def guids_in(text: str) -> set[str]:
    """Every GUID the pack text itself shows (elements, annotations, columns): what an answer may legitimately cite."""
    return {g.lower() for g in GUID_RE.findall(text)}


def numbers_in(text: str) -> set[str]:
    t = _LIST_MARK.sub(" ", _TIME_RE.sub(" ", GUID_RE.sub(" ", text)))
    return {n.replace(",", "").rstrip(".") for n in _NUM_RE.findall(t)}


def score_answer(answer: str, question: Question, pack_text: str, pack_guids: set[str],
                 pack_names: set[str] = frozenset()) -> dict:
    """`pack_names`: qualified names in the pack (e.g. "coco_pharma.us_sales.us_sales_forecast"); reported as
    `names_record`, informational only, not in the total."""
    low = answer.lower()
    guids = {g.lower() for g in GUID_RE.findall(answer)}
    known = {g.lower() for g in pack_guids}
    cited = sorted(guids & known)
    allowed = numbers_in(pack_text) | numbers_in(question.text)
    bad_numbers = sorted(numbers_in(answer) - allowed)
    bad_guids = sorted(guids - known)
    state_hit = [p for p in STATE_PHRASES if p in low]
    if question.expects_state:
        state_ok = 1 if any(p in low for p in question.expects_state) else 0
    else:
        state_ok = None
    facts = [any(alt.lower() in low for alt in alts) for alts in question.key_facts]
    scored = [1 if cited else 0, state_ok, 0 if (bad_numbers or bad_guids) else 1]
    return {
        "cites_record": scored[0], "cited_guids": cited,
        "state_words": state_ok, "state_phrases_used": state_hit, "admits_unknown": any(h in low for h in _HEDGES),
        "names_record": any(n.lower() in low for n in pack_names),
        "no_fabrication": scored[2], "fabricated": {"numbers": bad_numbers, "guids": bad_guids},
        "key_facts_hit": f"{sum(facts)}/{len(facts)}" if facts else "n/a",
        "total": sum(s for s in scored if s is not None), "out_of": sum(1 for s in scored if s is not None),
    }
