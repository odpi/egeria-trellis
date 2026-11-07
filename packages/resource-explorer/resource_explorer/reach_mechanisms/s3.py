"""The built-in S3 reach mechanism (outcome 3, BRIEF-S3-REACH-AND-PARQUET-SLICE-2026-11-02 Part A).

S3 and S3-compatible stores (MinIO): a "folder" is a prefix. Three calls and no others:

    ListObjectsV2  Delimiter="/"   one call per 1,000 keys per prefix; CommonPrefixes are the sub-folders
    HeadObject                      stat: size, last modified, etag
    GetObject      Range=bytes=…   a capped read (the first N bytes, or the last N for a Parquet footer)

The client is wrapped in `_ReadOnlyClient`, which refuses every other operation by name, so a write cannot be
reached by accident: the mechanism never calls PutObject, DeleteObject or CopyObject, and the wrapper makes that a
property of the code rather than of care.

Credentials (D-S3-1/2, owner 2026-11-01): `none` (a public bucket, unsigned requests), `secret_ref` (a key held in
the `.omsecrets` store under "<slug>::S3 reader", by reference: never a registry column) or `profile` (the RE
host's own credential chain, by profile name). A key or secret is never logged, returned or put in an error string:
every error text goes through `secret_redaction` with the secret in hand.
"""
from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timezone

from resource_explorer import secret_redaction
from resource_explorer.reach_mechanisms import (
    CREDENTIAL_KINDS,
    CREDENTIAL_NONE,
    CREDENTIAL_PROFILE,
    CREDENTIAL_SECRET_REF,
    Entry,
    Listing,
    MechanismManifest,
    ReachError,
    ReachMechanism,
)

log = logging.getLogger(__name__)

PROTOCOL = "s3"
#: The suffix of a stored key's collection name in the secrets store.
SECRET_SUFFIX = "S3 reader"
#: The one sentence said for a write that was never tried (a later control may test it; this slice does not).
WRITE_NOT_CHECKED = "write permission not checked · Resource Explorer never attempts a write to find out"
#: Said on every S3 reach and publish line: Egeria has no remote file connector.
EGERIA_NO_REACH = "Egeria has no reach to this bucket"

#: The only client operations the mechanism may make.
_ALLOWED_OPERATIONS = frozenset({"list_objects_v2", "head_object", "get_object"})


def secret_ref(slug: str) -> str:
    """The collection name a file system's key is held under: "<slug>::S3 reader"."""
    return f"{slug}::{SECRET_SUFFIX}"


def ref_display_name(ref: str) -> str:
    """"S3 reader" from "<slug>::S3 reader" (a ref without "::" is its own display name)."""
    return (ref or "").rpartition("::")[2] or (ref or "")


def key_tail(access_key_id: str) -> str:
    """The last four characters of a key id as "…XXXX", for the reach line AND for the registry row (W42: the whole id is
    never stored). Idempotent: a tail given back reads as the same tail. Never the whole id; four or fewer characters
    (not a real key id) give ""."""
    k = (access_key_id or "").strip()
    return f"…{k[-4:]}" if len(k) > 4 else ""


def re_host() -> str:
    """The RE host's short name: the gate for outcome 3 (D-S3-4, owner 2026-11-01: "Yes, RE host")."""
    return (socket.gethostname() or "this server").split(".")[0]


def store_key(slug: str, access_key_id: str, secret_access_key: str, session_token: str = "") -> str:
    """Hold a key in the secrets store and return its reference. Refuses when no store is configured, exactly as an
    open-data portal key does: a key silently dropped is a file system that fails later."""
    from resource_explorer import omsecrets_store

    if not (access_key_id or "").strip() or not (secret_access_key or "").strip():
        raise ReachError("a stored key needs both its access key id and its secret access key")
    ref = secret_ref(slug)
    if not omsecrets_store.write_credential(ref, access_key_id.strip(), secret_access_key.strip(),
                                            extra_secrets={"sessionToken": (session_token or "").strip()}):
        raise ReachError("No secrets store is configured on this server, so an S3 key cannot be held. Choose a public "
                         "bucket or a host profile, or configure EGERIA_SECRETS_STORE_LOCAL_PATH.")
    return ref


def secrets_store_configured() -> bool:
    from resource_explorer import omsecrets_store

    return bool(omsecrets_store.local_path())


def _read_key(ref: str) -> tuple[str, str, str]:
    """(access key id, secret, session token) from the secrets store. Raises ReachError naming the REF, never a value."""
    from resource_explorer import omsecrets_store

    path = omsecrets_store.local_path()
    if not path:
        raise ReachError(f"No secrets store is configured on this server, so the key held as {ref!r} cannot be read")
    entry = omsecrets_store._load(path)["secretsCollections"].get(ref) or {}
    secrets = entry.get("secrets") or {}
    key_id, secret = str(secrets.get("userId") or ""), str(secrets.get("clearPassword") or "")
    if not key_id or not secret:
        raise ReachError(f"the secrets store holds no S3 key named {ref!r}")
    return key_id, secret, str(secrets.get("sessionToken") or "")


@dataclass(frozen=True)
class S3Settings:
    """What a registration gives the mechanism (the registry's additive columns)."""

    bucket: str
    endpoint_url: str = ""          # blank = AWS
    region: str = ""
    root_prefix: str = ""
    path_style: bool = False
    tls_verify: bool = True
    credential_kind: str = CREDENTIAL_NONE
    credential_ref: str = ""        # the secrets-store ref, or the profile name
    access_key_id: str = ""         # the id's tail ("…XXXX") as the row holds it (W42), or the whole id typed on the form

    @classmethod
    def from_filesystem(cls, fs) -> "S3Settings":
        g = lambda name, default="": getattr(fs, name, default)  # noqa: E731
        return cls(bucket=(g("bucket") or "").strip(), endpoint_url=(g("endpoint_url") or "").strip(),
                   region=(g("region") or "").strip(), root_prefix=g("root_prefix") or "",
                   path_style=bool(g("path_style", False)), tls_verify=bool(g("tls_verify", True)),
                   credential_kind=(g("credential_kind") or CREDENTIAL_NONE).strip(),
                   credential_ref=(g("credential_ref") or "").strip(),
                   access_key_id=(g("access_key_id") or "").strip())

    @property
    def root(self) -> str:
        """The root prefix as S3 wants it: no leading "/", one trailing "/" (or empty for the whole bucket)."""
        r = (self.root_prefix or "").strip().lstrip("/")
        return r if not r or r.endswith("/") else r + "/"

    @property
    def location(self) -> str:
        """s3://bucket/root, the file system's path in the words a person reads (and the FileFolder's pathName)."""
        return f"s3://{self.bucket}/{self.root}".rstrip("/")

    def credential_name(self) -> str:
        """Who RE is to the bucket, in the reach sentence. Never the secret, never the whole key id."""
        if self.credential_kind == CREDENTIAL_SECRET_REF:
            tail = key_tail(self.access_key_id)
            return f"key {tail} ({ref_display_name(self.credential_ref)})" if tail else (
                f"key ({ref_display_name(self.credential_ref)})")
        if self.credential_kind == CREDENTIAL_PROFILE:
            return f"host profile {self.credential_ref or 'default'}"
        return "public bucket"

    def with_key_id(self, access_key_id: str) -> "S3Settings":
        from dataclasses import replace

        return replace(self, access_key_id=(access_key_id or "").strip())

    def validate(self) -> "S3Settings":
        if not self.bucket:
            raise ReachError("an S3 file system needs a bucket")
        if "/" in self.bucket:
            raise ReachError("the bucket is a name, not a path: put the folder in the root prefix")
        if self.credential_kind not in CREDENTIAL_KINDS:
            raise ReachError(f"the credential source must be one of {CREDENTIAL_KINDS}, not {self.credential_kind!r}")
        if self.credential_kind == CREDENTIAL_SECRET_REF and not self.credential_ref:
            raise ReachError("a stored key is named by its secrets-store reference, and none was given")
        if self.endpoint_url and not self.endpoint_url.startswith(("http://", "https://")):
            raise ReachError("the endpoint URL starts with http:// or https://")
        return self


class _ReadOnlyClient:
    """The boto3 client with every operation but the three reads refused by name."""

    def __init__(self, client) -> None:
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name: str):
        if name not in _ALLOWED_OPERATIONS:
            raise PermissionError(f"the S3 reach mechanism makes reads only; {name!r} is not one")
        return getattr(object.__getattribute__(self, "_client"), name)

    def __setattr__(self, name, value):  # noqa: D401 -- the wrapper is immutable
        raise AttributeError("the read-only S3 client cannot be changed")


def _iso(dt) -> str:
    """A LastModified datetime as naive UTC ISO-8601, the local walk's own form."""
    if not isinstance(dt, datetime):
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _etag(raw) -> str:
    return str(raw or "").strip('"')


#: S3's error codes, in the words the reach line and the form carry.
_ERROR_WORDS = {
    "AccessDenied": "the bucket refused the request (AccessDenied)",
    "AllAccessDisabled": "the bucket refused the request (AllAccessDisabled)",
    "NoSuchBucket": "no such bucket at this endpoint",
    "InvalidAccessKeyId": "the endpoint does not know this access key id",
    "SignatureDoesNotMatch": "the endpoint refused the key's signature (a wrong secret?)",
    "ExpiredToken": "the session token has expired",
    "InvalidBucketName": "the bucket name is not valid",
    "PermanentRedirect": "the bucket is in another region: set the region",
    "AuthorizationHeaderMalformed": "the request was signed for the wrong region: set the region",
    "NoSuchKey": "no such object",
    "404": "no such object",
    "403": "the bucket refused the request (403)",
}


class S3Mechanism(ReachMechanism):
    manifest = MechanismManifest(
        name="s3",
        protocol=PROTOCOL,
        registration_fields=("endpoint_url", "region", "bucket", "root_prefix", "path_style", "tls_verify",
                             "credential_kind", "credential_ref", "access_key_id"),
        credential_kinds=CREDENTIAL_KINDS,
        cost_shape="one list call per 1,000 keys per prefix; bytes per object read",
    )

    def __init__(self, settings: S3Settings, *, client=None, key: tuple[str, str, str] | None = None) -> None:
        """`key` = (access key id, secret, session token) typed on the form for "Check reach" before Register: held
        in this instance's memory only, never written. Without it a `secret_ref` source reads the secrets store."""
        super().__init__()
        self.settings = settings.validate()
        self._secret = (key[1] if key else "") or ""
        self._key = key
        self._client = _ReadOnlyClient(client if client is not None else self._build_client())

    @classmethod
    def for_filesystem(cls, fs, *, client=None) -> "S3Mechanism":
        return cls(S3Settings.from_filesystem(fs), client=client)

    # ── the client ──────────────────────────────────────────────────────────────────────────────────────────────────

    def _build_client(self):
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config

        s = self.settings
        options: dict = {"retries": {"max_attempts": 3, "mode": "standard"}, "connect_timeout": 10, "read_timeout": 60}
        if s.path_style:
            options["s3"] = {"addressing_style": "path"}
        session_kw: dict = {}
        if s.credential_kind == CREDENTIAL_NONE:
            options["signature_version"] = UNSIGNED
        elif s.credential_kind == CREDENTIAL_PROFILE:
            if s.credential_ref:
                session_kw["profile_name"] = s.credential_ref
        else:
            key_id, secret, token = self._key if self._key else _read_key(s.credential_ref)
            self._secret = secret
            self._key = (key_id, secret, token)
            session_kw.update(aws_access_key_id=key_id, aws_secret_access_key=secret,
                              **({"aws_session_token": token} if token else {}))
        try:
            session = boto3.session.Session(**session_kw)
            return session.client("s3", endpoint_url=s.endpoint_url or None, region_name=s.region or None,
                                  verify=s.tls_verify, config=Config(**options))
        except Exception as exc:  # noqa: BLE001 -- a profile that does not exist, a bad endpoint
            raise ReachError(self._words(exc)) from None

    def _words(self, exc: BaseException) -> str:
        """An exception as a redacted sentence: S3's own code in words where it gave one, else the class and text."""
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str((response.get("Error") or {}).get("Code") or "")
        if code and code in _ERROR_WORDS:
            text = _ERROR_WORDS[code]
        else:
            text = f"{type(exc).__name__}: {exc}"
        text = secret_redaction.scrub_text(text)
        if self._secret:
            text = secret_redaction.scrub(text, self._secret)
        # The whole key id is not a secret, but it is never shown either: its last four are the reach line's.
        key_id = (self._key[0] if self._key else "") or self.settings.access_key_id
        if key_id and len(key_id) > 4:
            text = text.replace(key_id, key_tail(key_id))
        return text[:400]

    def _call(self, operation: str, **kwargs):
        self.calls += 1
        try:
            return getattr(self._client, operation)(**kwargs)
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001 -- every failure leaves as a redacted ReachError
            raise ReachError(self._words(exc)) from None

    # ── the interface ───────────────────────────────────────────────────────────────────────────────────────────────

    def _full(self, rel: str) -> str:
        return self.settings.root + (rel or "").lstrip("/")

    def _rel(self, key: str) -> str:
        root = self.settings.root
        return key[len(root):] if root and key.startswith(root) else key

    def list(self, prefix: str = "", *, limit: int | None = None) -> Listing:
        """One "directory": its objects and its sub-folder prefixes, paginated (one call per 1,000 keys). `limit` stops
        paginating once that many objects and folders are in hand (the walk's entry cap); `truncated` is then set."""
        rel = (prefix or "").lstrip("/")
        if rel and not rel.endswith("/"):
            rel += "/"
        full = self._full(rel)
        out = Listing()
        token = None
        while True:
            kw = {"Bucket": self.settings.bucket, "Prefix": full, "Delimiter": "/", "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            page = self._call("list_objects_v2", **kw)
            out.calls += 1
            for p in page.get("CommonPrefixes") or []:
                out.folders.append(self._rel(p.get("Prefix") or ""))
            for o in page.get("Contents") or []:
                key = o.get("Key") or ""
                if key == full or key.endswith("/"):
                    continue        # a zero-byte "folder marker" some tools write: the prefix is the folder
                out.entries.append(Entry(key=self._rel(key), size=int(o.get("Size") or 0),
                                         modified_at=_iso(o.get("LastModified")), etag=_etag(o.get("ETag"))))
            if limit is not None and len(out.entries) + len(out.folders) >= limit and page.get("IsTruncated"):
                out.truncated = True
                break
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
            if not token:
                break
        return out

    def stat(self, key: str) -> Entry:
        head = self._call("head_object", Bucket=self.settings.bucket, Key=self._full(key))
        return Entry(key=key, size=int(head.get("ContentLength") or 0), modified_at=_iso(head.get("LastModified")),
                     etag=_etag(head.get("ETag")))

    def read(self, key: str, max_bytes: int, *, tail: bool = False) -> bytes:
        """At most `max_bytes` of the object: the first ones, or with `tail` the LAST ones (a Parquet footer)."""
        if max_bytes <= 0:
            return b""
        rng = f"bytes=-{max_bytes}" if tail else f"bytes=0-{max_bytes - 1}"
        got = self._call("get_object", Bucket=self.settings.bucket, Key=self._full(key), Range=rng)
        body = got["Body"]
        data = body.read(max_bytes)
        body.close()
        self.bytes_read += len(data)
        return data

    def read_range(self, key: str, start: int, length: int) -> bytes:
        """`length` bytes from `start` (a Parquet column chunk)."""
        if length <= 0:
            return b""
        got = self._call("get_object", Bucket=self.settings.bucket, Key=self._full(key),
                         Range=f"bytes={start}-{start + length - 1}")
        data = got["Body"].read(length)
        self.bytes_read += len(data)
        return data

    # ── the reach check ─────────────────────────────────────────────────────────────────────────────────────────────

    def check_reach(self) -> dict:
        """One ListObjectsV2 with MaxKeys=1 under the root: the outcome-3 sentence, or the refusal in S3's words."""
        s = self.settings
        at = datetime.now(timezone.utc)
        base = {"protocol": PROTOCOL, "host": re_host(), "credential_name": s.credential_name(),
                "location": s.location, "endpoint_url": s.endpoint_url or "AWS", "checked_at": at.isoformat(),
                "write_permission": WRITE_NOT_CHECKED}
        try:
            self._call("list_objects_v2", Bucket=s.bucket, Prefix=s.root, MaxKeys=1)
        except ReachError as exc:
            return {**base, "reachable": False, "error": str(exc),
                    "sentence": unreachable_sentence(base["host"], base["credential_name"], str(exc), at.isoformat())}
        return {**base, "reachable": True, "error": "",
                "sentence": reach_sentence(base["host"], base["credential_name"], at.isoformat())}


def _stamp(iso: str) -> str:
    """"11-02 17:10" from an ISO time (MM-DD HH:MM, as the brief writes it)."""
    return f"{iso[5:10]} {iso[11:16]}" if len(iso or "") >= 16 else (iso or "")


def reach_sentence(host: str, credential_name: str, checked_at: str) -> str:
    return (f"reachable by Resource Explorer over S3 from {host} as {credential_name} · {EGERIA_NO_REACH} · "
            f"checked {_stamp(checked_at)}")


def unreachable_sentence(host: str, credential_name: str, why: str, checked_at: str) -> str:
    return (f"Resource Explorer could not reach this bucket over S3 from {host} as {credential_name} · {why} · "
            f"checked {_stamp(checked_at)}")


def publish_sentence(host: str, credential_name: str) -> str:
    """The words on RE's own SurveyReport for an S3 walk (D-S3-6)."""
    return f"surveyed by Resource Explorer · over S3 from {host} as {credential_name} · {EGERIA_NO_REACH}"
