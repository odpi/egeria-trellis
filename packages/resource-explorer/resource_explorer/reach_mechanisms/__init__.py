"""The reach-mechanism plugin point (entry point group `trellis.file_reach`): how Resource Explorer reads a file system
that is not mounted on its host (outcome 3, DESIGN-FILES-REMOTE-REACH §3).

Same shape as the Analytic Library's and open data's (`open_data/adapters.py`): a package ships a mechanism class
carrying a `manifest`, and installing it registers it. The built-ins in this tree (`s3`) are always present, and an
entry point of the same name replaces one. An entry point that fails to load, or whose manifest is refused, is listed
by name with its error (`mechanism_load_failures`), never hidden.

A manifest says what the mechanism speaks, what a registration must give it, which credential sources it accepts, its
credential tier and its cost shape, because the price line is shown BEFORE the press.

The interface is three reads and nothing else (no put, no delete, no copy, no command on the remote host):

    list(prefix)          -> Listing(entries, folders, calls)    one "directory": its objects and its sub-folders
    stat(key)             -> Entry                               size, modified time, etag
    read(key, max_bytes)  -> bytes                               a capped read (`tail=True`: the LAST max_bytes)

Keys and prefixes are relative to the file system's root (the walk's `rel_path`); the mechanism adds its own root.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import metadata
from typing import ClassVar

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "trellis.file_reach"

#: The credential sources a registration may name (D-S3-1, owner 2026-11-01: "Yes, all three").
CREDENTIAL_NONE = "none"            # a public bucket: anonymous
CREDENTIAL_SECRET_REF = "secret_ref"  # a key held in the `.omsecrets` store, by reference only
CREDENTIAL_PROFILE = "profile"      # the RE host's own credential chain (AWS_PROFILE, ~/.aws, a role)
CREDENTIAL_KINDS = (CREDENTIAL_NONE, CREDENTIAL_SECRET_REF, CREDENTIAL_PROFILE)

#: The only tier a built-in declares: it reads the resource and never writes it.
READ_ONLY = "read-only"

#: Said for a field a remote source does not carry (owner, group, POSIX mode on an object store). Never blank.
NOT_REPORTED = "not reported by this source"

#: Entry points the last discovery skipped, with the reason.
LOAD_FAILURES: dict[str, str] = {}


class ManifestError(ValueError):
    """A mechanism manifest that cannot be priced or registered."""


class ReachError(RuntimeError):
    """A read the remote source refused or could not answer. Its text is already redacted."""


@dataclass(frozen=True)
class MechanismManifest:
    name: str                                 # the mechanism's id, e.g. "s3"
    protocol: str                             # what a registration's `protocol` column says, e.g. "s3"
    #: The registration fields it needs beyond slug and display name.
    registration_fields: tuple[str, ...] = ()
    credential_kinds: tuple[str, ...] = ()
    credential_tier: str = READ_ONLY
    #: Cost shape, in the words the price line carries. Required: a walk is priced before it runs.
    cost_shape: str = ""
    #: A mechanism that runs commands on the remote host must say so; the owner's configuration must then allow it.
    executes_commands: bool = False

    def validate(self) -> "MechanismManifest":
        if not self.name or not self.protocol:
            raise ManifestError("a reach mechanism manifest needs a name and the protocol it speaks")
        if not self.cost_shape:
            raise ManifestError(f"reach mechanism {self.name!r} declares no cost shape: the price is shown before a "
                                "walk runs, so cost_shape is required")
        if self.credential_tier != READ_ONLY:
            raise ManifestError(f"reach mechanism {self.name!r}: credential tier must be {READ_ONLY!r}, "
                                f"not {self.credential_tier!r}")
        unknown = [k for k in self.credential_kinds if k not in CREDENTIAL_KINDS]
        if not self.credential_kinds or unknown:
            raise ManifestError(f"reach mechanism {self.name!r}: credential kinds must be among {CREDENTIAL_KINDS}"
                                f"{f', not {unknown}' if unknown else ''}")
        if self.executes_commands:
            raise ManifestError(f"reach mechanism {self.name!r} executes commands on the remote host, which this "
                                "deployment does not allow")
        return self

    def as_dict(self) -> dict:
        return {"name": self.name, "protocol": self.protocol, "registration_fields": list(self.registration_fields),
                "credential_kinds": list(self.credential_kinds), "credential_tier": self.credential_tier,
                "cost_shape": self.cost_shape, "executes_commands": self.executes_commands}


@dataclass(frozen=True)
class Entry:
    """One object as a listing or a stat saw it. `key` is relative to the file system's root."""

    key: str
    size: int
    modified_at: str = ""      # ISO-8601, UTC
    etag: str = ""             # the source's own tag, quotes removed

    @property
    def multipart(self) -> bool:
        """An S3 multipart upload's etag is "<md5 of the part md5s>-<parts>": not a hash of the content."""
        return "-" in self.etag


@dataclass
class Listing:
    entries: list[Entry] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)    # sub-folder prefixes, relative, each ending "/"
    calls: int = 0
    #: The listing stopped at a caller's limit before the source said it was done.
    truncated: bool = False


class ReachMechanism:
    """Base for mechanisms. `manifest` is the class-level declaration; an instance is bound to one file system."""

    manifest: ClassVar[MechanismManifest]

    def __init__(self) -> None:
        #: Round trips and bytes this instance has spent: the price line's measured half.
        self.calls = 0
        self.bytes_read = 0

    def list(self, prefix: str = "") -> Listing:
        raise NotImplementedError

    def stat(self, key: str) -> Entry:
        raise NotImplementedError

    def read(self, key: str, max_bytes: int, *, tail: bool = False) -> bytes:
        raise NotImplementedError


def _builtins() -> list[type[ReachMechanism]]:
    from resource_explorer.reach_mechanisms.s3 import S3Mechanism

    return [S3Mechanism]


def _entry_points():
    return metadata.entry_points(group=ENTRY_POINT_GROUP)


def discover_mechanisms() -> dict[str, type[ReachMechanism]]:
    """name -> mechanism class: the built-ins, then every installed entry point (which wins on a name clash). One bad
    package does not take the others down; it is kept in `mechanism_load_failures()` and logged."""
    found: dict[str, type[ReachMechanism]] = {}
    LOAD_FAILURES.clear()
    for cls in _builtins():
        found[cls.manifest.validate().name] = cls
    for ep in _entry_points():
        try:
            cls = ep.load()
            manifest = getattr(cls, "manifest", None)
            if not isinstance(manifest, MechanismManifest):
                raise ManifestError("no MechanismManifest on the mechanism")
            found[manifest.validate().name] = cls
        except (ImportError, AttributeError, TypeError, ManifestError) as exc:
            LOAD_FAILURES[ep.name] = f"{type(exc).__name__}: {exc}"[:240]
            log.warning("file reach: mechanism entry point %r skipped: %s", ep.name, exc)
    return found


def mechanism_load_failures() -> dict[str, str]:
    """entry point name -> why it was skipped, as of the last `discover_mechanisms()`."""
    return dict(LOAD_FAILURES)


def mechanism_for_protocol(protocol: str) -> type[ReachMechanism] | None:
    wanted = (protocol or "").strip().lower()
    for cls in discover_mechanisms().values():
        if cls.manifest.protocol == wanted:
            return cls
    return None


def is_remote(fs) -> bool:
    """A file system read through a mechanism (any protocol but a local mount)."""
    return (getattr(fs, "protocol", "") or "local") != "local"


def mechanism_for(fs) -> ReachMechanism:
    """The mechanism instance bound to a registered remote file system. Raises ReachError when none speaks its protocol."""
    cls = mechanism_for_protocol(getattr(fs, "protocol", ""))
    if cls is None:
        raise ReachError(f"no reach mechanism speaks {getattr(fs, 'protocol', '')!r} on this server")
    return cls.for_filesystem(fs)  # type: ignore[attr-defined]
