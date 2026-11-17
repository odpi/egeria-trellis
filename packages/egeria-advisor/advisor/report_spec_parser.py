"""
report_spec_parser.py — Markdown parser and validator for Report Spec Documents (RSDs).

Parses Markdown report specs using Dr. Egeria UniversalExtractor conventions:
- ## Create Report Spec -> Defines metadata (Target Type, Heading, Description, Action Function, etc.)
- ## Create Column      -> Defines column properties (Name, Key, Format, Detail Spec, Formats)
"""
from __future__ import annotations

import os
import sys
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

# Add egeria-python to path if not present to resolve md_processing and pyegeria
try:
    import md_processing
except ImportError:
    # __file__ = <repo>/packages/egeria-advisor/advisor/report_spec_parser.py
    repo_root = Path(__file__).parent.parent.parent.parent
    candidates = []
    env_root = os.environ.get("EGERIA_PYTHON_PATH")
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates += [
        repo_root.parent / "egeria-python",       # sibling of <repo>'s parent (dwolfson's layout)
        repo_root.parent.parent / "egeria-python",  # sibling of <repo>'s grandparent
        Path("~/localGit/egeria-python").expanduser(),
    ]
    for p in candidates:
        if p.exists():
            sys.path.insert(0, str(p))
            break

try:
    from md_processing.v2.extraction import UniversalExtractor
    from pyegeria.view._output_format_models import FormatSet, Format, Column, ActionParameter, QuestionSpec
except ImportError as e:
    logger.error(f"Failed to import pyegeria / md_processing modules: {e}")
    raise


def _split_top_level(text: str) -> List[str]:
    """Split on commas that are not inside [...], {...}, or a quoted string within them."""
    parts, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'" and depth:  # quotes count only inside a list/object (an apostrophe in a bare value is text)
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p for p in parts if p.strip()]


def _parse_spec_params(param_str: str) -> Dict[str, Any]:
    """Parse key=value parameters from a spec parameter string.

    Accepts both comma-separated (legacy) and newline-separated (current markdown) formats.
    """
    result = {}
    if not param_str.strip():
        return result
    # Normalise: replace newlines with commas so both formats go through the same splitter
    normalised = re.sub(r'\r?\n', ',', param_str)
    # Split on commas outside brackets/braces, e.g. ['A', 'B'] or {"k": 1, "j": 2} stay whole
    parts = _split_top_level(normalised)
    for part in parts:
        if '=' in part:
            k, v = part.split('=', 1)
            k = k.strip()
            v = v.strip()
            
            # Type conversion
            if v.lower() == 'true':
                v = True
            elif v.lower() == 'false':
                v = False
            elif v.startswith('{') and v.endswith('}'):
                # A JSON object (e.g. saved_query's `params`); left as text when it is not JSON
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
            elif v.startswith('[') and v.endswith(']'):
                try:
                    # Clean up JSON formatting or evaluate list
                    cleaned_v = v.replace("'", '"')
                    v = json.loads(cleaned_v)
                except Exception:
                    # Fallback string list split
                    v = [item.strip().strip('"').strip("'") for item in v[1:-1].split(',')]
            else:
                try:
                    if '.' in v:
                        v = float(v)
                    else:
                        v = int(v)
                except ValueError:
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
            result[k] = v
    return result


def parse_report_spec_markdown(content: str) -> FormatSet:
    """
    Parse a Report Spec Document (.md) into a FormatSet model.
    Reuses md_processing's UniversalExtractor to extract commands and attributes.

    PROTOTYPE, only with EA_SPEC_AUTHORING_V2 on: a document pyegeria wrote (it carries a `### Microflow`)
    is read through its microflow by pyegeria instead, so every caller reads both kinds. Callers this
    reaches: agents/report_spec_agent.execute (non-V2 docs only; it reads V2 docs itself),
    report_spec_docs (register on create/update, validate, inbox registration at load),
    web/reports.py (draft preview, register on run, the validate and import routes).
    """
    # Only a document with a microflow can take the V2 path, so look for one before importing the Advisor:
    # trellis-analytics loads this module by path, without the `advisor` package.
    if "### Microflow" in content:
        from advisor.spec_authoring_v2 import enabled as _v2_enabled, format_set_from_document
        if _v2_enabled():
            m = re.search(r"^### Display Name\s*\n(.+)$", content, flags=re.M)
            return format_set_from_document(content, (m.group(1).strip() if m else "ea-spec"))
    extractor = UniversalExtractor(content)
    commands = extractor.extract_commands()
    
    spec_cmd = None
    column_cmds = []
    
    for cmd in commands:
        if not cmd.is_command:
            continue
        # Support both Create/Define verb, and Report Spec / Attribute object types
        verb = cmd.verb.lower()
        obj = cmd.object_type.lower()
        if verb in ("create", "define") and obj == "report spec":
            spec_cmd = cmd
        elif verb in ("create", "define", "link", "add") and obj in ("column", "attribute"):
            column_cmds.append(cmd)
            
    if not spec_cmd:
        raise ValueError("No 'Create Report Spec' command found in markdown.")
        
    attrs = spec_cmd.attributes
    heading = attrs.get("Heading", "Untitled Report")
    description = attrs.get("Description", "No description provided.")
    target_type = attrs.get("Target Type") or None
    family = attrs.get("Family") or None
    
    # Action Parameter parsing
    action_function = attrs.get("Action Function")
    action_obj = None
    analytic_function = attrs.get("Analytic Function") or None      # a registered name (Q2(b)), not a Python path
    analytic_spec_params = _parse_spec_params(attrs.get("Analytic Spec Params", ""))
    if analytic_function and not action_function:
        # an analytic-only spec (no find method), as pyegeria's analytic demo specs are
        # (pyegeria 6.2.2, ISSUE-141: `function` is optional when `analytic_function` is set)
        action_obj = ActionParameter(analytic_function=analytic_function,
                                     analytic_spec_params=analytic_spec_params)
    elif action_function:
        required_params = [p.strip() for p in attrs.get("Required Params", "").split(",") if p.strip()]
        optional_params = [p.strip() for p in attrs.get("Optional Params", "").split(",") if p.strip()]
        # Legacy Spec Params (flat key=value)
        spec_params: Dict[str, Any] = {}
        if "Spec Params" in attrs:
            spec_params = _parse_spec_params(attrs["Spec Params"])
        # Three-category parameter model — merged into spec_params at parse time
        for section_key in ("Content Filters", "Shape Defaults", "Performance Hints"):
            raw = attrs.get(section_key, "")
            if raw.strip():
                spec_params.update(_parse_spec_params(raw))
        action_obj = ActionParameter(
            function=action_function,
            required_params=required_params,
            optional_params=optional_params,
            spec_params=spec_params,
            analytic_function=analytic_function,
            analytic_spec_params=analytic_spec_params,
        )
        
    # Perspectives and Questions parsing
    perspectives = [p.strip() for p in attrs.get("Perspectives", "").split(",") if p.strip()]
    questions = [q.strip() for q in attrs.get("Questions", "").split(",") if q.strip()]
    question_spec_obj = None
    if perspectives or questions:
        question_spec_obj = [QuestionSpec(perspectives=perspectives, questions=questions)]
        
    # Columns parsing
    format_columns: Dict[str, List[Column]] = {}
    
    for col_cmd in column_cmds:
        col_attrs = col_cmd.attributes
        name = col_attrs.get("Name", "Unnamed Column")
        key = col_attrs.get("Key", "")
        
        # Pydantic format Union validation (boolean or string)
        fmt_val = col_attrs.get("Format", "False")
        if fmt_val.lower() == "true":
            format_prop = True
        elif fmt_val.lower() == "false":
            format_prop = False
        else:
            format_prop = fmt_val  # e.g., "bulleted-list"
            
        detail_spec = col_attrs.get("Detail Spec") or None
        
        col_obj = Column(
            name=name,
            key=key,
            format=format_prop,
            detail_spec=detail_spec
        )
        
        # Map column to format types
        formats_attr = col_attrs.get("Formats", "ALL")
        formats_list = [f.strip() for f in formats_attr.split(",") if f.strip()]
        for f in formats_list:
            if f not in format_columns:
                format_columns[f] = []
            format_columns[f].append(col_obj)
            
    # Output Types names the types of a single Format with no attributes (an analytic result has none to list)
    output_types = [t.strip() for t in attrs.get("Output Types", "").split(",") if t.strip()]
    # Default to ALL if no columns/formats were added
    if not format_columns and not output_types:
        format_columns["ALL"] = []
        
    formats = []
    if output_types and not format_columns:
        formats.append(Format(types=output_types, attributes=[]))
    for fmt_type, attributes in format_columns.items():
        formats.append(Format(
            types=[fmt_type],
            attributes=attributes
        ))
        
    aliases = [a.strip() for a in attrs.get("Aliases", "").split(",") if a.strip()]
    
    return FormatSet(
        target_type=target_type,
        heading=heading,
        description=description,
        family=family,
        aliases=aliases,
        formats=formats,
        action=action_obj,
        question_spec=question_spec_obj
    )


def validate_report_spec(spec: FormatSet) -> List[str]:
    """Validate a parsed FormatSet against Egeria class/method conventions."""
    warnings = []
    if spec.action and not spec.action.function and spec.action.analytic_function:
        # An analytic-only spec has no find method by design (pyegeria ISSUE-141): nothing to warn about.
        return warnings
    if not spec.action or not spec.action.function:
        warnings.append("No Action Function defined.")
        return warnings
        
    func_str = spec.action.function
    if "." not in func_str:
        warnings.append(f"Invalid Action Function format: '{func_str}'. Must be 'ClientClass.method'.")
        return warnings
        
    client_name, method_name = func_str.split(".", 1)
    
    try:
        import pyegeria
        client_class = getattr(pyegeria, client_name, None)
        if client_class is None:
            warnings.append(f"Client class '{client_name}' not found in pyegeria.")
        elif not hasattr(client_class, method_name):
            warnings.append(f"Method '{method_name}' not found on {client_name}.")
    except Exception as e:
        warnings.append(f"Could not perform client validation: {e}")
        
    return warnings


#: Names EA itself registered (its own report-spec document store, drafts, the Spec Builder). pyegeria
#: counts them in its "local" tier together with its CONFIG specs; EA's catalog shows them as "user".
_EA_REGISTERED: set = set()


def ea_registered_specs() -> set:
    """The spec names EA registered through `register_report_spec` in this process."""
    return set(_EA_REGISTERED)


#: The RUNTIME-tier source label EA registers under (pyegeria `register_report_specs(source=...)`).
EA_REGISTRY_SOURCE = "egeria-advisor"


class ReportSpecNameTaken(ValueError):
    """The name is already a spec from another source (a config/user specs directory, or another
    app's runtime specs): EA cannot register its own spec under it."""


def register_report_spec(name: str, spec: FormatSet) -> None:
    """Register an EA spec (document store, draft, Spec Builder) in pyegeria's RUNTIME tier.

    Goes through pyegeria's public `register_report_specs(..., source="egeria-advisor")`, never by
    writing pyegeria's private dicts (it used to set `report_specs[name]` and
    `_RUNTIME_REPORT_FORMATS[name]` in every loaded copy of the module, which made an EA spec look
    built-in and bypassed the collision check). Re-registering a name EA registered replaces it. A
    name another source holds raises ReportSpecNameTaken with words; a built-in or Egeria-held spec
    of the same name is shadowed, not a clash (pyegeria's tier order: local > egeria > builtin).
    """
    from pyegeria.view.base_report_formats import register_report_specs
    try:
        from pyegeria.view.base_report_formats import ReportFormatCollision
    except ImportError:  # pragma: no cover -- every supported pyegeria has it
        ReportFormatCollision = ValueError  # type: ignore[assignment,misc]
    try:
        register_report_specs({name: spec}, source=EA_REGISTRY_SOURCE)
    except ReportFormatCollision as exc:
        raise ReportSpecNameTaken(
            f"A report spec named '{name}' already comes from another source, so this one was not "
            f"registered; rename it: {exc}") from exc
    _EA_REGISTERED.add(name)



