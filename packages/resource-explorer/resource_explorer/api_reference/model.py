"""The kind-agnostic interface model.

An `Interface` is one description of one kind of surface, from one source:

    kind        what sort of surface: "http_api" today. `KINDS` names, per kind, what its operations are
                called ("operations") and what identifies one. The MCP follow-on adds "mcp" here
                (operations = tools, prompts and resources; `Operation.kind_detail` says which).
    source      "committed_spec" | "running_instance" | "derived_from_code"
    operations  `Operation`s. Every field is meaningful for any kind; http-only ones (`method`, `path`) are
                empty for a kind that has none. `key` is the stable identity used to compare two sources
                (HTTP: "GET /orders"; MCP: "tool:run_tests"), `name` is the display text, `group` (tags)
                is the pane's grouping, `input_schema` is the argument schema (an MCP tool's inputSchema;
                an HTTP operation leaves it None because its inputs are parameters + a request body).

Everything round-trips through JSON (`to_dict` / `from_dict`) so a parsed committed spec can be stored in a
finding and a stored instance document can be re-parsed with no second code path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SOURCE_COMMITTED = "committed_spec"
SOURCE_LIVE = "running_instance"
SOURCE_DERIVED = "derived_from_code"
#: Preference order, best first: a contract the repository commits, then what the running service says
#: about itself, then what the code registers.
SOURCE_PREFERENCE = (SOURCE_COMMITTED, SOURCE_LIVE, SOURCE_DERIVED)

SOURCE_WORDS = {
    SOURCE_COMMITTED: "committed spec",
    SOURCE_LIVE: "running instance",
    SOURCE_DERIVED: "derived from code",
}

#: kind -> how the pane and the compile name its members.
KINDS = {
    "http_api": {"noun": "operations", "singular": "operation", "title": "HTTP API"},
}


def kind_noun(kind: str) -> str:
    return (KINDS.get(kind) or {}).get("noun", "operations")


@dataclass
class Operation:
    key: str
    name: str
    kind: str = "http_api"
    kind_detail: str = ""                   # MCP: "tool" | "prompt" | "resource"
    method: str = ""
    path: str = ""
    summary: str = ""
    description: str = ""
    operation_id: str = ""
    tags: list = field(default_factory=list)
    parameters: list = field(default_factory=list)     # [{name, in, required, type, description}]
    request_body: dict | None = None                    # {required, content_types, schema_name}
    responses: list = field(default_factory=list)       # [{status, description, content_types, schema_name}]
    security: list = field(default_factory=list)        # scheme names that apply; [] = none
    deprecated: bool = False
    input_schema: dict | None = None
    handler: str = ""                       # derived: qualified name of the handler
    handler_signature: str = ""
    location: str = ""                      # derived: "path/to/file.py:42"

    @property
    def group(self) -> str:
        return self.tags[0] if self.tags else ""

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "kind": self.kind, "kind_detail": self.kind_detail,
            "method": self.method, "path": self.path, "summary": self.summary, "description": self.description,
            "operation_id": self.operation_id, "tags": list(self.tags), "group": self.group,
            "parameters": self.parameters, "request_body": self.request_body, "responses": self.responses,
            "security": list(self.security), "deprecated": self.deprecated, "input_schema": self.input_schema,
            "handler": self.handler, "handler_signature": self.handler_signature, "location": self.location,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Operation":
        keys = {"key", "name", "kind", "kind_detail", "method", "path", "summary", "description", "operation_id",
                "tags", "parameters", "request_body", "responses", "security", "deprecated", "input_schema",
                "handler", "handler_signature", "location"}
        return cls(**{k: v for k, v in d.items() if k in keys})


@dataclass
class Interface:
    kind: str
    source: str
    origin: str = ""                 # file path (committed), URL (running instance), "" (code)
    as_of: str = ""                  # fetched_at / surveyed_at, ISO
    title: str = ""
    version: str = ""
    spec_version: str = ""           # "openapi 3.1.0" / "swagger 2.0"
    description: str = ""
    base_paths: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    total_operations: int = 0        # before any cap; operations may hold fewer
    schemas: dict = field(default_factory=dict)
    security_schemes: dict = field(default_factory=dict)    # name -> "http bearer"
    groups: dict = field(default_factory=dict)              # tag -> description
    notes: list = field(default_factory=list)

    def __post_init__(self):
        if not self.total_operations:
            self.total_operations = len(self.operations)

    @property
    def source_phrase(self) -> str:
        """The label shown beside every list, built from the facts, never from the click."""
        if self.source == SOURCE_COMMITTED:
            return f"committed spec file {self.origin}" + (f", read at the survey of {self.as_of}" if self.as_of else "")
        if self.source == SOURCE_LIVE:
            return f"from the running instance at {self.origin}, fetched {self.as_of or 'at an unrecorded time'}"
        return "derived from code, not a published contract" + (f" (code surveyed {self.as_of})" if self.as_of else "")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "source": self.source, "origin": self.origin, "as_of": self.as_of,
            "title": self.title, "version": self.version, "spec_version": self.spec_version,
            "description": self.description, "base_paths": list(self.base_paths),
            "operations": [o.to_dict() for o in self.operations], "total_operations": self.total_operations,
            "schemas": self.schemas, "security_schemes": self.security_schemes, "groups": self.groups,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Interface":
        ops = [Operation.from_dict(o) for o in d.get("operations") or []]
        return cls(
            kind=d.get("kind", "http_api"), source=d.get("source", ""), origin=d.get("origin", ""),
            as_of=d.get("as_of", ""), title=d.get("title", ""), version=d.get("version", ""),
            spec_version=d.get("spec_version", ""), description=d.get("description", ""),
            base_paths=list(d.get("base_paths") or []), operations=ops,
            total_operations=d.get("total_operations") or len(ops), schemas=d.get("schemas") or {},
            security_schemes=d.get("security_schemes") or {}, groups=d.get("groups") or {},
            notes=list(d.get("notes") or []))

    def api_payload(self) -> dict:
        """What the pane receives: the interface plus the words around it."""
        d = self.to_dict()
        d["noun"] = kind_noun(self.kind)
        d["source_phrase"] = self.source_phrase
        d["source_word"] = SOURCE_WORDS.get(self.source, self.source)
        d["count"] = len(self.operations)
        return d
