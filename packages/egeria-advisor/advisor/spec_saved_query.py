"""Route B as a spec choice (brief B6, N6): a report spec whose source is a SavedQuery.

The spec builder can make an analytic spec, `analytic_function: saved_query`, with
`{name, mode, output, params}` (pyegeria 6.2.34, `pyegeria.view.saved_query`):

- `name`: the SavedQuery's qualifiedName (found as the viewer, so zones apply);
- `mode`: `cached` (its Results Set, "as of" its last refresh) or `live` (run now, as the viewer);
- `output`: `count` (a KPI) or `list` (the elements);
- `params`: values for the SavedQuery's declared parameters, the shared P-2 model (typed values only,
  never a URL). P-3: a Smart Collection's cached answer stands only for its stored values; any other
  value is refused by pyegeria with a sentence that points to live mode, and EA shows that sentence.

This module only checks the shape the builder sends; pyegeria checks the values against the
SavedQuery's declared parameters at run time and refuses with words.
"""
from __future__ import annotations

from typing import Any, Dict

MODES = ("cached", "live")
OUTPUTS = ("count", "list")
SMART_COLLECTION_NOTE = (
    "A Smart Collection's cached answer stands only for the values its last refresh recorded. A "
    "different value is refused (Egeria re-runs the stored request itself); use live mode to run it "
    "with other values.")


def check_saved_query(raw: Any) -> Dict[str, Any]:
    """The builder's `saved_query` block, normalised, or ValueError with words."""
    if not isinstance(raw, dict):
        raise ValueError("saved_query must be an object: {name, mode, output, params}.")
    name = str(raw.get("name") or "").strip()
    if not name or "\n" in name:
        raise ValueError("A saved query needs its qualifiedName (one line).")
    mode = str(raw.get("mode") or "cached").strip().lower()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}.")
    output = str(raw.get("output") or "count").strip().lower()
    if output not in OUTPUTS:
        raise ValueError(f"output must be one of {', '.join(OUTPUTS)}, not {output!r}.")
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object of parameter name to value.")
    for k, v in params.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError("Every parameter needs a name.")
        if isinstance(v, (dict,)) or (isinstance(v, list) and any(isinstance(i, (dict, list)) for i in v)):
            raise ValueError(f"Parameter {k!r}: a value is a string, number, true/false or a list of those.")
    return {"name": name, "mode": mode, "output": output, "params": dict(params)}


def spec_md_lines(sq: Dict[str, Any]) -> list:
    """The Create Report Spec lines for a saved_query source (the RSD parser reads them back)."""
    import json
    lines = ["### Analytic Function", "saved_query", "", "### Analytic Spec Params",
             f"name={sq['name']}", f"mode={sq['mode']}", f"output={sq['output']}"]
    if sq.get("params"):
        lines.append("params=" + json.dumps(sq["params"], ensure_ascii=False, separators=(",", ":")))
    lines += ["", "### Output Types", "DICT, JSON", ""]
    return lines
