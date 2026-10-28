"""Survey metadata agent — answers questions about analysis runs, data sources, and annotations.

Does not use BeeAI or pgvector. Queries the local registry directly and
uses the LLM to compose a natural-language answer from the structured data.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


class SurveyMetaAgent:
    """
    Answers questions about:
    - Survey history and when analyses were last run
    - Where data in the survey report comes from (local scan vs Egeria)
    - Annotations and RequestForAction items
    - Activity log entries
    - Stored context (environment, owner, sensitivity, etc.)
    - Available and configured analyses
    """

    def handle(self, query: str, resource_slug: str | None = None) -> str:
        context = self._gather_context(query, resource_slug)
        return self._generate(query, context)

    # ── context gathering ─────────────────────────────────────────────────────

    def _gather_context(self, query: str, slug: str | None) -> dict:
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

        registry = ProjectRegistry()
        q = query.lower()
        ctx: dict = {}

        # Determine entity type from slug
        entity_type: str | None = None
        if slug:
            if registry.get(slug):
                entity_type = "repo"
            elif registry.get_database(slug, allow_unreadable=True):
                entity_type = "database"

        # Activity log — always useful
        limit = 20
        if slug:
            entries = registry.list_activity(limit=limit)
            entries = [e for e in entries if e.get("entity_slug") == slug][:10]
        else:
            entries = registry.list_activity(limit=limit)
        ctx["recent_activity"] = [
            {
                "ts": e["ts"][:16],
                "operation": e.get("operation"),
                "intent": e.get("intent"),
                "entity_type": e.get("entity_type"),
                "entity_slug": e.get("entity_slug"),
                "status": e.get("status"),
                "summary": e.get("summary", ""),
                "annotation_count": len(e.get("annotations") or []),
            }
            for e in entries
        ]

        # RFAs
        if "rfa" in q or "request" in q or "annotation" in q or "open" in q:
            rfas = []
            for entry in registry.list_activity(limit=200):
                if slug and entry.get("entity_slug") != slug:
                    continue
                for ann in (entry.get("annotations") or []):
                    if "RequestForAction" in (ann.get("annotation_type") or ""):
                        rfas.append({
                            "entity": entry.get("entity_slug"),
                            "analysis": ann.get("analysis_name"),
                            "summary": ann.get("summary", ""),
                            "status": ann.get("status"),
                        })
            ctx["rfas"] = rfas[:20]

        # Survey history
        if slug and entity_type == "repo":
            history = registry.query_file_type_history(slug)
            ctx["survey_history"] = [
                {"ts": r["surveyed_at"][:16], "total_files": int(r["total_files"] or 0), "source": r.get("source", "local")}
                for r in history
            ]
        elif slug and entity_type == "database":
            surveys = registry.get_database_surveys(slug)
            ctx["survey_history"] = [
                {
                    "ts": s.get("surveyed_at", "")[:16],
                    "schemas": s.get("schema_count"),
                    "tables": s.get("table_count"),
                    "columns": s.get("column_count"),
                    "source": s.get("source", "local"),
                }
                for s in surveys[:10]
            ]

        # Context (human-provided)
        if slug and entity_type:
            stored_ctx = registry.get_context(entity_type, slug) or {}
            if stored_ctx:
                ctx["resource_context"] = stored_ctx

        # Available / scheduled analyses
        if "analys" in q or "schedule" in q or "available" in q:
            rtype = entity_type or ("database" if "database" in q else "repo")
            ctx["available_analyses"] = [
                {"id": a["id"], "name": a["name"], "intent": a["intent"], "source": a["source"], "run_time": a["run_time"]}
                for a in get_analyses(rtype)
            ]
            if slug and entity_type:
                schedules = registry.get_schedules(entity_type, slug)
                ctx["schedules"] = schedules

        ctx["query_time"] = datetime.now(timezone.utc).isoformat()[:16]
        if slug:
            ctx["resource_slug"] = slug
            ctx["entity_type"] = entity_type or "unknown"
        return ctx

    # ── LLM generation ────────────────────────────────────────────────────────

    def _generate(self, query: str, context: dict) -> str:
        ctx_text = json.dumps(context, indent=2, default=str)
        prompt = (
            "You are an assistant that answers questions about survey history, "
            "data sources, analysis runs, and annotations for a metadata catalog tool.\n\n"
            f"Available context (from local registry):\n{ctx_text}\n\n"
            f"Question: {query}\n\n"
            "Answer concisely using only the data above. If the data is empty or insufficient, "
            "say so clearly. Use markdown lists or tables where helpful."
        )
        try:
            from resource_explorer.llm_client import get_llm
            llm = get_llm()
            return llm.complete(prompt)
        except Exception:
            # Fallback: format the context as plain text without LLM
            return self._format_fallback(query, context)

    def _format_fallback(self, query: str, context: dict) -> str:
        lines = [f"**Survey metadata for:** {context.get('resource_slug', 'all resources')}\n"]

        if context.get("recent_activity"):
            lines.append("**Recent activity:**")
            for e in context["recent_activity"][:5]:
                lines.append(
                    f"- {e['ts']} · {e['operation']} · {e['entity_slug']} · {e['status']}"
                    + (f" — {e['summary']}" if e['summary'] else "")
                )
            lines.append("")

        if context.get("survey_history"):
            lines.append("**Survey history:**")
            for r in context["survey_history"][:5]:
                if "total_files" in r:
                    lines.append(f"- {r['ts']} · {r['total_files']} files · {r['source']}")
                else:
                    lines.append(
                        f"- {r['ts']} · schemas:{r.get('schemas')} tables:{r.get('tables')} "
                        f"columns:{r.get('columns')} · {r.get('source')}"
                    )
            lines.append("")

        if context.get("rfas"):
            lines.append(f"**Open RFAs ({len(context['rfas'])}):**")
            for r in context["rfas"][:5]:
                lines.append(f"- [{r['entity']}] {r['analysis']}: {r['summary']}")
            lines.append("")

        if context.get("resource_context"):
            lines.append("**Stored context:**")
            for k, v in context["resource_context"].items():
                if v:
                    lines.append(f"- {k}: {v}")
            lines.append("")

        if not any([context.get("recent_activity"), context.get("survey_history"),
                    context.get("rfas"), context.get("resource_context")]):
            lines.append("No survey metadata found. Run a survey first.")

        return "\n".join(lines)
