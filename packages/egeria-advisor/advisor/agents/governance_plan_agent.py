"""
GovernancePlanAgent — orchestrates Governance Plan Document generation.

Phase 1 workflow (document generation only — no execution):
  1. Intent decomposition — LLM breaks the user description into governance objects
  2. Template selection   — _find_dre_template_raw / parse_template per object
  3. Dependency ordering  — predefined command-ordering rules
  4. Parameter extraction — LLM fills known params, marks TODO for unknowns
  5. Narrative generation — Goal / Requirements / Approach sections
  6. Document composition — assembles full GPD markdown
  7. Persistence          — DocumentManager.create() → inbox/
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from advisor import confirm_gate


# ---------------------------------------------------------------------------
# Dependency ordering rules (lower number = must run first)
# ---------------------------------------------------------------------------

_COMMAND_ORDER_RULES: List[Tuple[str, int]] = [
    # More-specific patterns MUST come before less-specific ones (substring matching).
    ("create glossary term", 30),
    ("create glossary category", 22),
    ("create glossary", 10),
    ("create collection", 10),
    ("create project", 10),
    ("create community", 10),
    ("create governance zone", 10),
    ("create personal profile", 15),
    ("create actor profile", 15),
    ("create person role", 20),
    ("create it profile role", 20),
    ("create team role", 20),
    ("create team", 20),
    ("create governance definition", 20),
    ("create data asset", 30),
    ("create schema", 30),
    ("link term to category", 42),
    ("link term", 40),
    ("link glossary", 40),
    ("link person role appointment", 50),
    ("link person", 50),
    ("link team", 50),
    ("appointment", 50),
    ("assign", 50),
    ("classify", 55),
    ("set classification", 55),
]


def _command_order_key(command_name: str) -> int:
    """Return ordering weight for a command (lower = runs first)."""
    cn = command_name.lower().strip()
    for pattern, order in _COMMAND_ORDER_RULES:
        if pattern in cn:
            return order
    return 25


def _command_to_entity_type(command: str) -> str:
    """
    Derive a snake_case entity-type label from a catalog command name, e.g.
    "Create External Reference" -> "external_reference". Used only for display/
    title purposes when a command was resolved via the keyword index rather than
    a hand-written pattern -- the actual action to execute is carried separately
    (obj["action"]), not re-derived from this label.
    """
    words = command.split()
    if words and words[0].lower() in ("create", "link", "add", "attach", "classify", "set"):
        words = words[1:]
    return "_".join(w.lower() for w in words) or "project"


# ---------------------------------------------------------------------------
# GovernancePlanAgent
# ---------------------------------------------------------------------------


def unchecked_context_warnings(entities: dict) -> list[str]:
    """One warning per distinct reason Egeria was not read during enrichment (actor_found or
    exists_in_egeria is None), naming what went unchecked. Never phrased as "not found"."""
    by_reason: dict = {}
    for role in entities.get("roles", []):
        if role.get("person") and role.get("actor_found") is None and "actor_lookup_message" in role:
            by_reason.setdefault(role.get("actor_lookup_message") or "Egeria not reached.", []).append(role["person"])
    for obj in entities.get("objects", []):
        if obj.get("name") and obj.get("exists_in_egeria") is None and "exists_check_message" in obj:
            by_reason.setdefault(obj.get("exists_check_message") or "Egeria not reached.", []).append(obj["name"])
    return [
        f"Not checked against Egeria: {', '.join(repr(n) for n in names)}. {reason}"
        for reason, names in by_reason.items()
    ]

class GovernancePlanAgent:
    """
    Generates a full Governance Plan Document from a natural language description.

    Returns a standard RAGSystem result dict with query_type="plan" and a doc_id
    pointing to the saved inbox document.
    """

    def handle(
        self, query: str, perspective: str | None = None, mode: str = "basic",
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Start a new conversational planning session via PlanElicitor."""
        logger.info(f"GovernancePlanAgent.handle: delegating to PlanElicitor, query={query[:80]!r}")
        try:
            from advisor.agents.plan_elicitor import get_plan_elicitor
            result = get_plan_elicitor().start(query, perspective=perspective, mode=mode,
                                                egeria_credentials=egeria_credentials)
            result.setdefault("routing_agent", "governance_plan_agent")
            return result
        except Exception as exc:
            logger.error(f"GovernancePlanAgent.handle: PlanElicitor failed: {exc}")
            return _error_result(query, f"Planning session could not be started: {exc}")

    def continue_draft(
        self, draft_id: str, user_response: str,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Route a user response to the active planning Q&A session."""
        from advisor.agents.plan_elicitor import get_plan_elicitor
        result = get_plan_elicitor().process(draft_id, user_response, egeria_credentials=egeria_credentials)
        result.setdefault("routing_agent", "governance_plan_agent")
        return result

    def back(self, draft_id: str) -> Dict[str, Any]:
        from advisor.agents.plan_elicitor import get_plan_elicitor
        result = get_plan_elicitor().back(draft_id)
        result.setdefault("routing_agent", "governance_plan_agent")
        return result

    def cancel(self, draft_id: str) -> Dict[str, Any]:
        from advisor.agents.plan_elicitor import get_plan_elicitor
        result = get_plan_elicitor().cancel(draft_id)
        result.setdefault("routing_agent", "governance_plan_agent")
        return result

    def save_and_exit(self, draft_id: str) -> Dict[str, Any]:
        from advisor.agents.plan_elicitor import get_plan_elicitor
        result = get_plan_elicitor().save_and_exit(draft_id)
        result.setdefault("routing_agent", "governance_plan_agent")
        return result

    def resume(self, draft_id: str) -> Dict[str, Any]:
        from advisor.agents.plan_elicitor import get_plan_elicitor
        result = get_plan_elicitor().resume(draft_id)
        result.setdefault("routing_agent", "governance_plan_agent")
        return result

    def restart_qa(self, draft_id: str) -> Dict[str, Any]:
        from advisor.agents.plan_elicitor import get_plan_elicitor
        result = get_plan_elicitor().restart_qa(draft_id)
        result.setdefault("routing_agent", "governance_plan_agent")
        return result

    def discard(self, draft_id: str) -> Dict[str, Any]:
        from advisor.agents.plan_elicitor import get_plan_elicitor
        result = get_plan_elicitor().discard(draft_id)
        result.setdefault("routing_agent", "governance_plan_agent")
        return result

    def save_as_template(self, draft_id: str, template_name: str) -> Dict[str, Any]:
        from advisor.governance_draft import get_draft_manager
        from advisor.governance_docs import get_doc_manager, strip_outcome_sections
        from advisor.plan_templates import get_template_manager
        dm = get_draft_manager()
        spec = dm.load(draft_id)
        if spec is None:
            return _error_result(draft_id, f"Draft `{draft_id}` not found.")
        doc_id = dm.resolve_live_doc_id(draft_id, spec=spec)
        if not doc_id:
            return _error_result(draft_id, "Plan has not been generated yet — complete the Q&A first.")
        content = get_doc_manager().load(doc_id)
        if not content:
            return _error_result(draft_id, f"Plan document `{doc_id}` not found.")
        content = strip_outcome_sections(content)
        stem = get_template_manager().save(template_name, content)
        return {
            "query": f"save as template {template_name}",
            "response": f"Plan saved as template **{template_name}** (`{stem}.md`).",
            "query_type": "plan",
            "routing_agent": "governance_plan_agent",
            "draft_id": None,
            "sources": [], "num_sources": 0,
            "retrieval_time": 0.0, "generation_time": 0.0,
            "avg_relevance_score": 0.0, "context_length": 0,
        }

    def _handle_legacy_generate(self, query: str, perspective: str | None = None) -> Dict[str, Any]:
        """Original single-shot document generation (kept for direct calls)."""
        from advisor.llm_client import get_planning_llm
        from advisor.governance_docs import get_doc_manager
        from advisor.agents.dr_egeria_agent import DrEgeriaActionAgent

        llm = get_planning_llm()
        action_agent = DrEgeriaActionAgent()

        logger.info(
            f"GovernancePlanAgent._handle_legacy_generate: query={query[:80]!r}, perspective={perspective!r}"
        )

        # ------------------------------------------------------------------ #
        # Step 1: Decompose intent                                             #
        # ------------------------------------------------------------------ #
        decomp = self._decompose_intent(query, perspective, llm)
        title = decomp.get("title", "Data Management Plan")
        purpose = decomp.get("purpose", query)
        commands_spec = decomp.get("commands", [])

        if not commands_spec:
            return _error_result(
                query,
                "I couldn't identify the governance objects to create from your description. "
                "Please describe the specific items you want to set up — for example: "
                "'a glossary with terms and a data steward role'.",
            )

        # ------------------------------------------------------------------ #
        # Step 2 + 3: Template selection + dependency ordering                #
        # ------------------------------------------------------------------ #
        raw_commands: List[Dict] = []
        for spec in commands_spec:
            action = spec.get("action", "")
            display_name = spec.get("display_name", "")
            description = spec.get("description", "")
            template_parsed = self._load_template(action)
            raw_commands.append(
                {
                    "action": action,
                    "display_name": display_name,
                    "description": description,
                    "spec": spec,
                    "template_parsed": template_parsed,
                    "order": _command_order_key(action),
                }
            )

        ordered = sorted(raw_commands, key=lambda x: x["order"])

        # ------------------------------------------------------------------ #
        # Step 4: Parameter extraction                                         #
        # ------------------------------------------------------------------ #

        # Build cross-reference table: first Display Name per action family.
        # Used to seed reference attributes (e.g. Glossary Name for terms).
        _first_created: Dict[str, str] = {}
        for cmd in ordered:
            action = cmd["action"]
            dn = cmd.get("display_name") or cmd.get("description", "")
            family_key = action.split()[-1].lower()  # "Glossary", "Term" → last word
            if dn and family_key not in _first_created:
                _first_created[family_key] = dn
            # Also key by full action for exact matching
            if dn and action not in _first_created:
                _first_created[action] = dn

        _CROSS_REF_MAP: Dict[str, List[str]] = {
            # attr name → list of _first_created keys to try (in priority order)
            "Glossary Name": ["Create Glossary", "glossary"],
            "Project Name":  ["Create Campaign", "Create Project", "campaign", "project"],
            "Parent Project": ["Create Campaign", "campaign"],
        }

        filled: List[Dict] = []
        for cmd in ordered:
            params: Dict[str, Any] = {}
            template = cmd["template_parsed"]

            if template:
                try:
                    combined = f"{query}\n{cmd.get('display_name', '')}\n{cmd['description']}"
                    params = action_agent.extract_params(combined, template)
                except Exception as exc:
                    logger.warning(
                        f"GovernancePlanAgent: param extraction failed for "
                        f"{cmd['action']!r}: {exc}"
                    )

                # Seed the primary required Simple attribute (Display Name / Term Name /
                # etc.) from the decompose output if extract_params didn't fill it.
                seed_name = cmd.get("display_name") or cmd.get("description", "")
                if seed_name:
                    for attr in template["attributes"]:
                        if attr["required"] and attr["type"] in ("Simple", "simple", ""):
                            canon = attr["name"]
                            if not params.get(canon) and not params.get(canon.lower()):
                                params[canon] = seed_name
                            break

                # Seed optional Description from rationale if not already extracted.
                rationale = cmd.get("spec", {}).get("rationale", "")
                if rationale:
                    desc_attr = next(
                        (a for a in template["attributes"]
                         if a["name"].lower() == "description" and not a["required"]),
                        None,
                    )
                    if desc_attr:
                        canon = desc_attr["name"]
                        if not params.get(canon) and not params.get(canon.lower()):
                            params[canon] = rationale

                # Seed cross-reference attributes (e.g. Glossary Name on a term
                # command) from the first matching object created earlier in the plan.
                # These attrs are often optional but should be pre-filled when the
                # parent object is being created in the same plan.
                for attr in template["attributes"]:
                    candidates = _CROSS_REF_MAP.get(attr["name"], [])
                    if not candidates:
                        continue
                    canon = attr["name"]
                    if params.get(canon) or params.get(canon.lower()):
                        continue  # already filled
                    for key in candidates:
                        if key in _first_created:
                            params[canon] = _first_created[key]
                            break

            filled.append({**cmd, "params": params})

        # ------------------------------------------------------------------ #
        # Step 5: Narrative generation                                         #
        # ------------------------------------------------------------------ #
        goal, requirements, approach = self._generate_narrative(
            query, purpose, perspective, filled, llm
        )

        # ------------------------------------------------------------------ #
        # Step 6: Document composition                                         #
        # ------------------------------------------------------------------ #
        doc_content = self._compose_document(
            title=title,
            purpose=purpose,
            perspective=perspective or "Anyone",
            goal=goal,
            requirements=requirements,
            approach=approach,
            commands=filled,
        )

        # ------------------------------------------------------------------ #
        # Step 7: Save to inbox/                                               #
        # ------------------------------------------------------------------ #
        doc_manager = get_doc_manager()
        doc_id = doc_manager.create(title, doc_content)
        logger.info(f"GovernancePlanAgent: saved plan doc_id={doc_id}")

        try:
            from advisor.metrics_collector import get_metrics_collector
            families = ",".join(sorted({c["action"].split()[0] for c in filled}))
            get_metrics_collector().record_plan_event(
                doc_id, "created",
                title=title,
                command_families=families,
                perspective=perspective,
            )
        except Exception:
            pass

        nc = len(filled)
        summary = (
            f"I've created a Data Management Plan for **{title}**.\n\n"
            f"Saved to your inbox as `{doc_id}.md` "
            f"({nc} command{'s' if nc != 1 else ''} in sequence).\n\n"
            f"Review the plan below. You can ask me to make changes, add or remove commands, "
            f"or adjust any parameter values. "
            f"When you are satisfied, say **'execute the plan {doc_id}'** to submit it to Dr.Egeria.\n\n"
            f"---\n\n{doc_content}"
        )

        return {
            "query": query,
            "response": summary,
            "query_type": "plan",
            "doc_id": doc_id,
            "title": title,
            "num_commands": nc,
            "sources": [f"Dr.Egeria template: {c['action']}" for c in filled],
            "num_sources": nc,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": len(doc_content),
        }

    # ---------------------------------------------------------------------- #
    # Execution (Phase 2)                                                      #
    # ---------------------------------------------------------------------- #

    # ── The plan confirm gate (2026-11-13; advisor.confirm_gate) ──────────────────────────────────
    #
    # A plan document is processed in Egeria only after it was SHOWN (prepare_execute: the Command
    # Sequence, and a pending action holding the SHA-256 of the whole document as it was then) and
    # CONFIRMED with that digest by the same sign-in within PLAN_CONFIRM_TTL_S. Before running, the
    # document is read again: if it is no longer the text that was shown, nothing runs. Each pending
    # action runs once. dry_run writes nothing and is not gated.

    def prepare_execute(
        self,
        doc_id: str,
        source_folder: str = "inbox",
        draft_id: str | None = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
        action: str = "execute",
    ) -> Dict[str, Any]:
        """Show a plan before it runs, and register the pending action that confirm()/execute() need.

        action "execute" (inbox, or outbox in place for Re-run) or "retry" (outbox → inbox, then run).
        Nothing is written. Returns query_type "plan_confirm" with pending_id and plan_digest."""
        if action not in ("execute", "retry"):
            raise ValueError(f"unknown plan action {action!r}")
        folder = "outbox" if action == "retry" else source_folder
        content = _load_plan(doc_id, folder)
        if not content:
            return _error_result(doc_id, f"Plan document `{doc_id}` not found in {folder}.")
        command_section = self._extract_command_section(content)
        if not command_section.strip():
            return _error_result(doc_id, f"Plan document `{doc_id}` has no Command Sequence section to execute.")
        conn, refused = _plan_conn(doc_id, egeria_credentials)
        if refused:
            return refused
        names = plan_command_names(command_section)
        refused = refused_plan_steps(names)
        if refused:
            return _refused(doc_id, _steps_refused_words(refused))
        n = len(names)
        from advisor.governance_docs import previously_executed
        ran_before = previously_executed(content) if folder == "inbox" else None   # recovered from outbox
        rerun = action == "retry" or folder == "outbox" or bool(ran_before)
        unsafe = non_idempotent_commands(names) if rerun else []
        pending = _PLAN_GATE.register(confirm_gate.caller_identity(conn), content,
                                      {"doc_id": doc_id, "source_folder": folder, "draft_id": draft_id,
                                       "action": action, "ack_required": bool(unsafe), "non_idempotent": unsafe})
        verb = {"execute": "execute" if folder == "inbox" else "re-run", "retry": "retry"}[action]
        reply = (f"Reply **yes** to {verb} exactly this plan, or **no** to cancel." if not unsafe else
                 "Reply **yes, re-run** to run all of it again anyway, or **no** to cancel. (A plain **yes** "
                 "is not enough for a re-run that repeats these commands.)")
        return {
            "query": doc_id,
            "response": (
                f"**Plan `{doc_id}`**: {n} command(s) would be processed in Egeria as **{conn['user_id']}**. "
                f"**Nothing has been written.**"
                + (f" This plan ran before ({ran_before}) and was recovered for editing, so running it is a "
                   f"re-run." if ran_before else "")
                + f"\n\n```markdown\n{command_section.strip()}\n```\n\n"
                + (_unsafe_words(unsafe) + "\n\n" if unsafe else "")
                + f"{reply} (Plan digest `{pending.digest[:12]}`; good for {PLAN_CONFIRM_TTL_S // 60} minutes. "
                f"If the plan is edited before you confirm, it will not run.)"
            ),
            "rerun": rerun,
            "previously_executed": ran_before,
            "ack_required": bool(unsafe),
            "non_idempotent": unsafe,
            "command_names": names,
            "query_type": "plan_confirm",
            "state": "confirm_required",
            "confirm_required": True,
            "doc_id": doc_id,
            "source_folder": folder,
            "action": action,
            "command_section": command_section,
            "command_count": n,
            "pending_id": pending.pending_id,
            "plan_digest": pending.digest,
            "next_context": {"task": "plan_execute_confirm", "pending_id": pending.pending_id,
                             "digest": pending.digest, "draft_id": draft_id, "ack_required": bool(unsafe)},
            "sources": [], "num_sources": 0, "retrieval_time": 0.0, "generation_time": 0.0,
            "avg_relevance_score": 0.0, "context_length": len(command_section),
        }

    def confirm(
        self,
        pending_id: str,
        digest: str,
        perspective: str | None = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
        ack_non_idempotent: bool = False,
    ) -> Dict[str, Any]:
        """Run the plan a pending action names (chat: "yes" to a shown plan; "yes, re-run" also acknowledges
        a re-run's non-idempotent commands)."""
        pending = _PLAN_GATE.pending.get(pending_id) if isinstance(pending_id, str) else None
        if pending is None:
            return _refused(pending_id or "", PLAN_CONFIRM_UNKNOWN)
        pl = pending.payload
        if pl["action"] == "retry":
            return self.retry(pl["doc_id"], perspective=perspective, egeria_credentials=egeria_credentials,
                              pending_id=pending_id, digest=digest, ack_non_idempotent=ack_non_idempotent)
        return self.execute(pl["doc_id"], perspective=perspective, source_folder=pl["source_folder"],
                            draft_id=pl["draft_id"], egeria_credentials=egeria_credentials,
                            pending_id=pending_id, digest=digest, ack_non_idempotent=ack_non_idempotent)

    def cancel_execute(self, pending_id: str, draft_id: str | None = None) -> Dict[str, Any]:
        """Drop a shown plan; nothing runs. A draft's Q&A context comes back. (cancel() is the draft's own.)"""
        _PLAN_GATE.discard(pending_id)
        out = _error_result(pending_id or "", "Cancelled: the plan was not run, and nothing was written.")
        out.update(query_type="general", state="cancelled",
                   next_context={"task": "plan_elicitor", "draft_id": draft_id} if draft_id else None)
        return out

    def _gated(self, doc_id, source_folder, action, pending_id, digest, egeria_credentials, ack=False):
        """(the pending action, None) when the plan may run now, else (None, a refusal result)."""
        conn, refused = _plan_conn(doc_id, egeria_credentials)
        if refused:
            return None, refused
        if not pending_id:
            return None, _refused(doc_id, PLAN_CONFIRM_REQUIRED)
        pending, why = _PLAN_GATE.check(pending_id, confirm_gate.caller_identity(conn), digest)
        if pending is None:
            return None, _refused(doc_id, {confirm_gate.UNKNOWN: PLAN_CONFIRM_UNKNOWN,
                                           confirm_gate.OTHER_SIGN_IN: PLAN_CONFIRM_OTHER_SIGN_IN,
                                           confirm_gate.CHANGED: PLAN_CONFIRM_CHANGED}[why])
        pl = pending.payload
        if (pl["doc_id"], pl["source_folder"], pl["action"]) != (doc_id, source_folder, action):
            return None, _refused(doc_id, PLAN_CONFIRM_OTHER_PLAN)
        if pl.get("ack_required") and ack is not True:
            # not consumed: the same shown plan can still be run, with the acknowledgement
            out = _refused(doc_id, PLAN_ACK_REQUIRED + "\n\n" + _unsafe_words(pl["non_idempotent"]))
            out.update(ack_required=True, non_idempotent=pl["non_idempotent"],
                       next_context={"task": "plan_execute_confirm", "pending_id": pending_id, "digest": digest,
                                     "draft_id": pl.get("draft_id"), "ack_required": True})
            return None, out
        return pending, None

    def execute(
        self,
        doc_id: str,
        perspective: str | None = None,
        dry_run: bool = False,
        source_folder: str = "inbox",
        draft_id: str | None = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
        pending_id: str | None = None,
        digest: str | None = None,
        ack_non_idempotent: bool = False,
    ) -> Dict[str, Any]:
        """Run a plan that was shown (prepare_execute) and confirmed with its digest; once. A re-run that
        repeats non-idempotent commands also needs ack_non_idempotent=True.

        dry_run extracts the commands and writes nothing, so it needs no confirm. Otherwise refused, in
        words, without a pending action this sign-in was shown for this plan, with the shown digest,
        within the TTL, and when the document is still the text that was shown."""
        if dry_run:
            return self._run_plan(doc_id, perspective=perspective, dry_run=True, source_folder=source_folder,
                                  draft_id=draft_id, egeria_credentials=egeria_credentials)
        pending, refused = self._gated(doc_id, source_folder, "execute", pending_id, digest, egeria_credentials,
                                       ack=ack_non_idempotent)
        if refused:
            return refused
        with pending.lock:   # one run per pending action; a retry gets the first answer
            if pending.result is not None:
                return _already(pending.result)
            content = _load_plan(doc_id, source_folder) or ""
            if confirm_gate.text_digest(content) != pending.digest:
                _PLAN_GATE.discard(pending.pending_id)
                return _refused(doc_id, PLAN_DOCUMENT_CHANGED)
            refused = refused_plan_steps(plan_command_names(self._extract_command_section(content)))
            if refused:   # the real guard: a plan never dispatches a step chat may not propose
                _PLAN_GATE.discard(pending.pending_id)
                return _refused(doc_id, _steps_refused_words(refused))
            pending.result = self._run_plan(
                doc_id, perspective=perspective, source_folder=source_folder,
                draft_id=draft_id or pending.payload.get("draft_id"), egeria_credentials=egeria_credentials)
            return pending.result

    def _run_plan(
        self,
        doc_id: str,
        perspective: str | None = None,
        dry_run: bool = False,
        source_folder: str = "inbox",
        draft_id: str | None = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an approved plan document and append the outcome section. UNGATED: call execute(),
        which checks the confirm and digest first; only execute()/retry() call this.

        Steps:
          1. Load document from source_folder ("inbox" — normal first execution;
             "outbox" — Re-run Now, re-executing an already-executed plan in place)
          2. Extract the Command Sequence section
          3. Submit to Dr.Egeria via DrEgeriaActionAgent.execute()
          4. Run OutcomeReporter to produce outcome section
          5. inbox source: move document to outbox with outcome appended
             outbox source: append a new "## Outcome (Run N)" section in place

        draft_id: when given and this is a first execution (inbox -> outbox),
        the originating draft's doc_id is updated to the new outbox id, so a
        later "resume draft" doesn't hand back a doc_id that no longer exists
        anywhere. See BACKLOG.md.

        Returns a standard result dict with query_type="plan_executed".
        """
        from advisor.governance_docs import get_doc_manager
        from advisor.agents.dr_egeria_agent import DrEgeriaActionAgent
        from advisor.agents.outcome_reporter import get_outcome_reporter

        doc_manager = get_doc_manager()
        plan_content = (
            doc_manager.load_outbox(doc_id) if source_folder == "outbox"
            else doc_manager.load(doc_id)
        )

        if not plan_content:
            return _error_result(
                doc_id,
                f"Plan document `{doc_id}` not found in {source_folder}.",
            )

        # Extract the Command Sequence section for execution
        command_section = self._extract_command_section(plan_content)
        if not command_section.strip():
            return _error_result(
                doc_id,
                f"Plan document `{doc_id}` has no Command Sequence section to execute.",
            )

        # Count H2 command headers so OutcomeReporter can detect partial execution
        expected_command_count = len(re.findall(r'^##\s+\S', command_section, re.MULTILINE))

        logger.info(
            f"GovernancePlanAgent.execute: doc_id={doc_id!r}, "
            f"dry_run={dry_run}, commands={expected_command_count}, "
            f"command_chars={len(command_section)}"
        )

        # Execute via Dr.Egeria MCP
        action_agent = DrEgeriaActionAgent()
        logger.info(
            f"GovernancePlanAgent.execute: sending {len(command_section)} chars to Dr.Egeria\n"
            f"--- command section (first 400 chars) ---\n{command_section[:400]}\n---"
        )
        try:
            execution_output = action_agent.execute(
                command_section,
                directive="process",
                dry_run=dry_run,
                egeria_credentials=egeria_credentials,
            )
        except ConnectionError as exc:
            return _error_result(
                doc_id,
                f"Could not execute plan: Egeria MCP server is not reachable.\n\n"
                f"Ensure Dr.Egeria is running, then try again.\n\nDetails: {exc}",
            )
        except Exception as exc:
            execution_output = (
                f"Execution error: {exc}\n\n"
                f"This usually means the Egeria REST API returned an unexpected response. "
                f"Check that Egeria is running at the configured URL and that credentials are valid."
            )
            logger.error(f"GovernancePlanAgent.execute: MCP call failed: {exc}", exc_info=True)

        if dry_run:
            return {
                "query": doc_id,
                "response": (
                    f"**Dry run — plan not submitted to Dr.Egeria.**\n\n"
                    f"Command sequence extracted from `{doc_id}`:\n\n"
                    f"```markdown\n{command_section}\n```"
                ),
                "query_type": "plan_executed",
                "doc_id": doc_id,
                "dry_run": True,
                "sources": [],
                "num_sources": 0,
                "retrieval_time": 0.0,
                "generation_time": 0.0,
                "avg_relevance_score": 0.0,
                "context_length": len(command_section),
            }

        # Parse structured response from Dr.Egeria
        ex_success, ex_output, ex_val_errs, ex_exe_errs, ex_counts = _parse_dr_egeria_response(execution_output)

        # Split Dr.Egeria's echoed output into refreshed command definitions
        # (resolved GUID/Qualified Name etc., for re-editing) and materialized
        # display content (report tables, Mermaid diagrams), instead of leaving
        # them mixed together in one raw blob. Only the inbox (first-execution)
        # path refreshes the Command Sequence in place today — see BACKLOG.md.
        materialized_display = ""
        try:
            original_steps = self._parse_command_steps(command_section)
            echoed_blocks, _provenance = _split_augmented_output(ex_output or "")
            if original_steps and echoed_blocks:
                new_command_md, materialized_display = self._rebuild_command_sequence(
                    original_steps, echoed_blocks, ex_counts.get('detail', []),
                    mode=self._draft_mode(draft_id),
                )
                if source_folder != "outbox":
                    refreshed_plan_content = self._replace_command_section(plan_content, new_command_md)
                    if not doc_manager.update(doc_id, refreshed_plan_content):
                        logger.warning(
                            f"GovernancePlanAgent.execute: could not refresh Command "
                            f"Sequence for {doc_id!r} before moving to outbox"
                        )
        except Exception as exc:
            logger.warning(f"GovernancePlanAgent.execute: could not split augmented output: {exc}")

        # Generate outcome section — pass structured data to reporter
        reporter = get_outcome_reporter()
        outcome_md = reporter.generate(
            plan_content, ex_output or execution_output, perspective,
            expected_command_count=ex_counts.get('total', expected_command_count),
            commands_succeeded=ex_counts.get('succeeded'),
            commands_failed=ex_counts.get('failed'),
            validation_errors=ex_val_errs,
            execution_errors=ex_exe_errs,
            commands_detail=ex_counts.get('detail', []),
            materialized_display=materialized_display,
            executed_by=(egeria_credentials or {}).get("user_id"),
        )

        # Append raw Dr.Egeria output as a separate section so it's always available.
        # This contains the augmented plan markdown, View Report output, and Mermaid diagrams.
        raw_section = _build_raw_output_section(ex_output or execution_output)

        outbox_doc_id = doc_id
        if source_folder == "outbox":
            moved = doc_manager.append_rerun_outcome(doc_id, outcome_md + "\n\n" + raw_section)
            if moved:
                logger.info(f"GovernancePlanAgent.execute: appended re-run outcome to {doc_id}")
            else:
                logger.warning(f"GovernancePlanAgent.execute: could not append re-run outcome to {doc_id}")
        else:
            moved_doc_id = doc_manager.move_to_outbox(doc_id, outcome_md + "\n\n" + raw_section)
            if moved_doc_id:
                outbox_doc_id = moved_doc_id
                logger.info(f"GovernancePlanAgent.execute: moved {doc_id} to outbox as {outbox_doc_id}")
                if draft_id:
                    from advisor.governance_draft import get_draft_manager
                    if not get_draft_manager().update_doc_id(draft_id, outbox_doc_id):
                        logger.warning(
                            f"GovernancePlanAgent.execute: could not update draft "
                            f"{draft_id!r}.doc_id to {outbox_doc_id!r}"
                        )
            else:
                logger.warning(
                    f"GovernancePlanAgent.execute: could not move {doc_id} to outbox"
                )

        status_line = self._extract_status_from_outcome(outcome_md)

        try:
            from advisor.metrics_collector import get_metrics_collector
            get_metrics_collector().record_plan_event(
                outbox_doc_id, "executed",
                outcome_status=status_line,
                perspective=perspective,
            )
        except Exception:
            pass

        response = (
            f"Plan **{outbox_doc_id}** has been executed.\n\n"
            f"**Status:** {status_line}\n\n"
            f"The completed document (plan + outcome) has been saved to your outbox.\n\n"
            f"---\n\n{outcome_md}"
        )

        return {
            "query": doc_id,
            "response": response,
            "query_type": "plan_executed",
            "doc_id": outbox_doc_id,
            "dry_run": False,
            "execution_output": execution_output[:500],
            "sources": [],
            "num_sources": 0,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": len(outcome_md),
        }

    def validate(self, doc_id: str, egeria_credentials: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Validate a plan's command sequence against Egeria without executing it.

        Uses Dr.Egeria's 'validate' directive which checks connectivity and
        command syntax but does not create or modify any Egeria objects.
        """
        from advisor.governance_docs import get_doc_manager
        from advisor.agents.dr_egeria_agent import DrEgeriaActionAgent

        # Accept both inbox and outbox plans
        doc_manager = get_doc_manager()
        plan_content = doc_manager.load(doc_id)
        if not plan_content:
            plan_content = doc_manager.load_outbox(doc_id)
        if not plan_content:
            return _error_result(
                doc_id,
                f"Plan document `{doc_id}` not found in inbox or outbox.",
            )

        command_section = self._extract_command_section(plan_content)
        if not command_section.strip():
            return _error_result(
                doc_id,
                f"Plan document `{doc_id}` has no Command Sequence section to validate.",
            )

        action_agent = DrEgeriaActionAgent()
        try:
            raw_output = action_agent.execute(
                command_section,
                directive="validate",
                dry_run=False,
                egeria_credentials=egeria_credentials,
            )
        except ConnectionError as exc:
            return _error_result(
                doc_id,
                f"Cannot validate: Egeria MCP server is not reachable.\n\nDetails: {exc}",
            )
        except Exception as exc:
            return _error_result(
                doc_id,
                f"Validation failed: {exc}",
            )

        success, output_text, val_errs, exe_errs, counts = _parse_dr_egeria_response(raw_output)
        logger.info(
            f"validate({doc_id}): success={success} "
            f"val_errs={len(val_errs)} exe_errs={len(exe_errs)} "
            f"raw_type={type(raw_output).__name__} raw_prefix={str(raw_output)[:120]!r}"
        )

        result: Dict[str, Any] = {
            "query": doc_id,
            "query_type": "plan_validated",
            "doc_id": doc_id,
            "success": success,
            "output": output_text,
            "response": output_text,
            "validation_errors": val_errs,
            "execution_errors": exe_errs,
            "sources": [],
            "num_sources": 0,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": len(command_section),
        }
        result.update(counts)
        return result

    def retry(
        self, doc_id: str, perspective: str | None = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
        pending_id: str | None = None,
        digest: str | None = None,
        ack_non_idempotent: bool = False,
    ) -> Dict[str, Any]:
        """
        Move a failed outbox plan back to inbox and re-execute it: only once it was shown
        (prepare_execute(action="retry")) and confirmed with the outbox document's digest.

        The existing outcome section is stripped so the document is clean
        for a fresh execution attempt.
        """
        from advisor.governance_docs import get_doc_manager
        pending, refused = self._gated(doc_id, "outbox", "retry", pending_id, digest, egeria_credentials,
                                       ack=ack_non_idempotent)
        if refused:
            return refused
        with pending.lock:
            if pending.result is not None:
                return _already(pending.result)
            content = _load_plan(doc_id, "outbox") or ""
            if confirm_gate.text_digest(content) != pending.digest:
                _PLAN_GATE.discard(pending.pending_id)
                return _refused(doc_id, PLAN_DOCUMENT_CHANGED)
            refused = refused_plan_steps(plan_command_names(self._extract_command_section(content)))
            if refused:
                _PLAN_GATE.discard(pending.pending_id)
                return _refused(doc_id, _steps_refused_words(refused))
            inbox_doc_id = get_doc_manager().move_to_inbox(doc_id)
            if not inbox_doc_id:
                return _error_result(
                    doc_id,
                    f"Could not move `{doc_id}` back to inbox. "
                    f"It may not be in the outbox, or the inbox already has a file with that name.",
                )
            pending.result = self._run_plan(inbox_doc_id, perspective=perspective,
                                            egeria_credentials=egeria_credentials)
            return pending.result

    @staticmethod
    def _extract_command_section(plan_content: str) -> str:
        """Return the raw text of the Command Sequence section.

        Stops at '## Outcome' (added post-execution) or end of file.
        Does NOT stop at command-name ## headers inside the section.
        """
        import re
        m = re.search(
            r'^##\s+Command Sequence\s*\n(.*?)(?=^##\s+Outcome\b|\Z)',
            plan_content,
            re.MULTILINE | re.DOTALL,
        )
        return m.group(1) if m else ""

    @staticmethod
    def _replace_command_section(plan_content: str, new_command_section_md: str) -> str:
        """Replace the Command Sequence section's content with a refreshed version."""
        new_content, n = re.subn(
            r'^##\s+Command Sequence\s*\n(.*?)(?=^##\s+Outcome\b|\Z)',
            lambda m: f"## Command Sequence\n\n{new_command_section_md}\n",
            plan_content,
            count=1,
            flags=re.MULTILINE | re.DOTALL,
        )
        return new_content if n else plan_content

    # Steps composed by _compose_command_block look like:
    #   <!-- Step 1: Create Solution Blueprint
    #        wrapped narrative text... -->
    #   ## Create Solution Blueprint
    _STEP_COMMENT_RE = re.compile(
        r'<!--\s*Step\s+(\d+):\s*(.*?)-->\s*\n##\s+(.+?)\s*\n',
        re.DOTALL,
    )

    @classmethod
    def _parse_command_steps(cls, command_section: str) -> List[Dict[str, Any]]:
        """Parse the original Command Sequence into ordered {step, action, narrative, fields} dicts.

        `fields` (the pre-execution ### FieldName values) lets
        _rebuild_command_sequence() fall back to what was there before when
        Dr.Egeria's post-execution echo carries no field data for a step —
        true for Link/relationship and View Report commands, whose echo is
        just a result sentence or rendered report content, never a field echo.
        """
        steps: List[Dict[str, Any]] = []
        matches = list(cls._STEP_COMMENT_RE.finditer(command_section))
        for i, m in enumerate(matches):
            step_num = int(m.group(1))
            raw_comment = m.group(2)
            action = m.group(3).strip()
            # raw_comment is "<action>\n     <wrapped narrative>" — the compose
            # side only ever inserts "\n     " as a chunk separator when wrapping,
            # so removing it recovers the original narrative losslessly.
            first_nl = raw_comment.find("\n")
            narrative = (
                raw_comment[first_nl + 1:].replace("\n     ", "").strip()
                if first_nl != -1 else ""
            )

            body_start = m.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(command_section)
            body = command_section[body_start:body_end]
            # Only the part before the block's closing "---" separator is field data.
            fields_body = re.split(r'\n-{3,}\s*(?:\n|$)', body, maxsplit=1)[0]
            fields: Dict[str, str] = {}
            for part in re.split(r'(?m)(?=^###\s)', fields_body):
                fm = re.match(r'^###\s+([^\n]+)\n([\s\S]*)$', part.strip())
                if not fm:
                    continue
                fname = fm.group(1).strip()
                fval = fm.group(2).strip()
                if fval and not re.match(r'^<!--\s*TODO', fval, re.IGNORECASE):
                    fields[fname] = fval

            steps.append({"step": step_num, "action": action, "narrative": narrative, "fields": fields})
        return steps

    _FAILURE_STATUSES = frozenset(("failure", "failed"))
    _VERB_FLIP_VERBS = frozenset(("Create", "Update"))

    def _rebuild_command_sequence(
        self,
        original_steps: List[Dict[str, Any]],
        echoed_blocks: List[Dict[str, Any]],
        commands_detail: List[Dict[str, Any]],
        mode: str = "basic",
    ) -> Tuple[str, str]:
        """
        Build a refreshed Command Sequence from Dr.Egeria's echoed output, plus
        the combined materialized display content (report/diagram output) for
        the Outcome section — so re-editing a plan starts from the resolved
        attributes (Qualified Name, GUID, etc.) rather than the original,
        under-specified commands.

        A Create->Update verb change from the echo is trusted only when
        commands_detail confirms that step succeeded; a step marked failed
        keeps its original verb, since a command that didn't run shouldn't
        be treated as if it created the object.

        *mode* must be the plan's own tier: this re-composes the whole Command
        Sequence, so re-composing an advanced plan against the basic template
        would strip its advanced fields on the execution round-trip — the same
        loss as PC-1, one step later.
        """
        by_step = {d.get("step"): d for d in (commands_detail or []) if d.get("step") is not None}

        command_parts: List[str] = []
        display_parts: List[str] = []

        for i, orig in enumerate(original_steps):
            step_num = orig["step"]
            echoed = echoed_blocks[i] if i < len(echoed_blocks) else None

            if echoed is None:
                # Dr.Egeria didn't echo this step back (e.g. execution stopped
                # early) — keep the original block unchanged, including its
                # pre-execution field values.
                command_parts.append(self._compose_command_block(
                    {
                        "action": orig["action"],
                        "narrative": orig["narrative"],
                        "params": orig.get("fields", {}),
                        "template_parsed": self._load_template(orig["action"], mode),
                    },
                    step_num,
                ))
                continue

            final_action = orig["action"]
            orig_verb = orig["action"].split(" ", 1)[0]
            echoed_verb = echoed["action"].split(" ", 1)[0]
            if orig_verb in self._VERB_FLIP_VERBS and echoed_verb in self._VERB_FLIP_VERBS:
                detail = by_step.get(step_num)
                status = str((detail or {}).get("status", "")).lower()
                failed = status in self._FAILURE_STATUSES
                if failed and echoed_verb != orig_verb:
                    logger.warning(
                        f"GovernancePlanAgent: Dr.Egeria echoed {echoed['action']!r} for "
                        f"step {step_num} but commands_detail marks it failed "
                        f"({status!r}) — keeping original verb {orig['action']!r}."
                    )
                else:
                    final_action = echoed["action"]
            # else: not a Create/Update-style command (e.g. View Report / Report /
            # Link X) — keep the original action name rather than adopting any
            # cosmetic rename Dr.Egeria's echo happens to use.

            echoed_params = {k: v for k, v in echoed["fields"].items() if v and v != "None"}
            # Dr.Egeria's raw echo only carries field values for Create/Update
            # commands — a Link/relationship command's echo is a one-line result
            # sentence ("Linked X to Y") and View Report's is the rendered report
            # content, neither of which include a field echo. Start from the
            # pre-execution values so those step types don't get silently blanked
            # on every execution; genuinely-echoed values (Create/Update) still win.
            params = {**orig.get("fields", {}), **echoed_params}
            command_parts.append(self._compose_command_block(
                {
                    "action": final_action,
                    "narrative": orig["narrative"],
                    "params": params,
                    "template_parsed": self._load_template(final_action, mode),
                },
                step_num,
            ))

            if echoed["display"]:
                display_parts.append(f"**Step {step_num} — {orig['action']}:**\n\n{echoed['display']}")

        return "\n".join(command_parts), "\n\n".join(display_parts)

    @staticmethod
    def _extract_status_from_outcome(outcome_md: str) -> str:
        import re
        m = re.search(r'\*\*Status:\*\*\s*(\w+)', outcome_md)
        return m.group(1) if m else "Unknown"

    # ---------------------------------------------------------------------- #
    # Intent decomposition                                                     #
    # ---------------------------------------------------------------------- #

    # ── Entity type → Dr.Egeria action mapping ──────────────────────────── #
    # Maps Dr.Egeria action name → Egeria open-metadata type name, used to
    # auto-generate qualified names matching pyegeria's __create_qualified_name__ convention:
    # "{EgeriaType}::{display-name-with-dashes}"
    _ACTION_TO_EGERIA_TYPE: Dict[str, str] = {
        "Create Campaign":                "Project",
        "Create Project":                 "Project",
        "Create Personal Project":        "Project",
        "Create Study Project":           "Project",
        "Create Task":                    "Project",
        "Create Glossary":                "Glossary",
        "Create Glossary Term":           "GlossaryTerm",
        "Create Glossary Category":       "GlossaryCategory",
        "Create Team":                    "Team",
        "Create Organization":            "Organization",
        "Create Collection":              "Collection",
        "Create Data Dictionary":         "Collection",
        "Create Data Structure":          "DataStructure",
        "Create Data Field":              "DataField",
        "Create Data Class":              "DataClass",
        "Create Data Spec":               "Collection",
        "Create Governance Zone":         "GovernanceZone",
        "Create Governance Policy":       "GovernancePolicy",
        "Create Governance Definition":   "GovernanceDefinition",
        "Create Governance Role":         "GovernanceRole",
        "Create Governance Driver":       "GovernanceDriver",
        "Create Business Imperative":     "GovernanceDriver",
        "Create Certification Type":      "CertificationType",
        "Create Regulation":              "Regulation",
        "Create Regulation Article":      "RegulationArticle",
        "Create License Type":            "LicenseType",
        "Create Digital Product":         "DigitalProduct",
        "Create Agreement":               "Agreement",
        "Create Data Sharing Agreement":  "Agreement",
        "Create Person Role":             "PersonRole",
        "Create Community":               "Community",
        "Create Actor Profile":           "ActorProfile",
        "Create User Identity":           "UserIdentity",
        "Create Solution Blueprint":      "SolutionBlueprint",
        "Create Solution Component":      "SolutionComponent",
        "Create Information Supply Chain":"InformationSupplyChain",
        "Create Solution Role":           "SolutionRole",
        "Create External Reference":      "ExternalReference",
        "Create Subject Area":            "Collection",
        "Create Informal Tag":            "InformalTag",
        "Create Comment":                 "Comment",
    }

    _ENTITY_TO_ACTION: Dict[str, str] = {
        "campaign":          "Create Campaign",
        "project":           "Create Project",
        "sub_project":       "Create Project",
        "personal_project":  "Create Personal Project",
        "study_project":     "Create Study Project",
        "task":              "Create Task",
        "glossary":          "Create Glossary",
        "glossary_term":     "Create Glossary Term",
        "glossary_category": "Create Glossary Category",
        "team":              "Create Team",
        "organization":      "Create Organization",
        "collection":        "Create Collection",
        "governance_zone":   "Create Governance Zone",
        "governance_policy": "Create Governance Policy",
        "governance_definition": "Create Governance Definition",
        "data_dictionary":   "Create Data Dictionary",
        "data_structure":    "Create Data Structure",
        "data_field":        "Create Data Field",
        "data_class":        "Create Data Class",
        "digital_product":   "Create Digital Product",
        "agreement":              "Create Agreement",
        "data_sharing_request":   "Create Agreement",
        "data_sharing_agreement": "Create Agreement",
        "external_reference":     "Create External Reference",
        "solution_blueprint":     "Create Solution Blueprint",
        "blueprint":              "Create Solution Blueprint",
        "solution_component":     "Create Solution Component",
        "component":              "Create Solution Component",
        "information_supply_chain": "Create Information Supply Chain",
        "supply_chain":           "Create Information Supply Chain",
        "solution_role":          "Create Solution Role",
        "view_report":            "View Report",
        "report":                 "View Report",
    }

    # Maps entity type → View Report "Report Spec" field value
    _ENTITY_TO_REPORT_SPEC: Dict[str, str] = {
        "solution_blueprint":  "Solution-Blueprint",
        "solution_component":  "Solution-Blueprint",
        "glossary":            "Glossaries",
        "glossary_term":       "Glossary-Terms",
        "collection":          "Collections",
        "project":             "Projects",
        "campaign":            "Projects",
        "digital_product":     "Digital-Products",
        "data_dictionary":     "Data-Dictionaries",
        "governance_zone":     "Governance-Zones",
    }

    # Detects "view/run/show report for X", "report on X", "print list/mermaid/report [for X]",
    # "mermaid diagram/graph of X", "view X as mermaid", "architecture diagram for X".
    # _VR_STOP terminates the name capture at common clause boundaries.
    _VR_STOP = r'(?=\s*(?:as\s+a\s+mermaid|as\s+mermaid|\.\s|\s*$))'
    _VIEW_REPORT_PATTERN = re.compile(
        r'\b(?:view|run|show|display|get)\s+(?:a\s+)?(?:the\s+)?report\s+(?:for|on|of)\s+"?(.+?)' + _VR_STOP
        + r'|\breport\s+on\s+(?:the\s+)?"?(.+?)' + _VR_STOP
        + r'|\bprint\s+(?:list|mermaid|report)\s+(?:for|of|on)\s+(?:the\s+)?"?(.+?)' + _VR_STOP
        + r'|\bprint\s+(?:list|mermaid|report)\b'
        + r'|\bmermaid\s+(?:diagram|graph|chart)\s+(?:of|for)\s+"?(.+?)' + _VR_STOP
        + r'|\bview\s+"?(.+?)"?\s+as\s+(?:a\s+)?mermaid'
        + r'|\b(?:architecture|system)\s+diagram\s+(?:of|for)\s+"?(.+?)' + _VR_STOP,
        re.IGNORECASE,
    )

    def _decompose_intent(
        self,
        query: str,
        perspective: str | None,
        llm,
        existing_commands: Optional[List[Dict]] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Two-stage decomposition:
          Stage 1 (LLM)  — extract entities and roles from natural language
          Stage 2 (rules) — map entities → Dr.Egeria commands deterministically

        This split keeps the LLM prompt simple enough for local 8B models while
        ensuring command names and structure are correct by construction.

        existing_commands: commands already in the plan (for addition requests).
        Returns a dict: {title, purpose, commands, validator_warnings}
        """
        perspective_hint = f"User role: {perspective}.\n" if perspective else ""

        existing_hint = ""
        if existing_commands:
            lines = ["Already in the plan — do NOT include these again:"]
            for c in existing_commands:
                lines.append(f"  - {c.get('display_name', '?')} ({c['action']})")
            lines.append("Add ONLY new objects the user is now requesting.")
            existing_hint = "\n".join(lines) + "\n\n"

        # ── Stage 1: entity extraction ──────────────────────────────────── #
        # Try pattern-based extraction first (reliable for common phrasings),
        # fall back to LLM for complex cases.
        entities = self._extract_entities_patterns(query)
        if not entities.get("objects"):
            entities = self._extract_entities_llm(
                query, perspective_hint, existing_hint, llm
            )
        if not entities.get("objects"):
            entities = {"title": query[:50], "purpose": query, "objects": [], "roles": []}

        # ── Stage 1b: Egeria context enrichment ────────────────────────── #
        # Best-effort: look up actor profiles and check entity existence in Egeria.
        # Enriches entities in place; silently skips if Egeria is unreachable.
        context_warnings: list[str] = []
        try:
            from advisor.egeria_context import EgeriaContext
            ctx = EgeriaContext(egeria_credentials=egeria_credentials)
            ctx.enrich_entities(entities)
            # Surface "already exists" warnings so the user can decide to update instead
            for obj in entities.get("objects", []):
                if obj.get("exists_in_egeria") and not obj.get("type", "").endswith("sub_project"):
                    context_warnings.append(
                        f"'{obj['name']}' already exists in Egeria "
                        f"(GUID: {obj['egeria_guid'][:8]}…). "
                        f"The plan will create a new one — rename it if you meant to update the existing one."
                    )
            # Surface unresolved actors. False = Egeria answered "no such actor"; None = Egeria
            # was NOT read, which must never be stated as "not found" (A-P2).
            for role in entities.get("roles", []):
                if role.get("actor_found") is False:
                    context_warnings.append(
                        f"'{role['person']}' was not found in Egeria's actor profiles. "
                        f"A 'Create Actor Profile' step may be needed, or check the spelling."
                    )
            context_warnings.extend(unchecked_context_warnings(entities))
        except Exception as _ctx_exc:
            logger.info(f"GovernancePlanAgent: context enrichment skipped: {_ctx_exc}")
            context_warnings.append(
                f"Egeria was not checked for this plan (context enrichment failed: {_ctx_exc}). "
                "People and names in it are unverified."
            )

        # ── Stage 2: deterministic command mapping ──────────────────────── #
        commands = self._entities_to_commands(entities, existing_commands or [])

        # Apply post-processing validator
        from advisor.plan_validator import validate_commands
        commands, _, warnings = validate_commands(commands, {})
        if warnings:
            logger.info(f"GovernancePlanAgent: validator fixes: {warnings}")

        # Collect low-confidence suggestions from all extracted objects
        keyword_suggestions: list[dict] = []
        for obj in entities.get("objects", []):
            keyword_suggestions.extend(obj.pop("low_confidence_suggestions", []))

        # Collect auto-appended steps (e.g. View Report added for SA plans)
        auto_appended: list[str] = []
        for cmd in commands:
            if cmd.pop("_auto_appended", False):
                spec = (cmd.get("pre_filled") or {}).get("Report Spec", "")
                fmt  = (cmd.get("pre_filled") or {}).get("Output Format", "")
                auto_appended.append(
                    f"Added **View Report** ({spec}, {fmt}) — visualizes the result. "
                    "Remove it in the canvas if not needed."
                )

        return {
            "title":              entities.get("title", query[:50]).strip(),
            "purpose":            entities.get("purpose", query),
            "commands":           commands,
            "validator_warnings": warnings + context_warnings,
            "keyword_suggestions": keyword_suggestions,
            "auto_appended":      auto_appended,
        }

    # Name stops at these words (sentence-level boundaries)
    _NAME_STOP = r'(?=\s*(?:,|\.|\s+-\s+|\bwith\b|\bhave\b|\bto\s+be\b|\bled\s+by\b|\bto\s+create\b|\band\b|\bincluding\b|\bwhere\b|\busing\b|$))'

    # Pattern vocab: (regex, entity_type) — name captured in group 1
    _ENTITY_PATTERNS = [
        # "called <name>" / "named <name>" — generic; entity type inferred from context word
        (r'\b(?:project|campaign|glossary|collection|task|team|agreement|study)\s+(?:called|named)\s+"?(.+?)"?' + _NAME_STOP, None),
        # "a data sharing request/agreement called/named/for <name>"
        (r'\b(?:a\s+)?data\s+sharing\s+(?:request|agreement)\s+(?:called|named|for)\s+"?(.+?)"?' + _NAME_STOP, "agreement"),
        # "an agreement called/named/for <name>"
        (r'\ban?\s+agreement\s+(?:called|named|for)\s+"?(.+?)"?' + _NAME_STOP, "agreement"),
        # "a task called/named/for <name>"
        (r'\ba\s+task\s+(?:called|named|for)\s+"?(.+?)"?' + _NAME_STOP, "task"),
        # "a team called/named/for <name>"
        (r'\ba\s+team\s+(?:called|named|for)\s+"?(.+?)"?' + _NAME_STOP, "team"),
        # "a campaign for <name>"
        (r'\ba\s+campaign\s+for\s+"?(.+?)"?' + _NAME_STOP, "campaign"),
        # "a project for / project called"
        (r'\ba\s+project\s+(?:for|called)\s+"?(.+?)"?' + _NAME_STOP, "project"),
        # "a glossary for / called"
        (r'\ba\s+glossary\s+(?:for|called)\s+"?(.+?)"?' + _NAME_STOP, "glossary"),
        # Solution Architect: "a blueprint called/named/for <name>" (singular — use LLM for lists)
        (r'\b(?:a\s+)?(?:solution\s+)?blueprint\s+(?:called|named|for)\s+"?(.+?)"?' + _NAME_STOP, "solution_blueprint"),
        # "set up a <type>" — name after "for" or "called"
        (r'\bset\s+up\s+a\s+(?:glossary|project|campaign|task|team)\s+(?:for\s+the\s+|for\s+|called\s+)?"?(.+?)"?' + _NAME_STOP, None),
        # "create a data sharing request called <name>" (handles "I want to create a data sharing request...")
        (r'\bcreate\s+(?:a\s+)?data\s+sharing\s+(?:request|agreement)\s+(?:called\s+|named\s+)?"?(.+?)"?' + _NAME_STOP, "agreement"),
        # Generic catch-all — MUST stay last so the more specific patterns above win when
        # they also match. Covers "create/add/set up a/an <any type phrase> called/named/
        # for/to <name>", for any of the ~126 known Dr.Egeria commands, not just the
        # hand-listed types above (e.g. "Create an External Reference to the X web site",
        # "Add a Digital Product for Y"). Sentinel etype "__generic__" tells the caller to
        # resolve group(1) (the type phrase) against the command keyword index — SCOPED to
        # just that phrase, not the whole query, so a proper noun elsewhere in the name
        # (e.g. "...to the Egeria Project web site") can't be mistaken for the type.
        (r'\b(?:create|add|set\s+up)\s+(?:an?\s+)?([\w\s]{2,40}?)\s+(?:called|named|for|to)\s+"?(.+?)"?' + _NAME_STOP, "__generic__"),
    ]
    # Role: "led by <person> as <role>" / "with <person> as <role>" /
    #        "have <person> be the <role>" / "<role> as <person>"
    _ROLE_PATTERNS = [
        r'\b(?:to\s+be\s+)?led\s+by\s+"?([A-Z][a-zA-Z\s\.]{1,30}?)"?\s+as\s+(?:the\s+)?([\w\s]{2,30})',
        r'\b(?:to\s+be\s+)?led\s+by\s+"?([A-Z][a-zA-Z\s\.]{1,30}?)"?' + _NAME_STOP,
        r'\bwith\s+"?([A-Z][a-zA-Z\s\.]{1,30}?)"?\s+as\s+(?:the\s+)?([\w\s]{2,30})',
        # "have Tom Tally be the leader" / "have Tom as the project manager"
        r'\bhave\s+"?([A-Z][a-zA-Z\s\.]{1,30}?)"?\s+(?:be\s+(?:the\s+)?|as\s+(?:the\s+)?)([\w\s]{2,30})',
        # "project leader as Tom Tally" / "leader: Tom Tally" (role first, then name)
        r'\b(?:project\s+)?(?:leader|manager|steward|owner|sponsor|lead)\s+(?:as\s+|:\s*)?"?([A-Z][a-zA-Z\s\.]{1,30}?)"?' + _NAME_STOP,
    ]
    _SUBPROJECT_PATTERN = re.compile(
        r'\bsub[-\s]?projects?\s+(?:for\s+)?["\']?(.+?)(?=["\']?\s*(?:$|\.|,\s*(?:led|with|and\s+[a-z])))',
        re.IGNORECASE,
    )

    # Multi-item list: "[plural type keyword] for/called name1, name2, and nameN"
    # e.g. "solution components for UK DB, EU DB and WorldWide DB"
    _MULTI_ENTITY_PATTERN = re.compile(
        r'\b((?:solution\s+)?components?|(?:solution\s+)?blueprints?|glossary\s+terms?'
        r'|tasks?|data\s+structures?|data\s+fields?)\s+(?:for|called|named)\s+'
        r'(.+?)(?=\.\s+[A-Z]|\s*\.\s*$|\s*$)',
        re.IGNORECASE | re.DOTALL,
    )
    # Geographic / regional prefixes to strip when auto-naming containers
    _GEO_PREFIX = re.compile(
        r'^(UK|EU|EU|US|USA|EMEA|APAC|Canada|WorldWide|Worldwide|Global|LATAM|MEA'
        r'|North\s+America|South\s+America|Asia\s+Pacific|Europe|Asia)\s+',
        re.IGNORECASE,
    )

    def _extract_entities_patterns(self, query: str) -> Dict:
        """
        Rule-based entity extraction for common phrasings.
        Returns entities dict if confident; empty objects list if not matched.
        """
        q = query.strip()
        objects = []
        roles   = []

        ql = q.lower()

        # keyword → entity_type map for fast inline matching
        _KW_MAP = {
            "campaign": "campaign", "glossary": "glossary",
            "collection": "collection", "task": "task", "team": "team",
            "blueprint": "solution_blueprint", "component": "solution_component",
            "supply chain": "information_supply_chain", "solution role": "solution_role",
            "study": "study_project", "personal": "personal_project",
        }

        # ── Multi-item list detection ──────────────────────────────────────
        # Handles "solution components for UK X, EU Y and WorldWide Z"
        multi_m = self._MULTI_ENTITY_PATTERN.search(q)
        if multi_m:
            kw_raw = multi_m.group(1).lower()
            # Normalise singular: "components" → "component", "blueprints" → "blueprint"
            kw = re.sub(r's$', '', kw_raw.strip())
            # Map to entity type
            etype = _KW_MAP.get(kw) or _KW_MAP.get(kw.replace("solution ", ""))
            if etype:
                raw_names = multi_m.group(2)
                names = [n.strip().strip('"\'') for n in
                         re.split(r'\s*,\s*|\s+and\s+', raw_names) if n.strip()]
                # Sanity check: a genuine list item is a short proper name. If
                # the query names a container elsewhere ("Blueprint called X")
                # AND separately lists items ("components for A, B, C"), the
                # shared (?:for|called|named) group above can match the wrong
                # keyword occurrence and the lazy capture can run away,
                # swallowing the rest of the sentence into one "name" (confirmed
                # live 2026-07-10). Rather than trust a garbled result, bail out
                # entirely — _decompose_intent's LLM extraction fallback handles
                # this combined case correctly.
                _SUSPICIOUS_NAME_WORDS = (
                    "is part of", "the final step", "solution component", "each component",
                )
                if names and any(
                    len(n) > 60 or any(w in n.lower() for w in _SUSPICIOUS_NAME_WORDS)
                    for n in names
                ):
                    return {"objects": [], "roles": []}
                if names:
                    # ── Auto-name a container when query mentions "blueprint" ─
                    container_name = ""
                    bp_named = re.search(
                        r'\bblueprint\s+(?:called|named)\s+"?([^",.]+?)"?(?=[,.]|$)', ql
                    )
                    if bp_named:
                        container_name = bp_named.group(1).strip().title()
                    elif "blueprint" in ql and etype == "solution_component":
                        # Derive name from common suffix of all item names
                        words_lists = [n.split() for n in names]
                        min_len = min(len(w) for w in words_lists)
                        suffix_words: list[str] = []
                        for i in range(1, min_len + 1):
                            candidates = [w[-i].lower() for w in words_lists]
                            if len(set(candidates)) == 1:
                                suffix_words.insert(0, words_lists[0][-i])
                            else:
                                break
                        if suffix_words:
                            container_name = " ".join(suffix_words).title() + " Blueprint"
                        else:
                            # Fallback: strip geo prefix from first name
                            stripped = self._GEO_PREFIX.sub("", names[0]).strip()
                            container_name = (stripped or names[0]).title() + " Blueprint"

                    if container_name:
                        objects.append({
                            "type": "solution_blueprint",
                            "name": container_name,
                            "low_confidence_suggestions": [],
                        })
                    for name in names:
                        objects.append({
                            "type": etype,
                            "name": name,
                            "blueprint_parent": container_name,
                            "low_confidence_suggestions": [],
                        })
                    # Role extraction still applies
                    roles = self._extract_roles(q)
                    title_base = container_name or names[0]
                    return {
                        "title":   f"{title_base} Plan",
                        "purpose": q[:120],
                        "objects": objects,
                        "roles":   roles,
                    }

        # ── View Report / Mermaid diagram detection ────────────────────────
        # Handles "view report for Solution Blueprint", "mermaid graph of X", etc.
        # Checked AFTER multi-item list detection above: a request like
        # "...create solution components for A, B, C... The final step is to
        # View Report..." must not have its Blueprint/Components silently
        # discarded just because it also mentions a report at the end — this
        # early-return path is only for queries that are ~exclusively about
        # viewing a report, with nothing else to create. (Confirmed live
        # 2026-07-10: this block firing first caused exactly that — a
        # 5-object plan request came back with only the View Report step.)
        # When multi-item/main-entity detection above DOES match a Solution
        # Blueprint creation, the auto-append step in _entities_to_commands
        # already adds a View Report/Mermaid step automatically, so nothing
        # requested is lost by deferring to it instead.
        vr_m = self._VIEW_REPORT_PATTERN.search(q)
        if vr_m:
            target_name = next((g.strip() for g in vr_m.groups() if g), "")
            # Strip leading articles ("the", "a", "an")
            target_name = re.sub(r'^(?:the|a|an)\s+', '', target_name, flags=re.IGNORECASE)
            # Detect explicit output format from the phrase used
            if re.search(r'\bmermaid\b', ql):
                output_fmt = "MERMAID"
            elif re.search(r'\bprint\s+report\b|\bfull\s+report\b', ql):
                output_fmt = "MD"
            elif re.search(r'\bprint\s+list\b', ql):
                output_fmt = "LIST"
            else:
                output_fmt = "LIST"
            # Infer report spec from entity type keywords in query
            report_spec = "Solution-Blueprint"  # default
            for kw, spec in [
                ("glossary term", "Glossary-Terms"),
                ("glossary",      "Glossaries"),
                ("collection",    "Collections"),
                ("project",       "Projects"),
                ("campaign",      "Projects"),
                ("digital product", "Digital-Products"),
                ("data dictionary", "Data-Dictionaries"),
                ("governance zone", "Governance-Zones"),
                ("blueprint",     "Solution-Blueprint"),
                ("solution",      "Solution-Blueprint"),
            ]:
                if kw in ql:
                    report_spec = spec
                    break
            return {
                "title": f"View {report_spec} Report",
                "purpose": q[:120],
                "objects": [{
                    "type": "view_report",
                    "name": target_name or report_spec,
                    "report_spec": report_spec,
                    "output_format": output_fmt,
                    "search_string": target_name,
                    "low_confidence_suggestions": [],
                }],
                "roles": [],
            }

        # Tracks entity-type inferences that used the keyword index (low confidence)
        # so confirm_commands can surface "Did you mean X?"
        _low_confidence_suggestions: list[dict] = []

        def _infer_type_from_context(scope: str = "") -> tuple[str, Optional[str]]:
            """
            Returns (entity_type, action). action is None when entity_type is one of
            the small set of hand-mapped types below (the existing _ENTITY_TO_ACTION
            lookup in _entities_to_commands already resolves those correctly). For
            anything else, action carries the *actual* resolved command name from the
            keyword index directly — this covers every one of the ~126 known Dr.Egeria
            commands, not just the ~25 pre-registered in _ENTITY_TO_ACTION. Previously
            a confident keyword-index match for a command NOT already in that dict was
            silently discarded (the inverse-lookup `inv.get(...)` returned None) and
            fell through to a generic "project" default — this is what made e.g.
            "Create an External Reference to X" fail even though External Reference IS
            a real, catalogued Dr.Egeria action.

            `scope`, when given, restricts the search to just that text (the captured
            "type phrase" from the generic catch-all pattern) instead of the whole
            query. Without this, a query like "Create an external reference to the
            Egeria Project web site" would find "Project" (an exact, high-confidence
            match, since it's also a real command name) inside the *name* portion of
            the sentence and wrongly prefer it over the weaker partial match on
            "external reference" earlier in the sentence — the same class of bug as
            the CreateRouter "Egeria-Project" false positive, one layer deeper.
            """
            search_text = scope.lower() if scope else ql
            for kw, etype in _KW_MAP.items():
                if kw in search_text:
                    return etype, None
            if "agreement" in search_text or "data sharing" in search_text:
                return "agreement", None
            if "investigation" in search_text:
                return "study_project", None
            # Last resort: consult the full keyword index (~126 commands) against the
            # (scoped, if given) text, longest phrase first so multi-word command
            # names/aliases ("external reference", "digital product") aren't shadowed
            # by a shorter, weaker single-word partial match.
            try:
                from advisor.command_keyword_index import get_command_keyword_index
                idx = get_command_keyword_index()
                words = search_text.split()
                best_match = None
                best_phrase = ""
                for length in (4, 3, 2, 1):
                    for i in range(len(words) - length, -1, -1):
                        phrase = " ".join(words[i:i + length])
                        match = idx.lookup(phrase)
                        if match and (best_match is None or match.confidence > best_match.confidence):
                            best_match, best_phrase = match, phrase
                    if best_match and best_match.confidence >= 0.90:
                        break  # exact match already found at this length; no need to try shorter phrases
                if best_match and best_match.confidence >= 0.50:
                    if best_match.confidence < 0.80:
                        _low_confidence_suggestions.append({
                            "phrase": best_phrase,
                            "suggested_command": best_match.command,
                            "family": best_match.family,
                            "confidence": best_match.confidence,
                        })
                    return _command_to_entity_type(best_match.command), best_match.command
            except Exception:
                pass
            return "project", None

        # Detect main entity type and name
        main_type = ""
        main_action: Optional[str] = None
        main_name = ""
        for pattern, etype in self._ENTITY_PATTERNS:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                if etype == "__generic__":
                    # Two-group pattern: group(1) is the type phrase, group(2) is the
                    # name. Resolve the type SCOPED to just that phrase — never the
                    # whole query — so a proper noun elsewhere in the name can't be
                    # mistaken for the type (see _infer_type_from_context docstring).
                    type_phrase = m.group(1).strip()
                    main_name = m.group(2).strip().strip('"\'')
                    main_type, main_action = _infer_type_from_context(scope=type_phrase)
                    break
                main_name = m.group(1).strip().strip('"\'')
                if etype:
                    main_type = etype
                else:
                    matched_lower = m.group(0).lower()
                    matched_type = next(
                        (et for kw, et in _KW_MAP.items() if kw in matched_lower), None
                    )
                    if matched_type:
                        main_type = matched_type
                    elif "agreement" in matched_lower or "data sharing" in matched_lower:
                        main_type = "agreement"
                    else:
                        main_type, main_action = _infer_type_from_context()
                break

        if not main_name:
            return {"objects": [], "roles": []}

        main_obj = {
            "type": main_type or "project",
            "name": main_name,
            "low_confidence_suggestions": _low_confidence_suggestions,
        }
        if main_action:
            main_obj["action"] = main_action
        objects.append(main_obj)

        # Sub-projects
        sub_m = self._SUBPROJECT_PATTERN.search(q)
        if sub_m:
            sub_text = sub_m.group(1)
            # Split on commas, "and", quotes
            sub_names = re.split(r'",?\s+"|\s*,\s*|\s+and\s+', sub_text)
            for sn in sub_names:
                sn = sn.strip().strip('"\'').strip()
                if sn and sn.lower() != main_name.lower():
                    objects.append({"type": "sub_project", "name": sn, "parent": main_name})

        # Role assignments
        roles = self._extract_roles(q)

        main_type_label = main_type.replace("_", " ").title()
        title = f"{main_name} {main_type_label} Setup" if main_name else query[:50]
        return {
            "title":   title,
            "purpose": f"Set up a {main_type_label.lower()} called {main_name}",
            "objects": objects,
            "roles":   roles,
        }

    def _extract_roles(self, q: str) -> list[dict]:
        """Extract role assignments from query text using _ROLE_PATTERNS."""
        roles = []
        for pattern in self._ROLE_PATTERNS:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                person = m.group(1).strip().strip('"\'')
                role   = (m.group(2).strip().title()
                          if m.lastindex and m.lastindex >= 2 and m.group(2)
                          else "Project Leader")
                if person and 1 <= len(person.split()) <= 5:
                    roles.append({"role": role, "person": person})
                    break
        return roles

    def _extract_entities_llm(
        self, query: str, perspective_hint: str, existing_hint: str, llm
    ) -> Dict:
        """LLM-based entity extraction — fallback when pattern matching fails."""
        prompt = f"""Extract ALL objects and role assignments from this request.
Return ONLY valid JSON. Each distinct named object appears EXACTLY ONCE.

Object types:
  project, campaign, sub_project (child project — include "parent" field),
  glossary, glossary_term, glossary_category,
  team, collection, governance_zone,
  solution_blueprint, solution_component (architectural building block),
  information_supply_chain, solution_role,
  data_dictionary, data_structure, data_field, data_spec,
  certification_type, governance_policy, governance_rule,
  digital_product, agreement, external_reference

Rules:
- For solution_component: if the user mentions a blueprint as container, also include
  a solution_blueprint object (infer name from context if not explicitly given).
- For sub_project: include "parent" with the parent campaign/project name.
- "name" must be copied EXACTLY from the request — never use the type word as the name.
- If a list of names is given for the same type, create one object per name.
- "title" must be a real 3-6 word summary of THIS request (e.g. "Finance Zone Setup") —
  never copy the placeholder text below verbatim.

{existing_hint}{perspective_hint}Request: "{query}"

Return (the field values below are placeholders showing the expected shape —
replace every one of them with real content derived from the request):
{{
  "title": "<3-6 word summary of this request>",
  "purpose": "<one sentence>",
  "objects": [{{"type": "...", "name": "exact name from request"}}],
  "roles": [{{"role": "role title", "person": "person name"}}]
}}
JSON:"""
        try:
            raw = llm.generate(prompt, temperature=0.0, max_tokens=700)
            raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw.strip())
            m   = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                raise ValueError("no JSON in LLM output")
            result = json.loads(_extract_balanced_json(m.group()))
            # Defense in depth: a weak model can still echo the placeholder
            # verbatim (confirmed live 2026-07-09 — literally returned the
            # string "short title"). Never trust a title that looks like the
            # prompt's own placeholder text rather than real content.
            bad_titles = {"short title", "<3-6 word summary of this request>", ""}
            if str(result.get("title", "")).strip().lower() in bad_titles:
                result["title"] = query[:50].strip()
            return result
        except Exception as exc:
            logger.warning(f"GovernancePlanAgent: LLM extraction failed: {exc}")
            return {"objects": [], "roles": []}

    def _entities_to_commands(
        self, entities: Dict, existing_commands: List[Dict]
    ) -> List[Dict]:
        """
        Deterministically map extracted entities and roles to Dr.Egeria commands.
        """
        from advisor.action_catalog import get_action_catalog
        catalog = get_action_catalog()
        commands: List[Dict] = []
        existing_names = {c.get("display_name", "").lower() for c in existing_commands}
        _ACTION_TO_EGERIA_TYPE = self._ACTION_TO_EGERIA_TYPE

        def _make_cmd(action: str, display_name: str, pre_filled: Optional[Dict] = None,
                      narrative: str = "") -> Dict:
            pf = dict(pre_filled) if pre_filled else {}
            # Auto-generate Qualified Name using the same convention as pyegeria's
            # __create_qualified_name__(type_name, display_name) → "Type::display-name"
            egeria_type = _ACTION_TO_EGERIA_TYPE.get(action)
            if egeria_type and display_name and "Qualified Name" not in pf:
                # Strip anything that isn't a word char/space/hyphen first (quotes,
                # colons, slashes, pipes, parens, …) so the result is always a
                # valid identifier, then collapse whitespace to hyphens.
                dn_slug = re.sub(r'[^\w\s-]', '', display_name.strip())
                dn_slug = re.sub(r'\s+', '-', dn_slug).strip('-') or 'unnamed'
                pf["Qualified Name"] = f"{egeria_type}::{dn_slug}"
            return {
                "action":       action,
                "display_name": display_name,
                # Unique key so multiple commands of the same action type each get
                # their own slot in the answers dict (fixes display of repeated actions)
                "_answers_key": f"{action}:{display_name}" if display_name else action,
                "description":  "",
                "rationale":    "",
                "narrative":    narrative or catalog.narrative_template(action),
                "pre_filled":   pf,
                "placeholders": {},
            }

        # Identify the top-level container (campaign or first unparented project)
        # so we can infer parent for unparented sub-items
        top_level_name = ""
        for obj in entities.get("objects", []):
            otype = (obj.get("type") or "").lower()
            if otype in ("campaign",):
                top_level_name = (obj.get("name") or "").strip()
                break
        if not top_level_name:
            # Also check existing commands for a campaign/top-level project
            for ec in existing_commands:
                if ec.get("action") in ("Create Campaign", "Create Project") \
                        and not (ec.get("pre_filled") or {}).get("Parent ID"):
                    top_level_name = ec.get("display_name", "")
                    break

        # Identify the solution blueprint qualified name (from extracted objects or existing
        # commands) so solution_components can pre-fill "In Solution Blueprints" at creation.
        # Using the qualified name (not just display name) ensures Dr.Egeria resolves the
        # reference unambiguously.  Falls back to display name if QN not yet computed.
        blueprint_qname = ""
        blueprint_name = ""
        for obj in entities.get("objects", []):
            otype = (obj.get("type") or "").lower().replace("-", "_").replace(" ", "_")
            if otype in ("solution_blueprint", "blueprint"):
                blueprint_name = (obj.get("name") or "").strip()
                if blueprint_name:
                    slug = re.sub(r'\s+', '-', blueprint_name.strip())
                    blueprint_qname = f"SolutionBlueprint::{slug}"
                break
        if not blueprint_name:
            for ec in existing_commands:
                if ec.get("action") == "Create Solution Blueprint":
                    blueprint_name = ec.get("display_name", "")
                    # Try to read QN from already-computed pre_filled
                    blueprint_qname = (
                        (ec.get("pre_filled") or {}).get("Qualified Name")
                        or (f"SolutionBlueprint::{re.sub(r'\s+', '-', blueprint_name)}"
                            if blueprint_name else "")
                    )
                    break

        for obj in entities.get("objects", []):
            entity_type = (obj.get("type") or "").lower().replace("-", "_").replace(" ", "_")
            name = (obj.get("name") or "").strip()
            if not name or name.lower() in existing_names:
                continue

            # ── View Report: read-only command, pre-fill from extracted attrs ──
            if entity_type in ("view_report", "report"):
                report_spec   = obj.get("report_spec", "Solution-Blueprint")
                output_format = obj.get("output_format", "TABLE")
                search_string = obj.get("search_string", name)
                pf: Dict[str, str] = {
                    "Report Spec":   report_spec,
                    "Output Format": output_format,
                }
                if search_string:
                    pf["Search String"] = search_string
                # No Qualified Name for View Report — it's not creating an Egeria object
                cmd = {
                    "action":       "View Report",
                    "display_name": search_string or report_spec,
                    "_answers_key": f"View Report:{search_string or report_spec}",
                    "description":  "",
                    "rationale":    "",
                    "narrative":    catalog.narrative_template("View Report")
                        or f"Runs the {report_spec} report to view results.",
                    "pre_filled":   pf,
                    "placeholders": {},
                }
                commands.append(cmd)
                continue

            # A "project" with a parent field is implicitly a sub-project
            parent = (obj.get("parent") or "").strip()
            if parent and entity_type == "project":
                entity_type = "sub_project"

            # Unparented projects when a campaign exists → infer as sub-projects
            if entity_type == "project" and not parent and top_level_name \
                    and name != top_level_name:
                parent = top_level_name
                entity_type = "sub_project"

            # obj["action"], when present, is a command name already resolved directly
            # against the full command keyword index (see _infer_type_from_context) —
            # use it as-is rather than re-deriving from entity_type, which only covers
            # the ~25 types hand-registered in _ENTITY_TO_ACTION.
            action = obj.get("action") or self._ENTITY_TO_ACTION.get(entity_type)
            if not action:
                action = catalog.find_by_alias(entity_type) or "Create Project"

            pre_filled: Dict[str, str] = {"Display Name": name}
            if parent and entity_type == "sub_project":
                pre_filled["Parent ID"] = parent
                pre_filled["Parent Relationship Type Name"] = "ProjectHierarchy"

            # Pre-fill the parent-reference field on children when the container is known.
            # Use the qualified name so Dr.Egeria resolves the cross-reference unambiguously.
            bp_ref = (obj.get("blueprint_parent") and
                      (f"SolutionBlueprint::{re.sub(r'\s+', '-', obj['blueprint_parent'])}"
                       if obj.get("blueprint_parent") else "")) or blueprint_qname
            if action == "Create Solution Component" and bp_ref:
                pre_filled["In Solution Blueprints"] = bp_ref

            commands.append(_make_cmd(action, name, pre_filled))

        for role in entities.get("roles", []):
            # Accept both "role" and "role_name" as field names
            role_title  = (role.get("role") or role.get("role_name") or "").strip().title()
            person_name = (role.get("person") or role.get("person_name") or "").strip()
            if not role_title:
                continue

            commands.append(_make_cmd(
                "Create Person Role", role_title,
                {"Display Name": role_title},
            ))
            if person_name:
                appt_pre_filled: Dict[str, str] = {
                    "role_name":   role_title,
                    "person_name": person_name,
                }
                # Use Egeria-resolved qualified name when available (avoids execution failure)
                if role.get("actor_found") and role.get("actor_qualified_name"):
                    appt_pre_filled["Actor Profile Qualified Name"] = role["actor_qualified_name"]
                commands.append(_make_cmd(
                    "Link Person Role Appointment", "",
                    appt_pre_filled,
                ))

        # ── Auto-append visualization report for Solution Architect plans ──
        # When the plan creates a Solution Blueprint and no View Report step
        # already exists (new or existing commands), add one automatically so
        # the user gets a Mermaid diagram of the result after execution.
        all_cmds = commands + existing_commands
        has_view_report = any(c.get("action") == "View Report" for c in all_cmds)
        if blueprint_name and not has_view_report:
            commands.append({
                "action":       "View Report",
                "display_name": blueprint_name,
                "_answers_key": f"View Report:{blueprint_name}",
                "description":  "",
                "rationale":    "",
                "narrative":    (
                    f"Visualizes the completed '{blueprint_name}' solution blueprint "
                    "as a Mermaid architecture diagram."
                ),
                "pre_filled": {
                    "Report Spec":   "Solution-Blueprint",
                    "Output Format": "MERMAID",
                    "Search String": blueprint_name,
                },
                "placeholders": {},
                "_auto_appended": True,  # flag for elicitor to surface a note
            })

        return commands

    # ---------------------------------------------------------------------- #
    # Template loading                                                         #
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _draft_mode(draft_id: Optional[str]) -> str:
        """The tier ("basic"/"advanced") a draft was authored at.

        Falls back to "basic" whenever the draft can't be read — an execution
        must not fail because the tier lookup did. A missing draft here is
        normal, not exceptional: a plan can be executed straight from a
        document with no draft behind it.
        """
        if not draft_id:
            return "basic"
        try:
            from advisor.governance_draft import get_draft_manager

            spec = get_draft_manager().load(draft_id)
            return (spec or {}).get("mode") or "basic"
        except Exception as exc:
            logger.debug(f"GovernancePlanAgent: could not read mode from draft {draft_id!r}: {exc}")
            return "basic"

    def _load_template(self, action: str, mode: str = "basic") -> Optional[Dict]:
        """
        Find and parse the best-matching template file for *action* at the
        given tier ("basic" or "advanced").

        Returns the parsed template dict, or None if not found.

        **This used to load the basic tier unconditionally** (BACKLOG.md PC-1),
        which silently dropped every advanced-only field at compose time —
        `_compose_command_block` only emits an attribute that appears in the
        loaded template's `attributes` list. Confirmed live 2026-07-06: a
        `Parent ID` / `Parent Relationship Type Name` set on a Create Project
        command rendered as neither field, with no error and no warning.

        Switching tiers is safe rather than a trade-off, and that was measured
        rather than assumed (2026-08-28, all four template roots on this
        machine — the trellis copy, egeria-workspaces' two, and
        egeria-python's sample-data; the trellis copy was
        `packages/egeria-advisor/examples/templates` when measured and moved
        to `config/dr-egeria-templates` on 2026-09-06, same content):

          * every basic template has an advanced counterpart (325/325 in
            the trellis copy, 0 missing anywhere);
          * advanced is a strict superset of basic — no field exists in basic
            and not in advanced, anywhere;
          * the *required* set is identical in every one of the 325 pairs, so
            loading advanced cannot introduce a new "<!-- TODO: fill in -->"
            line. The extra advanced attributes are all optional, and
            `_compose_command_block` emits an optional attribute only when it
            has a value.

        So there is no cross-tier merge to perform — the question BACKLOG.md
        left open. Advanced mode simply stops discarding what the user set.
        The fallback chain (requested tier -> basic -> root) still covers a
        template collection that predates the two-tier layout.
        """
        from advisor.agents.tools import _templates_root, _normalise
        from advisor.agents.dr_egeria_agent import parse_template

        root = _templates_root()
        if root is None:
            return None

        level_dir = root / (mode or "basic")
        if not level_dir.is_dir():
            level_dir = root / "basic"
        if not level_dir.is_dir():
            level_dir = root

        query_norm = _normalise(action)
        words = [_normalise(w) for w in action.split() if len(w) > 3]

        best_score = 0
        best_file: Optional[Path] = None

        for md_file in sorted(level_dir.rglob("*.md")):
            stem_norm = _normalise(md_file.stem)
            score = 0
            if query_norm == stem_norm:
                score = 50          # exact match: highest priority
            elif query_norm in stem_norm:
                score = 40          # query is a prefix/substring of stem
            elif stem_norm in query_norm:
                score = 35          # stem is a prefix of query (less specific template)
            elif words:
                hits = sum(1 for w in words if w in stem_norm)
                if hits == len(words):
                    score = 30
                elif hits > 0:
                    score = 20 + hits
            if score > best_score:
                best_score = score
                best_file = md_file

        if best_file is None or best_score == 0:
            return None

        try:
            return parse_template(str(best_file))
        except Exception as exc:
            logger.warning(
                f"GovernancePlanAgent: failed to parse template {best_file}: {exc}"
            )
            return None

    # ---------------------------------------------------------------------- #
    # Narrative generation                                                     #
    # ---------------------------------------------------------------------- #

    def _generate_narrative(
        self,
        query: str,
        purpose: str,
        perspective: str | None,
        commands: List[Dict],
        llm,
    ) -> Tuple[str, List[str], str]:
        """
        Generate Goal (paragraph), Requirements (bullet list), and Approach (numbered list).
        """
        command_list = "\n".join(
            f"  {i + 1}. {c['action']}: {c.get('description', '')}"
            for i, c in enumerate(commands)
        )
        perspective_line = (
            f"User role: {perspective}\n" if perspective else ""
        )

        prompt = f"""Write three sections for a data management plan.

User request: "{query}"
{perspective_line}Commands to execute (in order):
{command_list}

Write these three sections in order:

GOAL:
A single paragraph explaining what this plan achieves and why.

REQUIREMENTS:
3-5 bullet points (one per line, starting with "-") listing key requirements or constraints.

APPROACH:
A numbered list matching the commands above. Each line: "N. Command Name (Family) — brief rationale".

Keep all sections concise and use plain language.

GOAL:"""

        try:
            raw = llm.generate(prompt, temperature=0.3, max_tokens=700)

            # Parse the three sections
            parts = re.split(
                r'\n(?:REQUIREMENTS?|APPROACH):\s*\n?', raw, flags=re.IGNORECASE
            )

            goal = ""
            requirements: List[str] = []
            approach = ""

            if parts:
                goal = re.sub(r'^GOAL:\s*', '', parts[0], flags=re.IGNORECASE).strip()
            if len(parts) >= 2:
                req_block = parts[1].strip()
                requirements = [
                    line.lstrip("-•*0123456789. ").strip()
                    for line in req_block.splitlines()
                    if line.strip() and len(line.strip()) > 5
                ]
            if len(parts) >= 3:
                approach = parts[2].strip()

        except Exception as exc:
            logger.warning(
                f"GovernancePlanAgent: narrative generation failed: {exc}"
            )
            goal = purpose
            requirements = []
            approach = ""

        # Fallback: build approach from command list if LLM didn't produce one
        if not approach:
            approach = "\n".join(
                f"{i + 1}. {c['action']} — {c['spec'].get('rationale', c.get('description', ''))}"
                for i, c in enumerate(commands)
            )

        if not requirements:
            requirements = [
                "All required governance objects must be created before linking steps",
                "Use consistent display names that match your organisation's naming conventions",
                "Fill in any `<!-- TODO: fill in -->` placeholders before execution",
            ]

        return goal or purpose, requirements, approach

    # ---------------------------------------------------------------------- #
    # Document composition                                                     #
    # ---------------------------------------------------------------------- #

    def _compose_command_block(
        self, cmd: Dict, step_num: int
    ) -> str:
        """Compose one annotated Dr.Egeria command block."""
        action = cmd["action"]
        # Prefer user-edited narrative, then LLM rationale, then description
        narrative = (
            cmd.get("narrative")
            or cmd.get("spec", {}).get("rationale")
            or cmd.get("rationale")
            or cmd.get("description", "")
        )
        params: Dict[str, Any] = cmd.get("params", {})
        template: Optional[Dict] = cmd.get("template_parsed")

        lines: List[str] = []

        # Narrative comment header
        comment_body = action
        if narrative:
            # Wrap long narrative at ~80 chars, indented
            wrapped = "\n     ".join(
                narrative[i:i+80] for i in range(0, len(narrative), 80)
            )
            comment_body += f"\n     {wrapped}"
        lines.append(f"<!-- Step {step_num}: {comment_body} -->")

        lines.append(f"## {action}")
        lines.append("")

        if template:
            for attr in template["attributes"]:
                attr_name = attr["name"]

                # Resolve value: direct match, then alias
                value = params.get(attr_name) or params.get(attr_name.lower())
                if value is None:
                    for alias in attr.get("alternative_labels", []):
                        if not alias:
                            continue
                        for k, v in params.items():
                            if k.lower() == alias.lower():
                                value = v
                                break
                        if value is not None:
                            break

                if attr["required"]:
                    display = str(value) if value else "<!-- TODO: fill in -->"
                    lines.append(f"### {attr_name}")
                    lines.append(display)
                    lines.append("")
                elif value:
                    lines.append(f"### {attr_name}")
                    lines.append(str(value))
                    lines.append("")
        else:
            # No template available — minimal placeholder block
            display_name = (
                cmd.get("display_name") or cmd.get("description") or "<!-- TODO: fill in -->"
            )
            lines.append(f"### Display Name")
            lines.append(display_name)
            lines.append("")

        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    def _compose_document(
        self,
        title: str,
        purpose: str,
        perspective: str,
        goal: str,
        requirements: List[str],
        approach: str,
        commands: List[Dict],
        created_by: Optional[str] = None,
    ) -> str:
        """Assemble the complete GPD markdown."""
        import os
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        creator = created_by or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"

        parts: List[str] = [
            f"# {title}",
            f"**Created:** {now}   **Last edited:** {now}   **Status:** Draft",
            f"**Created by:** {creator}   **Last edited by:** {creator}   **Perspective:** {perspective}",
            f"**Purpose:** {purpose}",
            "",
            "---",
            "",
            "## Goal",
            "",
            goal,
            "",
            "## Requirements",
            "",
        ]

        for req in requirements:
            parts.append(f"- {req}")

        parts += [
            "",
            "## Approach",
            "",
            approach,
            "",
            "---",
            "",
            "## Command Sequence",
            "",
        ]

        for i, cmd in enumerate(commands):
            parts.append(self._compose_command_block(cmd, i + 1))

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: How long a shown plan can be confirmed (seconds).
PLAN_CONFIRM_TTL_S = 15 * 60
_PLAN_GATE = confirm_gate.ConfirmGate(PLAN_CONFIRM_TTL_S)

PLAN_CONFIRM_REQUIRED = ("A plan runs only after it has been shown and confirmed: ask to execute it, check the "
                         "Command Sequence shown, then confirm. Nothing was run.")
PLAN_CONFIRM_UNKNOWN = ("That plan confirmation has expired or is unknown, so nothing was run. Ask to execute "
                        "the plan again to see it before confirming.")
PLAN_CONFIRM_OTHER_SIGN_IN = ("That plan was shown to another sign-in, so nothing was run. Ask to execute it "
                              "again as you.")
PLAN_CONFIRM_CHANGED = ("The plan digest you confirmed is not the one that was shown, so nothing was run. Ask to "
                        "execute the plan again to see the current version.")
PLAN_CONFIRM_OTHER_PLAN = ("That confirmation was for a different plan or action, so nothing was run. Ask to "
                           "execute this plan to see it before confirming.")
PLAN_DOCUMENT_CHANGED = ("The plan document has changed since it was shown, so nothing was run. Ask to execute "
                         "it again to see the current version before confirming.")


PLAN_ACK_REQUIRED = ("This re-run repeats commands that are not safe to run twice, so a plain yes is not enough "
                     "and nothing was run. Acknowledge them to run the plan again anyway (chat: **yes, re-run**).")


def plan_command_names(command_section: str) -> List[str]:
    """The plan's commands, in order: its `## <Command>` headers."""
    return [m.strip() for m in re.findall(r'^##\s+(.+?)\s*$', command_section, re.MULTILINE)]


#: Verbs whose commands are unsafe to run twice when pyegeria's spec says nothing (`idempotent` unset), and why.
#: pyegeria 6.2.40 sets the flag on no command, so today these decide. Read-back
#: READBACK-ea-plan-rerun-ack-eae05be9-2026-11-13 widened them from Link alone.
UNSAFE_FALLBACK_VERBS: Dict[str, str] = {
    "link": "a Link command: running it again adds the relationship again",
    "attach": "an Attach command: running it again attaches it again",
    "initiate": "an Initiate command: running it again starts another run",
    "import": "an Import command: running it again imports it again",
}


def non_idempotent_commands(names: List[str]) -> List[Dict[str, Any]]:
    """The commands a re-run would repeat with a different result: [{command, count, why}], first-seen order.

    pyegeria's compact spec decides when it says (`idempotent`, on the command or its family): false is unsafe,
    true is safe whatever the verb. Where it says nothing, a command whose verb (pyegeria's spec verb, else the
    name's first word) is in UNSAFE_FALLBACK_VERBS is unsafe: Link, Attach, Initiate, Import."""
    from advisor import compact_specs
    found: Dict[str, Dict[str, Any]] = {}
    for name in names:
        flag = compact_specs.flag(name, "idempotent")
        spec = compact_specs.command(name) or {}
        if flag is False:
            why = "its Dr.Egeria specification marks it non-idempotent"
        elif flag is None:
            verb = str(spec.get("verb", "")).lower()
            first = (name.split()[:1] or [""])[0].lower()
            why = UNSAFE_FALLBACK_VERBS.get(verb) or UNSAFE_FALLBACK_VERBS.get(first)
            if not why:
                continue
        else:
            continue
        found.setdefault(name, {"command": name, "count": 0, "why": why})["count"] += 1
    return list(found.values())


def refused_plan_steps(names: List[str]) -> List[str]:
    """The refusal sentence for each distinct step chat may never propose (advisor.chat_command_policy)."""
    from advisor.chat_command_policy import plan_step_refusal
    out: List[str] = []
    for name in dict.fromkeys(names):
        words = plan_step_refusal(name)
        if words:
            out.append(words)
    return out


def _steps_refused_words(refused: List[str]) -> str:
    return ("This plan can't run, and nothing was run: it contains commands an Advisor plan never runs.\n\n"
            + "\n".join(f"- {w}" for w in refused)
            + "\n\nRemove them from the plan, or run them from a reviewed Dr.Egeria document instead.")


def _unsafe_words(unsafe: List[Dict[str, Any]]) -> str:
    lines = ["**This is a re-run, and these commands are not safe to run twice:**"]
    for u in unsafe:
        times = f" (×{u['count']})" if u["count"] > 1 else ""
        lines.append(f"- {u['command']}{times}: {u['why']}")
    return "\n".join(lines)


def _reset_plan_gate_for_tests() -> None:
    _PLAN_GATE.clear()


def _load_plan(doc_id: str, source_folder: str) -> Optional[str]:
    from advisor.governance_docs import get_doc_manager
    dm = get_doc_manager()
    return dm.load_outbox(doc_id) if source_folder == "outbox" else dm.load(doc_id)


def _plan_conn(doc_id: str, egeria_credentials: Optional[Dict[str, str]]):
    """(the caller's Egeria connection, None), or (None, the sign-in refusal): who a plan would run as."""
    from advisor.agents.dr_egeria_agent import DrEgeriaAuthError, get_dr_egeria_agent
    try:
        return get_dr_egeria_agent()._get_egeria_conn(egeria_credentials), None
    except DrEgeriaAuthError as err:
        out = _refused(doc_id, err.message)
        out["auth_required"] = True
        return None, out


def _refused(doc_id: str, words: str) -> Dict[str, Any]:
    out = _error_result(doc_id, words)
    out.update(state="refused", doc_id=doc_id, next_context=None)
    return out


def _already(result: Dict[str, Any]) -> Dict[str, Any]:
    return {**result, "state": "already_run",
            "response": "This plan was already run from that confirmation; nothing was run again.\n\n"
                        + str(result.get("response", ""))}


def _error_result(query: str, message: str) -> Dict[str, Any]:
    return {
        "query": query,
        "response": message,
        "query_type": "plan",
        "sources": [],
        "num_sources": 0,
        "retrieval_time": 0.0,
        "generation_time": 0.0,
        "avg_relevance_score": 0.0,
        "context_length": 0,
    }


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

def _extract_balanced_json(raw: str) -> str:
    """
    Find the outermost balanced {...} object in raw, even if the LLM appended
    trailing text or commentary after the closing brace.
    """
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_str:
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return raw[:i + 1]
    return raw  # fallback: return as-is


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_agent: Optional[GovernancePlanAgent] = None


# Signals that a plain-text (non-JSON-envelope) Dr.Egeria response is actually
# a failure — an unhandled exception/crash on Dr.Egeria's side (e.g. a
# database error) comes back as plain text too, not the expected envelope, and
# must never be reported as a successful execution just because it didn't
# parse as JSON. See BACKLOG.md.
_PLAIN_TEXT_FAILURE_RE = re.compile(
    r'\b(traceback|exception|error|failed|failure|no space left|'
    r'connection refused|could not connect|permission denied|'
    r'internal server error|out of memory)\b',
    re.IGNORECASE,
)


def _parse_dr_egeria_response(raw: str) -> "tuple[bool, str, list[dict], list[dict], dict]":
    """
    Parse Dr.Egeria's output from validate/process directives.

    Returns (success, output_text, validation_errors, execution_errors, counts)
    where counts = {total, succeeded, failed}.

    Handles two formats:
    1. JSON envelope: {"success": bool, "output": "...", "validation_errors": [...], ...}
    2. Plain text: scanned for common failure signals before being trusted as
       success — see _PLAIN_TEXT_FAILURE_RE.
    """
    if not raw or not raw.strip():
        return False, "(no output returned by Dr.Egeria)", [], [], {}

    stripped = raw.strip()
    if stripped.startswith('{'):
        data = None
        try:
            import json as _json
            data = _json.loads(stripped)
        except Exception:
            pass
        if data is None:
            # MCP may return Python repr (single quotes, True/False) instead of JSON
            try:
                import ast as _ast
                data = _ast.literal_eval(stripped)
            except Exception:
                pass
        if isinstance(data, dict) and 'success' in data:
            success  = bool(data['success'])
            output   = str(data.get('output', '') or '')
            val_errs = data.get('validation_errors', [])
            exe_errs = data.get('execution_errors', [])
            counts   = {
                'total':     data.get('commands_total', 0),
                'succeeded': data.get('commands_succeeded', 0),
                'failed':    data.get('commands_failed', 0),
                'detail':    data.get('commands_detail', []),
            }
            return success, output, val_errs, exe_errs, counts

    # Plain text — no structured envelope. Don't blindly assume success: an
    # unhandled crash on Dr.Egeria's side (e.g. the Postgres "no space left on
    # device" case) also comes back as plain text, not JSON, and reporting
    # that as a successful execution would be actively misleading.
    if _PLAIN_TEXT_FAILURE_RE.search(raw):
        return (
            False, raw, [],
            [{"step": "?", "command": "?", "message": raw.strip()[:500]}],
            {},
        )
    return True, raw, [], [], {}


def _strip_trailing_separators(text: str) -> str:
    """
    Strip one or more trailing "---" block separators (with surrounding blank
    lines). Loops a simple, anchored, non-ambiguous regex rather than using a
    single quantified group of overlapping character classes (e.g.
    `(?:\\n+-{3,}\\s*)+`) — that shape causes catastrophic backtracking on
    inputs with several separators in a row, hanging for many seconds or more.
    """
    text = text.rstrip()
    while re.search(r'\n-{3,}\Z', text) or re.match(r'^-{3,}\Z', text):
        text = re.sub(r'\n?-{3,}\Z', '', text).rstrip()
    return text


def _split_augmented_output(ex_output: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Split Dr.Egeria's augmented execution output into per-command echo blocks
    plus a trailing provenance note.

    Each command is echoed back as a "## Action" block with resolved field
    values. Some commands (e.g. View Report) also render display content —
    that content always starts at the first bare "# " (H1) line inside the
    block, with no other separator; everything before it is field data,
    everything from it onward is materialized report/diagram output, not
    part of the command definition.

    Returns (blocks, provenance) where each block is:
        {"action": str, "fields": {name: value}, "display": str}
    """
    if not ex_output or not ex_output.strip():
        return [], ""

    # Split off a trailing "## Provenance:" footer, if present.
    provenance = ""
    prov_m = re.search(r'^##\s+Provenance:?\s*\n(.*)\Z', ex_output, re.MULTILINE | re.DOTALL)
    body = ex_output[:prov_m.start()] if prov_m else ex_output
    if prov_m:
        provenance = prov_m.group(1).strip()

    blocks: List[Dict[str, Any]] = []
    for part in re.split(r'(?m)^##\s+', body)[1:]:
        lines = part.splitlines()
        if not lines:
            continue
        action = lines[0].strip()
        rest = "\n".join(lines[1:])

        h1_m = re.search(r'^#(?!#)\s+.*$', rest, re.MULTILINE)
        if h1_m:
            fields_text = rest[:h1_m.start()]
            display_text = rest[h1_m.start():].strip()
        else:
            fields_text = rest
            display_text = ""

        # Trim trailing "---" block separator(s) — pyegeria sometimes emits more
        # than one in a row before the next block.
        fields_text = _strip_trailing_separators(fields_text)
        display_text = _strip_trailing_separators(display_text)

        fields: Dict[str, str] = {}
        for fp in re.split(r'(?m)^###\s+', fields_text)[1:]:
            f_lines = fp.splitlines()
            if not f_lines:
                continue
            f_name = f_lines[0].strip()
            fields[f_name] = "\n".join(f_lines[1:]).strip()

        blocks.append({"action": action, "fields": fields, "display": display_text})

    return blocks, provenance


def _build_raw_output_section(raw_output: str) -> str:
    """
    Wrap the raw Dr.Egeria execution output in a collapsible markdown section.
    This is appended to the outbox plan so the full output (including View Report
    results and Mermaid diagrams) is always preserved and accessible.
    """
    if not raw_output or not raw_output.strip():
        return ""
    return (
        "## Dr.Egeria Execution Output\n\n"
        "<details>\n"
        "<summary>View raw Dr.Egeria output (click to expand)</summary>\n\n"
        f"{raw_output.strip()}\n\n"
        "</details>\n"
    )


def get_governance_plan_agent() -> GovernancePlanAgent:
    global _agent
    if _agent is None:
        _agent = GovernancePlanAgent()
    return _agent
