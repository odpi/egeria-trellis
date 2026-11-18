"""The pyegeria contract test (EA Phase 1 Q3,
BRIEFS-EA-PHASE1-QUALITY-FLOOR-2026-11-10).

EA calls pyegeria by name in three ways, and each has drifted silently
before (DESIGN-EA-REVIEW-TO-RE-LEVEL-2026-11-10 §2):

(a) imports: ``from pyegeria... import X`` / ``import pyegeria.x.y`` must
    name a module and attribute that exist in the INSTALLED pyegeria.
    (``scripts/generate_report_specs.py`` still imports the pre-6.x paths
    ``pyegeria.base_report_formats`` and ``pyegeria._output_format_models``.)
(b) client attributes: ``client.<sub>``, ``client.<sub>.<method>`` and
    ``client.<method>``, where ``client`` is an ``EgeriaTech`` (or another
    pyegeria client class) built in the same function, must resolve the way
    EgeriaTech.__getattr__ resolves them at runtime: ``_subclient_map`` first,
    then any sub-client that has the method. (``perspective_manager`` calls
    ``client.actor_mgr``; the attribute is ``actor_manager``, so the live
    perspective load has always fallen back to the CSV.)
(c) MCP tool names: every literal tool name EA calls (``_call_tool("x")``,
    ``execute_tool("x")``, ``call_tool("x")``, values of ``*TOOL*`` dicts such as
    ``_LISTING_TOOL_BY_SPEC``) must be registered by pyegeria's own MCP server
    (``pyegeria/core/mcp_server.py``). A name only another configured MCP
    server could provide is reported separately as "needs another MCP server",
    not failed here. A tool EA means to get from another server is declared as
    a ``("server", "tool")`` pair in a ``*TOOL*`` dict; it passes only when that
    server is configured, enabled, and its description lists the tool, and it
    is then reported as "needs another MCP server". (``egeria_list_glossaries``
    / ``egeria_list_collections`` are dr-egeria's; ``report_pipeline`` now
    declares them so, and says so at run time when that server is absent.)

Deliberate dynamic access goes in ``pyegeria_contract_allow.json`` with a
reason per entry. Today's known breaks are in KNOWN_BREAKS below as STRICT
xfails naming the Phase 3 brief that fixes them: when the fix lands, the
xfail turns into an XPASS, which fails, so the entry is removed in the same
change. Policy: every pyegeria floor bump runs this test.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

EA_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("advisor", "scripts")
ALLOW_PATH = Path(__file__).resolve().parent / "pyegeria_contract_allow.json"
MCP_CONFIG = EA_ROOT / "advisor" / "configdata" / "mcp_servers.json"

# Finding ids are line-free so they survive unrelated edits:
#   import:<file>::<module>[:<name>]
#   attr:<file>::<function>::<chain>
#   mcp:<tool name>
KNOWN_BREAKS: dict = {
    # Emptied by Phase 3 A-P (2026-11-11): actor_mgr -> actor_manager (EA-P1); the dead
    # generate_report_specs.py imports went with the script (EA-P3); the two dr-egeria listing
    # tools are now declared as (server, tool) pairs and checked against that server (EA-P3).
}


# ── what the installed pyegeria offers ────────────────────────────────────────

@lru_cache(maxsize=None)
def _egeria_tech():
    from pyegeria import EgeriaTech
    # Constructing it makes no network call; the sub-client map is built here. Building a SUB-client does (its
    # constructor GETs /api/about), so nothing here builds one: sub-clients are checked by class, via
    # _subclient_class. The URL points nowhere on purpose, so an accidental connection fails rather than passing
    # only where an Egeria happens to be up (it did: CI failed on 9206e8aa, laz passed because 9443 was up).
    return EgeriaTech("contract-test-server", "https://127.0.0.1:9", "contract-test-user")


def _subclient_class(name: str):
    return _egeria_tech()._subclient_map[name]


def _egeria_tech_has(attr: str) -> bool:
    """EgeriaTech.__getattr__'s own resolution order: sub-client name, own
    attribute, then any sub-client class that provides it."""
    et = _egeria_tech()
    if attr in et._subclient_map or attr in dir(et):
        return True
    return any(hasattr(cls, attr) for cls in et._subclient_map.values())


def _pyegeria_client_classes() -> dict:
    """Class name -> class for every pyegeria client EA might construct."""
    import pyegeria
    from pyegeria import EgeriaTech
    out = {"EgeriaTech": EgeriaTech}
    for cls in set(_egeria_tech()._subclient_map.values()):
        out[cls.__name__] = cls
    for name in dir(pyegeria):
        obj = getattr(pyegeria, name, None)
        if isinstance(obj, type) and obj.__name__ in out:
            out[name] = obj
    return out


@lru_cache(maxsize=None)
def _pyegeria_mcp_tools() -> frozenset:
    """Tool names pyegeria's MCP server registers: ``@srv.tool(name="x")``,
    or the function name for a bare ``@srv.tool()``."""
    spec = importlib.util.find_spec("pyegeria.core.mcp_server")
    tree = ast.parse(Path(spec.origin).read_text())
    names = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in fn.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            target = call.func if call else dec
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                kw = {k.arg: k.value for k in (call.keywords if call else [])}
                if isinstance(kw.get("name"), ast.Constant):
                    names.add(kw["name"].value)
                else:
                    names.add(fn.name)
    return frozenset(names)


def _configured_server_offers(server: str, tool: str) -> bool:
    """The named server is configured, enabled, and its description lists ``tool``."""
    try:
        cfg = json.loads(MCP_CONFIG.read_text())
    except Exception:
        return False
    s = cfg.get("mcpServers", cfg).get(server)
    return (isinstance(s, dict) and bool(s.get("enabled"))
            and re.search(rf"\b{re.escape(tool)}\b", s.get("description", "")) is not None)


def _other_configured_servers_offering(tool: str) -> list[str]:
    """Enabled non-pyegeria MCP servers whose config says they provide
    ``tool``. Their code isn't in this repo (dr-egeria lives in
    egeria-workspaces-fs, at a host-specific path), so the config entry's
    own description is the declaration we can check."""
    try:
        cfg = json.loads(MCP_CONFIG.read_text())
    except Exception:
        return []
    servers = cfg.get("mcpServers", cfg)
    return sorted(
        name for name, s in servers.items()
        if name != "pyegeria" and isinstance(s, dict) and s.get("enabled")
        and re.search(rf"\b{re.escape(tool)}\b", s.get("description", ""))
    )


# ── scanning EA ───────────────────────────────────────────────────────────────

def _py_files():
    for d in SCAN_DIRS:
        yield from sorted((EA_ROOT / d).rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(EA_ROOT).as_posix()


def _module_exists(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _check_imports(tree: ast.AST, rel: str, out: dict) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == "pyegeria" and not _module_exists(a.name):
                    out[f"import:{rel}::{a.name}"] = f"{rel}:{node.lineno} no module {a.name}"
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                and node.module.split(".")[0] == "pyegeria":
            if not _module_exists(node.module):
                out[f"import:{rel}::{node.module}"] = f"{rel}:{node.lineno} no module {node.module}"
                continue
            mod = importlib.import_module(node.module)
            for a in node.names:
                if a.name == "*" or hasattr(mod, a.name) or _module_exists(f"{node.module}.{a.name}"):
                    continue
                out[f"import:{rel}::{node.module}:{a.name}"] = (
                    f"{rel}:{node.lineno} {node.module} has no {a.name}")


def _pyegeria_names_in(tree: ast.AST) -> dict:
    """Local name -> pyegeria client class, for names imported from pyegeria."""
    classes = _pyegeria_client_classes()
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "pyegeria":
            for a in node.names:
                if a.name in classes:
                    names[a.asname or a.name] = classes[a.name]
    return names


def _chain(node: ast.Attribute) -> tuple[str | None, list[str]]:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return (node.id if isinstance(node, ast.Name) else None), list(reversed(parts))


def _resolve_chain(cls, chain: list[str]) -> str | None:
    """None if the chain resolves; else the first part that doesn't."""
    from pyegeria import EgeriaTech
    first = chain[0]
    if cls is EgeriaTech:
        if not _egeria_tech_has(first):
            return first
        if first in _egeria_tech()._subclient_map and len(chain) > 1:
            if not hasattr(_subclient_class(first), chain[1]):
                return f"{first}.{chain[1]}"
        return None
    return None if hasattr(cls, first) else first


def _client_class_of(value: ast.AST, classes: dict, accessors: dict):
    """The pyegeria class an assigned expression yields, if recognisable:
    ``C(...)``, ``<x>._make_client(C)``, ``self.<accessor>()`` (a method that
    stores one of those on self), and ``<one of those> or False``."""
    if isinstance(value, ast.BoolOp) and value.values:
        return _client_class_of(value.values[0], classes, accessors)
    if not isinstance(value, ast.Call):
        return None
    fn = value.func
    if isinstance(fn, ast.Name) and fn.id in classes:
        return classes[fn.id]
    if isinstance(fn, ast.Attribute) and fn.attr == "_make_client" and value.args \
            and isinstance(value.args[0], ast.Name) and value.args[0].id in classes:
        return classes[value.args[0].id]
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "self" \
            and fn.attr in accessors:
        return accessors[fn.attr]
    return None


def _accessor_methods(tree: ast.AST, classes: dict) -> dict:
    """Method name -> pyegeria class, for methods that cache a client on self
    (``self._gov = self._make_client(GovernanceOfficer) or False``), the
    pattern advisor/egeria_context.py uses for every client."""
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) for t in node.targets):
                cls = _client_class_of(node.value, classes, {})
                if cls is not None:
                    out[fn.name] = cls
    return out


def _check_client_attrs(tree: ast.AST, rel: str, out: dict) -> None:
    classes = _pyegeria_names_in(tree)
    if not classes:
        return
    accessors = _accessor_methods(tree, classes)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        clients = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                cls = _client_class_of(node.value, classes, accessors)
                if cls is None:
                    continue
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        clients[t.id] = cls
        if not clients:
            continue
        seen = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Attribute):
                continue
            root, chain = _chain(node)
            if root not in clients or not chain:
                continue
            key = (root, tuple(chain[:2]))
            if key in seen:
                continue
            seen.add(key)
            bad = _resolve_chain(clients[root], chain[:2])
            if bad:
                out[f"attr:{rel}::{fn.name}::{bad}"] = (
                    f"{rel}:{node.lineno} {clients[root].__name__} has no {bad}")


_TOOL_CALLS = {"_call_tool", "execute_tool", "call_tool"}


def _tool_names_called(tree: ast.AST) -> dict:
    """Literal MCP tool name -> first line it is used at."""
    return {t: line for t, (line, _server) in _tool_uses(tree).items()}


def _tool_uses(tree: ast.AST) -> dict:
    """Literal MCP tool name -> (first line, declared server or None). A ``*TOOL*`` dict value may
    be a ``("server", "tool")`` pair, declaring which MCP server provides the tool."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name in _TOOL_CALLS and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                found.setdefault(node.args[0].value, (node.lineno, None))
        targets = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for t in targets:
            if isinstance(t, ast.Name) and "TOOL" in t.id.upper() and isinstance(value, ast.Dict):
                for v in value.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        found.setdefault(v.value, (v.lineno, None))
                    elif isinstance(v, ast.Tuple) and len(v.elts) == 2 and all(
                            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in v.elts):
                        found.setdefault(v.elts[1].value, (v.lineno, v.elts[0].value))
    return found


@lru_cache(maxsize=None)
def _scan() -> tuple[dict, dict]:
    """(findings, needs_other_server). findings: id -> human-readable site."""
    findings: dict = {}
    tools: dict = {}
    for path in _py_files():
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        rel = _rel(path)
        _check_imports(tree, rel, findings)
        _check_client_attrs(tree, rel, findings)
        for t, (line, server) in _tool_uses(tree).items():
            tools.setdefault(t, (f"{rel}:{line}", server))
    registered = _pyegeria_mcp_tools()
    other = {}
    for t, (site, server) in sorted(tools.items()):
        if t in registered:
            continue
        if server is not None:
            if _configured_server_offers(server, t):
                other[t] = [server]
            else:
                findings[f"mcp:{t}"] = (f"{site} tool {t!r} is declared as {server}'s, but {server} is not a "
                                        "configured, enabled MCP server whose description lists it")
            continue
        servers = _other_configured_servers_offering(t)
        findings[f"mcp:{t}"] = f"{site} tool {t!r} not registered by pyegeria's MCP server"
        if servers:
            other[t] = servers
    return findings, other


def _allow() -> dict:
    return json.loads(ALLOW_PATH.read_text())["allow"]


# ── tests ─────────────────────────────────────────────────────────────────────

def test_installed_pyegeria_is_inspectable():
    """The probes this test relies on still exist in the installed pyegeria."""
    assert len(_egeria_tech()._subclient_map) > 20
    assert {"list_reports", "run_report"} <= _pyegeria_mcp_tools()


def test_no_unexpected_contract_breaks():
    findings, _ = _scan()
    unexpected = {k: v for k, v in findings.items() if k not in KNOWN_BREAKS and k not in _allow()}
    assert not unexpected, (
        "EA names something the installed pyegeria does not have:\n"
        + "\n".join(f"  {k}\n      {v}" for k, v in sorted(unexpected.items()))
        + "\n\nFix the call, or, if the access is deliberately dynamic, add it to "
        "tests/unit/pyegeria_contract_allow.json with a reason."
    )


def test_needs_another_mcp_server_is_reported(capsys):
    """Names only another configured MCP server offers are listed, not failed
    (they are failures only via KNOWN_BREAKS, until Phase 3 decides)."""
    _, other = _scan()
    if other:
        with capsys.disabled():
            print("\nneeds another MCP server: "
                  + ", ".join(f"{t} ({', '.join(s)})" for t, s in sorted(other.items())))


def test_allow_list_entries_are_live_and_reasoned():
    """Every allow-list entry has a reason and still matches a finding, so
    the list can't quietly keep covering a site that was fixed or moved."""
    findings, _ = _scan()
    allow = _allow()
    assert all(isinstance(r, str) and r.strip() for r in allow.values())
    stale = sorted(set(allow) - set(findings))
    assert not stale, f"allow-list entries no longer found (remove them): {stale}"


@pytest.mark.parametrize(
    "finding_id",
    [pytest.param(k, marks=pytest.mark.xfail(strict=True, reason=v), id=k)
     for k, v in KNOWN_BREAKS.items()] or [pytest.param(None, id="none-known")],
)
def test_known_break_is_fixed(finding_id):
    """Each KNOWN_BREAKS entry fails today (xfail). When its Phase 3 fix
    lands it passes, and strict xfail turns that into a failure: remove the
    entry in the same change."""
    if finding_id is None:
        return  # no known breaks today
    findings, _ = _scan()
    assert finding_id not in findings, findings.get(finding_id)


class TestDetector:
    """The scanner on constructed sources, including the three real shapes."""

    def _findings(self, src: str, rel: str = "advisor/x.py") -> dict:
        out: dict = {}
        tree = ast.parse(src)
        _check_imports(tree, rel, out)
        _check_client_attrs(tree, rel, out)
        return out

    def test_flags_actor_mgr_shape(self):
        src = (
            "from pyegeria.egeria_tech_client import EgeriaTech\n"
            "def load():\n"
            "    client = EgeriaTech(**conn)\n"
            "    return client.actor_mgr.find_perspectives(search_string='*')\n"
        )
        assert set(self._findings(src)) == {"attr:advisor/x.py::load::actor_mgr"}

    def test_accepts_real_subclient_and_method(self):
        src = (
            "from pyegeria import EgeriaTech\n"
            "def load():\n"
            "    client = EgeriaTech('s', 'u', 'id')\n"
            "    client.actor_manager.find_perspectives('*')\n"
            "    return client.get_my_profile()\n"
        )
        assert self._findings(src) == {}

    def test_flags_missing_method_on_real_subclient(self):
        src = (
            "from pyegeria import EgeriaTech\n"
            "def load():\n"
            "    c = EgeriaTech('s', 'u', 'id')\n"
            "    return c.glossary_manager.no_such_method()\n"
        )
        assert set(self._findings(src)) == {"attr:advisor/x.py::load::glossary_manager.no_such_method"}

    def test_flags_dead_import_paths(self):
        src = (
            "try:\n"
            "    from pyegeria.base_report_formats import report_specs\n"
            "except ImportError:\n"
            "    pass\n"
            "from pyegeria.view.base_report_formats import no_such_name\n"
        )
        assert set(self._findings(src)) == {
            "import:advisor/x.py::pyegeria.base_report_formats",
            "import:advisor/x.py::pyegeria.view.base_report_formats:no_such_name",
        }

    def test_follows_make_client_accessors(self):
        """egeria_context's shape: an accessor caches _make_client(C) on self."""
        src = (
            "from pyegeria.omvs.governance_officer import GovernanceOfficer\n"
            "class Ctx:\n"
            "    def _governance_officer(self):\n"
            "        self._gov = self._make_client(GovernanceOfficer) or False\n"
            "        return self._gov or None\n"
            "    def zones(self):\n"
            "        gov = self._governance_officer()\n"
            "        gov.find_governance_definitions('*')\n"
            "        return gov.find_zonez('*')\n"
        )
        assert set(self._findings(src)) == {"attr:advisor/x.py::zones::find_zonez"}

    def test_tool_names_from_calls_and_tool_dicts(self):
        src = (
            "class P:\n"
            "    _LISTING_TOOL_BY_SPEC = {'glossaries': 'egeria_list_glossaries'}\n"
            "    def f(self):\n"
            "        return self._call_tool('list_reports', {})\n"
        )
        assert set(_tool_names_called(ast.parse(src))) == {"egeria_list_glossaries", "list_reports"}

    def test_declared_server_tool_pairs(self):
        src = (
            "class P:\n"
            "    _LISTING_TOOL_BY_SPEC = {'glossaries': ('dr-egeria', 'egeria_list_glossaries')}\n"
        )
        assert _tool_uses(ast.parse(src))["egeria_list_glossaries"][1] == "dr-egeria"

    def test_declared_pair_passes_only_for_a_server_that_offers_it(self):
        assert _configured_server_offers("dr-egeria", "egeria_list_glossaries")
        assert not _configured_server_offers("dr-egeria", "no_such_tool")
        assert not _configured_server_offers("no-such-server", "egeria_list_glossaries")
        assert not _configured_server_offers("_example_remote_sse", "egeria_list_glossaries")  # disabled
