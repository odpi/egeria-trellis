"""Every report spec in pyegeria's registry names its analytic functions by registered name, never a dotted path.

Since pyegeria 6.2.19 a report spec that names a dotted path is refused (ISSUE-173). This walks the registry the way
8813 and the Portal build it (pyegeria's own specs plus every `pyegeria.report_specs` / `pyegeria.analytics` entry
point installed), collects each `analytic_function` an action names and each function-valued `metric_path`
parameter, and reports the ones that do not resolve by registered name.

    uv run python scripts/specs_check.py        # prints the counts; exit 1 when `bad` is not empty

On 2026-10-31, pyegeria 6.2.18: 424 specs, 26 refs (8813), bad [].
"""
from __future__ import annotations

import json
import sys


def _actions(spec):
    for attr in ("action", "get_additional_props"):
        act = getattr(spec, attr, None)
        if act is not None:
            yield act
    for fmt in getattr(spec, "formats", None) or []:
        for attr in ("action", "get_additional_props"):
            act = fmt.get(attr) if isinstance(fmt, dict) else getattr(fmt, attr, None)
            if act is not None:
                yield act


def _get(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def check() -> dict:
    """{"specs": N, "refs": N, "bad": [{"spec", "ref"}]}: `bad` is empty when every reference is a registered name."""
    from pyegeria.view.analytic_registry import get_analytic_registry
    from pyegeria.view.base_report_formats import get_report_registry

    names = set(get_analytic_registry())
    specs = get_report_registry()
    refs, bad = 0, []
    for spec_name, spec in specs.items():
        for act in _actions(spec):
            found = [_get(act, "analytic_function")]
            params = _get(act, "analytic_spec_params") or {}
            found.append(params.get("metric_path") if isinstance(params, dict) else None)
            for ref in found:
                if not ref:
                    continue
                refs += 1
                if ref not in names:
                    bad.append({"spec": spec_name, "ref": ref})
    return {"specs": len(specs), "refs": refs, "bad": bad}


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2))
    sys.exit(1 if result["bad"] else 0)
