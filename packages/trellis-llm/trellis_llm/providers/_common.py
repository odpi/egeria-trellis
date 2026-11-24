from __future__ import annotations

from typing import Any, Optional

from trellis_llm.errors import MissingExtra
from trellis_llm.secret import Secret, scrub


def messages(prompt: str, system: str) -> list[dict[str, str]]:
    out = [{"role": "system", "content": system}] if system else []
    out.append({"role": "user", "content": prompt})
    return out


def import_client(provider: str, extra: str, module: str):
    """The client library, or MissingExtra naming the extra to install."""
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingExtra(provider, extra) from exc


def field(obj: Any, name: str) -> Any:
    """`obj.name` or `obj[name]`, None when absent (the clients' responses are objects, dicts or both)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    try:
        value = getattr(obj, name)
    except AttributeError:
        try:
            value = obj[name]
        except Exception:  # noqa: BLE001
            return None
    return value


def api_error_message(body: Any, fallback: str, key: Optional[Secret]) -> str:
    """The provider's own words from an error body ({"error": {"message": ..}} or {"error": ".."}), key scrubbed."""
    text = fallback
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            text = str(err["message"])
        elif isinstance(err, str):
            text = err
    return scrub(" ".join(str(text).split()), key)
