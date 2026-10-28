"""
ReportSpecAgent — handles execution, retry, and recovery of Report Spec Documents (RSDs).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List
from loguru import logger

from advisor.report_spec_docs import get_report_spec_doc_manager
from advisor.report_spec_parser import parse_report_spec_markdown, register_report_spec, validate_report_spec
from advisor.report_pipeline import get_report_pipeline, _conn_is_complete


def calculate_required_depth(key: str) -> int:
    """Determine the required Egeria graph query depth for a given dot-notation path."""
    if not key:
        return 0
    parts = key.split('.')
    if len(parts) <= 1:
        return 0
    
    ancestors = parts[:-1]
    depth = 0
    for anc in ancestors:
        clean = anc.replace("[]", "").strip()
        if clean in ("properties", "elementHeader", "relatedElement", "relationshipProperties"):
            continue
        depth += 1
    return depth


class ReportSpecAgent:
    """Agent for executing report specs and managing run outcomes."""

    def execute(
        self,
        doc_id: str,
        perspective: Optional[str] = None,
        dry_run: bool = False,
        source_folder: str = "inbox",
        output_format: str = "TABLE",
        custom_params: Optional[Dict[str, Any]] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a report spec document and append the run outcome.
        
        Steps:
          1. Load document content from inbox or outbox.
          2. Parse the markdown into a FormatSet model and register it.
          3. Validate the Action Function.
          4. Execute via pyegeria.exec_report_spec with merged parameters.
          5. Format the output to markdown.
          6. Move/write the document to outbox with Outcome section appended.
        """
        base_doc_id = re.sub(r'_executed_\d{8}_\d{6}$', '', doc_id)
        if base_doc_id.startswith("draft_report_"):
            from advisor.report_draft import get_report_draft_manager
            from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
            dm = get_report_draft_manager()
            draft = dm.load(base_doc_id)
            if not draft:
                return self._error_result(
                    doc_id,
                    f"Report spec draft `{doc_id}` not found."
                )
            elicitor = get_report_spec_elicitor()
            content = elicitor._generate_report_spec_md(draft)
        else:
            doc_manager = get_report_spec_doc_manager()
            content = doc_manager.load(base_doc_id)

        if not content:
            return self._error_result(
                doc_id,
                f"Report spec document `{doc_id}` not found."
            )

        try:
            spec = parse_report_spec_markdown(content)
            # Ensure the spec is registered under this doc_id
            register_report_spec(base_doc_id, spec)
        except Exception as exc:
            return self._error_result(
                doc_id,
                f"Failed to parse report spec markdown: {exc}"
            )

        # Validate action function
        val_warnings = validate_report_spec(spec)
        if val_warnings:
            logger.warning(f"Validation warnings for report spec {doc_id}: {val_warnings}")

        if dry_run:
            summary = (
                f"**Dry run — report spec not executed.**\n\n"
                f"Parsed Report Spec details:\n"
                f"- **Heading:** {spec.heading}\n"
                f"- **Description:** {spec.description}\n"
                f"- **Target Type:** {spec.target_type or 'None'}\n"
                f"- **Action Function:** {spec.action.function if spec.action else 'None'}\n"
            )
            if val_warnings:
                summary += f"- **Validation warnings:** {', '.join(val_warnings)}\n"
            return {
                "query": doc_id,
                "response": summary,
                "query_type": "report_executed",
                "doc_id": doc_id,
                "dry_run": True,
                "sources": [],
                "num_sources": 0,
                "retrieval_time": 0.0,
                "generation_time": 0.0,
                "avg_relevance_score": 0.0,
                "context_length": len(summary),
            }

        # Resolve connection details
        pipeline = get_report_pipeline()
        try:
            conn = pipeline._read_pyegeria_connection(egeria_credentials=egeria_credentials)
        except Exception as exc:
            logger.error(f"Failed to read Egeria connection info: {exc}")
            conn = {}

        # A signed-in caller carries an Egeria bearer token and no password
        # (since 2026-09-04); only the service-account fallback has a password.
        if not _conn_is_complete(conn):
            return self._error_result(
                doc_id,
                "Egeria connection is not configured (config/mcp_servers.json -> pyegeria.env)"
            )

        # Parameter merging
        params: Dict[str, Any] = {
            "search_string": "*",
            "page_size": 100,
            "start_from": 0,
        }
        if spec.action and spec.action.spec_params:
            params.update(spec.action.spec_params)
        if custom_params:
            params.update(custom_params)
        # Egeria type names are always PascalCase — auto-capitalize first letter
        if "metadata_element_type" in params and isinstance(params["metadata_element_type"], str):
            met = params["metadata_element_type"].strip()
            if met and met[0].islower():
                params["metadata_element_type"] = met[0].upper() + met[1:]

        # Determine optimized graph query depth from active column keys
        column_keys = set()
        for fmt in spec.formats:
            for col in fmt.attributes:
                if col.key:
                    column_keys.add(col.key)
                    
        max_depth = 0
        for k in column_keys:
            depth = calculate_required_depth(k)
            if depth > max_depth:
                max_depth = depth
                
        # Auto-tune: set graph_query_depth if not explicitly overridden by custom_params
        if not custom_params or "graph_query_depth" not in custom_params:
            params["graph_query_depth"] = max_depth
            logger.info(f"Auto-tuned graph_query_depth to {max_depth} based on column projection keys")
            
        # Backlog shape optimization: if max_depth is 0, auto-set skip_relationships = True
        if max_depth == 0 and (not custom_params or "skip_relationships" not in custom_params):
            params["skip_relationships"] = True
            logger.info("Auto-tuned skip_relationships to True (depth is 0)")

        from pyegeria import exec_report_spec

        # Request raw JSON from pyegeria when TABLE or DICT is selected,
        # allowing Egeria Advisor to apply its own multi-column pipeline formatters.
        fetch_format = "JSON" if output_format in ("TABLE", "DICT") else output_format

        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "Completed"
        error_msg = ""
        execution_output = ""

        try:
            raw = exec_report_spec(
                base_doc_id,
                output_format=fetch_format,
                params=params,
                view_server=conn["view_server"],
                view_url=conn["platform_url"],
                user=conn["user_id"],
                user_pass=conn["user_pwd"],
                token=conn.get("token") or None,
            )
            # exec_report_spec returns a shaped envelope — unwrap before formatting
            if raw is None or (isinstance(raw, dict) and raw.get("kind") == "empty"):
                execution_output = "*No results returned.*"
            elif isinstance(raw, dict):
                kind = raw.get("kind")
                if kind == "json":
                    execution_output = pipeline._format_output(raw.get("data"), output_format, spec.heading, spec=spec)
                elif kind == "text":
                    execution_output = raw.get("content", "")
                else:
                    execution_output = pipeline._format_output(raw, output_format, spec.heading, spec=spec)
            else:
                execution_output = pipeline._format_output(raw, output_format, spec.heading, spec=spec)
        except Exception as exc:
            status = "Failed"
            error_msg = str(exc)
            execution_output = f"**Error:** {exc}"
            logger.error("ReportSpecAgent.execute: exec_report_spec failed: {}", str(exc), exc_info=True)

        # Display the result directly — save to outbox only as an optional follow-up
        # (the "What next?" buttons let the user decide whether to archive it)
        response = f"# {spec.heading}\n\n{execution_output}"

        return {
            "query": doc_id,
            "response": response,
            "query_type": "act_report_result",   # shows "What next?" buttons
            "matched_spec_id": base_doc_id,       # used by Modify / Save buttons
            "execution_output": execution_output, # raw for optional save
            "base_doc_id": base_doc_id,
            "dry_run": False,
            "status": status,
            "error_message": error_msg,
            "sources": [],
            "num_sources": 0,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": len(response),
        }

    def retry(
        self, doc_id: str, perspective: Optional[str] = None, output_format: str = "TABLE",
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Re-execute a report spec.

        Strips any _executed_<ts> suffix to find the base spec in the catalog (inbox),
        then runs it again.  The spec is never removed from inbox so no move is needed.
        """
        base_doc_id = re.sub(r'_executed_\d{8}_\d{6}$', '', doc_id)
        return self.execute(base_doc_id, perspective=perspective, output_format=output_format,
                             egeria_credentials=egeria_credentials)

    def recover(self, doc_id: str) -> Dict[str, Any]:
        """Confirm the spec is available in the catalog (inbox).

        Under the new lifecycle model specs are never removed from inbox on execute,
        so this is a no-op confirmation rather than a file move.
        """
        base_doc_id = re.sub(r'_executed_\d{8}_\d{6}$', '', doc_id)
        doc_manager = get_report_spec_doc_manager()
        content = doc_manager.load(base_doc_id)
        if content is None:
            return self._error_result(doc_id, f"Report spec `{base_doc_id}` not found in catalog.")
        response_msg = f"Report spec `{base_doc_id}` is in your catalog and ready to run."
        return {
            "query": doc_id,
            "response": response_msg,
            "query_type": "report_recovered",
            "doc_id": base_doc_id,
            "sources": [],
            "num_sources": 0,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": len(response_msg),
        }

    def _error_result(self, query: str, message: str) -> Dict[str, Any]:
        return {
            "query": query,
            "response": message,
            "query_type": "report_executed",
            "doc_id": None,
            "status": "Error",
            "sources": [],
            "num_sources": 0,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": len(message),
        }


_agent: Optional[ReportSpecAgent] = None


def get_report_spec_agent() -> ReportSpecAgent:
    global _agent
    if _agent is None:
        _agent = ReportSpecAgent()
    return _agent
