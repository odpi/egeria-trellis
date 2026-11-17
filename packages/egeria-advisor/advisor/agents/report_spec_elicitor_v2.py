"""The report-spec elicitor on pyegeria's authoring API (EA re-base prototype; EA_SPEC_AUTHORING_V2=1).

Same conversation and phases as `report_spec_elicitor` (confirm_action -> elicit_columns -> generate ->
refine, with Back / Save & exit / Resume), and the same draft and document stores. What changes:

- the LLM proposes a target type from Egeria's types, a condition and columns, or asks ONE clarifying
  question when the request doesn't say what kind of thing it is about (phase "clarify");
- the find method and its scoping come from the type's registered spec (`spec_authoring_v2.find_action`);
- columns are grounded in the type's attributes; anything else is dropped and the reply says so;
- generation validates with pyegeria, serializes with pyegeria, and, when the caller is signed in, runs it
  as them and puts the answer in the reply;
- a refinement edits the draft (columns, condition) and regenerates, instead of the LLM rewriting markdown.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from advisor import spec_authoring_v2 as v2
from advisor.llm_client import get_planning_llm
from advisor.report_draft import get_report_draft_manager
from advisor.report_spec_docs import get_report_spec_doc_manager

#: Signed-in clients for live drafts, by draft id (never written to the draft file).
_CLIENTS: Dict[str, Any] = {}


def _json(raw: str) -> Any:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()
    m = re.search(r"[\[{].*[\]}]", raw, flags=re.S)
    return json.loads(m.group(0) if m else raw)


def _types_line() -> str:
    return ", ".join(v2.known_types())


def _menu_text(type_name: str, limit: int = 60) -> str:
    return "\n".join(f"- {k}: {label}" for k, label in v2.attribute_menu(type_name)[:limit])


def _status_words(type_name: Optional[str] = None) -> str:
    """The status values on offer: the type's own when it is known, else every functional status."""
    props = v2.status_properties(type_name) if type_name else list(v2.FUNCTIONAL_STATUS_VALUES)
    if type_name and not props:
        return f"{type_name} has no status of its own: leave functional_status out"
    return "; ".join(f"{p}: {', '.join(v2.FUNCTIONAL_STATUS_VALUES[p])}" for p in props)


def _condition_help(type_name: Optional[str] = None) -> str:
    """The conditions the LLM may set. It names status VALUES only; the code picks the property from the
    type. Egeria's element status (ACTIVE/DELETED) is offered only for deleted elements."""
    return f"""Conditions you may set (leave out what the question doesn't ask for):
- "search_string": text the element's names must contain (default "*", everything)
- "functional_status": a list of the status values the question asks for, e.g. ["DRAFT"],
  ["UNDER_DEVELOPMENT"], ["ACTIVE"]. Only when the question asks about a status or stage ("draft",
  "approved", "in development", "active", "failed"); not for words like "current", "our" or "all".
  Values: {_status_words(type_name)}
- "element_status": only when the user explicitly asks for deleted elements: "DELETED", or
  "INCLUDE_DELETED" for deleted ones as well as the rest
- "output_sort_order": one of {list(v2.SORT_ORDERS)}; with PROPERTY_ASCENDING/DESCENDING also
  "order_property_name": the attribute to sort by. "highest/lowest <attribute>" is a sort, not a filter:
  "Which risks have the highest importance?" -> {{"output_sort_order": "PROPERTY_DESCENDING",
  "order_property_name": "importance"}}, with that attribute as a column"""


_CONDITION_HELP = _condition_help()


def narrower_line(spec: Dict[str, Any]) -> str:
    """The save-time line for a spec narrower than its question, so saving never narrows it silently."""
    return f"⚠️ **Saved spec is narrower than your question. Not applied:** {spec['unmet']}"


def _add_unmet(spec: Dict[str, Any], unmet: List[str]) -> None:
    """Add words for conditions not applied to the draft's `unmet`, keeping the LLM's own."""
    have = [spec["unmet"]] if spec.get("unmet") else []
    items = have + [u for u in unmet if u and u not in have]
    spec["unmet"] = "; ".join(items) if items else None


def _start_rebase_only(query: str, perspective: Optional[str]) -> Dict[str, Any]:
    """The re-base alone: EA's own decomposition prompt and conversation, unchanged. Grounding then takes
    over the type (Egeria's), the find method (the type's registered spec) and the columns (exact keys)."""
    from advisor.agents.report_spec_elicitor import decompose_prompt
    try:
        data = _json(get_planning_llm().generate(decompose_prompt(query, perspective), temperature=0.1,
                                                 max_tokens=1000))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"v2 decompose failed: {exc}")
        data = {}
    return {k: data.get(k) for k in ("title", "heading", "description", "target_type", "columns")}


def start(elicitor, query: str, perspective: Optional[str] = None, egeria_client: Any = None) -> Dict[str, Any]:
    if not v2.fixes_enabled():
        data = _start_rebase_only(query, perspective)
        target = v2.resolve_type(data.get("target_type"))
        if not target:
            # Grounding refuses a type Egeria hasn't got; asking is an elicitor fix, so this is an error.
            from advisor.agents.report_spec_elicitor import _error_result
            return _error_result(query, f"'{data.get('target_type')}' isn't an Egeria type I can report on.")
        return _create(elicitor, query, data, target, {}, egeria_client, [])
    llm = get_planning_llm()
    prompt = f"""You help a user build an Egeria report that answers their question.
User request: "{query}"
Perspective: {perspective or "None"}

Egeria element types you can report on:
{_types_line()}

Decide first: does the request name or clearly imply a kind of element (projects, glossary terms, data
products, risks, metrics, assets, collections, subscriptions, ...)?
- YES: do NOT ask a question. Pick the closest type from the list and build the report, even when part of
  the condition can't be expressed (say so in "unmet"). "Which glossaries are approved?" -> Glossary.
  "Which teams have no leader?" -> Team, unmet: "no leader".
- NO, it names no kind of element at all ("tell me about our stuff", "what should I look at?",
  "is everything OK?"): do NOT guess. Ask ONE short clarifying question offering two or three readings.

{_CONDITION_HELP}

Respond ONLY with JSON, either
{{"clarify": "your one question"}}
or
{{"title": "short-slug-title", "heading": "Report heading", "description": "one sentence",
  "target_type": "one type from the list above",
  "conditions": {{}},
  "columns": [{{"name": "Column Label", "key": "snake_case_attribute"}}],
  "unmet": "any part of the question these conditions cannot apply (e.g. an absence), or null when
            everything the question asks is applied; never for something the question doesn't ask"}}
"""
    try:
        data = _json(llm.generate(prompt, temperature=0.1, max_tokens=700))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"v2 decompose failed: {exc}")
        data = {"clarify": "I couldn't work out what to report on. Which kind of element do you mean "
                           "(for example projects, glossary terms or digital products), and what about them?"}
    named = v2.types_named_in(query)
    if data.get("clarify") and named:
        # The user named the kind of element: build it rather than ask (one retry, told the type).
        try:
            data = _json(llm.generate(prompt + f"\nThe request names {named[0]} elements: do not ask; build "
                                      f"the report with target_type {named[0]}.\n", temperature=0.1, max_tokens=700))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"v2 retry failed: {exc}")
            data = {"target_type": named[0], "columns": []}
        if data.get("clarify"):
            data = {"target_type": named[0], "title": named[0], "heading": named[0], "columns": []}
    if data.get("clarify"):
        return _clarify(query, str(data["clarify"]))
    target = v2.resolve_type(data.get("target_type"))
    if not target:
        return _clarify(query, f"I don't know an Egeria type called '{data.get('target_type')}'. Which kind of "
                               f"element do you mean? For example: {', '.join(v2.known_types()[:12])}.")
    conditions, cnotes, unmet = v2.ground_conditions(data.get("conditions") or {}, target)
    conditions, snote = v2.sort_from_question(query, target, conditions)
    if snote:
        cnotes.append(snote)
    return _create(elicitor, query, data, target, conditions, egeria_client, cnotes, unmet)


def _create(elicitor, query: str, data: Dict[str, Any], target: str, conditions: Dict[str, Any],
            egeria_client: Any, notes: List[str], unmet: Optional[List[str]] = None) -> Dict[str, Any]:
    cols, dropped = v2.ground_columns(target, data.get("columns") or [])
    cols = v2.condition_columns(target, cols, conditions)
    action = v2.find_action(target, conditions)
    dm = get_report_draft_manager()
    spec = dm.create(title=data.get("title") or "custom_report", original_query=query,
                     action_function=action["function"], target_type=target, columns=cols,
                     answers={"Heading": data.get("heading"), "Description": data.get("description")})
    spec["conditions"] = conditions
    # only an LLM "unmet" that names something not applied; "the request doesn't specify X" narrows nothing
    spec["unmet"] = str(data["unmet"]).strip() if v2.narrows(data.get("unmet")) else None
    _add_unmet(spec, unmet or [])
    spec["v2"] = True
    spec["phase"] = "confirm_action"
    spec["phase_label"] = "Confirming report basic configuration"
    dm.save(spec)
    if egeria_client is not None:
        _CLIENTS[spec["draft_id"]] = egeria_client
    return confirm_response(elicitor, spec, notes=dropped + notes)


def _clarify(query: str, question: str) -> Dict[str, Any]:
    return {"query": query, "response": question, "query_type": "report_spec_clarification",
            "routing_agent": "report_spec_agent", "draft_id": None, "phase": "clarify", "can_go_back": False,
            "navigation": [], "next_context": None, "sources": [], "num_sources": 0, "retrieval_time": 0.0,
            "generation_time": 0.0, "avg_relevance_score": 0.0, "context_length": len(question)}


def _conditions_line(spec: Dict[str, Any]) -> str:
    out = []
    for k, v in (spec.get("conditions") or {}).items():
        if k == "functional_status" and isinstance(v, dict):
            out.append(f"{v.get('property')} in {', '.join(v.get('values') or [])} ({v.get('note')})")
        elif k == "element_status":
            out.append(f"element status {', '.join(v)} (Egeria's header status)")
        elif k == v2.STATUS_REFUSED:
            out.append(f"status refused: {v}")
        else:
            out.append(f"{k} = {v}")
    return ", ".join(out) or "none (everything of this type)"


def confirm_response(elicitor, spec: Dict[str, Any], notes: Optional[List[str]] = None) -> Dict[str, Any]:
    from advisor.agents.report_spec_elicitor import _NAV_CONFIRM, _clarification_result
    action = v2.find_action(spec["target_type"], spec.get("conditions"))
    lines = [f"⚠️ Left out: {n}." for n in notes or []]
    lines += [f"### Proposed Report Spec: **{spec['answers'].get('Heading')}**\n",
              f"- **Target Type:** {spec.get('target_type')}",
              f"- **Found with:** `{action['function']}` (from {action['from']})",
              f"- **Condition:** {_conditions_line(spec)}",
              f"- **Description:** {spec['answers'].get('Description')}"]
    if spec.get("unmet"):
        lines.append(f"- **Not applied:** {spec['unmet']}")
    lines += ["\n---\n", "Does this look correct? Use the buttons below, or describe adjustments."]
    return _clarification_result(spec, "\n".join(lines), phase_override="confirm_action",
                                 can_go_back=bool(spec.get("history_stack")), nav=_NAV_CONFIRM)


def columns_response(elicitor, spec: Dict[str, Any], notes: Optional[List[str]] = None) -> Dict[str, Any]:
    from advisor.agents.report_spec_elicitor import _NAV_MIDDLE, _clarification_result
    lines = [f"⚠️ Left out: {n}." for n in notes or []]
    lines.append("### Columns Configuration\n")
    for i, col in enumerate(spec["columns"], 1):
        lines.append(f"**{i}. {col['name']}** — key: `{col['key']}`")
    lines += [f"\n**Condition:** {_conditions_line(spec)}", "\n---\n",
              "Does this look right? Use **Generate now**, or describe changes (only attributes of "
              f"{spec['target_type']} can be columns)."]
    return _clarification_result(spec, "\n".join(lines), phase_override="elicit_columns", can_go_back=True,
                                 nav=_NAV_MIDDLE)


def update_from_request(spec: Dict[str, Any], user_response: str) -> List[str]:
    """Apply a chat request to the draft's columns and condition, grounded. Returns notes."""
    llm = get_planning_llm()
    prompt = f"""Report on Egeria {spec['target_type']} elements.
Current columns: {json.dumps(spec['columns'])}
Current conditions: {json.dumps(spec.get('conditions') or {})}
User request: "{user_response}"

Columns can only use these attributes of {spec['target_type']} (key: label):
{_menu_text(spec['target_type'])}

{_condition_help(spec['target_type']) if v2.fixes_enabled() else ""}

Return ONLY JSON: {{"columns": [{{"name": "...", "key": "..."}}]{', "conditions": {}' if v2.fixes_enabled() else ''}}}
"""
    try:
        data = _json(llm.generate(prompt, temperature=0.1, max_tokens=700))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"v2 column update failed: {exc}")
        return ["I couldn't apply that change; please rephrase it"]
    notes: List[str] = []
    if isinstance(data, list):
        data = {"columns": data}
    if data.get("columns"):
        spec["columns"], dropped = v2.ground_columns(spec["target_type"], data["columns"])
        notes += dropped
    if "conditions" in data and v2.fixes_enabled():
        spec["conditions"], cnotes, unmet = v2.ground_conditions(data.get("conditions") or {},
                                                                 spec["target_type"])
        notes += cnotes
        _add_unmet(spec, unmet)
        spec["action_function"] = v2.find_action(spec["target_type"], spec["conditions"])["function"]
    spec["columns"] = v2.condition_columns(spec["target_type"], spec["columns"], spec.get("conditions") or {})
    return notes


def retarget(spec: Dict[str, Any], user_response: str) -> List[str]:
    """A confirm-phase change: maybe a new target type (re-grounding the columns), heading or condition."""
    m = re.search(r"\btarget\s+type\s*(?:is|=|:|to|as)?\s*[\"']?([\w][\w\s]*?)(?:[\"'\.,]|$)", user_response,
                  re.IGNORECASE)
    notes: List[str] = []
    if m:
        t = v2.resolve_type(m.group(1).strip())
        if not t:
            return [f"'{m.group(1).strip()}' isn't an Egeria type I can report on"]
        spec["target_type"] = t
        cond = dict(spec.get("conditions") or {})
        status = cond.get("functional_status")
        if isinstance(status, dict):
            # the status words were read for the old type: read them again for the new one
            cond["functional_status"] = status.get("values")
            cond, cnotes, unmet = v2.ground_conditions(cond, t)
            spec["conditions"] = cond
            dropped_status = cnotes
            _add_unmet(spec, unmet)
        else:
            dropped_status = []
        spec["action_function"] = v2.find_action(t, spec.get("conditions"))["function"]
        spec["columns"], dropped = v2.ground_columns(t, spec.get("columns") or [])
        spec["columns"] = v2.condition_columns(t, spec["columns"], spec.get("conditions") or {})
        return dropped + dropped_status
    return update_from_request(spec, user_response) + notes


def generate(elicitor, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate with pyegeria; serialize with pyegeria; run as the user when signed in."""
    from advisor.agents.report_spec_elicitor import _NAV_FINAL
    dm = get_report_draft_manager()
    client = _CLIENTS.get(spec["draft_id"])
    run = None
    if client is not None:
        from pyegeria.authoring import inprocess_runner
        inner = inprocess_runner(client)
        run = lambda md, d: inner(md, "validate")  # noqa: E731 -- generation only ever validates
    result = v2.validate(spec, client=client, run=run)
    if not result.ok:
        dm.save(spec)
        lines = ["**This spec doesn't validate yet:**"] + [f"- {e}" for e in result.errors]
        resp = columns_response(elicitor, spec)
        resp["response"] = "\n".join(lines) + "\n\n" + resp["response"]
        resp["validation"] = result.model_dump()
        return resp
    content = v2.serialize(spec)
    doc_mgr = get_report_spec_doc_manager()
    if spec.get("doc_id") and doc_mgr.load(spec["doc_id"]) is not None:
        doc_mgr.update(spec["doc_id"], content)
        doc_id = spec["doc_id"]
    else:
        doc_id = doc_mgr.create(spec["title"], content)
    spec["doc_id"] = doc_id
    spec["format_set"] = v2.build_format_set(spec).model_dump(exclude_none=True)
    spec["phase"] = "refine"
    spec["phase_label"] = "Refinement session"
    dm.save(spec)
    out: Dict[str, Any] = {
        "query": spec["original_query"], "query_type": "report_spec_clarification",
        "routing_agent": "report_spec_agent", "draft_id": spec["draft_id"], "phase": "refine",
        "can_go_back": True, "navigation": _NAV_FINAL, "doc_id": doc_id, "next_context": None,
        "validation": result.model_dump(),
    }
    head = f"**{spec['answers'].get('Heading')}** (draft spec **{doc_id}**, validated with pyegeria)"
    if spec.get("unmet"):
        head += "\n\n" + narrower_line(spec)
    if client is None:
        out["response"] = head + "\n\nSign in to see the answer here; you can run it with **Execute Spec**."
        return out
    ans = v2.answer(spec, client)
    out["answer"] = ans
    out["response"] = f"{head}\n\n{ans['markdown']}\n\n---\nTell me what to change, or save it."
    return out


def refine(elicitor, spec: Dict[str, Any], user_response: str) -> Dict[str, Any]:
    notes = update_from_request(spec, user_response)
    res = generate(elicitor, spec)
    if notes:
        res["response"] = "\n".join(f"⚠️ Left out: {n}." for n in notes) + "\n\n" + res["response"]
    return res
