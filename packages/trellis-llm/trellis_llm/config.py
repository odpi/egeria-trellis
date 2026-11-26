"""One resolver for every app's LLM settings (TR-38).

Precedence, per setting:  <APP>_LLM_*  >  TRELLIS_LLM_*  >  the app's legacy names (each warns once)  >  defaults.
`resolve()` reads the environment (and, for Egeria Advisor, an advisor.yaml given as data) and returns frozen LLMSettings;
it raises ConfigError, in words, for anything wrong or missing, and RemoteNotAllowed for a destination that would send
prompts off this network without <APP>_LLM_ALLOW_REMOTE=true. Nothing calls it yet (steps 2 and 3 move RE and EA onto it).

Settings (each as `<APP>_LLM_<NAME>` or `TRELLIS_LLM_<NAME>`):
  PROVIDER      ollama (default) | openai_compatible | anthropic
  BASE_URL      ollama: default http://localhost:11434; openai_compatible: required; anthropic: optional
  API_KEY       held as a Secret, never printed
  MODEL         the model for every slot; MODEL_<SLOT> the model for one slot (a slot is an app's own word: query, code ...)
  TIER          dev | demo-gpu | demo-cpu: OLLAMA ONLY (models per slot and the num_ctx ceiling)
  NUM_CTX       Ollama's context window, overriding the tier's
  ALLOW_REMOTE  true to allow a destination that is not loopback or private (and the anthropic provider)
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

from trellis_llm.errors import ConfigError, RemoteNotAllowed
from trellis_llm.secret import Secret

log = logging.getLogger("trellis_llm")

PROVIDERS = ("ollama", "openai_compatible", "anthropic")
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
ANTHROPIC_HOST = "api.anthropic.com"
_TRUE = {"1", "true", "yes", "on"}

#: Ollama tiers (RE's and EA's TIER_PRESETS, as data). `models` is per slot; an unlisted slot uses the "default" entry.
TIER_PRESETS: dict[str, dict[str, Any]] = {
    "dev": {"num_ctx": 32768, "models": None},
    "demo-gpu": {"num_ctx": 8192, "models": {"default": "llama3.1:8b", "code": "codellama:13b",
                                             "maintenance": "codellama:13b"}},
    "demo-cpu": {"num_ctx": 8192, "models": {"default": "llama3.1:8b"}},
}

#: The apps' legacy names, as data. Each is read only for its own app, and warns once. SETTING -> env names.
LEGACY: dict[str, dict[str, Any]] = {
    "RE": {
        "PROVIDER": ("LLM__BACKEND",), "BASE_URL": ("LLM__OLLAMA__BASE_URL",), "TIER": ("EXPLORER_MODEL_TIER",),
        # RE has one model slot; which legacy name holds it depends on the backend
        "MODEL_BY_PROVIDER": {"ollama": ("LLM__OLLAMA__MODEL",), "openai_compatible": ("LLM__OPENAI__MODEL",),
                              "anthropic": ("LLM__ANTHROPIC__MODEL",)},
    },
    "EA": {
        "BASE_URL": ("OLLAMA_BASE_URL",), "TIER": ("ADVISOR_MODEL_TIER",),
        "MODEL": ("OLLAMA_MODEL",),
        # OLLAMA_MODEL does not reach the planning slot (EA's own rule, advisor/config.py)
        "MODEL_SLOTS": {"query": ("OLLAMA_MODEL",), "conversation": ("OLLAMA_MODEL",), "code": ("OLLAMA_CODE_MODEL",),
                        "maintenance": ("OLLAMA_CODE_MODEL",)},
    },
}
_PROVIDER_ALIASES = {"openai": "openai_compatible"}      # RE's legacy backend name
_WARNED: set[str] = set()


@dataclass(frozen=True)
class LLMSettings:
    app: str
    provider: str
    base_url: Optional[str]
    api_key: Optional[Secret] = None
    models: Mapping[str, str] = field(default_factory=dict)      # slot -> model
    tier: Optional[str] = None
    num_ctx: Optional[int] = None
    allow_remote: bool = False
    notes: tuple[str, ...] = ()

    def model_for(self, slot: str = "default") -> str:
        model = self.models.get(slot) or self.models.get("default")
        if not model:
            raise ConfigError(f"no model is set for slot {slot!r}: set {self.app}_LLM_MODEL_{slot.upper()} or "
                              f"{self.app}_LLM_MODEL")
        return model

    @property
    def host(self) -> str:
        """The network location prompts go to (host[:port])."""
        if self.base_url:
            return urlparse(self.base_url).netloc
        return ANTHROPIC_HOST if self.provider == "anthropic" else ""


# ── egress ────────────────────────────────────────────────────────────────────

def _system_resolver(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def is_local_host(host: str, resolver: Callable[[str], list[str]] = _system_resolver) -> bool:
    """True for loopback and private addresses (10/8, 172.16/12, 192.168/16, link-local, ::1, ULA) and for a hostname
    whose EVERY address is one. A name that cannot be resolved is not local: that fails closed."""
    host = host.strip("[]").lower()
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        ips = []
        for a in resolver(host):
            try:
                ips.append(ipaddress.ip_address(a.split("%")[0]))
            except ValueError:
                pass
    return bool(ips) and all(ip.is_loopback or ip.is_private for ip in ips)


def check_egress(settings: LLMSettings, resolver: Callable[[str], list[str]] = _system_resolver) -> None:
    """Refuse, in words, a destination that would send prompts off this network without ALLOW_REMOTE."""
    if settings.allow_remote:
        return
    if settings.provider == "anthropic":
        raise RemoteNotAllowed(
            f"the anthropic provider sends prompts to {settings.host or ANTHROPIC_HOST}, off this network: set "
            f"{settings.app}_LLM_ALLOW_REMOTE=true to allow it, or choose ollama or an openai_compatible server you run.")
    host = urlparse(settings.base_url or "").hostname or ""
    if not is_local_host(host, resolver):
        raise RemoteNotAllowed(
            f"{settings.app}_LLM_BASE_URL points at {settings.host or host!r}, which is not a loopback or private address "
            f"(or could not be resolved), so prompts would leave this network: set {settings.app}_LLM_ALLOW_REMOTE=true "
            f"to allow it.")


def startup_line(settings: LLMSettings) -> str:
    """One line: provider, where prompts go, and the model per slot. The API key is never in it."""
    models = ", ".join(f"{slot}={model}" for slot, model in settings.models.items())
    extra = "".join([f", tier={settings.tier}" if settings.tier else "",
                     f", num_ctx={settings.num_ctx}" if settings.num_ctx else ""])
    return (f"LLM ({settings.app}): provider={settings.provider}, host={settings.host}, models: {models}{extra}; "
            f"prompts go to {settings.host}")


def log_startup(settings: LLMSettings) -> str:
    line = startup_line(settings)
    log.info("%s", line)
    return line


# ── resolution ────────────────────────────────────────────────────────────────

class _Layers:
    """Looks one setting up through the three layers and says which one answered."""

    def __init__(self, app: str, env: Mapping[str, str], legacy_files: Mapping[str, Mapping[str, Any]]):
        self.app, self.env, self.files = app, env, legacy_files

    def _warn(self, old: str, new: str) -> None:
        if old not in _WARNED:
            _WARNED.add(old)
            log.warning("%s is deprecated: set %s_LLM_%s (or TRELLIS_LLM_%s) instead.", old, self.app, new, new)

    def _env(self, name: str) -> Optional[str]:
        v = self.env.get(name)
        return v.strip() if isinstance(v, str) and v.strip() else None

    def _legacy_env(self, names, new: str) -> Optional[str]:
        found = None
        for n in names:
            v = self._env(n)
            if v is not None:
                self._warn(n, new)
                found = found if found is not None else v
        return found

    def _yaml(self, path: tuple[str, ...]) -> Optional[str]:
        data: Any = self.files.get("advisor.yaml") if self.app == "EA" else None
        for key in path:
            data = data.get(key) if isinstance(data, Mapping) else None
        return str(data).strip() if data not in (None, "", {}) and not isinstance(data, Mapping) else None

    def get(self, name: str, provider: Optional[str] = None) -> Optional[str]:
        for prefix in (f"{self.app}_LLM_", "TRELLIS_LLM_"):
            v = self._env(prefix + name)
            if v is not None:
                return v
        legacy = LEGACY.get(self.app, {})
        names = legacy.get(name, ())
        if name == "MODEL" and provider and "MODEL_BY_PROVIDER" in legacy:
            names = legacy["MODEL_BY_PROVIDER"].get(provider, ())
        v = self._legacy_env(names, name)
        if v is not None:
            return v
        return {"PROVIDER": self._yaml(("llm", "provider")), "BASE_URL": self._yaml(("llm", "base_url")),
                "TIER": self._yaml(("llm", "tier"))}.get(name)

    def model(self, slot: str, provider: str) -> Optional[str]:
        """The explicit model for a slot, by layer: a layer's slot value, else its generic MODEL."""
        for prefix in (f"{self.app}_LLM_", "TRELLIS_LLM_"):
            for key in ((f"{prefix}MODEL_{slot.upper()}",) if slot != "default" else ()) + (f"{prefix}MODEL",):
                v = self._env(key)
                if v is not None:
                    return v
        legacy = LEGACY.get(self.app, {})
        v = None
        if slot != "default":
            v = self._legacy_env(legacy.get("MODEL_SLOTS", {}).get(slot, ()), f"MODEL_{slot.upper()}")
        if v is None:
            names = legacy.get("MODEL", ())
            if "MODEL_BY_PROVIDER" in legacy:
                names = legacy["MODEL_BY_PROVIDER"].get(provider, ())
            v = self._legacy_env(names, "MODEL")
        if v is None:
            yaml_models = (self.files.get("advisor.yaml") or {}).get("llm", {}).get("models", {}) if self.app == "EA" else {}
            v = str(yaml_models[slot]).strip() if isinstance(yaml_models, Mapping) and yaml_models.get(slot) else None
        return v


def resolve(app: str, env: Optional[Mapping[str, str]] = None, *, slots: tuple[str, ...] = ("default",),
            legacy_files: Optional[Mapping[str, Mapping[str, Any]]] = None,
            resolver: Callable[[str], list[str]] = _system_resolver) -> LLMSettings:
    """The LLM settings for `app` ("RE", "EA", "STUDIO" ...). See the module docstring for precedence and settings."""
    import os

    app = app.upper()
    layers = _Layers(app, os.environ if env is None else env, legacy_files or {})
    notes: list[str] = []

    provider = (layers.get("PROVIDER") or "ollama").lower()
    provider = _PROVIDER_ALIASES.get(provider, provider)
    if provider not in PROVIDERS:
        raise ConfigError(f"{app}_LLM_PROVIDER={provider!r} is not a provider: choose one of {', '.join(PROVIDERS)}.")

    base_url = layers.get("BASE_URL")
    if provider == "ollama":
        base_url = base_url or DEFAULT_OLLAMA_URL
    elif provider == "openai_compatible" and not base_url:
        raise ConfigError(f"provider openai_compatible needs {app}_LLM_BASE_URL (for example http://localhost:1234/v1 for "
                          f"LM Studio): it has no default, so prompts are never sent somewhere you did not name.")
    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigError(f"{app}_LLM_BASE_URL={base_url!r} is not an http(s) URL with a host.")

    key = layers.get("API_KEY")
    api_key = Secret(key) if key else None

    tier = layers.get("TIER")
    num_ctx_text = layers.get("NUM_CTX")
    num_ctx: Optional[int] = None
    preset: dict[str, Any] = {}
    if provider != "ollama":
        if tier:
            notes.append(f"tier {tier!r} ignored: tiers apply only to the ollama provider")
        tier = None
    else:
        if tier:
            tier = tier.lower()
            if tier not in TIER_PRESETS:
                raise ConfigError(f"{app}_LLM_TIER={tier!r} is not a tier: choose one of {', '.join(sorted(TIER_PRESETS))}.")
            preset = TIER_PRESETS[tier]
            num_ctx = preset["num_ctx"]
        if num_ctx_text:
            try:
                num_ctx = int(num_ctx_text)
            except ValueError:
                raise ConfigError(f"{app}_LLM_NUM_CTX={num_ctx_text!r} is not a whole number.") from None

    models: dict[str, str] = {}
    for slot in slots:
        model = layers.model(slot, provider)
        if model is None and preset.get("models"):
            model = preset["models"].get(slot) or preset["models"].get("default")
        if model is None and provider == "ollama":
            model = DEFAULT_OLLAMA_MODEL
        if model is None:
            raise ConfigError(f"provider {provider} needs a model: set {app}_LLM_MODEL"
                              + (f" (or {app}_LLM_MODEL_{slot.upper()} for the {slot!r} slot)" if slot != "default" else "")
                              + "; there is no default, so it never falls back to a model name from another provider.")
        models[slot] = model

    allow = (layers._env(f"{app}_LLM_ALLOW_REMOTE") or layers._env("TRELLIS_LLM_ALLOW_REMOTE") or "").lower() in _TRUE
    settings = LLMSettings(app=app, provider=provider, base_url=base_url, api_key=api_key, models=models, tier=tier,
                           num_ctx=num_ctx, allow_remote=allow, notes=tuple(notes))
    check_egress(settings, resolver)
    return settings
