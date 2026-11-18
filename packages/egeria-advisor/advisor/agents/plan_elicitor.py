"""
PlanElicitor — conversational Q&A engine for Governance Plan generation.

Drives the multi-phase planning flow:
  confirm_commands → show proposed command set; ask user to confirm or extend
  elicit_required  → ask about missing required template fields
  elicit_optional  → offer optional fields (basic/advanced mode)
  generate         → compose and save the plan document
  refine           → NL-driven iterative change loop
  template_offer   → offer to save the result as a reusable template
  done             → terminal state

Each phase returns a standard result dict with query_type="plan_clarification"
and navigation metadata so the UI can render Back / Save & Exit / Start Over buttons.

The DraftManager handles all persistence; this module is stateless between calls.
"""
from __future__ import annotations

import json
import re
import copy
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from advisor.governance_draft import DraftManager, get_draft_manager
from advisor.plan_templates import get_template_manager

# Navigation button sets per phase
_NAV_FIRST   = ["save_exit", "cancel"]                               # first step — no Back
_NAV_MIDDLE  = ["back", "save_exit", "cancel"]                       # mid-flow
_NAV_FINAL   = ["back", "cancel"]                                    # last step
_NAV_CONFIRM = ["generate_now", "completely_wrong", "save_exit", "cancel"]  # confirm_commands step

_PHASE_LABELS = {
    "confirm_commands": "Confirming plan steps",
    "elicit_required":  "Answering required field questions",
    "elicit_optional":  "Choosing optional fields",
    "generate":         "Ready to generate plan",
    "refine":           "Reviewing and refining the plan",
    "template_offer":   "Offering template save",
    "done":             "Complete",
}

# "move <ref> to [be] <target>" — e.g. "move step 3 to be the first step",
# "move Create Campaign to step 1", "move Campaign to be the first step".
_MOVE_RE = re.compile(r'^\s*move\s+(.+?)\s+to\s+(?:be\s+)?(.+?)\s*$', re.IGNORECASE)

# Project Hierarchy phrasings — resolved to an embedded Parent ID mutation on
# existing Create Project command(s), never a standalone Link Project Hierarchy
# command (see CLAUDE.md design rule 13).
_HIERARCHY_PARENT_OF_RE = re.compile(
    r'^\s*make\s+(.+?)\s+(?:the|a)\s+parent\s+of\s+(.+?)\s*$', re.IGNORECASE
)
_HIERARCHY_SUBPROJECT_OF_RE = re.compile(
    r'^\s*(?:link\s+)?(.+?)\s+as\s+(?:a\s+)?sub-?project\s+of\s+(.+?)\s*$', re.IGNORECASE
)

# Project Dependency phrasings — resolved to a standalone Link Project Dependency
# command per pair (Dependent Project = dependent, Depends on Project = depended-upon).
_DEPENDENT_ON_RE = re.compile(
    r'^\s*(?:link\s+)?(.+?)\s+as\s+dependent\s+on\s+(.+?)\s*$', re.IGNORECASE
)
_DEPENDS_ON_RE = re.compile(
    r'^\s*(.+?)\s+depends?\s+on\s+(.+?)\s*$', re.IGNORECASE
)

# Rename the plan document itself — distinct from renaming a Dr.Egeria command's
# Display Name. "rename the plan to X" / "rename it to X" / "call it X" /
# "call the plan X" / "title it X" / "name it X" / "change the title to X".
_RENAME_PLAN_RE = re.compile(
    r'^\s*(?:rename\s+(?:the\s+)?plan\s+to|rename\s+(?:it|this)\s+to|'
    r'call\s+(?:the\s+)?plan|call\s+it|title\s+it|name\s+it|'
    r'change\s+the\s+title\s+to)\s+["\']?(.+?)["\']?\s*[.!]?\s*$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public entry points (called from GovernancePlanAgent)
# ---------------------------------------------------------------------------

class PlanElicitor:
    """Drives the multi-turn planning Q&A flow."""

    # ------------------------------------------------------------------
    # Phase 1 — start a new elicitation session
    # ------------------------------------------------------------------

    def start(
        self,
        query: str,
        perspective: Optional[str],
        mode: str = "basic",
        template_name: Optional[str] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Decompose the user's intent, pre-fill what we can from the query,
        save a draft spec, and return the confirm_commands response.

        The first response shows the proposed command set (with template-informed
        field status) and asks the user to confirm or extend before any field
        elicitation begins.
        """
        from advisor.llm_client import get_planning_llm
        from advisor.agents.governance_plan_agent import GovernancePlanAgent

        llm = get_planning_llm()
        agent = GovernancePlanAgent()

        # --- Decompose intent ------------------------------------------
        _val_warnings: List[str] = []
        _keyword_suggestions: List[dict] = []
        _auto_appended: List[str] = []
        if template_name:
            commands = get_template_manager().template_to_commands(template_name)
            title = template_name
            purpose = query
        else:
            decomp = agent._decompose_intent(query, perspective, llm, egeria_credentials=egeria_credentials)
            title = decomp.get("title", query[:50])
            purpose = decomp.get("purpose", query)
            _val_warnings = decomp.get("validator_warnings") or []
            _keyword_suggestions = decomp.get("keyword_suggestions") or []
            from advisor.action_catalog import get_action_catalog
            catalog = get_action_catalog()
            _auto_appended = decomp.get("auto_appended") or []
            commands = [
                {
                    "action":       c.get("action", ""),
                    "display_name": c.get("display_name", ""),
                    "_answers_key": c.get("_answers_key", ""),
                    "description":  c.get("description", ""),
                    "rationale":    c.get("rationale", ""),
                    # narrative: prefer LLM-generated, fall back to catalog template
                    "narrative":    (
                        c.get("narrative", "")
                        or catalog.narrative_template(c.get("action", ""))
                    ),
                    # pre_filled from _make_cmd (keyed "pre_filled"); fall back to
                    # legacy "params" key for any older code paths
                    "pre_filled":   dict(c.get("pre_filled") or c.get("params") or {}),
                    "placeholders": {},
                }
                for c in decomp.get("commands", [])
                if c.get("action")
            ]

        if not commands:
            return _error_result(
                query,
                "I couldn't identify specific Dr.Egeria commands from your description.\n\n"
                "Try being more specific — for example:\n"
                "> *\"Set up a glossary called Finance Terms with five terms and a data steward\"*",
            )

        # --- Pre-fill names and values from the query text -------------
        pre_filled = self._pre_fill(query, commands, llm)
        for cmd in commands:
            action_fills = pre_filled.get(cmd["action"], {})
            cmd["pre_filled"].update(action_fills)
            if cmd.get("display_name") and "Display Name" not in cmd["pre_filled"]:
                cmd["pre_filled"]["Display Name"] = cmd["display_name"]

        # Build initial answers from pre_filled (pending_questions deferred
        # until after the user confirms the command set).
        # Use _answers_key (action:display_name) when present so multiple commands
        # of the same action type each get their own slot in the answers dict.
        answers: Dict[str, Dict[str, str]] = {}
        for cmd in commands:
            if cmd["pre_filled"]:
                key = cmd.get("_answers_key") or cmd["action"]
                answers[key] = dict(cmd["pre_filled"])

        dm = get_draft_manager()
        spec = dm.create(
            title=title,
            original_query=query,
            commands_identified=commands,
            pending_questions={"required": [], "optional": []},
            pre_filled_answers=answers,
            mode=mode,
            perspective=perspective,
            template_name=template_name,
        )
        # Override the default phase set by DraftManager.create
        spec["phase"] = "confirm_commands"
        spec["phase_label"] = _PHASE_LABELS["confirm_commands"]
        dm.save(spec)

        # Log session start
        try:
            from advisor.session_logger import get_session_logger
            import os
            sl = get_session_logger()
            sl.log_turn(
                spec["draft_id"], role="user", content=query,
                phase="confirm_commands", perspective=perspective,
                metadata={
                    "user": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
                    "mode": mode,
                    "template_name": template_name,
                    "commands_count": len(commands),
                },
            )
        except Exception:
            pass

        # Surface auto-corrections, auto-appended steps, and keyword suggestions
        init_note_parts: list[str] = []
        if _val_warnings:
            init_note_parts.append("Auto-corrected: " + "; ".join(_val_warnings))
        for note in _auto_appended:
            init_note_parts.append(f"ℹ️ {note}")
        if _keyword_suggestions:
            for s in _keyword_suggestions:
                init_note_parts.append(
                    f"⚠️ I interpreted **\"{s['phrase']}\"** as "
                    f"**{s['suggested_command']}** — if that's not right, "
                    f"say *\"change it to [command name]\"* or describe what you meant."
                )
        # Store suggestions in spec so re-shown if user loops back
        spec["keyword_suggestions"] = _keyword_suggestions

        init_note = "\n\n".join(init_note_parts) if init_note_parts else None
        return self._build_confirm_commands_response(spec, note=init_note)

    # ------------------------------------------------------------------
    # Phase dispatch — continue an existing draft
    # ------------------------------------------------------------------

    def process(
        self, draft_id: str, user_response: str,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Receive a user message for an active draft and advance the phase.
        """
        dm = get_draft_manager()
        spec = dm.load(draft_id)
        if spec is None:
            return _error_result(
                draft_id,
                f"Draft `{draft_id}` not found. It may have been discarded. "
                f"Start a new plan by describing what you want to set up.",
            )

        # Log the user turn
        try:
            from advisor.session_logger import get_session_logger
            get_session_logger().log_turn(
                draft_id, role="user", content=user_response,
                phase=spec.get("phase"),
                perspective=spec.get("perspective"),
            )
        except Exception:
            pass

        phase = spec.get("phase", "confirm_commands")

        # Draft/document desync check: "generate", "refine", and "template_offer"
        # all assume spec["doc_id"] points at a live, editable document. Resolve
        # first — a stale-but-repairable doc_id (the common case: an execution
        # renamed the file and this draft's pointer was never updated) should
        # self-heal here rather than falsely reporting the plan as permanently
        # deleted. If that fails too, surface a clear message instead of letting
        # the phase handler fail or misbehave (e.g. _apply_change silently
        # writing to a document that's no longer there).
        doc_id = dm.resolve_live_doc_id(draft_id, spec=spec)
        if doc_id != spec.get("doc_id"):
            spec["doc_id"] = doc_id
        if phase in ("generate", "refine", "template_offer") and doc_id:
            from advisor.governance_docs import get_doc_manager
            folder = get_doc_manager().folder_of(doc_id)
            if folder is None:
                result = _clarification_result(
                    spec,
                    f"This plan's document (`{doc_id}`) no longer exists — it may have "
                    f"been permanently deleted. Start a new plan, or check Version "
                    f"History if you believe this is unexpected.",
                    nav=["cancel"],
                )
                self._log_system_response(draft_id, spec, result)
                return result
            if folder == "trash":
                result = _clarification_result(
                    spec,
                    f"This plan (`{doc_id}`) was deleted. Restore it from the Trash "
                    f"section in the sidebar to continue editing it here, or start a new plan.",
                    nav=["cancel"],
                )
                self._log_system_response(draft_id, spec, result)
                return result

        if phase == "confirm_commands":
            result = self._handle_confirm_commands(spec, user_response, egeria_credentials=egeria_credentials)
        elif phase == "elicit_required":
            result = self._handle_elicit_required(spec, user_response)
        elif phase == "elicit_optional":
            result = self._handle_elicit_optional(spec, user_response)
        elif phase == "generate":
            result = self._handle_post_generate(spec, user_response, egeria_credentials=egeria_credentials)
        elif phase == "refine":
            result = self._handle_refine(spec, user_response, egeria_credentials=egeria_credentials)
        elif phase == "template_offer":
            result = self._handle_template_offer(spec, user_response)
        else:
            result = _error_result(draft_id, f"Unknown draft phase: {phase!r}")

        # Log system response and finalize session on terminal states
        self._log_system_response(draft_id, spec, result)
        return result

    # ------------------------------------------------------------------
    # Navigation actions
    # ------------------------------------------------------------------

    def back(self, draft_id: str) -> Dict[str, Any]:
        """Rewind one step in the history stack."""
        dm = get_draft_manager()
        spec = dm.load(draft_id)
        if spec is None:
            return _error_result(draft_id, f"Draft `{draft_id}` not found.")

        if not dm.pop_history(spec):
            return _clarification_result(
                spec,
                "You're already at the beginning — there's nowhere to go back to.\n\n"
                + self._format_current_state(spec),
                phase_override="elicit_required",
                can_go_back=False,
                nav=_NAV_FIRST,
            )

        dm.save(spec)
        phase = spec["phase"]
        if phase == "confirm_commands":
            return self._build_confirm_commands_response(spec)
        elif phase == "elicit_required":
            return self._build_elicit_required_response(spec)
        elif phase == "elicit_optional":
            return self._build_elicit_optional_response(spec)
        elif phase in ("generate", "refine"):
            return self._build_post_generate_response(spec)
        else:
            return self._build_confirm_commands_response(spec)

    def cancel(self, draft_id: str) -> Dict[str, Any]:
        """Delete the draft and return to idle."""
        try:
            from advisor.session_logger import get_session_logger
            import os
            get_session_logger().finalize(
                draft_id, outcome="cancelled",
                user=os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
            )
        except Exception:
            pass
        deleted = get_draft_manager().delete(draft_id)
        from advisor.agents.report_spec_elicitor import NO_SUCH_DRAFT
        return {
            "query": draft_id,
            # W1: "discarded" only when a draft was deleted
            "response": (
                ("Planning session cancelled. Your draft has been discarded." if deleted else NO_SUCH_DRAFT)
                + "\n\nStart a new plan any time by describing what you want to set up."
            ),
            "query_type": "plan_clarification",
            "routing_agent": "governance_plan_agent",
            "draft_id": None,
            "phase": "done",
            "can_go_back": False,
            "navigation": [],
            "next_context": None,
            "sources": [], "num_sources": 0,
            "retrieval_time": 0.0, "generation_time": 0.0,
            "avg_relevance_score": 0.0, "context_length": 0,
        }

    def save_and_exit(self, draft_id: str) -> Dict[str, Any]:
        """Confirm the draft is saved and exit the Q&A flow."""
        dm = get_draft_manager()
        spec = dm.load(draft_id)
        title = spec.get("title", draft_id) if spec else draft_id
        return {
            "query": draft_id,
            "response": (
                f"Your planning session for **{title}** has been saved.\n\n"
                f"You can pick it up from the **Drafts** section in the sidebar whenever "
                f"you're ready to continue. I'll show you exactly where you left off."
            ),
            "query_type": "plan_clarification",
            "routing_agent": "governance_plan_agent",
            "draft_id": None,   # clear active draft in UI
            "phase": "saved",
            "can_go_back": False,
            "navigation": [],
            "next_context": None,
            "sources": [], "num_sources": 0,
            "retrieval_time": 0.0, "generation_time": 0.0,
            "avg_relevance_score": 0.0, "context_length": 0,
        }

    def resume(self, draft_id: str) -> Dict[str, Any]:
        """Show a summary of where the draft is and offer to continue."""
        dm = get_draft_manager()
        spec = dm.load(draft_id)
        if spec is None:
            return _error_result(draft_id, f"Draft `{draft_id}` not found.")
        spec["doc_id"] = dm.resolve_live_doc_id(draft_id, spec=spec)
        return self._build_resume_response(spec)

    def restart_qa(self, draft_id: str) -> Dict[str, Any]:
        """Keep identified commands but clear all answers and restart Q&A."""
        dm = get_draft_manager()
        spec = dm.load(draft_id)
        if spec is None:
            return _error_result(draft_id, f"Draft `{draft_id}` not found.")

        # Clear answers (but keep pre-fills from the original query)
        spec["answers"] = {}
        spec["history_stack"] = []
        spec["summary_of_answers"] = ""
        # Rebuild pre-fills from commands' pre_filled dicts
        for cmd in spec["commands_identified"]:
            if cmd.get("pre_filled"):
                spec["answers"][cmd["action"]] = dict(cmd["pre_filled"])
        # Rebuild pending questions
        spec["pending_questions"] = self._build_pending_questions(
            spec["commands_identified"], spec["answers"], spec["mode"]
        )
        spec["phase"] = "elicit_required"
        spec["phase_label"] = _PHASE_LABELS["elicit_required"]
        dm.save(spec)
        return self._build_elicit_required_response(spec)

    def discard(self, draft_id: str) -> Dict[str, Any]:
        """Permanently delete a draft (same as cancel but used from sidebar)."""
        return self.cancel(draft_id)

    # ------------------------------------------------------------------
    # Phase handlers
    # ------------------------------------------------------------------

    def _handle_confirm_commands(
        self, spec: Dict, user_response: str,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Process the user's response to the confirm_commands step.

        Accepted responses:
          - Confirmation ("yes", "looks good", "continue", "generate now", …)
            → advance to elicit_required or directly to generate
          - Addition ("also add X", "include a term for Y", …)
            → re-decompose additions and merge, then re-show confirm_commands
          - Removal ("remove the zone", "don't need the hierarchy", …)
            → remove matching commands, re-show confirm_commands
          - "fill in details" / "details first"
            → advance to elicit_required
          - "generate now" / "skip details"
            → advance directly to generate (plan with TODOs)
        """
        from advisor.llm_client import get_planning_llm
        from advisor.agents.governance_plan_agent import GovernancePlanAgent

        dm = get_draft_manager()
        low = user_response.lower().strip()

        confirm_words = (
            "yes", "ok", "okay", "looks good", "that's right", "correct",
            "continue", "proceed", "sounds good", "perfect", "great",
        )
        generate_now_words = ("generate now", "skip details", "generate", "create plan now")
        detail_words = ("fill in", "details first", "add details", "more details")

        # --- Direct generation (skip field elicitation) ----------------
        if any(w in low for w in generate_now_words):
            dm.push_history(spec)
            spec["phase"] = "generate"
            spec["phase_label"] = _PHASE_LABELS["generate"]
            dm.save(spec)
            return self._generate_plan(spec)

        # --- Advance to field elicitation ------------------------------
        if any(w in low for w in confirm_words + detail_words):
            dm.push_history(spec)
            spec["phase"] = "elicit_required"
            spec["phase_label"] = _PHASE_LABELS["elicit_required"]
            spec["pending_questions"] = self._build_pending_questions(
                spec["commands_identified"], spec["answers"], spec["mode"]
            )
            dm.save(spec)
            return self._build_elicit_required_response(spec)

        # --- Duplicate / correction detection --------------------------
        # Must come before the re-decompose fallback to avoid treating
        # "steps 1 and 2 are duplicated" as a request to add something.
        dedup_signals = ("duplicate", "duplicated", "same step", "repeated", "appears twice")
        if any(w in low for w in dedup_signals):
            from advisor.plan_validator import validate_commands
            fixed, spec["answers"], val_warnings = validate_commands(
                spec["commands_identified"], spec["answers"]
            )
            if len(fixed) < len(spec["commands_identified"]):
                dm.push_history(spec)
                spec["commands_identified"] = fixed
                dm.save(spec)
                return self._build_confirm_commands_response(
                    spec,
                    note="Removed duplicates. Does this look right now?",
                )
            else:
                return self._build_confirm_commands_response(
                    spec,
                    note=(
                        "I checked for duplicates but didn't find any identical steps. "
                        "Could you point out which step number(s) are the problem? "
                        "For example: *\"remove step 2\"*"
                    ),
                )

        # "Completely wrong" — user wants to describe their intent from scratch
        restart_signals = (
            "completely wrong", "totally wrong", "not what i wanted",
            "not what i asked", "all wrong", "got it wrong", "missed the point",
            "that's not what i", "that is not what i", "misunderstood",
            "start over", "start again", "redo this", "try again",
            "nothing like what", "nothing like i asked",
        )
        if any(w in low for w in restart_signals):
            dm.push_history(spec)
            spec["phase"] = "confirm_commands"
            spec["commands_identified"] = []
            spec["answers"] = {}
            dm.save(spec)
            return _clarification_result(
                spec,
                "No problem — let's start fresh. "
                "Describe what you want to accomplish and I'll build a new plan.\n\n"
                "For example: *\"Create a campaign called X with sub-projects for A, B, C, "
                "led by [name] as project leader\"*",
                phase_override="confirm_commands",
                can_go_back=True,
                nav=["back", "cancel"],
            )

        correction_signals = (
            "that's wrong", "that is wrong", "incorrect", "not right",
            "shouldn't have", "should not have", "didn't ask", "i didn't ask",
            "that's not", "wrong step",
        )
        if any(w in low for w in correction_signals):
            return self._build_confirm_commands_response(
                spec,
                note=(
                    "Which step is wrong? You can:\n"
                    "- Say **\"remove step N\"** to delete a specific step\n"
                    "- Say **\"remove the [command name]\"** to remove by name\n"
                    "- Describe what should change instead\n"
                    "- Say **\"completely wrong\"** to describe your intent from scratch"
                ),
            )

        # --- Removal request -------------------------------------------
        removal_words = ("remove", "don't need", "drop", "delete", "take out", "without",
                         "remove step")
        if any(w in low for w in removal_words):
            updated = self._remove_commands(spec["commands_identified"], user_response)
            if len(updated) < len(spec["commands_identified"]):
                dm.push_history(spec)
                spec["commands_identified"] = updated
                # Purge answers for removed commands
                kept_actions = {c["action"] for c in updated}
                spec["answers"] = {k: v for k, v in spec["answers"].items() if k in kept_actions}
                dm.save(spec)
                return self._build_confirm_commands_response(
                    spec, note="I've removed those steps. Does the updated plan look right?"
                )

        # --- Move / reorder request -------------------------------------
        if _MOVE_RE.match(user_response.strip()):
            reordered = self._handle_move_request(spec["commands_identified"], user_response)
            if reordered is not None:
                dm.push_history(spec)
                spec["commands_identified"] = reordered
                dm.save(spec)
                return self._build_confirm_commands_response(
                    spec, note="Done — I've reordered the steps. Does the plan look right now?"
                )
            return self._build_confirm_commands_response(
                spec,
                note=(
                    "I wasn't able to tell which step to move or where — could you be more "
                    "specific?\n\nFor example: *\"move step 3 to be the first step\"* or "
                    "*\"move Create Campaign to step 1\"*."
                ),
            )

        # --- Rename the plan itself ("rename the plan to X", "call it X") --
        note = self._apply_rename_request(spec, user_response)
        if note is not None:
            dm.save(spec)
            return self._build_confirm_commands_response(
                spec, note=note + " Does the plan look right now?"
            )

        # --- Project hierarchy request ("make Campaign the parent of...",  --
        # --- "link Project 1 as a sub-project of Campaign") ----------------
        note = self._apply_hierarchy_request(spec, user_response)
        if note is not None:
            dm.save(spec)
            return self._build_confirm_commands_response(
                spec, note=note + " Does the plan look right now?"
            )

        # --- Project dependency request ("Project 1 depends on Project 2", -
        # --- "link project 1 as dependent on project 2") -------------------
        note = self._apply_dependency_request(spec, user_response)
        if note is not None:
            dm.save(spec)
            return self._build_confirm_commands_response(
                spec, note=note + " Does the plan look right now?"
            )

        # --- Addition / refinement request ----------------------------
        # Re-decompose the addition with context of what's already in the plan
        llm = get_planning_llm()
        agent = GovernancePlanAgent()
        try:
            new_decomp = agent._decompose_intent(
                user_response,
                spec.get("perspective"),
                llm,
                existing_commands=spec["commands_identified"],
                egeria_credentials=egeria_credentials,
            )
            from advisor.action_catalog import get_action_catalog
            catalog = get_action_catalog()
            new_commands = []
            for c in new_decomp.get("commands", []):
                if not c.get("action"):
                    continue
                new_commands.append({
                    "action":       c["action"],
                    "display_name": c.get("display_name", ""),
                    "_answers_key": c.get("_answers_key", ""),
                    "description":  c.get("description", ""),
                    "rationale":    c.get("rationale", ""),
                    "narrative":    (
                        c.get("narrative", "")
                        or catalog.narrative_template(c["action"])
                    ),
                    "pre_filled":   dict(c.get("pre_filled") or c.get("params") or {}),
                    "placeholders": {},
                })

            # Skip genuine duplicates only: same action AND same display_name already in
            # the plan. Matches validate_commands()'s _deduplicate() exactly (same
            # (action, display_name) key) — deliberately NOT "same action type", which
            # used to wrongly block e.g. a second "Create External Reference" step for a
            # different name ("also create an external reference for the PDR web site"
            # was silently discarded because the plan already had one External Reference,
            # even though they're for two different sites). "Create Project" needed no
            # special-casing once this was fixed the general way — every action type now
            # allows multiple distinctly-named instances the same way Create Project always did.
            existing_keys = {
                (c["action"], (c.get("display_name") or "").strip().lower())
                for c in spec["commands_identified"]
            }
            added = []
            for cmd in new_commands:
                key = (cmd["action"], (cmd.get("display_name") or "").strip().lower())
                if key not in existing_keys:
                    added.append(cmd)
                    existing_keys.add(key)

            if added:
                for cmd in added:
                    if cmd.get("display_name") and "Display Name" not in cmd["pre_filled"]:
                        cmd["pre_filled"]["Display Name"] = cmd["display_name"]
                    if cmd["pre_filled"]:
                        # Use action+display_name as key to keep sub-projects distinct
                        key = f"{cmd['action']}:{cmd.get('display_name','')}"
                        spec["answers"].setdefault(key, {}).update(cmd["pre_filled"])
                        cmd["_answers_key"] = key   # remember the key for later lookup

                from advisor.plan_validator import validate_commands
                merged_cmds = spec["commands_identified"] + added
                merged_cmds, spec["answers"], val_warnings = validate_commands(
                    merged_cmds, spec["answers"]
                )
                dm.push_history(spec)
                spec["commands_identified"] = merged_cmds
                dm.save(spec)
                note_parts = [f"Added {len(added)} step(s)."]
                if val_warnings:
                    note_parts.append(
                        "Auto-corrected: " + "; ".join(val_warnings)
                    )
                note_parts.append("Does the plan look right now?")
                return self._build_confirm_commands_response(
                    spec, note=" ".join(note_parts)
                )
        except Exception as exc:
            logger.debug(f"_handle_confirm_commands: addition re-decompose failed: {exc}")

        # Fallback — couldn't parse a structural change; prompt the user
        return self._build_confirm_commands_response(
            spec,
            note=(
                "I wasn't sure how to update the plan from that — could you be more specific?\n\n"
                "For example: *\"Add a glossary term for Revenue\"*, *\"Remove the governance zone\"*,\n"
                "or say **\"yes\"** if the steps look right."
            ),
        )

    def _remove_commands(self, commands: List[Dict], request: str) -> List[Dict]:
        """Heuristically drop commands mentioned in a removal request."""
        import re as _re
        low = request.lower()

        # "remove step N" or "remove steps N and M" — by 1-based index
        indices_to_remove: set = set()
        for m in _re.finditer(r'\bstep[s]?\s+(\d+)', low):
            idx = int(m.group(1)) - 1  # convert to 0-based
            if 0 <= idx < len(commands):
                indices_to_remove.add(idx)
        if indices_to_remove:
            result = [c for i, c in enumerate(commands) if i not in indices_to_remove]
            return result if result else commands

        # Keyword match on action name or display name
        result = []
        for cmd in commands:
            action_low = cmd["action"].lower()
            name_low = (cmd.get("display_name") or "").lower()
            keep = True
            for word in action_low.split() + name_low.split():
                if len(word) > 3 and word in low:
                    keep = False
                    break
            if keep:
                result.append(cmd)
        return result if result else commands  # never remove everything

    def _resolve_command_ref(self, ref_text: str, commands: List[Dict]) -> Optional[int]:
        """
        Resolve a natural-language reference to a single command's 0-based index.

        Tries, in order of confidence: "step N" (1-based) -> exact display-name
        match -> exact action-name match -> ordinal-of-type ("Project 2" = the
        2nd command whose action mentions "project") -> unique action-name
        substring match -> unique display-name keyword match. Returns None if
        nothing matches or a bare keyword matches more than one command
        (ambiguous — caller should ask for clarification rather than guess).

        This never treats an ordinal reference like "Project 1" as if it named
        a literal Display Name — it only reads existing commands' names to
        locate them, never writes a reference string back as a name.
        """
        ref = ref_text.strip().strip('"\'')
        if not ref:
            return None
        low = ref.lower()

        # "step N"
        m = re.search(r'\bstep\s+(\d+)\b', low)
        if m:
            idx = int(m.group(1)) - 1
            return idx if 0 <= idx < len(commands) else None

        # Exact display-name match — highest-confidence real named entity
        for i, cmd in enumerate(commands):
            dn = (cmd.get("display_name") or "").strip().lower()
            if dn and dn == low:
                return i

        # Exact action-name match ("Create Campaign")
        for i, cmd in enumerate(commands):
            if cmd["action"].strip().lower() == low:
                return i

        # Ordinal-of-type ("Project 2" -> 2nd command whose action mentions "project")
        om = re.match(r'^(.+?)\s+(\d+)$', ref)
        if om:
            type_word = om.group(1).strip().lower()
            ordinal = int(om.group(2))
            type_matches = [
                i for i, cmd in enumerate(commands) if type_word in cmd["action"].lower()
            ]
            if 1 <= ordinal <= len(type_matches):
                return type_matches[ordinal - 1]

        # Unique action-name substring match ("Campaign" -> "Create Campaign")
        action_matches = [i for i, cmd in enumerate(commands) if low in cmd["action"].lower()]
        if len(action_matches) == 1:
            return action_matches[0]

        # Unique display-name keyword match (last resort)
        kw_matches = [
            i for i, cmd in enumerate(commands)
            if low and low in (cmd.get("display_name") or "").lower()
        ]
        if len(kw_matches) == 1:
            return kw_matches[0]

        return None

    def _resolve_target_position(self, ref_text: str):
        """
        Resolve a move-target phrase to 'first', 'last', or a 0-based step index.
        Returns None if the phrase doesn't specify a recognizable position.
        """
        low = ref_text.strip().lower()
        if "first" in low:
            return "first"
        if "last" in low:
            return "last"
        m = re.search(r'\bstep\s+(\d+)\b', low)
        if m:
            return int(m.group(1)) - 1
        return None

    def _handle_move_request(
        self, commands: List[Dict], user_response: str
    ) -> Optional[List[Dict]]:
        """
        Parse a "move <ref> to [be] <target>" request and return the reordered
        command list, or None if the source/target couldn't be confidently resolved.
        """
        m = _MOVE_RE.match(user_response.strip())
        if not m:
            return None
        source_ref, target_ref = m.group(1).strip(), m.group(2).strip()

        src_idx = self._resolve_command_ref(source_ref, commands)
        if src_idx is None:
            return None
        target = self._resolve_target_position(target_ref)
        if target is None:
            return None

        new_commands = list(commands)
        moved = new_commands.pop(src_idx)
        if target == "first":
            tgt_idx = 0
        elif target == "last":
            tgt_idx = len(new_commands)
        else:
            tgt_idx = target - 1 if target > src_idx else target
        tgt_idx = max(0, min(tgt_idx, len(new_commands)))
        new_commands.insert(tgt_idx, moved)
        return new_commands

    def _resolve_bulk_command_refs(
        self, ref_text: str, commands: List[Dict],
        action_filter: Optional[str] = None, exclude_idx: Optional[int] = None,
    ) -> List[int]:
        """
        Resolve a reference phrase to one or more 0-based indices. Recognizes
        bulk selectors ("all projects", "all other projects", "the rest of the
        projects", "everything else") in addition to single references (which
        fall through to _resolve_command_ref). `action_filter`, if given,
        restricts matches to commands whose action contains this substring
        (case-insensitive); `exclude_idx` is always excluded (typically the
        anchor/parent of the request).
        """
        low = ref_text.strip().lower()
        bulk_words = ("all ", "all other", "the rest", "everything", "the other")
        is_bulk = any(w in low for w in bulk_words)
        if not is_bulk:
            idx = self._resolve_command_ref(ref_text, commands)
            return [idx] if idx is not None and idx != exclude_idx else []
        return [
            i for i, cmd in enumerate(commands)
            if (action_filter is None or action_filter in cmd["action"].lower())
            and i != exclude_idx
        ]

    def _split_multi_target_refs(self, text: str) -> List[str]:
        """
        Split "Project 2 and 3" / "Project 2, Project 3" / "Project 2 and Project 3"
        into individual reference strings, propagating a shared type-word ("Project")
        onto bare-number fragments so "3" alone resolves the same way "Project 3" would.
        """
        parts = re.split(r'\s*,\s*|\s+and\s+', text.strip())
        result: List[str] = []
        last_type_word: Optional[str] = None
        for p in parts:
            p = p.strip()
            if not p:
                continue
            m = re.match(r'^(.+?)\s+(\d+)$', p)
            if m:
                last_type_word = m.group(1).strip()
                result.append(p)
            elif re.match(r'^\d+$', p) and last_type_word:
                result.append(f"{last_type_word} {p}")
            else:
                result.append(p)
                last_type_word = None
        return result

    def _parse_hierarchy_request(self, text: str) -> Optional[Tuple[str, str]]:
        """Return (parent_ref, child_ref) if text expresses a project-hierarchy request."""
        t = text.strip()
        m = _HIERARCHY_PARENT_OF_RE.match(t)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        m = _HIERARCHY_SUBPROJECT_OF_RE.match(t)
        if m:
            # subject/object flipped: "X as a sub-project of Y" -> (parent=Y, child=X)
            return m.group(2).strip(), m.group(1).strip()
        return None

    def _parse_dependency_request(self, text: str) -> Optional[Tuple[str, str]]:
        """Return (child_ref_text, parent_ref_text) for a project-dependency request."""
        t = text.strip()
        m = _DEPENDENT_ON_RE.match(t)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        m = _DEPENDS_ON_RE.match(t)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return None

    def _apply_rename_request(self, spec: Dict, user_response: str) -> Optional[str]:
        """
        If user_response asks to rename the plan itself ("rename the plan to
        X", "call it X"), mutate spec["title"] in place and return a status
        note. Returns None if the text isn't a rename request at all. Caller
        is responsible for persisting the draft (dm.save) and, in the refine
        phase, also updating the document's H1 heading — this method only
        touches spec["title"].
        """
        m = _RENAME_PLAN_RE.match(user_response.strip())
        if not m:
            return None
        new_title = m.group(1).strip().strip('"\'')
        if not new_title:
            return None
        old_title = spec.get("title", "")
        get_draft_manager().push_history(spec)
        spec["title"] = new_title
        return f"Done — renamed the plan from \"{old_title}\" to **{new_title}**."

    def _apply_hierarchy_request(self, spec: Dict, user_response: str) -> Optional[str]:
        """
        If user_response expresses a project-hierarchy request ("make Campaign
        the parent of...", "link Project 1 as a sub-project of Campaign"),
        mutate spec["commands_identified"]/spec["answers"] in place — always an
        embedded mutation, never a standalone Link Project Hierarchy command
        (CLAUDE.md design rule 13; the validator would rewrite it anyway) — and
        return a status note (success or "couldn't resolve"). Returns None if
        the text isn't a hierarchy request at all, so the caller can try
        something else. Caller is responsible for persisting the draft
        (dm.save) after a non-None return.

        Appends to the *parent's* `Sub-Projects` field (Reference Name List,
        top-down) rather than setting `Parent ID` on each child (bottom-up).

        This was originally a workaround: `Parent ID`/`Parent Relationship Type
        Name` exist only in the advanced Create Project template, and
        _load_template()/_compose_command_block() used to validate against the
        basic tier regardless of spec["mode"], so an advanced-only field was
        silently dropped from the rendered document (BACKLOG.md PC-1).
        **That root cause is fixed** — composition now loads the draft's own
        tier — so the bottom-up form would render in advanced mode. The
        top-down `Sub-Projects` form is kept anyway: it is basic-tier, so it
        works at *both* tiers, which the bottom-up form does not.
        """
        hier = self._parse_hierarchy_request(user_response)
        if not hier:
            return None
        parent_ref, child_ref = hier
        commands = spec["commands_identified"]
        parent_idx = self._resolve_command_ref(parent_ref, commands)
        if parent_idx is None:
            return (f"I couldn't find a step matching \"{parent_ref}\" to use as the "
                    "parent. Which step did you mean?")
        parent_cmd = commands[parent_idx]
        parent_label = parent_cmd.get("display_name") or parent_cmd["action"]
        child_indices = self._resolve_bulk_command_refs(
            child_ref, commands, action_filter="project", exclude_idx=parent_idx
        )
        if not child_indices:
            return (f"I couldn't find any project steps matching \"{child_ref}\" to "
                    f"link under {parent_label}.")

        get_draft_manager().push_history(spec)
        parent_cmd.setdefault("pre_filled", {})
        existing_raw = parent_cmd["pre_filled"].get("Sub-Projects", "")
        existing_names = [n.strip() for n in existing_raw.split(",") if n.strip()]
        linked_labels = []
        for i in child_indices:
            child_cmd = commands[i]
            child_ref_name = (child_cmd.get("pre_filled") or {}).get("Qualified Name") \
                or child_cmd.get("display_name") or child_cmd["action"]
            if child_ref_name not in existing_names:
                existing_names.append(child_ref_name)
            linked_labels.append(child_cmd.get("display_name") or child_cmd["action"])
        parent_cmd["pre_filled"]["Sub-Projects"] = ", ".join(existing_names)
        key = parent_cmd.get("_answers_key") or parent_cmd["action"]
        spec["answers"].setdefault(key, {}).update(parent_cmd["pre_filled"])
        return (f"Done — linked {', '.join(linked_labels)} under **{parent_label}** "
                "as sub-project(s).")

    def _apply_dependency_request(self, spec: Dict, user_response: str) -> Optional[str]:
        """
        If user_response expresses a project-dependency request ("Project 1
        depends on Project 2 and 3", "link Project 1 as dependent on Project 2"),
        insert one standalone Link Project Dependency command per pair into
        spec["commands_identified"] (a peer relationship, not an embeddable
        creation-time attribute — unlike hierarchy). Returns a status note, or
        None if the text isn't a dependency request at all. Caller is
        responsible for persisting the draft (dm.save) after a non-None return.
        """
        dep = self._parse_dependency_request(user_response)
        if not dep:
            return None
        child_ref_text, parent_ref_text = dep
        commands = spec["commands_identified"]
        child_indices = []
        for ref in self._split_multi_target_refs(child_ref_text):
            child_indices.extend(
                self._resolve_bulk_command_refs(ref, commands, action_filter="project")
            )
        parent_indices = []
        for ref in self._split_multi_target_refs(parent_ref_text):
            parent_indices.extend(
                self._resolve_bulk_command_refs(ref, commands, action_filter="project")
            )
        child_indices = list(dict.fromkeys(child_indices))
        parent_indices = list(dict.fromkeys(parent_indices))

        if not child_indices or not parent_indices:
            return (f"I couldn't confidently match the projects in \"{user_response}\" to "
                    "steps in the plan — could you name them more specifically "
                    "(e.g. \"step 2\")?")

        new_links: List[Dict] = []
        added_labels = []
        for ci in child_indices:
            child_cmd = commands[ci]
            child_qn = (child_cmd.get("pre_filled") or {}).get("Qualified Name")
            for pi in parent_indices:
                if pi == ci or not child_qn:
                    continue
                parent_cmd = commands[pi]
                parent_qn = (parent_cmd.get("pre_filled") or {}).get("Qualified Name")
                if not parent_qn:
                    continue
                child_label = child_cmd.get("display_name") or child_cmd["action"]
                parent_label = parent_cmd.get("display_name") or parent_cmd["action"]
                new_links.append({
                    "action":       "Link Project Dependency",
                    "display_name": "",
                    "_answers_key": f"Link Project Dependency:{child_qn}->{parent_qn}",
                    "description":  "",
                    "rationale":    "",
                    "narrative":    f"Links {child_label} as dependent on {parent_label}.",
                    "pre_filled":   {"Dependent Project": child_qn, "Depends on Project": parent_qn},
                    "placeholders": {},
                })
                added_labels.append(f"{child_label} → {parent_label}")

        if not added_labels:
            return ("I found the projects but couldn't establish the dependency — "
                     "make sure both have names filled in.")

        get_draft_manager().push_history(spec)
        for link_cmd in new_links:
            spec["answers"][link_cmd["_answers_key"]] = dict(link_cmd["pre_filled"])
            commands.append(link_cmd)
        return f"Added {len(added_labels)} dependency link(s): {', '.join(added_labels)}."

    def _handle_elicit_required(self, spec: Dict, user_response: str) -> Dict[str, Any]:
        dm = get_draft_manager()
        llm_answers = self._parse_answers(
            user_response,
            spec["pending_questions"].get("required", []),
            spec["commands_identified"],
        )
        _merge_answers(spec["answers"], llm_answers)

        # Check which required fields are still missing
        still_missing = self._get_missing_required(spec)

        if still_missing:
            # Some required fields still unanswered — stay in this phase
            spec["pending_questions"]["required"] = still_missing
            spec["summary_of_answers"] = self._build_summary(spec)
            dm.save(spec)
            return self._build_elicit_required_response(spec, partial=True)

        # All required fields collected — advance to optional
        dm.push_history(spec)
        spec["phase"] = "elicit_optional"
        spec["phase_label"] = _PHASE_LABELS["elicit_optional"]
        spec["pending_questions"] = self._build_pending_questions(
            spec["commands_identified"], spec["answers"], spec["mode"]
        )
        spec["summary_of_answers"] = self._build_summary(spec)
        dm.save(spec)
        return self._build_elicit_optional_response(spec)

    def _handle_elicit_optional(self, spec: Dict, user_response: str) -> Dict[str, Any]:
        dm = get_draft_manager()
        low = user_response.lower().strip()

        # Check if user wants to skip optional fields
        if any(w in low for w in ("skip", "none", "no", "continue", "generate", "done", "that's all", "ok")):
            pass  # proceed to generate without merging optionals
        else:
            # Parse which optional fields they want and any values provided
            optional_qs = spec["pending_questions"].get("optional", [])
            if optional_qs:
                llm_answers = self._parse_answers(
                    user_response, optional_qs, spec["commands_identified"]
                )
                _merge_answers(spec["answers"], llm_answers)

        dm.push_history(spec)
        spec["phase"] = "generate"
        spec["phase_label"] = _PHASE_LABELS["generate"]
        spec["summary_of_answers"] = self._build_summary(spec)
        dm.save(spec)
        return self._generate_plan(spec)

    def _generate_plan(self, spec: Dict) -> Dict[str, Any]:
        """Build and save the plan document, then return a post-generate response."""
        from advisor.agents.governance_plan_agent import GovernancePlanAgent
        from advisor.governance_docs import get_doc_manager

        dm_draft = get_draft_manager()
        agent = GovernancePlanAgent()

        # Merge spec answers back into commands for the composer
        commands_with_params = self._merge_answers_into_commands(spec)

        # Generate narrative
        from advisor.llm_client import get_planning_llm
        llm = get_planning_llm()
        goal, requirements, approach = agent._generate_narrative(
            spec["original_query"],
            spec["original_query"],
            spec.get("perspective"),
            commands_with_params,
            llm,
        )

        doc_content = agent._compose_document(
            title=spec["title"],
            purpose=spec["original_query"],
            perspective=spec.get("perspective") or "Anyone",
            goal=goal,
            requirements=requirements,
            approach=approach,
            commands=commands_with_params,
        )

        doc_manager = get_doc_manager()
        doc_id = doc_manager.create(spec["title"], doc_content)

        spec["doc_id"] = doc_id
        spec["phase"] = "refine"
        spec["phase_label"] = _PHASE_LABELS["refine"]
        dm_draft.push_history(spec)
        dm_draft.save(spec)

        try:
            from advisor.metrics_collector import get_metrics_collector
            families = ",".join(sorted({c["action"].split()[0] for c in commands_with_params}))
            get_metrics_collector().record_plan_event(
                doc_id, "created",
                title=spec["title"],
                command_families=families,
                perspective=spec.get("perspective"),
            )
        except Exception:
            pass

        return self._build_post_generate_response(spec, doc_content=doc_content)

    def _handle_post_generate(
        self, spec: Dict, user_response: str,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """User has seen the generated plan — check if they want changes."""
        low = user_response.lower().strip()
        dm = get_draft_manager()

        done_signals = (
            "looks good", "that's good", "good", "perfect", "great", "done",
            "ok", "okay", "ready", "execute", "save as template", "template",
        )
        if any(s in low for s in done_signals) and "change" not in low and "edit" not in low:
            # Move to template offer
            dm.push_history(spec)
            spec["phase"] = "template_offer"
            spec["phase_label"] = _PHASE_LABELS["template_offer"]
            dm.save(spec)
            return self._build_template_offer_response(spec)

        # Treat this as a refinement request
        return self._handle_refine(spec, user_response, egeria_credentials=egeria_credentials)

    def _rebuild_command_sequence(self, spec: Dict, current_content: str) -> str:
        """
        Regenerate only the "## Command Sequence" block of a plan document from
        spec["commands_identified"], preserving the narrative header and any
        "## Outcome" section. Used for structural edits (reorder) that must not
        touch the narrative and must not go through the LLM. Mirrors the same
        technique used by PATCH /api/drafts/{id}/commands in app.py.
        """
        from advisor.agents.governance_plan_agent import GovernancePlanAgent

        commands_with_params = self._merge_answers_into_commands(spec)

        idx = current_content.find("## Command Sequence")
        narrative = current_content[:idx].strip() if idx != -1 else current_content.strip()

        outcome = ""
        out_idx = current_content.find("## Outcome")
        if out_idx != -1:
            outcome = current_content[out_idx:].strip()

        agent = GovernancePlanAgent()
        cmd_blocks = [
            agent._compose_command_block(cmd, i + 1)
            for i, cmd in enumerate(commands_with_params)
        ]
        new_content = narrative + "\n\n## Command Sequence\n\n" + "\n".join(cmd_blocks)
        if outcome:
            new_content += "\n\n" + outcome
        return new_content

    def _handle_refine(
        self, spec: Dict, user_response: str,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Parse a natural-language change request and update the plan document."""
        from advisor.governance_docs import get_doc_manager
        from advisor.llm_client import get_planning_llm

        dm = get_draft_manager()
        doc_id = dm.resolve_live_doc_id(spec["draft_id"], spec=spec)
        if not doc_id:
            return _error_result(spec["draft_id"], "No plan document found — please generate the plan first.")

        doc_manager = get_doc_manager()
        current_content = doc_manager.load(doc_id)
        if not current_content:
            return _error_result(spec["draft_id"], f"Plan document `{doc_id}` not found.")

        edited_by = (egeria_credentials or {}).get("user_id")

        low = user_response.lower().strip()
        done_signals = ("looks good", "that's good", "good", "perfect", "great", "done",
                        "ok", "okay", "ready", "no changes", "no more changes")
        if any(s in low for s in done_signals) and "change" not in low and "edit" not in low:
            dm.push_history(spec)
            spec["phase"] = "template_offer"
            spec["phase_label"] = _PHASE_LABELS["template_offer"]
            dm.save(spec)
            return self._build_template_offer_response(spec)

        # Move / reorder — handled deterministically against the structured
        # commands_identified list, never sent to the LLM. Reordering a whole
        # markdown document via free-text LLM patch is exactly the kind of
        # structural edit that risks duplication/corruption (see design rule 15
        # in CLAUDE.md re: keeping the LLM away from command structure).
        if _MOVE_RE.match(user_response.strip()):
            reordered = self._handle_move_request(spec["commands_identified"], user_response)
            if reordered is not None:
                spec["commands_identified"] = reordered
                new_content = self._rebuild_command_sequence(spec, current_content)
                synced_doc_id = dm.sync_document(spec["draft_id"], spec, new_content, edited_by=edited_by)
                return _clarification_result(
                    spec,
                    "Done — I've reordered the steps and updated the canvas. Describe "
                    "another change, or use **Validate** / **Execute** on the canvas when ready.",
                    can_go_back=False, nav=[],
                    extra={"doc_id": synced_doc_id or doc_id},
                )
            return _clarification_result(
                spec,
                "I wasn't able to tell which step to move or where — could you be more "
                "specific?\n\nFor example: *\"move step 3 to be the first step\"* or "
                "*\"move Create Campaign to step 1\"*.",
                can_go_back=False, nav=[],
                extra={"doc_id": doc_id},
            )

        # Rename the plan itself ("rename the plan to X", "call it X"). Updates
        # both spec["title"] and the document's H1 heading — DocumentManager
        # derives the displayed title fresh from the H1 line on every listing
        # (governance_docs._list_folder -> _extract_title), not from any
        # separately-stored metadata, so both must change together.
        note = self._apply_rename_request(spec, user_response)
        if note is not None:
            from advisor.governance_docs import _replace_title
            new_content = _replace_title(current_content, spec["title"])
            synced_doc_id = dm.sync_document(spec["draft_id"], spec, new_content, edited_by=edited_by)
            return _clarification_result(
                spec,
                f"{note} The canvas has been updated. Describe another change, or use "
                "**Validate** / **Execute** on the canvas when ready.",
                can_go_back=False, nav=[],
                extra={"doc_id": synced_doc_id or doc_id},
            )

        # Project hierarchy / dependency requests — same deterministic handlers
        # used in confirm_commands, reused here so relationship-establishment
        # works whether it's asked for before or after the plan is generated.
        note = self._apply_hierarchy_request(spec, user_response)
        if note is not None:
            new_content = self._rebuild_command_sequence(spec, current_content)
            synced_doc_id = dm.sync_document(spec["draft_id"], spec, new_content, edited_by=edited_by)
            return _clarification_result(
                spec,
                f"{note} The canvas has been updated. Describe another change, or use "
                "**Validate** / **Execute** on the canvas when ready.",
                can_go_back=False, nav=[],
                extra={"doc_id": synced_doc_id or doc_id},
            )

        note = self._apply_dependency_request(spec, user_response)
        if note is not None:
            new_content = self._rebuild_command_sequence(spec, current_content)
            synced_doc_id = dm.sync_document(spec["draft_id"], spec, new_content, edited_by=edited_by)
            return _clarification_result(
                spec,
                f"{note} The canvas has been updated. Describe another change, or use "
                "**Validate** / **Execute** on the canvas when ready.",
                can_go_back=False, nav=[],
                extra={"doc_id": synced_doc_id or doc_id},
            )

        # Guard: if the request looks like a single-word command or an affirmation
        # with no structural change verb, don't send it to the LLM — it would
        # interpret "execute" / "run" / "go" as modification instructions and
        # produce a truncated, corrupted plan document.
        _CHANGE_VERBS = ("add", "remove", "delete", "change", "update", "rename",
                         "replace", "move", "insert", "modify", "set", "create",
                         "put", "make", "use", "include", "exclude", "link")
        words = low.split()
        has_change_verb = any(w in _CHANGE_VERBS for w in words)
        if not has_change_verb and len(words) <= 4:
            return _clarification_result(
                spec,
                "I didn't recognise that as a plan change. Describe what you'd like "
                "to change — for example: *\"Rename the blueprint to X\"* or "
                "*\"Add a component for data quality\"*.\n\n"
                "Use **Validate** or **Execute** on the canvas when you're ready to proceed.",
                can_go_back=False, nav=[],
                extra={"doc_id": doc_id},
            )

        # Use LLM to apply the change
        llm = get_planning_llm()
        updated_content = self._apply_change(current_content, user_response, llm)

        if updated_content and updated_content != current_content:
            spec["phase"] = "refine"
            spec["phase_label"] = _PHASE_LABELS["refine"]
            synced_doc_id = dm.sync_document(spec["draft_id"], spec, updated_content, edited_by=edited_by)
            nc = len(re.findall(r"^## [^#]", updated_content, re.MULTILINE))
            return _clarification_result(
                spec,
                "Done — the canvas has been updated. Describe another change, "
                "or use **Validate** / **Execute** on the canvas when ready.",
                can_go_back=False,
                nav=[],
                extra={"doc_id": synced_doc_id or doc_id},
            )
        else:
            return _clarification_result(
                spec,
                "I wasn't able to identify a specific change from that — could you be more specific?\n\n"
                "For example: *\"Change the glossary name to Finance Terminology\"* or "
                "*\"Add a sub-project called Data Quality\"*.",
                can_go_back=False,
                nav=[],
                extra={"doc_id": doc_id},
            )

    def _handle_template_offer(self, spec: Dict, user_response: str) -> Dict[str, Any]:
        """User is responding to the template-save offer."""
        dm = get_draft_manager()
        low = user_response.lower().strip()

        decline_words = ("no", "skip", "don't", "nope", "not", "done", "finish")
        if any(w in low for w in decline_words):
            # Done — clean up draft
            dm.delete(spec["draft_id"])
            doc_id = spec.get("doc_id", "")
            return {
                "query": spec["draft_id"],
                "response": (
                    f"Great — your plan `{doc_id}` is saved in your inbox, ready to review and execute.\n\n"
                    f"Open the **Plan Editor** from the sidebar to review, validate, and execute when ready."
                ),
                "query_type": "plan",
                "routing_agent": "governance_plan_agent",
                "draft_id": None,
                "doc_id": doc_id,
                "phase": "done",
                "can_go_back": False,
                "navigation": [],
                "sources": [], "num_sources": 0,
                "retrieval_time": 0.0, "generation_time": 0.0,
                "avg_relevance_score": 0.0, "context_length": 0,
            }

        # Extract a template name from the response
        name_match = re.search(
            r'(?:call(?:ed)?|name(?:d)?|as)\s+["\']?([^"\']+?)["\']?\s*$',
            user_response, re.IGNORECASE
        )
        if name_match:
            template_name = name_match.group(1).strip()
        elif any(w in low for w in ("yes", "sure", "please", "save", "ok")):
            template_name = spec.get("title", "My Plan Template")
        else:
            # Assume the whole response IS the template name
            template_name = user_response.strip().strip('"\'') or spec.get("title", "My Plan Template")

        # Load and save the plan as a template
        doc_id = spec.get("doc_id", "")
        from advisor.governance_docs import get_doc_manager
        plan_content = get_doc_manager().load(doc_id)
        if plan_content:
            get_template_manager().save(template_name, plan_content)
            saved_msg = f"Saved as template **\"{template_name}\"** — available in the Templates section next time."
        else:
            saved_msg = "Couldn't load the plan document to save as template."

        dm.delete(spec["draft_id"])
        return {
            "query": spec["draft_id"],
            "response": (
                f"{saved_msg}\n\n"
                f"Your plan `{doc_id}` is also in your inbox, ready to execute."
            ),
            "query_type": "plan",
            "routing_agent": "governance_plan_agent",
            "draft_id": None,
            "doc_id": doc_id,
            "phase": "done",
            "can_go_back": False,
            "navigation": [],
            "sources": [], "num_sources": 0,
            "retrieval_time": 0.0, "generation_time": 0.0,
            "avg_relevance_score": 0.0, "context_length": 0,
        }

    # ------------------------------------------------------------------
    # Session logging helpers

    def _log_system_response(
        self, draft_id: str, spec: Dict, result: Dict[str, Any]
    ) -> None:
        """Log the system response and finalize session on terminal states."""
        try:
            from advisor.session_logger import get_session_logger
            import os
            sl = get_session_logger()
            response_text = result.get("response", "")
            result_phase  = result.get("phase", "")
            perspective   = spec.get("perspective") or result.get("perspective")

            sl.log_turn(
                draft_id, role="system",
                content=response_text[:2000],   # truncate long plan docs
                phase=result_phase,
                query_type=result.get("query_type"),
                perspective=perspective,
            )

            # Finalize on terminal states
            if result_phase in ("done", "saved", "error"):
                outcome_map = {
                    "done":  "plan_generated" if result.get("doc_id") else "cancelled",
                    "saved": "saved_in_progress",
                    "error": "error",
                }
                sl.finalize(
                    draft_id,
                    outcome=outcome_map.get(result_phase, result_phase),
                    doc_id=result.get("doc_id"),
                    perspective=perspective,
                    user=os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
                    command_families=",".join(sorted({
                        c["action"].split()[1] if len(c["action"].split()) > 1 else c["action"]
                        for c in spec.get("commands_identified", [])
                    })),
                )
        except Exception:
            pass

    # Response builders
    # ------------------------------------------------------------------

    def _build_confirm_commands_response(
        self, spec: Dict, note: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Show the proposed command sequence with template-informed field status,
        then ask the user to confirm, extend, or adjust before any field Q&A.
        """
        from advisor.agents.governance_plan_agent import GovernancePlanAgent
        agent = GovernancePlanAgent()

        # NOTE: spec["commands_identified"] order is authoritative here — it's
        # already priority-sorted once at initial decomposition (validate_commands
        # -> _sort_by_priority) and additions are merged in already-sorted. Do NOT
        # re-sort on every render: that would silently undo any user-requested
        # "move step N..." reorder the next time this response is built.
        commands = spec["commands_identified"]
        answers = spec["answers"]

        lines: List[str] = []
        if note:
            lines.append(f"{note}\n")
        lines.append(f"### {spec['title']}\n")
        lines.append("Here's what I'll create, in order:\n")

        for i, cmd in enumerate(commands, 1):
            action = cmd["action"]
            # Check action-only key first, then action:display_name key (used for sub-projects)
            answers_key = cmd.get("_answers_key") or action
            filled = answers.get(answers_key) or answers.get(action) or cmd.get("pre_filled") or {}
            dn = filled.get("Display Name") or cmd.get("display_name") or "*(name TBD)*"
            lines.append(f"**{i}. {action}** — {dn}")

            # Show pre-known params (e.g. Parent ID for sub-projects)
            pre_params = {k: v for k, v in filled.items()
                          if k not in ("Display Name",) and v}
            if pre_params:
                lines.append("   ✓ " + ", ".join(f"{k}: *{v}*" for k, v in pre_params.items()))

            # Use template metadata to flag any still-required fields
            template = agent._load_template(action)
            if template:
                needed = []
                for attr in template["attributes"]:
                    name = attr["name"]
                    if name == "Display Name":
                        continue
                    if attr.get("required") and not (filled.get(name) or filled.get(name.lower())):
                        needed.append(f"**{name}**")
                if needed:
                    lines.append(f"   ○ Still needed: {', '.join(needed)}")
            lines.append("")

        lines.append("---\n")
        lines.append("**Does this look right?**\n")
        lines.append(
            "- Say **\"yes\"** or **\"continue\"** to fill in any missing details\n"
            "- Say **\"generate now\"** to create the plan immediately (missing fields become placeholders)\n"
            "- Describe anything to **add**: *\"also create a sub-project for data collection\"*\n"
            "- Describe anything to **remove**: *\"remove the governance zone\"*\n"
            "- **Reorder** a step: *\"move step 3 to be the first step\"* or *\"move Campaign to step 1\"*\n"
            "- Set a **parent/sub-project**: *\"make Campaign the parent of all other projects\"*\n"
            "- Set a **dependency**: *\"Project 1 depends on Project 2 and 3\"*\n"
            "- Say **\"completely wrong\"** to describe your intent from scratch"
        )

        return _clarification_result(
            spec, "\n".join(lines),
            phase_override="confirm_commands",
            can_go_back=bool(spec.get("history_stack")),
            nav=_NAV_CONFIRM,
        )

    def _build_elicit_required_response(self, spec: Dict, partial: bool = False) -> Dict[str, Any]:
        required = spec["pending_questions"].get("required", [])
        answers = spec["answers"]
        commands = spec["commands_identified"]
        mode = spec.get("mode", "basic")

        lines = []
        if not partial:
            lines.append(f"### Planning: {spec['title']}\n")
            lines.append("Here's what I've identified from your description:\n")
            for cmd in commands:
                filled = answers.get(cmd["action"], {})
                dn = filled.get("Display Name") or cmd.get("display_name") or "*(name TBD)*"
                lines.append(f"- **{cmd['action']}** — {dn}")
            lines.append("")
            if mode == "advanced":
                lines.append("*(Advanced mode — all template fields will be shown)*\n")

        if required:
            lines.append("**I need a few more details:**\n")
            # Group questions by command
            by_action: Dict[str, List] = {}
            for q in required:
                by_action.setdefault(q["action"], []).append(q)

            for action, qs in by_action.items():
                lines.append(f"**{action}:**")
                for q in qs:
                    hint = f"*(e.g. {', '.join(q['valid_values'][:3])})*" if q.get("valid_values") else ""
                    desc = q.get("description", "")
                    req_mark = "⚠ required" if q.get("required") else "optional"
                    lines.append(f"- **{q['field']}** — {desc} {hint} *({req_mark})*")
                lines.append("")

            lines.append(
                "Please answer each question. You can answer all at once "
                "(e.g. *\"Zone: Data Management, Owner: finance-team\"*). "
                "Say **\"skip\"** for any you want to leave as TODO."
            )
        else:
            lines.append("All required fields are filled in. ✓")

        can_back = bool(spec["history_stack"])
        nav = _NAV_MIDDLE if can_back else _NAV_FIRST
        return _clarification_result(spec, "\n".join(lines), can_go_back=can_back, nav=nav)

    def _build_elicit_optional_response(self, spec: Dict) -> Dict[str, Any]:
        optional = spec["pending_questions"].get("optional", [])
        mode = spec.get("mode", "basic")

        lines = [f"### {spec['title']} — Required fields complete ✓\n"]
        lines.append(self._format_current_state(spec))
        lines.append("")

        if optional:
            lines.append("**Optional fields** (leave any blank to skip):\n")
            by_action: Dict[str, List] = {}
            for q in optional:
                by_action.setdefault(q["action"], []).append(q)
            for action, qs in by_action.items():
                lines.append(f"**{action}:**")
                for q in qs:
                    hint = f"*(e.g. {', '.join(q['valid_values'][:3])})*" if q.get("valid_values") else ""
                    lines.append(f"- **{q['field']}** {hint} — {q.get('description', '')}")
                lines.append("")
            lines.append(
                "Fill in any you'd like to include, or say **\"continue\"** / **\"skip\"** to generate the plan now."
            )
        else:
            lines.append("No optional fields to fill in.")
            lines.append("\nSay **\"continue\"** to generate the plan.")

        return _clarification_result(spec, "\n".join(lines), can_go_back=True, nav=_NAV_MIDDLE)

    def _build_post_generate_response(
        self, spec: Dict, doc_content: Optional[str] = None
    ) -> Dict[str, Any]:
        doc_id = spec.get("doc_id", "")
        if doc_content is None:
            from advisor.governance_docs import get_doc_manager
            doc_content = get_doc_manager().load(doc_id) or ""

        nc = len(re.findall(r"^<!-- Step \d+", doc_content, re.MULTILINE))
        msg = (
            f"Plan ready: **{spec['title']}** — {nc} command{'s' if nc != 1 else ''}, "
            f"saved to Inbox as `{doc_id}.md`.\n\n"
            "Review and edit commands in the canvas on the right, then use "
            "**Validate** or **Execute** when ready.\n\n"
            "*Describe any changes here and I'll update the plan.*"
        )
        return _clarification_result(
            spec, msg,
            can_go_back=False, nav=[],
            extra={"doc_id": doc_id, "query_type_override": "plan"},
        )

    def _build_resume_response(self, spec: Dict) -> Dict[str, Any]:
        from datetime import datetime
        updated = datetime.fromtimestamp(spec.get("updated_at", 0))
        age = _human_age(spec.get("updated_at", 0))

        lines = [
            f"### Resuming: {spec['title']}\n",
            f"*Last updated {age} — {spec['phase_label']}*\n",
            "",
            self._format_current_state(spec),
            "",
            "**What would you like to do?**",
        ]

        phase = spec["phase"]
        can_back = bool(spec["history_stack"])

        if phase in ("elicit_required", "elicit_optional"):
            return _clarification_result(
                spec, "\n".join(lines),
                can_go_back=can_back,
                nav=_NAV_MIDDLE if can_back else _NAV_FIRST,
                extra={"resume_options": ["continue", "restart", "discard"]},
            )
        elif phase in ("generate", "refine"):
            return _clarification_result(
                spec, "\n".join(lines),
                can_go_back=can_back,
                nav=_NAV_FINAL,
                extra={"doc_id": spec.get("doc_id"), "resume_options": ["continue", "discard"]},
            )
        else:
            return self._build_elicit_required_response(spec)

    def _build_template_offer_response(self, spec: Dict) -> Dict[str, Any]:
        doc_id = spec.get("doc_id", "")
        lines = [
            f"Your plan **{spec['title']}** is complete and saved to your inbox as `{doc_id}.md`.\n",
            "---\n",
            "**Would you like to save this as a reusable plan template?**\n",
            "Templates let you start future plans from this same structure — just fill in the specific names and details.\n",
            "- Say **\"yes\"** to save with the current name, or give it a name: *\"Save as Finance Glossary Template\"*",
            "- Say **\"no\"** or **\"skip\"** to finish without saving a template",
        ]
        return _clarification_result(
            spec, "\n".join(lines),
            can_go_back=True, nav=_NAV_FINAL,
        )

    # ------------------------------------------------------------------
    # Q&A helpers
    # ------------------------------------------------------------------

    def _build_pending_questions(
        self,
        commands: List[Dict],
        answers: Dict[str, Dict],
        mode: str,
    ) -> Dict[str, List]:
        """
        Build required and optional question lists from template attributes,
        excluding fields already in answers.
        """
        from advisor.agents.governance_plan_agent import GovernancePlanAgent
        agent = GovernancePlanAgent()

        required = []
        optional = []

        for cmd in commands:
            action = cmd["action"]
            # DELIBERATELY still basic-tier, even in advanced mode — this is the
            # half of PC-1 that is a product decision, not a bug fix, and it is
            # the project owner's call (BACKLOG.md PC-1, "elicitation half"). Loading
            # the advanced template here would make advanced mode ask about every
            # advanced-only field: measured 2026-08-28 across all 325 commands,
            # a mean of 27.4 optional questions per command instead of today's
            # 8.4 — roughly 137 questions on a five-command plan, versus ~42.
            # The composition half is fixed regardless, so advanced fields set
            # any other way (pre_filled, NL relationship editing) now survive.
            template = agent._load_template(action)
            if not template:
                continue

            filled = answers.get(action, {})

            for attr in template["attributes"]:
                name = attr["name"]
                # Skip if already answered
                if filled.get(name) or filled.get(name.lower()):
                    continue
                # Skip Display Name if pre-filled from command
                if name == "Display Name" and cmd.get("display_name"):
                    continue

                q = {
                    "action":      action,
                    "field":       name,
                    "required":    attr.get("required", False),
                    "type":        attr.get("type", "Simple"),
                    "description": attr.get("description", ""),
                    "valid_values": attr.get("valid_values", []),
                }
                if attr.get("required"):
                    required.append(q)
                elif mode == "advanced":
                    optional.append(q)
                else:
                    # Basic mode: offer a curated set of useful optionals
                    if name.lower() in ("description", "governance zone", "start date",
                                        "end date", "owner", "steward", "department"):
                        optional.append(q)

        return {"required": required, "optional": optional}

    def _get_missing_required(self, spec: Dict) -> List[Dict]:
        """Return required questions whose fields are still unanswered."""
        answers = spec["answers"]
        missing = []
        for q in spec["pending_questions"].get("required", []):
            action = q["action"]
            field = q["field"]
            filled = answers.get(action, {})
            if not filled.get(field) and not filled.get(field.lower()):
                missing.append(q)
        return missing

    def _pre_fill(
        self,
        query: str,
        commands: List[Dict],
        llm,
    ) -> Dict[str, Dict[str, str]]:
        """
        Use the LLM to extract field values from the user's initial query.
        Returns {action: {field: value}}.
        """
        field_list = []
        for cmd in commands:
            from advisor.agents.governance_plan_agent import GovernancePlanAgent
            template = GovernancePlanAgent()._load_template(cmd["action"])
            if template:
                for attr in template["attributes"][:6]:  # top 6 fields only
                    field_list.append(f"  {cmd['action']} → {attr['name']}")

        if not field_list:
            return {}

        prompt = (
            f"Extract any explicitly mentioned field values from this user request.\n"
            f"User request: \"{query}\"\n\n"
            f"Fields to look for:\n" + "\n".join(field_list) + "\n\n"
            f"Return ONLY a JSON object: {{\"Action Name\": {{\"Field Name\": \"extracted value\"}}}}.\n"
            f"Only include fields where the value is clearly stated in the request. "
            f"Do not invent values. If nothing clear, return {{}}.\nJSON:"
        )
        try:
            raw = llm.generate(prompt, temperature=0.0, max_tokens=500)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as exc:
            logger.debug(f"PlanElicitor._pre_fill failed: {exc}")
        return {}

    def _parse_answers(
        self,
        user_response: str,
        questions: List[Dict],
        commands: List[Dict],
    ) -> Dict[str, Dict[str, str]]:
        """
        Map a free-text user response to {action: {field: value}} using the LLM.
        """
        if not questions or not user_response.strip():
            return {}

        q_desc = "\n".join(
            f"  {q['action']} → {q['field']}: {q.get('description', '')}"
            for q in questions
        )

        prompt = (
            f"The user was asked these questions about a governance plan:\n{q_desc}\n\n"
            f"User's answer: \"{user_response}\"\n\n"
            f"Extract values from the user's answer for each question.\n"
            f"Return ONLY a JSON object: {{\"Action Name\": {{\"Field Name\": \"value\"}}}}.\n"
            f"Only include fields the user actually answered. If skipped or unclear, omit.\n"
            f"JSON:"
        )
        from advisor.llm_client import get_planning_llm
        llm = get_planning_llm()
        try:
            raw = llm.generate(prompt, temperature=0.0, max_tokens=600)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as exc:
            logger.debug(f"PlanElicitor._parse_answers failed: {exc}")
        return {}

    def _apply_change(self, doc_content: str, change_request: str, llm) -> str:
        """Use the LLM to apply a natural-language change to a plan document."""
        # Output budget must be at least as large as the input document to avoid
        # truncation.  Add 20% headroom for structural changes, cap at model max.
        input_chars = len(doc_content)
        # Rough char→token ratio ~3.5; ask for input_len/3 tokens with 20% headroom
        output_tokens = max(4000, int(input_chars / 3.5 * 1.2))
        output_tokens = min(output_tokens, 16000)  # stay within typical model ctx

        prompt = (
            f"You are editing a Dr.Egeria governance plan document.\n"
            f"Apply the following change to the document:\n\n"
            f"Change request: \"{change_request}\"\n\n"
            f"Current document:\n```markdown\n{doc_content}\n```\n\n"
            f"Return ONLY the complete updated document (no commentary, no code fences).\n"
            f"Preserve all existing sections and commands. Only change what was requested.\n"
            f"IMPORTANT: output the entire document — do not truncate or summarise.\n"
            f"Updated document:"
        )
        try:
            updated = llm.generate(prompt, temperature=0.1, max_tokens=output_tokens)
            # Strip accidental code fences
            updated = re.sub(r"^```(?:markdown)?\n?", "", updated.strip())
            updated = re.sub(r"\n?```$", "", updated.strip())
            return updated.strip()
        except Exception as exc:
            logger.warning(f"PlanElicitor._apply_change failed: {exc}")
            return doc_content

    def _merge_answers_into_commands(self, spec: Dict) -> List[Dict]:
        """Build a commands list with params merged from spec answers (for compose_document).

        Loads templates at the draft's own tier. Before that (BACKLOG.md PC-1)
        this always loaded basic, so any advanced-only field a user had set —
        `Parent ID`, `Parent Relationship Type Name`, anything reached through
        NL relationship editing — was dropped here without an error.
        """
        from advisor.agents.governance_plan_agent import GovernancePlanAgent, _command_order_key
        from advisor.action_catalog import get_action_catalog
        agent   = GovernancePlanAgent()
        catalog = get_action_catalog()
        mode    = spec.get("mode") or "basic"
        result  = []
        for cmd in spec["commands_identified"]:
            action      = cmd["action"]
            answers_key = cmd.get("_answers_key") or action
            params      = dict(spec["answers"].get(answers_key) or spec["answers"].get(action) or {})
            # Merge pre_filled params (e.g. Parent ID set during decomposition)
            for k, v in (cmd.get("pre_filled") or {}).items():
                params[k] = v
            if cmd.get("display_name") and "Display Name" not in params:
                params["Display Name"] = cmd["display_name"]
            template  = agent._load_template(action, mode)
            narrative = (
                cmd.get("narrative")
                or cmd.get("rationale")
                or catalog.narrative_template(action)
            )
            result.append({
                "action":          action,
                "display_name":    cmd.get("display_name", ""),
                "description":     cmd.get("description", ""),
                "narrative":       narrative,
                "spec":            {"rationale": cmd.get("rationale", "")},
                "template_parsed": template,
                "order":           _command_order_key(action),
                "params":          params,
            })
        # NOTE: spec["commands_identified"] order is authoritative — do NOT
        # re-sort by "order" here. This function backs both plan generation and
        # the Plan Canvas reorder PATCH (app.py: /api/drafts/{id}/commands), so
        # resorting would silently undo a user's manual/NL reorder on every save.
        # "order" is retained on each dict for callers that want the priority hint.
        return result

    def _format_current_state(self, spec: Dict) -> str:
        """Compact summary of what's been collected so far."""
        answers = spec["answers"]
        lines = ["**Collected so far:**\n"]
        for cmd in spec["commands_identified"]:
            action = cmd["action"]
            filled = answers.get(action, {})
            dn = filled.get("Display Name") or cmd.get("display_name") or "*(TBD)*"
            check = "✓" if filled else "○"
            detail = ", ".join(f"{k}: {v}" for k, v in filled.items() if k != "Display Name")
            lines.append(f"- {check} **{action}** — {dn}" + (f" ({detail})" if detail else ""))
        return "\n".join(lines)

    def _build_summary(self, spec: Dict) -> str:
        return self._format_current_state(spec)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _merge_answers(target: Dict[str, Dict], source: Dict[str, Dict]) -> None:
    for action, fields in source.items():
        target.setdefault(action, {}).update(fields)


def _clarification_result(
    spec: Dict,
    response_md: str,
    phase_override: Optional[str] = None,
    can_go_back: bool = False,
    nav: Optional[List[str]] = None,
    extra: Optional[Dict] = None,
) -> Dict[str, Any]:
    _phase = phase_override or spec.get("phase", "elicit_required")
    result: Dict[str, Any] = {
        "query":            spec.get("original_query", ""),
        "response":         response_md,
        "query_type":       "plan_clarification",
        "routing_agent":    "governance_plan_agent",
        "draft_id":         spec["draft_id"],
        "phase":            _phase,
        "can_go_back":      can_go_back,
        "navigation":       nav or _NAV_FIRST,
        "next_context": {
            "task":     "plan_elicitor",
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
    if extra:
        result.update(extra)
    return result


def _error_result(query: str, message: str) -> Dict[str, Any]:
    return {
        "query":            query,
        "response":         message,
        "query_type":       "plan_clarification",
        "routing_agent":    "governance_plan_agent",
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


def _human_age(ts: float) -> str:
    import time
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    elif diff < 3600:
        return f"{int(diff // 60)} minute{'s' if diff >= 120 else ''} ago"
    elif diff < 86400:
        return f"{int(diff // 3600)} hour{'s' if diff >= 7200 else ''} ago"
    else:
        return f"{int(diff // 86400)} day{'s' if diff >= 172800 else ''} ago"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_elicitor: Optional[PlanElicitor] = None


def get_plan_elicitor() -> PlanElicitor:
    global _elicitor
    if _elicitor is None:
        _elicitor = PlanElicitor()
    return _elicitor
