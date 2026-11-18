"""
Dr. Egeria Action Agent.

Handles write/action queries by:
1. Finding the appropriate Dr. Egeria command template
2. Extracting parameters from the user request via LLM
3. Composing a complete Dr. Egeria markdown file
4. Executing it in-process via md_processing's v2 command dispatcher

All public methods are synchronous to match the RAGSystem dispatch pattern.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

from advisor import confirm_gate


# ---------------------------------------------------------------------------
# Dr.Egeria markdown execution — in-process, no MCP
# ---------------------------------------------------------------------------
#
# pyegeria's MCP server (pyegeria.core.mcp_server, >=6.0.16) was scoped down to
# report-only tools (list_reports/find_report_specs/describe_report/run_report/
# prompt) — the "dr_egeria_run_block" tool it used to expose no longer exists
# in any locally available pyegeria build. Dr.Egeria command execution now
# lives only as an in-process engine (md_processing.v2's UniversalExtractor +
# V2Dispatcher), with no MCP wrapper; the standalone `dr_egeria`/`dr_egeria_md`
# CLI (commands.cat.dr_egeria:process_markdown_file) is a thin, file-based,
# rich-console-output shim over the same engine. This calls the engine
# directly instead, building the same {success, output, validation_errors,
# execution_errors, commands_total, commands_succeeded, commands_failed,
# commands_detail} envelope governance_plan_agent._parse_dr_egeria_response()
# has always expected. See docs/PROJECT_SUMMARY.md Phase 25.
#
# Identity (BRIEFS-EA-PHASE0-SAFETY-2026-11-10 §S3): a Dr.Egeria run goes out
# as the signed-in user, on that user's own Egeria bearer token
# (advisor.auth.apply_token). It never builds the client with a password and
# never calls create_egeria_bearer_token() for a signed-in user: pyegeria fills
# a missing password from its configured one, so minting a token "for" the
# user would act as that user signed in with the shared password. The
# configured service account is used only when there are no user credentials
# AND the caller explicitly marks the run as a service operation. Everything
# else is refused with SIGN_IN_TO_RUN. A token Egeria refuses (401) yields
# SIGN_IN_AGAIN; there is no retry with a password.

SIGN_IN_TO_RUN = "Sign in to run Dr.Egeria commands."
SIGN_IN_AGAIN = (
    "Egeria refused your sign-in (it may have expired). "
    "Sign in again to run Dr.Egeria commands."
)

# What a PyegeriaUnauthorizedException looks like once md_processing has caught
# it and turned it into a per-command failure message.
_UNAUTHORIZED_MARKERS = ("AUTHORIZATION_ERROR_401", "PyegeriaUnauthorizedException")


class DrEgeriaAuthError(Exception):
    """A Dr.Egeria run that may not, or could not, authenticate.

    ``reason``: "no_credentials" (anonymous, not a service operation),
    "no_token" (a user id without that user's token) or "token_refused"
    (Egeria answered 401, or the token had already expired).
    """

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.message = message
        self.reason = reason


def _auth_envelope(err: DrEgeriaAuthError) -> Dict[str, Any]:
    """The usual result envelope, failed, carrying the sign-in sentence."""
    return {
        "success": False,
        "output": err.message,
        "validation_errors": [],
        "execution_errors": [{"step": 0, "command": "", "message": err.message}],
        "commands_total": 0,
        "commands_succeeded": 0,
        "commands_failed": 0,
        "commands_detail": [],
        "auth_required": True,
        "auth_reason": err.reason,
    }


def _build_client(conn: Dict[str, str]):
    """An EgeriaTech authenticated the way ``conn["auth"]`` says.

    "user": no password passed, the user's token applied with apply_token.
    pyegeria never re-mints a caller-supplied token on a 401 (it only re-mints
    tokens a client minted for itself), so a refused token raises
    PyegeriaUnauthorizedException rather than silently switching identity.
    "service": the configured service account mints its own token.
    """
    from pyegeria import EgeriaTech

    if conn.get("auth") == "user":
        token = conn.get("token")
        if not token:  # _get_egeria_conn never builds this; refuse, don't mint
            raise DrEgeriaAuthError(SIGN_IN_TO_RUN, "no_token")
        from advisor.auth import apply_token
        from pyegeria.core._no_password import NO_PASSWORD
        # NO_PASSWORD (TR-34 R1): without it pyegeria's constructor fills user_pwd from its settings
        client = EgeriaTech(conn["server_name"], conn["url"], conn["user_id"], user_pwd=NO_PASSWORD)
        apply_token(client, token)
        return client
    if conn.get("auth") == "service":
        if not (conn.get("user_id") and conn.get("user_pass")):
            # TR-34: no service account configured. Minting with an empty id or password would let pyegeria fill
            # them from its own settings; refuse, in words.
            from advisor.config import NO_SERVICE_ACCOUNT
            raise DrEgeriaAuthError(NO_SERVICE_ACCOUNT, "no_service_account")
        client = EgeriaTech(conn["server_name"], conn["url"], conn["user_id"], conn["user_pass"])
        client.create_egeria_bearer_token(conn["user_id"], conn["user_pass"])   # explicit, never pyegeria's fallback
        return client
    raise DrEgeriaAuthError(SIGN_IN_TO_RUN, "no_credentials")


async def _check_user_token(client: Any, token: str) -> None:
    """Raise DrEgeriaAuthError(SIGN_IN_AGAIN) if the user's token is expired or
    Egeria refuses it.

    md_processing turns every Egeria exception into a per-command failure, and
    its as-is lookups treat a failed read as "not found", so a validate on a
    refused token would otherwise report "would create" for every element. One
    cheap per-user read (get_my_profile, the probe trellis_auth uses for Portal
    tokens) up front tells the user to sign in again instead. Any other probe
    error (no profile, Egeria briefly down) is not an identity problem and is
    left to the run itself.
    """
    try:
        from datetime import datetime, timezone
        from trellis_auth.auth import egeria_token_expiry
        exp = egeria_token_expiry(token)
        if exp is not None and exp <= datetime.now(timezone.utc).timestamp():
            raise DrEgeriaAuthError(SIGN_IN_AGAIN, "token_refused")
    except DrEgeriaAuthError:
        raise
    except Exception:
        pass

    from pyegeria import PyegeriaUnauthorizedException
    try:
        await client._async_get_my_profile()
    except PyegeriaUnauthorizedException as exc:
        logger.info(f"Dr.Egeria: Egeria refused the signed-in user's token: {exc}")
        raise DrEgeriaAuthError(SIGN_IN_AGAIN, "token_refused") from exc
    except Exception as exc:
        logger.debug(f"Dr.Egeria: token probe inconclusive ({type(exc).__name__}); running anyway")


async def _execute_dr_egeria_markdown(
    markdown: str, directive: str, conn: Dict[str, str]
) -> Dict[str, Any]:
    import uuid

    from md_processing.dr_egeria import setup_dispatcher
    from md_processing.v2 import UniversalExtractor
    from pyegeria import PyegeriaUnauthorizedException

    try:
        client = _build_client(conn)
        if conn.get("auth") == "user":
            await _check_user_token(client, conn["token"])
    except DrEgeriaAuthError as err:
        return _auth_envelope(err)

    commands = UniversalExtractor(markdown).extract_commands()
    dispatcher = setup_dispatcher(client)
    context = {"directive": directive, "request_id": str(uuid.uuid4())}
    try:
        results = await dispatcher.dispatch_batch(commands, context)
    except PyegeriaUnauthorizedException:
        return _auth_envelope(DrEgeriaAuthError(SIGN_IN_AGAIN, "token_refused"))

    commands_detail: List[Dict[str, Any]] = []
    validation_errors: List[Dict[str, Any]] = []
    execution_errors: List[Dict[str, Any]] = []
    outputs: List[str] = []
    step = 0
    succeeded = 0
    failed = 0

    for res in results:
        if res.get("output"):
            outputs.append(str(res["output"]))
        if not res.get("is_command", True):
            continue
        step += 1
        status = res.get("status", "unknown")
        command_name = f"{res.get('verb', '')} {res.get('object_type', '')}".strip()
        message = res.get("message", "")
        commands_detail.append({
            "step": step,
            "command": command_name,
            "status": status,
            "guid": res.get("guid") or "",
            "qualified_name": res.get("qualified_name") or "",
            "display_name": res.get("display_name") or "",
            "message": message,
        })
        if status == "success":
            succeeded += 1
        elif status == "failure":
            failed += 1
            execution_errors.append({"step": step, "command": command_name, "message": message})
        for err in res.get("errors", []) or []:
            validation_errors.append({"step": step, "command": command_name, "message": err})

    # A token that expired part-way through shows up only as per-command
    # failure text; say "sign in again" rather than leave it in the detail.
    token_refused = any(
        any(m in (d.get("message") or "") for m in _UNAUTHORIZED_MARKERS)
        for d in commands_detail
    )
    if token_refused:
        execution_errors.insert(0, {"step": 0, "command": "", "message": SIGN_IN_AGAIN})

    envelope = {
        "success": failed == 0 and not token_refused,
        "output": "\n\n".join(outputs),
        "validation_errors": validation_errors,
        "execution_errors": execution_errors,
        "commands_total": step,
        "commands_succeeded": succeeded,
        "commands_failed": failed,
        "commands_detail": commands_detail,
    }
    if token_refused:
        envelope["auth_required"] = True
        envelope["auth_reason"] = "token_refused"
    return envelope


# ---------------------------------------------------------------------------
# Template index — built from file system on first use
# ---------------------------------------------------------------------------

class TemplateIndex:
    """
    Lightweight in-memory index of Dr. Egeria command templates.

    Indexed from the installed pyegeria's packaged templates
    (``md_processing.templates``, BRIEF EA-CATCHUP-AND-DASHBOARDS-2026-11-10
    B2), so they match the dispatcher that runs the commands. Only when that
    pyegeria predates the accessor does it fall back, with a logged note, to
    the old egeria-python clone:
      data/repos/egeria-python/sample-data/templates/basic/
      data/repos/egeria-python/sample-data/templates/advanced/

    Each entry maps a normalised command name → (file_path, tier, family, command_name).
    """

    def __init__(self, base_paths: Optional[List[str]] = None):
        self._entries: List[Dict[str, str]] = []  # {name, family, tier, file_path}
        self._loaded = False
        self._base_paths = base_paths

    def _default_base_paths(self) -> List[Path]:
        """The installed pyegeria's template root; else the legacy paths."""
        from advisor.dre_templates import installed_templates_root, note_fallback

        root = installed_templates_root()
        if root is not None:
            return [root]
        legacy = self._legacy_base_paths()
        note_fallback("TemplateIndex", legacy[0] if legacy else None)
        return legacy

    def _legacy_base_paths(self) -> List[Path]:
        """Pre-B2: the egeria-python clone. Remove once trellis' pyegeria
        floor carries packaged templates."""
        candidates = []
        # Repo path from config (primary)
        try:
            from advisor.config import get_full_config
            cfg = get_full_config()
            ds = cfg.get("data_sources")
            if ds and hasattr(ds, "egeria_python_path"):
                p = Path(str(ds.egeria_python_path)) / "sample-data" / "templates"
                candidates.append(p)
        except Exception:
            pass

        # Fallback: the cloned egeria-python checkout under Advisor's writable
        # data root (resolve_advisor_data_root/ADVISOR_DATA_PATH) -- same
        # location clone_repos.py/admin.py resolve, not a path relative to
        # this file. See admin.py's _DATA_DIR for why that distinction matters.
        try:
            from advisor.config import resolve_advisor_data_root
            repo_path = resolve_advisor_data_root() / "repos" / "egeria-python" / "sample-data" / "templates"
            candidates.append(repo_path)
        except Exception:
            pass

        # Deduplicate by resolved path; use first existing match only
        seen: set = set()
        result = []
        for p in candidates:
            if not p.exists():
                continue
            resolved = str(p.resolve())
            if resolved not in seen:
                seen.add(resolved)
                result.append(p)
                break  # one source is enough to avoid duplicates
        return result

    def load(self) -> None:
        if self._loaded:
            return

        if self._base_paths:
            bases = [Path(p) for p in self._base_paths if Path(p).exists()]
        else:
            bases = self._default_base_paths()

        if not bases:
            logger.warning("No template base paths found — DrEgeriaActionAgent template index empty")
            self._loaded = True
            return

        for base in bases:
            for tier_dir in base.iterdir():
                if not tier_dir.is_dir():
                    continue
                tier = tier_dir.name.lower()  # "basic" or "advanced"
                for family_dir in tier_dir.iterdir():
                    if not family_dir.is_dir():
                        continue
                    family = family_dir.name
                    for md_file in family_dir.glob("*.md"):
                        command_name = _file_to_command_name(md_file.name)
                        self._entries.append({
                            "command_name": command_name,
                            "family": family,
                            "tier": tier,
                            "file_path": str(md_file),
                        })

        logger.info(f"TemplateIndex loaded {len(self._entries)} templates from {[str(b) for b in bases]}")
        self._loaded = True

    def find(self, query: str, prefer_basic: bool = True) -> List[Dict[str, str]]:
        """
        Find templates matching query keywords.

        Returns a list of matching entries sorted by relevance (required fields first,
        basic tier preferred by default).
        """
        self.load()
        if not self._entries:
            return []

        query_lower = query.lower()
        # Keep action verbs (create, add, link, etc.) since they appear in template names.
        # Only strip generic filler words.
        stop = {"a", "an", "the", "some", "my", "our", "for", "in", "to", "of",
                "with", "called", "named", "new", "please", "can", "you", "i",
                "would", "like", "want", "need", "help", "me", "us"}
        keywords = [
            w.strip("?.,!") for w in query_lower.split()
            if w.strip("?.,!") not in stop and len(w.strip("?.,!")) > 2
        ]

        # Use first 3 keywords for command-name matching (action + entity only).
        # Only match against template command names, not families, to avoid noise
        # from context words in the user's parameter values.
        intent_keywords = keywords[:3]

        scored = []
        for entry in self._entries:
            name_lower = entry["command_name"].lower()
            score = 0
            for kw in intent_keywords:
                if kw in name_lower:
                    idx = name_lower.find(kw)
                    position_bonus = 2 if idx < 15 else 1
                    score += position_bonus
            # Coverage bonus: fraction of significant template name words found in the query
            name_words = [w for w in name_lower.split() if len(w) > 3]
            coverage = sum(1 for w in name_words if w in query_lower) / max(len(name_words), 1)
            score += coverage * 0.5
            if score > 0.1:
                tier_bonus = 1 if (prefer_basic and entry["tier"] == "basic") else 0
                scored.append((score + tier_bonus, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored]

    def get_by_name(self, command_name: str, tier: str = "basic") -> Optional[Dict[str, str]]:
        """Get a template entry by exact command name (case-insensitive)."""
        self.load()
        name_lower = command_name.lower()
        tier_lower = tier.lower()
        # Exact match, tier preferred
        for entry in self._entries:
            if entry["command_name"].lower() == name_lower and entry["tier"] == tier_lower:
                return entry
        # Any tier
        for entry in self._entries:
            if entry["command_name"].lower() == name_lower:
                return entry
        return None


def _file_to_command_name(filename: str) -> str:
    """Convert 'Create_Glossary_Term.md' → 'Create Glossary Term'."""
    return filename.replace(".md", "").replace("_", " ")


# ---------------------------------------------------------------------------
# Template parsing
# ---------------------------------------------------------------------------

def parse_template(file_path: str) -> Dict[str, Any]:
    """
    Parse a Dr. Egeria template file into a structured dict.

    Returns:
      {
        "command_name": "Create Glossary",
        "description": "...",
        "attributes": [
          {"name": "Display Name", "required": True, "type": "Simple",
           "description": "...", "valid_values": [...], "default_value": "..."},
          ...
        ]
      }
    """
    text = Path(file_path).read_text(encoding="utf-8")
    lines = text.splitlines()

    result: Dict[str, Any] = {"command_name": "", "description": "", "attributes": []}
    current_attr: Optional[Dict[str, Any]] = None

    for line in lines:
        stripped = line.strip()

        # Command header: ## Create Glossary
        if stripped.startswith("## ") and not result["command_name"]:
            result["command_name"] = stripped[3:].strip()
            continue

        # Description line after command (before any attribute is started)
        if stripped.startswith(">") and result["command_name"] and current_attr is None:
            desc_text = stripped.lstrip("> ").strip()
            if not desc_text.startswith("**"):
                if result["description"]:
                    result["description"] += " " + desc_text
                else:
                    result["description"] = desc_text
            continue

        # Attribute header: ### Display Name
        if stripped.startswith("### "):
            if current_attr:
                result["attributes"].append(current_attr)
            current_attr = {
                "name": stripped[4:].strip(),
                "required": False,
                "type": "Simple",
                "description": "",
                "valid_values": [],
                "default_value": None,
                "alternative_labels": [],
            }
            continue

        if current_attr is None:
            continue

        # Attribute metadata lines: > **Input Required**: True
        m = re.match(r"^>\s*\*\*Input Required\*\*:\s*(.+)", stripped)
        if m:
            current_attr["required"] = m.group(1).strip().lower() == "true"
            continue

        m = re.match(r"^>\s*\*\*Attribute Type\*\*:\s*(.+)", stripped)
        if m:
            current_attr["type"] = m.group(1).strip()
            continue

        m = re.match(r"^>\s*\*\*Description\*\*:\s*(.+)", stripped)
        if m:
            current_attr["description"] = m.group(1).strip()
            continue

        m = re.match(r"^>\s*\*\*Valid Values\*\*:\s*(.+)", stripped)
        if m:
            current_attr["valid_values"] = [v.strip() for v in m.group(1).split(",")]
            continue

        m = re.match(r"^>\s*\*\*Default Value\*\*:\s*(.+)", stripped)
        if m:
            current_attr["default_value"] = m.group(1).strip()
            continue

        m = re.match(r"^>\s*\*\*Alternative Labels\*\*:\s*(.+)", stripped)
        if m:
            current_attr["alternative_labels"] = [
                v.strip().strip('"') for v in m.group(1).split(";")
            ]
            continue

    if current_attr:
        result["attributes"].append(current_attr)

    return result


# ---------------------------------------------------------------------------
# Command composition
# ---------------------------------------------------------------------------

def compose_command(template: Dict[str, Any], params: Dict[str, Any]) -> str:
    """
    Compose a Dr. Egeria markdown command block from template + extracted params.

    Only includes attributes that have a value in params.
    Returns a complete file with ___ delimiters.
    """
    lines = ["___", "", f"## {template['command_name']}", ""]
    for attr in template["attributes"]:
        attr_name = attr["name"]
        value = params.get(attr_name) or params.get(attr_name.lower())
        # Also check aliases
        if value is None:
            for alias in attr.get("alternative_labels", []):
                if alias and alias.lower() in {k.lower() for k in params}:
                    for k, v in params.items():
                        if k.lower() == alias.lower():
                            value = v
                            break
                    if value is not None:
                        break
        if value is not None:
            lines.append(f"### {attr_name}")
            if isinstance(value, list):
                for v in value:
                    lines.append(f"> {v}")
            else:
                lines.append(f"> {value}")
            lines.append("")
    lines += ["___", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------

# ── The chat-action confirm gate (design note rev 4 §1e item 1, 2026-11-12) ──────────────────────
#
# A chat message never writes. handle() composes the Dr.Egeria script from the request, runs it with
# the "validate" directive only, shows the script and the validate result, and registers a PENDING
# action: the exact script, its SHA-256 digest, and who asked (their credential identity). The only
# way that script is processed is confirm(pending_id, digest): the same sign-in, within the TTL, with
# the digest of the script that was shown. A changed script is refused in words; a second confirm of
# the same pending action answers with the first result and writes nothing again.

#: How long a shown script can be confirmed (seconds).
CONFIRM_TTL_S = 15 * 60

CONFIRM_UNKNOWN = ("That Dr.Egeria script has expired or is unknown, so nothing was processed. "
                   "Ask again to compose and validate it afresh.")
CONFIRM_OTHER_SIGN_IN = ("That Dr.Egeria script was shown to another sign-in, so nothing was processed. "
                         "Ask again to compose and validate it as you.")
CONFIRM_CHANGED = ("The Dr.Egeria script you confirmed is not the one that was shown (its digest differs), "
                   "so nothing was processed. Ask again to see the current script before confirming.")


def script_digest(markdown: str) -> str:
    """The SHA-256 of the exact script shown to the user, as confirmed."""
    return confirm_gate.text_digest(markdown)


#: The chat-action pending actions (advisor.confirm_gate: show, confirm + digest, once).
_GATE = confirm_gate.ConfirmGate(CONFIRM_TTL_S)
_pending = _GATE.pending      # the live dict, for tests


def _reset_pending_for_tests() -> None:
    _GATE.clear()


def _envelope_summary(env: Dict[str, Any], directive: str) -> str:
    """The validate (or process) result in words: counts, then every error said."""
    total = env.get("commands_total", 0)
    ok = env.get("commands_succeeded", 0)
    bad = env.get("commands_failed", 0)
    verb = "checked" if directive == "validate" else "processed"
    lines = [f"**{directive.capitalize()}:** {total} command(s) {verb}: {ok} succeeded, {bad} failed."]
    for e in list(env.get("validation_errors") or []) + list(env.get("execution_errors") or []):
        if isinstance(e, dict):
            lines.append(f"- {e.get('command') or 'command'}: {e.get('message') or e.get('error') or e}")
        else:
            lines.append(f"- {e}")
    if total == 0 and not env.get("success"):
        lines.append(f"- {env.get('output') or 'Dr.Egeria returned no result.'}")
    return "\n".join(lines)


def _validated_cleanly(env: Dict[str, Any]) -> bool:
    return bool(env.get("success")) and not env.get("commands_failed") and not env.get("validation_errors") \
        and not env.get("execution_errors") and bool(env.get("commands_total"))


class DrEgeriaActionAgent:
    """
    Agent for handling Dr. Egeria write/action requests.

    Usage:
        agent = DrEgeriaActionAgent()
        result = agent.handle("Create a glossary called Business Terms for our data stewards")
    """

    def __init__(self, config_path: str = str(Path(__file__).parent.parent / "configdata" / "mcp_servers.json")):
        self._config_path = config_path
        self._template_index = TemplateIndex()
        self._egeria_conn: Optional[Dict[str, str]] = None  # lazy

    def _get_egeria_conn(
        self,
        egeria_credentials: Optional[Dict[str, str]] = None,
        service_operation: bool = False,
    ) -> Dict[str, str]:
        """Extract Egeria connection params for Dr.Egeria command execution.

        url/server_name are static — same Egeria instance for everyone — resolved
        via advisor.mcp_config.get_pyegeria_platform_config() (env vars first,
        then config/mcp_servers.json) and cached on self. The identity is
        resolved FRESH on every call from the caller's own credentials (see
        advisor.auth.resolve_egeria_credentials) and is NEVER cached on self:
        this class is a process-wide singleton (get_dr_egeria_agent()) shared
        across every user's requests, so caching credentials here would leak the
        first caller's identity into every subsequent user's actions for the
        life of the process.

        Returns ``auth`` "user" with the user's ``token`` (and no password) for
        a signed-in caller, or ``auth`` "service" with the configured service
        account only when there are no user credentials and
        ``service_operation`` is True. Raises DrEgeriaAuthError(SIGN_IN_TO_RUN)
        otherwise, including for a user id that comes without its token.
        """
        if self._egeria_conn is None:
            from advisor.mcp_config import get_pyegeria_platform_config
            conn = get_pyegeria_platform_config(self._config_path)
            self._egeria_conn = {
                "url": conn["platform_url"] or "https://localhost:9443",
                "server_name": conn["view_server"] or "qs-view-server",
            }
        from advisor.auth import resolve_egeria_credentials
        if egeria_credentials and egeria_credentials.get("user_id"):
            creds = resolve_egeria_credentials(egeria_credentials)
            if not creds.get("token"):
                raise DrEgeriaAuthError(SIGN_IN_TO_RUN, "no_token")
            return {**self._egeria_conn, "auth": "user",
                    "user_id": creds["user_id"], "token": creds["token"]}
        if not service_operation:
            raise DrEgeriaAuthError(SIGN_IN_TO_RUN, "no_credentials")
        creds = resolve_egeria_credentials(None)
        return {**self._egeria_conn, "auth": "service",
                "user_id": creds["user_id"], "user_pass": creds["password"]}

    def find_template(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Find and parse the best matching template for this query.

        Returns the parsed template dict, or None if nothing suitable found.
        """
        matches = self._template_index.find(query, prefer_basic=True)
        if not matches:
            return None
        best = matches[0]
        try:
            template = parse_template(best["file_path"])
            # where it came from: the chat policy refuses by family (advisor.chat_command_policy)
            template.setdefault("family", best.get("family"))
            template.setdefault("tier", best.get("tier"))
            return template
        except Exception as e:
            logger.warning(f"Failed to parse template {best['file_path']}: {e}")
            return None

    @staticmethod
    def _normalize_key(k: str) -> str:
        return k.lower().replace("_", " ").replace("-", " ").strip()

    def _normalize_params(self, raw: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
        """
        Re-key LLM output so keys match template attribute names exactly.

        The LLM may return 'display_name' or 'Display name' when the attribute
        is called 'Display Name'. Normalise by comparing lowercase/stripped forms.
        """
        all_attrs = template["attributes"]
        # Build map: normalised-key → canonical attribute name
        norm_to_canon: Dict[str, str] = {}
        for a in all_attrs:
            norm_to_canon[self._normalize_key(a["name"])] = a["name"]
            for alias in a.get("alternative_labels", []):
                if alias:
                    norm_to_canon[self._normalize_key(alias)] = a["name"]

        result: Dict[str, Any] = {}
        for k, v in raw.items():
            canon = norm_to_canon.get(self._normalize_key(k))
            result[canon if canon else k] = v
        return result

    def _regex_pre_extract(self, query: str, template: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fast regex extraction for common patterns before the LLM call.

        Handles: called/named "X" or called/named X (unquoted word run).
        Maps extracted value to the first required attribute with type Simple.
        """
        params: Dict[str, Any] = {}

        # Find quoted or unquoted value after "called" / "named"
        m = re.search(r'\b(?:called|named)\s+"([^"]+)"', query, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(?:called|named)\s+'([^']+)'", query, re.IGNORECASE)
        if not m:
            # Unquoted: take words up to end-of-string or a sentence boundary
            m = re.search(r'\b(?:called|named)\s+([A-Za-z0-9][\w\s\-]*?)(?:\s*[.,;]|\s+(?:for|to|with|in|as|and|the)\b|$)', query, re.IGNORECASE)

        if m:
            value = m.group(1).strip().strip('"\'')
            # Target the first required Simple attribute
            for a in template["attributes"]:
                if a["required"] and a["type"] in ("Simple", "simple", ""):
                    params[a["name"]] = value
                    logger.debug(f"Regex pre-extracted {a['name']!r} = {value!r}")
                    break

        return params

    def extract_params(self, query: str, template: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use LLM to extract parameter values from the user query given the template schema.

        Returns a dict of attribute_name → value (only attributes the user specified).
        """
        from advisor.llm_client import get_ollama_client
        llm = get_ollama_client()

        # Regex pre-extraction for common patterns (reliable, fast)
        pre = self._regex_pre_extract(query, template)

        required_attrs = [a for a in template["attributes"] if a["required"]]
        optional_attrs = [a for a in template["attributes"] if not a["required"]]

        def fmt_attr(a: Dict) -> str:
            s = f"- {a['name']}"
            aliases = [lb for lb in a.get("alternative_labels", []) if lb]
            if aliases:
                s += f" (also: {', '.join(aliases)})"
            s += f" (type: {a['type']}"
            if a["valid_values"]:
                s += f", valid: {','.join(a['valid_values'])}"
            if a["default_value"]:
                s += f", default: {a['default_value']}"
            s += ")"
            if a.get("description"):
                s += f": {a['description']}"
            return s

        required_list = "\n".join(fmt_attr(a) for a in required_attrs) or "(none)"
        optional_list = "\n".join(fmt_attr(a) for a in optional_attrs[:10]) or "(none)"

        # Inject pre-extracted values as hints so the LLM doesn't contradict them
        pre_hint = ""
        if pre:
            pre_hint = f"\nAlready extracted: {json.dumps(pre)}\n"

        prompt = f"""You are extracting parameters for a Dr. Egeria command from a user request.

Command: {template['command_name']}
Description: {template['description']}

Required attributes:
{required_list}

Optional attributes (provide only if clearly specified by user):
{optional_list}
{pre_hint}
User request: "{query}"

Extract ALL parameter values the user specified. Use the exact attribute names shown above as JSON keys. Return ONLY a JSON object. Do not invent values. For required attributes already extracted above, include them in the output.

JSON:"""

        try:
            response = llm.generate(prompt=prompt, temperature=0.1, max_tokens=512)
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                llm_params = self._normalize_params(json.loads(json_match.group()), template)
                # Merge: pre-extracted values win for any key they set
                merged = {**llm_params, **pre}
                return merged
        except Exception as e:
            logger.warning(f"Parameter extraction failed: {e}")

        # Fallback: regex only
        return pre

    def execute(
        self,
        markdown: str,
        directive: str = "process",
        dry_run: bool = False,
        egeria_credentials: Optional[Dict[str, str]] = None,
        service_operation: bool = False,
    ) -> str:
        """
        Execute a composed Dr.Egeria markdown file in-process via md_processing's
        v2 command dispatcher (no MCP round-trip — see _execute_dr_egeria_markdown).

        Args:
            markdown: Complete Dr.Egeria markdown command file
            directive: "display" | "validate" | "process"
            dry_run: If True, return the markdown without executing
            egeria_credentials: the signed-in caller's {user_id, password, token}
                (advisor.auth.get_egeria_credentials); the run goes out on
                ``token``. None → refused with SIGN_IN_TO_RUN, unless
                ``service_operation``.
            service_operation: True only for a deliberate service-account run
                with no user behind it; ignored when user credentials are given.

        Returns:
            JSON-encoded envelope string ({success, output, validation_errors,
            execution_errors, commands_total, commands_succeeded, commands_failed,
            commands_detail}) — the same shape governance_plan_agent's
            _parse_dr_egeria_response() has always expected. A refused run is
            the same envelope with success False, the sign-in sentence as
            ``output``, and ``auth_required`` True.
        """
        if dry_run:
            return markdown

        try:
            conn = self._get_egeria_conn(egeria_credentials, service_operation=service_operation)
        except DrEgeriaAuthError as err:
            return json.dumps(_auth_envelope(err))

        from advisor.report_pipeline import _run_async
        envelope = _run_async(_execute_dr_egeria_markdown(markdown, directive, conn))
        return json.dumps(envelope)

    def handle(
        self,
        query: str,
        directive: str = "validate",
        dry_run: bool = False,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Chat action pipeline: find template → extract params → compose → VALIDATE → show.

        A chat message never writes. The composed script is run with "validate" (or "display") only,
        shown with that result, and registered as a pending action; confirm() with the shown script's
        digest is the only way it is processed (the confirm gate, above).

        Args:
            query: User's natural language action request
            directive: "validate" (default) | "display". "process" is refused: use confirm().
            dry_run: Return composed markdown without executing
            egeria_credentials: the signed-in caller's {user_id, password, token};
                without them (and outside dry_run) the run is refused with
                SIGN_IN_TO_RUN — handle() is never a service operation.

        Returns:
            Response dict compatible with RAGSystem result format.
        """
        if directive not in ("validate", "display"):
            raise ValueError("DrEgeriaActionAgent.handle() never processes: a chat action is validated and "
                             "shown, and processed only by confirm(pending_id, digest).")

        # Step 1: Find template
        template = self.find_template(query)
        if template is None:
            return _no_template_found(query)

        command_name = template["command_name"]
        logger.info(f"Selected template: {command_name}")

        # Step 1b: some commands are never composed from chat (type system, raw instances), whatever
        # the confirm gate would be answered: refused in words before anything is extracted or composed.
        from advisor.chat_command_policy import chat_refusal
        refusal = chat_refusal(command_name, template.get("family"))
        if refusal:
            logger.info(f"Chat action refused by policy: {command_name!r} (family {template.get('family')!r})")
            out = _no_template_found(query)
            out.update(response=refusal, command_name=command_name, family=template.get("family"),
                       chat_refused=True, next_context=None)
            return out

        # Step 2: Extract parameters
        params = self.extract_params(query, template)
        logger.info(f"Extracted params: {list(params.keys())}")

        # Step 3: Check required params are present (normalised key comparison)
        norm_params = {self._normalize_key(k): v for k, v in params.items()}
        missing = [
            a["name"] for a in template["attributes"]
            if a["required"] and not norm_params.get(self._normalize_key(a["name"]))
        ]
        if missing:
            return {
                "query": query,
                "response": (
                    f"To **{command_name}**, I need the following required information:\n\n"
                    + "\n".join(f"- **{m}**" for m in missing)
                    + "\n\nPlease provide these details and try again."
                ),
                "query_type": "command",
                "command_name": command_name,
                "missing_params": missing,
                "sources": [],
                "num_sources": 0,
                "retrieval_time": 0.0,
                "generation_time": 0.0,
                "avg_relevance_score": 0.0,
                "context_length": 0,
            }

        # Step 4: Compose markdown
        markdown = compose_command(template, params)
        base = {
            "query": query,
            "query_type": "command",
            "command_name": command_name,
            "params_extracted": params,
            "markdown": markdown,
            "dry_run": dry_run,
            "auth_required": False,
            "sources": [f"Dr.Egeria template: {command_name}"],
            "num_sources": 1,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": len(markdown),
        }
        if dry_run:
            return {**base, "response": f"**Composed Dr. Egeria command** (not executed):\n\n```markdown\n{markdown}\n```"}

        # Step 5: validate (never process), as the caller
        try:
            conn = self._get_egeria_conn(egeria_credentials)
        except DrEgeriaAuthError as err:
            return {**base, "response": err.message, "auth_required": True}
        try:
            env = json.loads(self.execute(markdown, directive=directive, egeria_credentials=egeria_credentials))
        except Exception as e:  # noqa: BLE001 -- said, and nothing is offered for confirm
            logger.error(f"DrEgeria {directive} failed: {e}")
            env = {"success": False, "output": f"{directive.capitalize()} failed: {e}", "commands_total": 0}
        if env.get("auth_required"):
            return {**base, "response": SIGN_IN_AGAIN if env.get("auth_reason") == "token_refused" else SIGN_IN_TO_RUN,
                    "auth_required": True}

        shown = (f"**{command_name}**, composed from your request. **Nothing has been written.**\n\n"
                 f"```markdown\n{markdown}\n```\n\n{_envelope_summary(env, directive)}")
        if directive != "validate" or not _validated_cleanly(env):
            return {**base, "response": shown + "\n\nNot offered to run: fix the request and ask again.",
                    "validation": env, "confirm_required": False, "next_context": None}
        pa = _GATE.register(confirm_gate.caller_identity(conn), markdown,
                            {"command_name": command_name, "markdown": markdown})
        return {
            **base,
            "response": (shown + f"\n\nReply **yes** to process this script in Egeria as **{conn['user_id']}**, "
                         f"or **no** to cancel. (Script digest `{pa.digest[:12]}`; good for "
                         f"{CONFIRM_TTL_S // 60} minutes.)"),
            "validation": env,
            "confirm_required": True,
            "pending_id": pa.pending_id,
            "script_digest": pa.digest,
            "next_context": {"task": "dr_egeria_confirm", "pending_id": pa.pending_id, "digest": pa.digest},
        }

    def confirm(
        self,
        pending_id: str,
        digest: str,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Process a shown script, once: the same sign-in, within CONFIRM_TTL_S, with the shown script's digest.

        {"state": "processed" | "already_processed" | "refused" | "failed", "response": words, ...}"""
        out: Dict[str, Any] = {"query_type": "command", "next_context": None, "sources": [], "num_sources": 0,
                               "retrieval_time": 0.0, "generation_time": 0.0, "avg_relevance_score": 0.0,
                               "context_length": 0, "auth_required": False}
        try:
            conn = self._get_egeria_conn(egeria_credentials)
        except DrEgeriaAuthError as err:
            # not consumed: signing in again and confirming still works within the TTL
            return {**out, "state": "refused", "response": err.message, "auth_required": True,
                    "next_context": {"task": "dr_egeria_confirm", "pending_id": pending_id, "digest": digest}}
        pa, why = _GATE.check(pending_id, confirm_gate.caller_identity(conn), digest)
        if pa is None:
            return {**out, "state": "refused", "response": {confirm_gate.UNKNOWN: CONFIRM_UNKNOWN,
                                                            confirm_gate.OTHER_SIGN_IN: CONFIRM_OTHER_SIGN_IN,
                                                            confirm_gate.CHANGED: CONFIRM_CHANGED}[why]}
        command_name, markdown = pa.payload["command_name"], pa.payload["markdown"]
        out.update(command_name=command_name, markdown=markdown, script_digest=pa.digest)
        with pa.lock:   # one process per pending action; a retry gets the first answer
            if pa.result is not None:
                return {**pa.result, "state": "already_processed",
                        "response": "This script was already processed; nothing was written again.\n\n"
                                    + pa.result["response"]}
            try:
                env = json.loads(self.execute(markdown, directive="process", egeria_credentials=egeria_credentials))
            except Exception as e:  # noqa: BLE001 -- it may have written part of the script: still one-shot
                logger.error(f"DrEgeria process failed after confirm: {e}")
                env = {"success": False, "output": f"Processing failed: {e}", "commands_total": 0}
            if env.get("auth_required") and not env.get("commands_total"):
                # refused before anything ran: not consumed
                return {**out, "state": "refused", "auth_required": True,
                        "response": SIGN_IN_AGAIN if env.get("auth_reason") == "token_refused" else SIGN_IN_TO_RUN,
                        "next_context": {"task": "dr_egeria_confirm", "pending_id": pending_id, "digest": digest}}
            pa.result = {**out, "state": "processed" if env.get("success") else "failed", "envelope": env,
                         "response": f"**{command_name}** processed as **{conn['user_id']}**.\n\n"
                                     + _envelope_summary(env, "process")}
            return pa.result

    def cancel(self, pending_id: str) -> Dict[str, Any]:
        """Drop a shown script; nothing is processed."""
        _GATE.discard(pending_id)
        return {"query_type": "general", "response": "Cancelled: nothing was written.", "next_context": None,
                "sources": [], "num_sources": 0, "retrieval_time": 0.0, "generation_time": 0.0,
                "avg_relevance_score": 0.0, "context_length": 0}


def _no_template_found(query: str) -> Dict[str, Any]:
    return {
        "query": query,
        "response": (
            "I couldn't find a matching Dr. Egeria command template for that request. "
            "Try phrasing your request as an action, for example: "
            "*'Create a glossary called Business Terms'* or "
            "*'Create a project called Data Governance Initiative'*."
        ),
        "query_type": "command",
        "sources": [],
        "num_sources": 0,
        "retrieval_time": 0.0,
        "generation_time": 0.0,
        "avg_relevance_score": 0.0,
        "context_length": 0,
    }


# Singleton
_dr_egeria_agent: Optional[DrEgeriaActionAgent] = None


def get_dr_egeria_agent() -> DrEgeriaActionAgent:
    global _dr_egeria_agent
    if _dr_egeria_agent is None:
        _dr_egeria_agent = DrEgeriaActionAgent()
    return _dr_egeria_agent
