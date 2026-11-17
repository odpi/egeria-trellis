"""EA re-base prototype harness (BRIEF-EA-REBASE-PROTOTYPE-2026-11-13 §2).

Drives EA's report-spec elicitor through the fixed question set with scripted replies, then checks each
generated spec with pyegeria and (live only) runs it as the signed-in persona. READ-ONLY against Egeria:
Dr.Egeria is only ever called with directive "validate", and the run is exec_report_spec through
pyegeria.authoring.preview, which registers nothing.

    python scripts/ea_rebase/harness.py --mode baseline --offline            # LLM + offline checks only
    python scripts/ea_rebase/harness.py --mode baseline --user erinoverview  # live: needs the owner's word

Live mode reads the persona's password from EA_HARNESS_EGERIA_PASSWORD and never prints it. The Egeria
platform comes from EGERIA_VIEW_SERVER_URL / EGERIA_VIEW_SERVER, else EA's own config
(advisor.mcp_config.get_pyegeria_platform_config).

`--mode rebased` sets EA_SPEC_AUTHORING_V2=1 (the prototype's feature flag) before EA is imported.

Drafts and spec documents go to a temp directory, never ~/egeria-reports. The elicitor's LLM is EA's
planning model on the local Ollama. Results: <out>/results_<mode>.json (raw, per question) and
<out>/summary_<mode>.md. Scoring against the rubric is separate (score.py) so it can be redone.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
PKG = HERE.parent.parent
QUESTIONS = HERE / "questions_2026-11-13.json"
MAX_TURNS = 8
PREVIEW_ROWS = 200


def _isolate_storage(base: Path) -> None:
    """Point EA's report drafts and spec documents at `base` (never the real ~/egeria-reports)."""
    import advisor.report_draft as rd
    import advisor.report_spec_docs as rsd

    def _drafts() -> Path:
        p = base / "drafts"
        p.mkdir(parents=True, exist_ok=True)
        return p

    rd._drafts_path = _drafts
    rsd._load_paths = lambda: {k: base / k for k in ("inbox", "outbox", "trash", "versions")}
    # The LLM client records query metrics into EA's Postgres; the harness has no business writing there.
    import advisor.llm_client as lc
    lc.get_metrics_collector = lambda: None


QUICKSTART_DOC = PKG.parent.parent / "docs" / "deploying-trellis-with-quickstart.md"


def _quickstart_password() -> Optional[str]:
    """The QuickStart demo users' shared password, from the documented persona source (§1.3 "All identities
    are the QuickStart's demo users, defined in `…` (password `…`)"). Never printed or logged."""
    import re
    try:
        text = QUICKSTART_DOC.read_text()
    except OSError:
        return None
    section = text.split("### 1.3 Identities", 1)
    if len(section) != 2:
        return None
    m = re.search(r"demo users,\s+defined in\s+`[^`]+`\s+\(password\s+`([^`]+)`\)", section[1][:600])
    return m.group(1) if m else None


def _signin(user: str) -> Any:
    """An EgeriaTech client signed in as `user` (bearer token). Reads, nothing else."""
    from pyegeria import EgeriaTech
    from advisor.mcp_config import get_pyegeria_platform_config
    pwd = os.environ.get("EA_HARNESS_EGERIA_PASSWORD") or _quickstart_password()
    if not pwd:
        raise SystemExit("No password: EA_HARNESS_EGERIA_PASSWORD is not set and the QuickStart persona source "
                         f"({QUICKSTART_DOC.name} §1.3) did not resolve. Not guessing; stopping.")
    cfg = get_pyegeria_platform_config()
    if not (cfg["view_server"] and cfg["platform_url"]):
        raise SystemExit("No Egeria platform configured (EGERIA_VIEW_SERVER_URL / EGERIA_VIEW_SERVER).")
    client = EgeriaTech(cfg["view_server"], cfg["platform_url"], user_id=user, user_pwd=pwd)
    client.create_egeria_bearer_token(user, pwd)
    print(f"signed in as {user} at {cfg['platform_url']} / {cfg['view_server']}", flush=True)
    return client


def _readonly_runner(client: Any):
    """Dr.Egeria runner that refuses anything but validate."""
    from pyegeria.authoring import inprocess_runner
    inner = inprocess_runner(client)

    def run(markdown: str, directive: str) -> Dict[str, Any]:
        if directive != "validate":
            raise RuntimeError(f"harness is read-only: refused Dr.Egeria directive {directive!r}")
        return inner(markdown, "validate")
    return run


# ── driving the elicitor ─────────────────────────────────────────────────────

def _reply_for(phase: str, q: Dict[str, Any], turn: int) -> Optional[str]:
    """The scripted reply: the question's override for this turn, else accept the proposal."""
    overrides = q.get("replies") or []
    if turn - 1 < len(overrides):
        return overrides[turn - 1]
    if phase == "confirm_action":
        return "continue_columns"
    if phase == "elicit_columns":
        return "generate now"
    return None


def drive(question: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
    from advisor.report_draft import get_report_draft_manager
    el = get_report_spec_elicitor()
    transcript: List[Dict[str, Any]] = []
    t0 = time.time()
    r = el.start(question["question"], **ctx.get("start_kwargs", {}))
    transcript.append({"turn": 1, "user": question["question"], "phase": r.get("phase"),
                       "response": r.get("response"), "s": round(time.time() - t0, 2)})
    turns = 1
    while turns < MAX_TURNS:
        phase = r.get("phase")
        if phase in ("refine", "answered", "error", "clarify", "cancelled"):
            break
        reply = _reply_for(phase, question, turns)
        if reply is None:
            break
        t = time.time()
        r = el.process(r["draft_id"], reply)
        turns += 1
        transcript.append({"turn": turns, "user": reply, "phase": r.get("phase"),
                           "response": r.get("response"), "s": round(time.time() - t, 2)})
    out = {"turns": turns, "final_phase": r.get("phase"), "transcript": transcript,
           "elicit_s": round(time.time() - t0, 2), "result": r}
    draft_id = r.get("draft_id")
    spec = get_report_draft_manager().load(draft_id) if draft_id else None
    out["draft"] = spec
    return out


# ── checks ───────────────────────────────────────────────────────────────────

def _doc(doc_id: Optional[str]) -> Optional[str]:
    if not doc_id:
        return None
    from advisor.report_spec_docs import get_report_spec_doc_manager
    return get_report_spec_doc_manager().load(doc_id)


def _format_set(draft: Optional[Dict[str, Any]], markdown: Optional[str]) -> tuple:
    """(FormatSet or None, how it was obtained, error). The re-based elicitor holds the FormatSet; the
    baseline's comes from EA's own parser, which is what EA runs."""
    from pyegeria.view._output_format_models import FormatSet
    if draft and draft.get("format_set"):
        try:
            return FormatSet.model_validate(draft["format_set"]), "draft", None
        except Exception as exc:  # noqa: BLE001
            return None, "draft", f"{type(exc).__name__}: {exc}"
    if markdown:
        try:
            from advisor.report_spec_parser import parse_report_spec_markdown
            return parse_report_spec_markdown(markdown), "ea_parser", None
        except Exception as exc:  # noqa: BLE001
            return None, "ea_parser", f"{type(exc).__name__}: {exc}"
    return None, None, "no spec document"


def nonexistent_attributes(fs) -> Dict[str, Any]:
    """Column keys that are neither an attribute of the target type (pyegeria's catalog) nor a key
    pyegeria's formatter derives or its registered specs use. `checked` False when there's no catalog."""
    from pyegeria.authoring.validate import DERIVED_KEYS, _registry_columns_and_types, type_attributes
    target = fs.target_type
    attrs = type_attributes(target) if target else None
    if attrs is None:
        return {"checked": False, "why": f"no attribute catalog for target type {target!r}", "keys": []}
    used, _ = _registry_columns_and_types()
    known = attrs | DERIVED_KEYS | used
    bad, strict = [], []
    for fmt in fs.formats or []:
        for col in fmt.attributes or []:
            if col.key and col.key not in known:
                bad.append(col.key)
            # Strict: the type's own attributes (snake_case) or a derived key, nothing borrowed from other
            # specs. pyegeria's validate accepts any key some registered spec uses, which is looser.
            if col.key and col.key not in attrs | DERIVED_KEYS and _snake(col.key) not in attrs | DERIVED_KEYS:
                strict.append(col.key)
    return {"checked": True, "keys": sorted(set(bad)), "strict_keys": sorted(set(strict))}


def _snake(key: str) -> str:
    from pyegeria.authoring.validate import _snake as s
    return s(key)


def _offline_dre_parse(markdown: str) -> Dict[str, Any]:
    """What Dr.Egeria makes of EA's document with no client: the commands it recognises."""
    try:
        from pyegeria.authoring.serialize import parsed_commands
        cmds = parsed_commands(markdown)
        return {"ok": True, "commands": [c for c, _ in cmds]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"}


def _rows_summary(prev: Dict[str, Any]) -> Dict[str, Any]:
    res = prev.get("result") or {}
    kind = res.get("kind") if isinstance(res, dict) else type(res).__name__
    out = {"kind": kind, "shown": prev.get("shown"), "capped": prev.get("capped"), "notes": prev.get("notes")}
    if isinstance(res, dict) and kind == "table":
        cols = res.get("columns") or []
        out["columns"] = [c.get("label") or c.get("key") if isinstance(c, dict) else c for c in cols]
        out["sample"] = (res.get("rows") or [])[:5]
    elif isinstance(res, dict):
        out["content"] = str(res.get("content") or res.get("message") or "")[:500]
    return out


def check(question: Dict[str, Any], drove: Dict[str, Any], client: Any, run: Any) -> Dict[str, Any]:
    from pyegeria.authoring import SpecDraft, preview, validate
    draft = drove.get("draft") or {}
    result = drove["result"]
    doc_id = result.get("doc_id") or draft.get("doc_id")
    markdown = _doc(doc_id)
    out: Dict[str, Any] = {"doc_id": doc_id, "document": markdown}
    out["completed"] = drove["final_phase"] in ("refine", "answered") and bool(markdown)
    out["asked_clarifying"] = drove["final_phase"] == "clarify"
    if markdown:
        out["dre_offline_parse"] = _offline_dre_parse(markdown)
    fs, source, err = _format_set(draft, markdown)
    out["format_set_source"] = source
    out["format_set_error"] = err
    if fs is None:
        return out
    out["target_type"] = fs.target_type
    out["action_function"] = fs.action.function if fs.action else None
    out["columns"] = [{"name": c.name, "key": c.key} for f in fs.formats or [] for c in f.attributes or []]
    out["nonexistent_attributes"] = nonexistent_attributes(fs)
    label = (draft.get("title") or doc_id or question["id"]).replace(" ", "_")
    sd = SpecDraft(label=f"ea-rebase-{question['id']}-{label}"[:80], format_set=fs)
    t = time.time()
    v = validate(sd, client, run=run)
    out["pyegeria_validate"] = {**v.model_dump(), "s": round(time.time() - t, 2)}
    if run is not None and markdown:
        # Dr.Egeria validate of EA's OWN document (validate(sd) above validates pyegeria's serialization).
        try:
            from pyegeria.view.report_spec_publish import summarise
            env = summarise(run(markdown, "validate"))
        except Exception as exc:  # noqa: BLE001
            env = {"success": False, "execution_errors": [{"message": f"{type(exc).__name__}: {exc}"}]}
        out["dre_validate_ea_doc"] = env
    out["validates"] = bool(v.ok) and (out.get("dre_validate_ea_doc", {}).get("success", True) if run else True)
    if client is None:
        out["run"] = {"ran": False, "why": "offline"}
        return out
    answered = (result.get("answer") or {}) if isinstance(result, dict) else {}
    if answered:
        # The re-based elicitor already ran it as the user and rendered it in the reply.
        out["run"] = {"ran": True, "by": "elicitor", **_rows_summary(answered), "s": answered.get("s")}
        return out
    params = {"search_string": "*", "start_from": 0}
    if fs.action and fs.action.spec_params:
        params.update({k: v for k, v in fs.action.spec_params.items() if k != "page_size"})
    t = time.time()
    try:
        prev = preview(sd, client=client, max_rows=PREVIEW_ROWS, params=params)
        out["run"] = {"ran": True, "by": "harness", **_rows_summary(prev), "s": round(time.time() - t, 2)}
    except Exception as exc:  # noqa: BLE001
        out["run"] = {"ran": False, "error": f"{type(exc).__name__}: {' '.join(str(exc).split())[:500]}",
                      "s": round(time.time() - t, 2)}
    return out


# ── main ─────────────────────────────────────────────────────────────────────

def summary_md(mode: str, rows: List[Dict[str, Any]]) -> str:
    head = ("| id | set | done | turns | target | valid (pyegeria) | Dr.E (EA doc) | ran | rows | bad attrs | "
            "clarified | s |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [f"# EA re-base harness: {mode}\n", head]
    for r in rows:
        c = r.get("check") or {}
        run = c.get("run") or {}
        bad = c.get("nonexistent_attributes") or {}
        dre = c.get("dre_validate_ea_doc")
        lines.append("| {id} | {set} | {done} | {turns} | {tt} | {val} | {dre} | {ran} | {rows} | {bad} | {cl} | {s} |".format(
            id=r["id"], set=r["set"], done="y" if c.get("completed") else "n", turns=r.get("turns"),
            tt=c.get("target_type") or "-",
            val="-" if "pyegeria_validate" not in c else ("ok" if c["pyegeria_validate"]["ok"] else
                                                         f"{len(c['pyegeria_validate']['errors'])} err"),
            dre="-" if dre is None else ("ok" if dre.get("success") else "fail"),
            ran="-" if not run else ("y" if run.get("ran") and not run.get("error") else "n"),
            rows=run.get("shown") if run.get("ran") else "-",
            bad=("?" if bad and not bad.get("checked") else len(bad.get("keys") or [])) if bad else "-",
            cl="y" if c.get("asked_clarifying") else "n", s=r.get("total_s")))
    n = len(rows)
    def pct(k):
        return f"{sum(1 for r in rows if k(r))}/{n}"
    lines += ["", f"- completed: {pct(lambda r: (r.get('check') or {}).get('completed'))}",
              f"- validates with pyegeria (model + attributes + resolution{' + Dr.Egeria' if any((r.get('check') or {}).get('dre_validate_ea_doc') for r in rows) else ''}): "
              f"{pct(lambda r: (r.get('check') or {}).get('validates'))}",
              f"- validates and runs: {pct(lambda r: (r.get('check') or {}).get('validates') and ((r.get('check') or {}).get('run') or {}).get('ran') and not ((r.get('check') or {}).get('run') or {}).get('error'))}",
              f"- ambiguous asked to clarify: {sum(1 for r in rows if r['set']=='ambiguous' and (r.get('check') or {}).get('asked_clarifying'))}/"
              f"{sum(1 for r in rows if r['set']=='ambiguous')}",
              f"- nonexistent attributes reaching a run: {sum(len(((r.get('check') or {}).get('nonexistent_attributes') or {}).get('keys') or []) for r in rows if ((r.get('check') or {}).get('run') or {}).get('ran'))}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("baseline", "rebased", "rebased_fixes"), required=True,
                    help="baseline: EA as is; rebased: EA_SPEC_AUTHORING_V2 alone; rebased_fixes: plus "
                         "EA_SPEC_ELICITOR_FIXES")
    ap.add_argument("--questions", default=str(QUESTIONS), help="question set JSON (default: the 30)")
    ap.add_argument("--tag", default="", help="suffix for the result files (e.g. holdout)")
    ap.add_argument("--offline", action="store_true", help="no Egeria at all: LLM + offline checks")
    ap.add_argument("--user", default="erinoverview")
    ap.add_argument("--ids", help="comma-separated question ids (default: all 30)")
    ap.add_argument("--out", default=str(HERE / "results"))
    args = ap.parse_args()

    os.environ.pop("EA_SPEC_AUTHORING_V2", None)
    os.environ.pop("EA_SPEC_ELICITOR_FIXES", None)
    if args.mode in ("rebased", "rebased_fixes"):
        os.environ["EA_SPEC_AUTHORING_V2"] = "1"
    if args.mode == "rebased_fixes":
        os.environ["EA_SPEC_ELICITOR_FIXES"] = "1"
    sys.path.insert(0, str(PKG))
    storage = Path(tempfile.mkdtemp(prefix=f"ea-rebase-{args.mode}-"))
    _isolate_storage(storage)
    from advisor.request_context import set_current_user
    set_current_user(args.user)

    client = run = None
    ctx: Dict[str, Any] = {"start_kwargs": {}}
    if not args.offline:
        client = _signin(args.user)
        run = _readonly_runner(client)
        if args.mode != "baseline":
            ctx["start_kwargs"] = {"egeria_client": client}

    qs = json.loads(Path(args.questions).read_text())["questions"]
    if args.ids:
        want = set(args.ids.split(","))
        qs = [q for q in qs if q["id"] in want]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for q in qs:
        print(f"[{q['id']}] {q['question']}", flush=True)
        row: Dict[str, Any] = {"id": q["id"], "set": q["set"], "question": q["question"], "expect": q.get("expect"),
                               "mode": args.mode, "offline": args.offline}
        t0 = time.time()
        try:
            drove = drive(q, ctx)
            row.update({k: drove[k] for k in ("turns", "final_phase", "transcript", "elicit_s")})
            row["draft"] = drove.get("draft")
            row["check"] = check(q, drove, client, run)
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["traceback"] = traceback.format_exc()
        row["total_s"] = round(time.time() - t0, 2)
        rows.append(row)
        c = row.get("check") or {}
        print(f"    phase={row.get('final_phase')} turns={row.get('turns')} target={c.get('target_type')} "
              f"valid={(c.get('pyegeria_validate') or {}).get('ok')} run={(c.get('run') or {}).get('ran')} "
              f"rows={(c.get('run') or {}).get('shown')} {row['total_s']}s {row.get('error') or ''}", flush=True)
        suffix = ("offline" if args.offline else "live") + (f"_{args.tag}" if args.tag else "")
        (out_dir / f"results_{args.mode}_{suffix}.json").write_text(json.dumps(rows, indent=1, default=str))
    suffix = ("offline" if args.offline else "live") + (f"_{args.tag}" if args.tag else "")
    (out_dir / f"summary_{args.mode}_{suffix}.md").write_text(summary_md(f"{args.mode} ({suffix})", rows))
    print(summary_md(f"{args.mode} ({suffix})", rows))
    print(f"storage (temp, discardable): {storage}")


if __name__ == "__main__":
    main()
