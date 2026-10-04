"""Independent retention store for audit anchors (FROZEN_CONTRACT §3.1, §9).

Why this module exists
----------------------
The audit chain lives in the primary database. An anchor is a *second,
independent* copy of the chain head, kept outside that database. This split is
the whole point: an attacker who can rewrite the chain in the primary DB must
also rewrite the anchor in this store, which requires a second, separate write
grant. Anchors written back into the primary DB provide zero tamper evidence,
because the same attacker rewrites both and verification still passes.

The FROZEN_CONTRACT (§3.1) requires ``audit_anchors`` to be an *independent*
anchor, and §9 requires that "the independent anchor must be written to a
retention location different from the primary database".

Honest threat model
-------------------
Migration out of the primary DB raises the bar; it does not make the chain
tamper-proof. Specifically:

- The hash chain only yields *post-hoc* tamper evidence. A principal with full
  primary-DB write access can rewrite the chain and its head.
- With :class:`FileAnchorStore`, the anchor directory is normally on the same
  host as the database. A principal with host-level write access (e.g. ``root``,
  or the DB service account) can rewrite both. **This is not absolute
  tamper-proofing.** Deployments that need a real second trust boundary should
  use WORM / object-lock storage, a separate retention account, or an
  independent instance — see :class:`S3AnchorStore`.
- Anchors deliberately contain no private source text: only sequence numbers,
  hashes and provenance metadata (FROZEN_CONTRACT §9).

Implementations
---------------
:class:`FileAnchorStore` — append-only JSON files under ``~/.find-yourself/anchors/``
(override with ``FY_ANCHOR_DIR``). Never overwrites an existing anchor.
:class:`S3AnchorStore` — append-only objects in an S3-compatible bucket.
:class:`NullAnchorStore` — logs and discards; for tests and explicit degradation.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from ..db.types import utcnow

log = logging.getLogger(__name__)

DEFAULT_ANCHOR_DIR = "~/.find-yourself/anchors"

#: Evidence marker recorded by the 0010 migration when it retires the in-DB
#: ``audit_anchors`` table. Reused here so the two agree on one vocabulary.
RETIREMENT_EVIDENCE_KEY = "r39_retirement"


def anchor_key(seq: int) -> str:
    """Zero-padded, lexicographically sortable object/file name for ``seq``."""
    return f"anchor-{seq:012d}.json"


def _is_missing_bucket(exc: Exception) -> bool:
    """True when an S3 error means 'bucket does not exist'."""
    response = getattr(exc, "response", None) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return code in {"404", "NoSuchBucket", "NotFound"}


@dataclass
class AnchorRecord:
    """One anchor: the chain head as observed at a point in time.

    ``storage`` is a locator for where this record physically lives, e.g.
    ``file:/srv/fy/anchors`` or ``s3:my-bucket/anchors``. It is descriptive
    metadata, not an enforcement mechanism.
    """

    seq: int
    head_hash: str
    storage: str
    evidence: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def to_json(self) -> str:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "AnchorRecord":
        data = json.loads(raw)
        created = data.get("created_at")
        if isinstance(created, str):
            data["created_at"] = datetime.fromisoformat(created)
        return cls(
            seq=int(data["seq"]),
            head_hash=str(data["head_hash"]),
            storage=str(data.get("storage", "")),
            evidence=data.get("evidence") or {},
            created_at=data["created_at"],
        )


@runtime_checkable
class AnchorStore(Protocol):
    """Retention store for audit anchors, independent of the primary DB."""

    def put(self, anchor: AnchorRecord) -> None:
        """Persist ``anchor``. Must never overwrite an existing anchor."""
        ...

    def latest(self) -> Optional[AnchorRecord]:
        """Return the highest-sequence anchor, or ``None`` if none was written."""
        ...


class FileAnchorStore:
    """Append-only anchor files on the local filesystem.

    Each anchor is one JSON file named by zero-padded sequence, so directory
    order matches chain order. Writes use ``O_EXCL``: re-anchoring an existing
    sequence raises instead of clobbering history.

    The directory defaults to ``~/.find-yourself/anchors/`` and is overridden by
    the ``FY_ANCHOR_DIR`` environment variable (matching the ``FY_`` prefix used
    by :class:`find_yourself.config.Settings`).

    Not absolute tamper-proofing: see the module docstring. For a real second
    trust boundary use :class:`S3AnchorStore` with a separate account/bucket.
    """

    def __init__(self, directory: Optional[str | Path] = None):
        raw = directory or os.environ.get("FY_ANCHOR_DIR") or DEFAULT_ANCHOR_DIR
        self.directory = Path(raw).expanduser().resolve()

    def location(self) -> str:
        return f"file:{self.directory.as_posix()}"

    def put(self, anchor: AnchorRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / anchor_key(anchor.seq)
        payload = anchor.to_json().encode("utf-8")
        try:
            # O_EXCL keeps this append-only: an existing anchor is never
            # overwritten, so post-hoc anchor rewriting is visible as a failure
            # rather than silently changing history.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise FileExistsError(
                f"anchor seq={anchor.seq} already exists at {path}; "
                "anchors are append-only and are never overwritten"
            )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        log.info("audit anchor written seq=%s storage=%s", anchor.seq, self.location())

    def latest(self) -> Optional[AnchorRecord]:
        if not self.directory.is_dir():
            return None
        latest: Optional[AnchorRecord] = None
        for path in self.directory.glob("anchor-*.json"):
            try:
                record = AnchorRecord.from_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, KeyError) as exc:
                log.warning("skipping unreadable anchor %s: %s", path, exc)
                continue
            if latest is None or record.seq > latest.seq:
                latest = record
        return latest


class S3AnchorStore:
    """Append-only anchors in an S3-compatible bucket (private, SigV4).

    Reuses the project's existing S3 posture: private buckets, SigV4 signing,
    path-style addressing — the same client configuration as
    :class:`find_yourself.adapters.artifacts.S3ArtifactStore`, so it talks to the
    in-repo S3 service (``find_yourself.adapters.s3_service``) as well as MinIO
    and AWS.

    Keys are ``anchors/anchor-<seq>.json``; ``latest()`` lists the prefix and
    takes the highest sequence. Writes are refused when the key already exists
    (``IfNoneMatch="*"`` semantics via a pre-flight ``head_object``), preserving
    append-only history.

    Enable bucket versioning plus object lock (WORM) to get storage-side
    immutability — that is the configuration in which this store provides a
    genuine second trust boundary rather than a second writable copy.
    """

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        prefix: str = "anchors",
    ):
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.prefix = prefix.strip("/")

    def _client(self):
        if not self.bucket:
            raise RuntimeError("S3AnchorStore requires a bucket name")
        import boto3
        from botocore.client import Config

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url or None,
            region_name=self.region,
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )

    def _key(self, seq: int) -> str:
        return f"{self.prefix}/{anchor_key(seq)}" if self.prefix else anchor_key(seq)

    def location(self) -> str:
        return f"s3:{self.bucket}/{self.prefix}"

    def put(self, anchor: AnchorRecord) -> None:
        client = self._client()
        try:
            client.head_bucket(Bucket=self.bucket)
        except Exception:
            client.create_bucket(Bucket=self.bucket)
        key = self._key(anchor.seq)
        try:
            client.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            pass
        else:
            raise FileExistsError(
                f"anchor seq={anchor.seq} already exists at s3://{self.bucket}/{key}; "
                "anchors are append-only and are never overwritten"
            )
        client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=anchor.to_json().encode("utf-8"),
            ContentType="application/json",
        )
        log.info("audit anchor written seq=%s storage=%s", anchor.seq, self.location())

    def latest(self) -> Optional[AnchorRecord]:
        client = self._client()
        latest: Optional[AnchorRecord] = None
        token: Optional[str] = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": f"{self.prefix}/" if self.prefix else ""}
            if token:
                kwargs["ContinuationToken"] = token
            try:
                resp = client.list_objects_v2(**kwargs)
            except Exception as exc:
                # A missing bucket means "no anchor was ever written", which is
                # not an error — mirrors FileAnchorStore.latest() on an absent
                # directory. Anything else is a real storage failure.
                if _is_missing_bucket(exc):
                    return None
                raise
            for obj in resp.get("Contents", []) or []:
                name = str(obj["Key"]).rsplit("/", 1)[-1]
                if not name.startswith("anchor-") or not name.endswith(".json"):
                    continue
                try:
                    seq = int(name[len("anchor-") : -len(".json")])
                except ValueError:
                    continue
                if latest is None or seq > latest.seq:
                    body = client.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
                    latest = AnchorRecord.from_json(body.decode("utf-8"))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return latest


class NullAnchorStore:
    """Discards anchors; ``latest()`` always returns ``None``.

    For tests and for explicitly degraded deployments. Using this store means
    **no** anchor-based tamper evidence exists — :meth:`AuditService.verify`
    reports ``anchored=False`` rather than pretending the chain was anchored.
    """

    def location(self) -> str:
        return "null:disabled"

    def put(self, anchor: AnchorRecord) -> None:
        log.info("NullAnchorStore discarding anchor seq=%s", anchor.seq)

    def latest(self) -> Optional[AnchorRecord]:
        return None
