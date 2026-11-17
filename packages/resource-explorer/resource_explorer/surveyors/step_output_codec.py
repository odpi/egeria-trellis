"""A dispatched step's output, encoded losslessly for flow_run_results, or not at all (TR-21 R4).

Architect ruling 2026-11-15 (R4): one codec, shared by the flow that writes a step's output and the Resource Explorer
process that reads it back. Found in the hedwig A/B rerun: a JSON dump with a str() fallback stored each annotation
dataclass as its `repr()` string, so the host got strings back where it publishes annotations. Before TR-21, Prefect
pickled the result and the objects survived.

- An annotation (a `survey_report.Annotation` dataclass) is encoded with `survey_snapshot.annotation_to_dict`, the
  encoding the kept survey snapshot already uses for publishing. It is wrapped in a marker:
  `{"__re_codec__": "annotation", "v": 1, "data": {...}}`. Decoding uses `annotation_from_dict`.
- JSON-native values (dict with str keys, list, str, int, float, bool, None) pass through unchanged.
- Anything else raises `StepOutputEncodeError` (a TypeError) naming the type and its key path. Nothing is converted
  with `str()`. The real outputs captured for the A/B step set (repo_symbol_extraction, database
  credential_capability and postgres_operations, filesystem_inventory; tests/fixtures/step_outputs) carry no other
  type. A type a real step does emit gets its own codec entry, with a test, when one is found.
- Decoding an unknown marker, an unknown version, or a marker that does not decode raises `StepOutputDecodeError`.
  The host reads that as result_unreadable (TR-20): nothing is published.

`norm_hash` is sha256 over the canonical encoded text (sort_keys, compact separators). Both the worker and the
in-process flow compute it through `encode_step_output`, so the same output gives the same hash on both paths.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from typing import Any

#: The marker key. A step output may not use it as a key of its own (an encode error says so).
MARKER = "__re_codec__"
#: The annotation codec's version. A row carrying another version is not decoded (unreadable, never guessed).
ANNOTATION_VERSION = 1


class StepOutputEncodeError(TypeError):
    """A step output value the codec cannot encode losslessly."""


class StepOutputDecodeError(ValueError):
    """A stored step output the codec cannot decode."""


def _is_annotation(value: Any) -> bool:
    from resource_explorer.surveyors.survey_report import Annotation

    return isinstance(value, Annotation) and dataclasses.is_dataclass(value)


def _encode(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise StepOutputEncodeError(f"step output at {path}: a non-finite float ({value!r}) is not JSON")
        return value
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise StepOutputEncodeError(f"step output at {path}: a key of type {type(key).__name__} "
                                            f"({key!r}); only str keys survive JSON")
            if key == MARKER:
                raise StepOutputEncodeError(f"step output at {path}: the key {MARKER!r} is reserved for the codec")
            out[key] = _encode(item, f"{path}.{key}")
        return out
    if isinstance(value, list):
        return [_encode(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if _is_annotation(value):
        from resource_explorer.surveyors.survey_snapshot import annotation_to_dict

        return {MARKER: "annotation", "v": ANNOTATION_VERSION, "data": annotation_to_dict(value)}
    raise StepOutputEncodeError(
        f"step output at {path}: a {type(value).__module__}.{type(value).__qualname__} has no lossless encoding "
        "(TR-21 R4: no str() fallback); add a codec entry for it, with a test, if a real step emits it")


def _decode(value: Any, path: str) -> Any:
    if isinstance(value, list):
        return [_decode(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if not isinstance(value, dict):
        return value
    if MARKER not in value:
        return {key: _decode(item, f"{path}.{key}") for key, item in value.items()}
    kind, version = value.get(MARKER), value.get("v")
    if kind != "annotation":
        raise StepOutputDecodeError(f"step output at {path}: unknown codec marker {kind!r}")
    if version != ANNOTATION_VERSION:
        raise StepOutputDecodeError(f"step output at {path}: annotation codec version {version!r} is not "
                                    f"{ANNOTATION_VERSION}")
    from resource_explorer.surveyors.survey_snapshot import annotation_from_dict

    try:
        return annotation_from_dict(value["data"])
    except Exception as e:  # an unknown type, a missing field: unreadable, never a guess
        raise StepOutputDecodeError(f"step output at {path}: the annotation does not decode "
                                    f"({type(e).__name__}: {e})") from e


def encode_step_output(output: Any) -> tuple[str, str]:
    """(canonical JSON text, norm_hash) of a step's output. Raises StepOutputEncodeError for any value with no
    lossless encoding; the flow run then FAILS rather than storing it."""
    encoded = _encode(output if output is not None else {}, "$")
    text = json.dumps(encoded, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def decode_step_output(text: str) -> Any:
    """The step output `encode_step_output` encoded. Raises StepOutputDecodeError when it cannot be rebuilt."""
    try:
        data = json.loads(text) if isinstance(text, str) else text
    except ValueError as e:
        raise StepOutputDecodeError(f"step output is not JSON ({e})") from e
    return _decode(data, "$")
