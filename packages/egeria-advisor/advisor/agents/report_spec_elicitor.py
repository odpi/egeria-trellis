"""
ReportSpecElicitor — conversational state machine for eliciting report specifications.
"""
from __future__ import annotations

import json
import re
import time
import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

from advisor.report_draft import get_report_draft_manager
from advisor.report_spec_docs import get_report_spec_doc_manager
from advisor.llm_client import get_planning_llm
from advisor import spec_authoring_v2 as _v2


def decompose_prompt(query: str, perspective: Optional[str] = None) -> str:
    """The elicitor's decomposition prompt (shared with the re-base-only V2 path, unchanged)."""
    return f"""
You are an expert Egeria metadata analyst. A user wants to build a report specification.
User request: "{query}"
Perspective: {perspective or "None"}

Please decompose this request into a report specification. Choose the most appropriate Action Function and Target Type.
Typical Egeria view client classes and methods:
- GlossaryManager.find_glossaries (for Glossaries)
- ProjectManager.find_projects (for Projects, Campaigns, Tasks)
- CollectionManager.find_collections (for Collections, Agreements)
- GovernanceOfficer.find_governance_definitions (for Policies, Rules)
- ExternalReferences.find_external_references (for External References, Cited Documents)
- MyProfile.get_my_profile (for User Profiles)

Respond ONLY with a JSON object containing the proposed spec:
{{
  "title": "A short slug-friendly title for the spec",
  "heading": "The full human-readable heading/title of the report",
  "target_type": "The Egeria entity type being reported on",
  "action_function": "The EgeriaTech.method action function",
  "description": "Brief description of the report",
  "columns": [
     {{
       "name": "Human Column Name",
       "key": "egeria_attribute_key",
       "format": false,
       "detail_spec": null,
       "formats": "ALL"
     }}
  ]
}}

JSON object:
"""


_NAV_FIRST   = ["save_exit", "cancel"]
_NAV_MIDDLE  = ["generate_now", "back", "cancel"]
_NAV_FINAL   = ["run_spec", "back", "cancel"]
_NAV_CONFIRM = ["continue_columns", "try_again", "cancel"]


class ReportSpecElicitor:
    """Conversational elicitor for report specs."""

    def start(
        self,
        query: str,
        perspective: Optional[str] = None,
        egeria_client: Any = None,
    ) -> Dict[str, Any]:
        """Decompose intent, propose basic structure, save draft, and return response."""
        if _v2.enabled():
            from advisor.agents import report_spec_elicitor_v2 as v2e
            return v2e.start(self, query, perspective, egeria_client)
        llm = get_planning_llm()
        prompt = decompose_prompt(query, perspective)
        try:
            raw = llm.generate(prompt, temperature=0.1, max_tokens=1000)
            # Extract JSON from Markdown block if present
            raw_json = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
            raw_json = re.sub(r"\s*```$", "", raw_json)
            raw_json = raw_json.strip()
            data = json.loads(raw_json)
        except Exception as exc:
            logger.error(f"Failed to decompose report spec intent: {exc}. Raw response: {raw if 'raw' in locals() else ''}")
            # Fallback proposal
            data = {
                "title": "custom_report",
                "heading": "Custom Report",
                "target_type": "Referenceable",
                "action_function": "CollectionManager.find_collections",
                "description": f"Custom report based on: {query}",
                "columns": [
                    {"name": "Display Name", "key": "display_name", "format": False, "detail_spec": None, "formats": "ALL"},
                    {"name": "Qualified Name", "key": "qualified_name", "format": False, "detail_spec": None, "formats": "ALL"},
                    {"name": "GUID", "key": "guid", "format": True, "detail_spec": None, "formats": "ALL"}
                ]
            }

        dm = get_report_draft_manager()
        spec = dm.create(
            title=data.get("title", "custom_report"),
            original_query=query,
            action_function=data.get("action_function"),
            target_type=data.get("target_type"),
            columns=data.get("columns", []),
            answers={
                "Heading": data.get("heading"),
                "Description": data.get("description"),
            }
        )
        spec["phase"] = "confirm_action"
        spec["phase_label"] = "Confirming report basic configuration"
        dm.save(spec)

        return self._build_confirm_response(spec)

    def process(self, draft_id: str, user_response: str) -> Dict[str, Any]:
        """Advance the elicitation conversation based on current phase and user response."""
        dm = get_report_draft_manager()
        spec = dm.load(draft_id)
        if not spec:
            return _error_result(draft_id, f"Draft session `{draft_id}` not found.")

        # Save history before changing
        dm.push_history(spec)

        response_clean = user_response.strip().lower()

        if spec.get("v2"):
            return self._process_v2(spec, user_response, response_clean)

        if spec["phase"] == "confirm_action":
            if response_clean in ("yes", "y", "continue", "confirm", "generate", "generate now", "generate_now",
                                  "continue to column definitions", "continue_columns"):
                # "generate now" with existing columns → skip review and write spec immediately
                if response_clean in ("generate now", "generate_now") and spec.get("columns"):
                    dm.save(spec)
                    return self._generate_report_spec(spec)
                spec["phase"] = "elicit_columns"
                spec["phase_label"] = "Reviewing and customizing columns"
                dm.save(spec)
                return self._build_elicit_columns_response(spec)
            elif response_clean in ("completely wrong", "completely_wrong", "try again", "try_again", "no", "n"):
                # restart with a fallback or ask user
                dm.pop_history(spec) # discard history snapshot of error
                return _error_result(draft_id, "Please describe the report you would like to build in more detail.")
            else:
                # Detect column-related requests — advance to elicit_columns then apply
                _col_signals = re.compile(
                    r'\b(add|include|column|field|attribute|description|owner|category|status|tag|label)\b',
                    re.IGNORECASE,
                )
                if _col_signals.search(user_response):
                    spec["phase"] = "elicit_columns"
                    spec["phase_label"] = "Reviewing and customizing columns"
                    dm.save(spec)
                    # Apply the column request immediately in the new phase
                    return self.process(draft_id, user_response)

                # Pre-extract explicit target type before the LLM can override it
                _tt_re = re.compile(
                    r'\btarget\s+type\s*(?:is|=|:|to|as)?\s*["\']?([\w][\w\s]*?)(?:["\'\.,]|$)',
                    re.IGNORECASE,
                )
                _tt_m = _tt_re.search(user_response)
                if _tt_m:
                    spec["target_type"] = _tt_m.group(1).strip()
                    dm.save(spec)
                    return self._build_confirm_response(spec, note=f"Target type updated to {spec['target_type']}.")

                # LLM-assisted adjustment of basic action
                llm = get_planning_llm()
                prompt = f"""
Current report spec:
- Heading: {spec['answers'].get('Heading')}
- Target Type: {spec.get('target_type')}
- Action Function: {spec.get('action_function')}
- Description: {spec['answers'].get('Description')}

User wants to change something: "{user_response}"

Update and return the spec fields in JSON format:
{{
  "heading": "...",
  "target_type": "...",
  "action_function": "...",
  "description": "..."
}}
"""
                try:
                    raw = llm.generate(prompt, temperature=0.1, max_tokens=500)
                    raw_json = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
                    raw_json = re.sub(r"\s*```$", "", raw_json).strip()
                    data = json.loads(raw_json)
                    spec["target_type"] = data.get("target_type", spec["target_type"])
                    spec["action_function"] = data.get("action_function", spec["action_function"])
                    spec["answers"]["Heading"] = data.get("heading", spec["answers"]["Heading"])
                    spec["answers"]["Description"] = data.get("description", spec["answers"]["Description"])
                except Exception as exc:
                    logger.error(f"Failed to update action configuration: {exc}")

                dm.save(spec)
                return self._build_confirm_response(spec, note="Updated report configuration based on your feedback.")

        elif spec["phase"] == "elicit_columns":
            if response_clean in ("yes", "y", "continue", "confirm", "generate now", "generate_now"):
                return self._generate_report_spec(spec)
            else:
                # LLM-assisted column addition / refinement
                llm = get_planning_llm()
                prompt = f"""
Current Columns:
{json.dumps(spec['columns'], indent=2)}

User request: "{user_response}"

Please update the list of columns based on the request.
Return the entire updated list of columns as JSON:
[
  {{
    "name": "Human Column Name",
    "key": "egeria_attribute_key",
    "format": false,
    "detail_spec": null,
    "formats": "ALL"
  }}
]
"""
                update_note = "Updated columns based on your feedback."
                try:
                    raw = llm.generate(prompt, temperature=0.1, max_tokens=800)
                    raw_json = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
                    raw_json = re.sub(r"\s*```$", "", raw_json).strip()
                    cols = json.loads(raw_json)
                    if isinstance(cols, list) and cols:
                        spec["columns"] = cols
                    else:
                        update_note = "⚠️ Could not parse updated columns — showing previous list."
                        logger.warning(f"elicit_columns: LLM returned non-list: {raw_json[:200]}")
                except Exception as exc:
                    update_note = "⚠️ Column update failed — please try rephrasing your request."
                    logger.error(f"Failed to update columns list: {exc}. Raw: {raw[:300] if 'raw' in dir() else 'n/a'}")

                dm.save(spec)
                return self._build_elicit_columns_response(spec, note=update_note)

        elif spec["phase"] == "refine":
            # The document is already generated.
            doc_id = spec["doc_id"]
            # Detect execute/run commands — redirect to report agent rather than treating
            # as a refinement request (avoids the LLM corrupting the spec markdown).
            _exec_re = re.compile(
                r'^(?:execute|run)(?:\s+(?:the\s+)?(?:report\s+spec|spec|report))?\b',
                re.IGNORECASE,
            )
            if _exec_re.match(user_response.strip()):
                from advisor.agents.report_spec_agent import get_report_spec_agent
                _fmt_m = re.search(r"\bfmt:'([^']+)'", user_response, re.IGNORECASE)
                _exec_fmt = _fmt_m.group(1).upper() if _fmt_m else "TABLE"
                result = get_report_spec_agent().execute(doc_id, output_format=_exec_fmt)
                result["next_context"] = None
                return result
            doc_manager = get_report_spec_doc_manager()
            doc_content = doc_manager.load(doc_id)
            if not doc_content:
                return _error_result(draft_id, "Report Spec Document was not found in inbox.")

            llm = get_planning_llm()
            prompt = f"""
We have the following Report Spec Document markdown:
```markdown
{doc_content}
```

The user requested a refinement/change:
"{user_response}"

Please output the COMPLETE updated markdown document. Follow the conventions exactly:
- `## Create Report Spec` followed by `### <Attribute Name>`
- `## Create Column` followed by `### <Attribute Name>`

Do not output any introductory or summary text. Just the markdown document:
"""
            try:
                updated_content = llm.generate(prompt, temperature=0.2, max_tokens=1500)
                # Strip markdown code fences if outputted
                updated_content = re.sub(r"^```markdown\s*", "", updated_content, flags=re.IGNORECASE)
                updated_content = re.sub(r"^```\s*", "", updated_content)
                updated_content = re.sub(r"\s*```$", "", updated_content)
                updated_content = updated_content.strip() + "\n"

                doc_manager.update(doc_id, updated_content)
            except Exception as exc:
                logger.error(f"Refinement failed: {exc}")
                return _error_result(draft_id, f"Refinement failed: {exc}")

            return {
                "query": user_response,
                "response": f"I've updated the report spec document **{doc_id}** in your inbox.",
                "query_type": "report_spec_clarification",
                "routing_agent": "report_spec_agent",
                "draft_id": draft_id,
                "phase": "refine",
                "can_go_back": True,
                "navigation": _NAV_FINAL,
                "doc_id": doc_id,
            }

        return _error_result(draft_id, f"Invalid phase: {spec['phase']}")

    def _process_v2(self, spec: Dict, user_response: str, response_clean: str) -> Dict[str, Any]:
        """A draft made with EA_SPEC_AUTHORING_V2: the same phases, on pyegeria's authoring API."""
        from advisor.agents import report_spec_elicitor_v2 as v2e
        dm = get_report_draft_manager()
        phase = spec["phase"]
        if phase == "confirm_action":
            if response_clean in ("generate now", "generate_now") and spec.get("columns"):
                return v2e.generate(self, spec)
            if response_clean in ("yes", "y", "continue", "confirm", "continue to column definitions",
                                  "continue_columns"):
                spec["phase"], spec["phase_label"] = "elicit_columns", "Reviewing and customizing columns"
                dm.save(spec)
                return v2e.columns_response(self, spec)
            if response_clean in ("completely wrong", "completely_wrong", "try again", "try_again", "no", "n"):
                dm.pop_history(spec)
                return _error_result(spec["draft_id"], "Please describe the report you would like to build in more detail.")
            notes = v2e.retarget(spec, user_response)
            dm.save(spec)
            return v2e.confirm_response(self, spec, notes=notes)
        if phase == "elicit_columns":
            if response_clean in ("yes", "y", "continue", "confirm", "generate now", "generate_now"):
                return v2e.generate(self, spec)
            notes = v2e.update_from_request(spec, user_response)
            dm.save(spec)
            return v2e.columns_response(self, spec, notes=notes)
        if phase == "refine":
            if re.match(r'^(?:execute|run)(?:\s+(?:the\s+)?(?:report\s+spec|spec|report))?\b',
                        user_response.strip(), re.IGNORECASE):
                from advisor.agents.report_spec_agent import get_report_spec_agent
                result = get_report_spec_agent().execute(spec["doc_id"], output_format="TABLE")
                result["next_context"] = None
                return result
            return v2e.refine(self, spec, user_response)
        return _error_result(spec["draft_id"], f"Invalid phase: {phase}")

    def back(self, draft_id: str) -> Dict[str, Any]:
        """Rewind to previous phase."""
        dm = get_report_draft_manager()
        spec = dm.load(draft_id)
        if not spec:
            return _error_result(draft_id, f"Draft `{draft_id}` not found.")
        if not dm.pop_history(spec):
            return _error_result(draft_id, "Cannot go back any further.")
        dm.save(spec)

        if spec.get("v2"):
            from advisor.agents import report_spec_elicitor_v2 as v2e
            if spec["phase"] == "confirm_action":
                return v2e.confirm_response(self, spec)
            if spec["phase"] == "elicit_columns":
                return v2e.columns_response(self, spec)
        if spec["phase"] == "confirm_action":
            return self._build_confirm_response(spec)
        elif spec["phase"] == "elicit_columns":
            return self._build_elicit_columns_response(spec)
        return _error_result(draft_id, f"Unknown phase: {spec['phase']}")

    def cancel(self, draft_id: str) -> Dict[str, Any]:
        """Discard the session."""
        get_report_draft_manager().delete(draft_id)
        return {
            "query": draft_id,
            "response": "Report building session cancelled. Draft has been discarded.",
            "query_type": "report_spec_clarification",
            "routing_agent": "report_spec_agent",
            "draft_id": None,
            "phase": "cancelled",
            "can_go_back": False,
            "navigation": [],
            "next_context": None,
        }

    def save_and_exit(self, draft_id: str) -> Dict[str, Any]:
        """Save draft and exit flow."""
        spec = get_report_draft_manager().load(draft_id)
        title = spec.get("title", draft_id) if spec else draft_id
        response = f"Session saved. You can resume building **{title}** from the drafts list."
        if spec and spec.get("v2") and spec.get("unmet"):
            from advisor.agents.report_spec_elicitor_v2 import narrower_line
            response += "\n\n" + narrower_line(spec)
        return {
            "query": draft_id,
            "response": response,
            "query_type": "report_spec_clarification",
            "routing_agent": "report_spec_agent",
            "draft_id": None,
            "phase": "saved",
            "can_go_back": False,
            "navigation": [],
            "next_context": None,
        }

    def resume(self, draft_id: str) -> Dict[str, Any]:
        """Resume a saved draft session."""
        spec = get_report_draft_manager().load(draft_id)
        if not spec:
            return _error_result(draft_id, f"Draft `{draft_id}` not found.")

        if spec.get("v2") and spec["phase"] in ("confirm_action", "elicit_columns"):
            from advisor.agents import report_spec_elicitor_v2 as v2e
            return (v2e.confirm_response if spec["phase"] == "confirm_action" else v2e.columns_response)(self, spec)
        if spec["phase"] == "confirm_action":
            return self._build_confirm_response(spec)
        elif spec["phase"] == "elicit_columns":
            return self._build_elicit_columns_response(spec)
        elif spec["phase"] == "refine":
            doc_id = spec["doc_id"]
            return {
                "query": spec["original_query"],
                "response": f"Resuming refinement for **{doc_id}**.",
                "query_type": "report_spec_clarification",
                "routing_agent": "report_spec_agent",
                "draft_id": draft_id,
                "phase": "refine",
                "can_go_back": True,
                "navigation": _NAV_FINAL,
                "doc_id": doc_id,
            }
        return _error_result(draft_id, f"Unknown phase: {spec['phase']}")

    def restart_qa(self, draft_id: str) -> Dict[str, Any]:
        """Restart Q&A from confirm_action phase."""
        dm = get_report_draft_manager()
        spec = dm.load(draft_id)
        if not spec:
            return _error_result(draft_id, f"Draft `{draft_id}` not found.")
        spec["phase"] = "confirm_action"
        spec["phase_label"] = "Confirming report basic configuration"
        spec["history_stack"] = []
        dm.save(spec)
        return self._build_confirm_response(spec)

    def discard(self, draft_id: str) -> Dict[str, Any]:
        """Discard session and clean up file."""
        return self.cancel(draft_id)

    # ------------------------------------------------------------------
    # Private Helpers
    # ------------------------------------------------------------------

    def _build_confirm_response(self, spec: Dict, note: Optional[str] = None) -> Dict[str, Any]:
        lines: List[str] = []
        if note:
            lines.append(f"{note}\n")
        lines.append(f"### Proposed Report Spec: **{spec['answers'].get('Heading')}**\n")
        lines.append(f"- **Target Type:** {spec.get('target_type')}")
        lines.append(f"- **Action Function:** `{spec.get('action_function')}`")
        lines.append(f"- **Description:** {spec['answers'].get('Description')}\n")
        lines.append("---\n")
        lines.append(
            "Does this look correct? Use the buttons below, "
            "or describe adjustments: *\"change the target type to Asset\"*."
        )
        return _clarification_result(
            spec,
            "\n".join(lines),
            phase_override="confirm_action",
            can_go_back=bool(spec.get("history_stack")),
            nav=_NAV_CONFIRM
        )

    def _build_elicit_columns_response(self, spec: Dict, note: Optional[str] = None) -> Dict[str, Any]:
        lines: List[str] = []
        if note:
            lines.append(f"{note}\n")
        lines.append("### Columns Configuration\n")
        lines.append("Here are the columns I'll configure for this report:\n")

        for i, col in enumerate(spec["columns"], 1):
            detail_suffix = f" (Details: **{col['detail_spec']}**)" if col.get("detail_spec") else ""
            fmt_suffix = f" [Formatted]" if col.get("format") and col.get("format") != "False" else ""
            lines.append(f"**{i}. {col['name']}** — key: `{col['key']}`{detail_suffix}{fmt_suffix}")

        lines.append("\n---\n")
        lines.append(
            "Does this look right? Use **Generate now** to write the spec, "
            "or describe changes: *\"add a prioritization column\"* / "
            "*\"set Detail Spec for Roles to UserRolesDetail\"*."
        )
        return _clarification_result(
            spec,
            "\n".join(lines),
            phase_override="elicit_columns",
            can_go_back=True,
            nav=_NAV_MIDDLE
        )

    def _generate_report_spec(self, spec: Dict) -> Dict[str, Any]:
        if spec.get("v2"):
            from advisor.agents import report_spec_elicitor_v2 as v2e
            return v2e.generate(self, spec)
        doc_manager = get_report_spec_doc_manager()
        doc_content = self._generate_report_spec_md(spec)
        doc_id = doc_manager.create(spec["title"], doc_content)

        spec["doc_id"] = doc_id
        spec["phase"] = "refine"
        spec["phase_label"] = "Refinement session"
        get_report_draft_manager().save(spec)

        lines = [
            f"🎉 **Report spec document generated!**\n",
            f"The spec document **{doc_id}** is now available in your Reports inbox.",
            f"You can view and edit the columns visually in the canvas sidebar or run it using **Execute Spec**.",
            f"\n---\n",
            f"If you'd like to adjust the specification via chat, tell me what to change!"
        ]

        return {
            "query": spec["original_query"],
            "response": "\n".join(lines),
            "query_type": "report_spec_clarification",
            "routing_agent": "report_spec_agent",
            "draft_id": spec["draft_id"],
            "phase": "refine",
            "can_go_back": True,
            "navigation": _NAV_FINAL,
            "doc_id": doc_id,
            # Clear task context — spec is generated and ready; subsequent actions
            # (▶ Run Report, canvas edits) should route directly, not via elicitor.
            "next_context": None,
        }

    def _generate_report_spec_md(self, spec: Dict) -> str:
        if spec.get("v2"):
            # pyegeria serializes a V2 draft (canvas edits come here too: re-ground the columns first)
            spec["columns"], _ = _v2.ground_columns(spec["target_type"], spec.get("columns") or [])
            return _v2.serialize(spec)
        lines = []
        lines.append(f"# {spec['answers'].get('Heading', 'Untitled Report')}\n")
        lines.append("## Create Report Spec")
        if spec.get("target_type"):
            lines.append("### Target Type")
            lines.append(spec.get("target_type"))
            lines.append("")
        lines.append("### Heading")
        lines.append(spec["answers"].get("Heading", "Untitled Report"))
        lines.append("")
        if spec["answers"].get("Description"):
            lines.append("### Description")
            lines.append(spec["answers"].get("Description"))
            lines.append("")
        if spec.get("perspectives"):
            lines.append("### Perspectives")
            lines.append(", ".join(spec.get("perspectives")))
            lines.append("")
        if spec.get("questions"):
            lines.append("### Questions")
            lines.append(", ".join(spec.get("questions")))
            lines.append("")
        if spec.get("saved_query"):
            # Route B (B6/N6): an analytic-only spec; no find method, no find params, no columns
            from advisor.spec_saved_query import spec_md_lines
            lines.extend(spec_md_lines(spec["saved_query"]))
            return "\n".join(lines)
        if spec.get("action_function"):
            lines.append("### Action Function")
            lines.append(spec.get("action_function"))
            lines.append("")
            lines.append("### Required Params")
            lines.append("search_string")
            lines.append("")

        # Three-category parameter model
        def _kv_block(d: dict) -> str:
            return "\n".join(f"{k}={v}" for k, v in d.items())

        content_filters = spec.get("content_filters") or {"search_string": "*"}
        shape_defaults = spec.get("shape_defaults") or {}
        performance_hints = spec.get("performance_hints") or {"page_size": 100, "start_from": 0}

        cf_text = _kv_block(content_filters)
        if cf_text:
            lines.append("### Content Filters")
            lines.append(cf_text)
            lines.append("")
        sd_text = _kv_block(shape_defaults)
        if sd_text:
            lines.append("### Shape Defaults")
            lines.append(sd_text)
            lines.append("")
        ph_text = _kv_block(performance_hints)
        if ph_text:
            lines.append("### Performance Hints")
            lines.append(ph_text)
            lines.append("")

        for col in spec["columns"]:
            lines.append("## Create Column")
            lines.append("### Name")
            lines.append(col["name"])
            lines.append("")
            lines.append("### Key")
            lines.append(col["key"])
            lines.append("")
            if col.get("format") and col.get("format") != "False":
                lines.append("### Format")
                lines.append(str(col["format"]))
                lines.append("")
            if col.get("detail_spec"):
                lines.append("### Detail Spec")
                lines.append(col["detail_spec"])
                lines.append("")
            if col.get("formats") and col.get("formats") != "ALL":
                lines.append("### Formats")
                lines.append(col["formats"])
                lines.append("")

        return "\n".join(lines)


def _clarification_result(
    spec: Dict,
    response_md: str,
    phase_override: Optional[str] = None,
    can_go_back: bool = False,
    nav: Optional[List[str]] = None,
) -> Dict[str, Any]:
    _phase = phase_override or spec.get("phase", "confirm_action")
    return {
        "query":            spec.get("original_query", ""),
        "response":         response_md,
        "query_type":       "report_spec_clarification",
        "routing_agent":    "report_spec_agent",
        "draft_id":         spec["draft_id"],
        "phase":            _phase,
        "can_go_back":      can_go_back,
        "navigation":       nav or _NAV_FIRST,
        "next_context": {
            "task":     "report_spec_elicitor",
            "draft_id": spec["draft_id"],
            "phase":    _phase,
        },
        "sources":          [],
        "num_sources":      0,
        "retrieval_time":   0.0,
        "generation_time":  0.0,
        "avg_relevance_score": 0.0,
        "context_length":   len(response_md),
    }


def _error_result(query: str, message: str) -> Dict[str, Any]:
    return {
        "query":            query,
        "response":         message,
        "query_type":       "report_spec_clarification",
        "routing_agent":    "report_spec_agent",
        "draft_id":         None,
        "phase":            "error",
        "can_go_back":      False,
        "navigation":       [],
        "sources":          [],
        "num_sources":      0,
        "retrieval_time":   0.0,
        "generation_time":  0.0,
        "avg_relevance_score": 0.0,
        "context_length":   len(message),
    }


_elicitor: Optional[ReportSpecElicitor] = None


def get_report_spec_elicitor() -> ReportSpecElicitor:
    global _elicitor
    if _elicitor is None:
        _elicitor = ReportSpecElicitor()
    return _elicitor
