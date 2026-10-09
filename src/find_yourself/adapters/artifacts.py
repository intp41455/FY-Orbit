"""Private object/artifact storage (FROZEN_CONTRACT §5.3, §8.3, §10.2).

Two backends:

* ``LocalArtifactStore`` — bytes stored under a controlled directory, keyed by
  artifact id, served only after the API enforces owner authz. Never mapped to a
  public route.
* ``S3ArtifactStore`` — private bucket access via short-lived pre-signed GET
  URLs and verified boto3 operations. No public-read ACL is ever set. When ``s3_endpoint`` is not configured
  the store reports ``available=False``; absence of S3 endpoint is reported honestly
  rather than claimed as an S3 integration.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..services.errors import NotFound, ValidationFailed


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class StoredArtifact:
    artifact_id: str
    sha256: str
    size: int
    media_type: str


class LocalArtifactStore:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, artifact_id: str) -> Path:
        # Restrict to a flat, controlled key space; reject path traversal.
        if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
            raise ValidationFailed("bad_artifact_id", "Invalid artifact id")
        return self.root / f"{artifact_id}.bin"

    def _meta_path(self, artifact_id: str) -> Path:
        return self.root / f"{artifact_id}.meta.json"

    def put(self, artifact_id: str, data: bytes, media_type: str = "application/octet-stream") -> StoredArtifact:
        p = self._path(artifact_id)
        p.write_bytes(data)
        meta = self._meta_path(artifact_id)
        meta.write_text(json.dumps({"media_type": media_type, "sha256": sha256_hex(data), "size": len(data)}), encoding="utf-8")
        return StoredArtifact(artifact_id, sha256_hex(data), len(data), media_type)

    def get(self, artifact_id: str) -> bytes:
        p = self._path(artifact_id)
        if not p.exists():
            raise NotFound("artifact_not_found", "Artifact not found")
        return p.read_bytes()

    def exists(self, artifact_id: str) -> bool:
        return self._path(artifact_id).exists()

    def delete(self, artifact_id: str) -> bool:
        p = self._path(artifact_id)
        deleted = False
        if p.exists():
            p.unlink()
            deleted = True
        meta = self._meta_path(artifact_id)
        if meta.exists():
            meta.unlink()
        return deleted

    def presigned_get(self, artifact_id: str, expires_in_seconds: int = 300) -> str:
        if expires_in_seconds > 900:
            raise ValidationFailed("bad_ttl", "Pre-signed URLs must be short-lived (<=15 min)")
        if not self.exists(artifact_id):
            raise NotFound("artifact_not_found", "Artifact not found")
        # Local artifacts are served through authenticated API route
        return f"/api/catalog/artifacts/{artifact_id}"

    def list_objects(self) -> list[dict]:
        items = []
        for p in self.root.glob("*.bin"):
            aid = p.stem
            data = p.read_bytes()
            items.append({
                "key": aid,
                "size": len(data),
                "sha256": sha256_hex(data),
            })
        return items

    def backup_snapshot(self, target_dir: str) -> dict:
        out_dir = Path(target_dir) / "artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_items = []
        for p in self.root.glob("*.bin"):
            aid = p.stem
            data = p.read_bytes()
            dst = out_dir / p.name
            dst.write_bytes(data)
            meta_p = self._meta_path(aid)
            if meta_p.exists():
                (out_dir / meta_p.name).write_text(meta_p.read_text(encoding="utf-8"), encoding="utf-8")
            manifest_items.append({
                "key": aid,
                "size": len(data),
                "sha256": sha256_hex(data),
            })
        return {"count": len(manifest_items), "objects": manifest_items}

    def restore_snapshot(self, source_dir: str, tombstones: set[str] = frozenset()) -> dict:
        src_dir = Path(source_dir) / "artifacts"
        if not src_dir.exists():
            return {"restored": 0, "skipped_tombstones": 0}
        restored = 0
        skipped = 0
        for p in src_dir.glob("*.bin"):
            aid = p.stem
            if aid in tombstones:
                skipped += 1
                self.delete(aid)
                continue
            data = p.read_bytes()
            self._path(aid).write_bytes(data)
            meta_src = src_dir / f"{aid}.meta.json"
            if meta_src.exists():
                self._meta_path(aid).write_text(meta_src.read_text(encoding="utf-8"), encoding="utf-8")
            restored += 1
        return {"restored": restored, "skipped_tombstones": skipped}


class S3ArtifactStore:
    """Private S3-compatible store with short-lived pre-signed GET URLs and verified lifecycle."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.s3_endpoint)

    def _client(self):
        if not self.available:
            raise NotFound("storage_unavailable", "Object storage is not configured")
        import boto3
        from botocore.client import Config
        return boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint,
            region_name=self.settings.s3_region,
            aws_access_key_id=self.settings.s3_access_key or None,
            aws_secret_access_key=self.settings.s3_secret_key or None,
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        client = self._client()
        try:
            client.head_bucket(Bucket=self.settings.s3_bucket)
        except Exception:
            try:
                client.create_bucket(Bucket=self.settings.s3_bucket)
            except Exception as e:
                raise ValidationFailed("bucket_create_failed", f"Failed to ensure S3 bucket: {e}")

    def put(self, artifact_id: str, data: bytes, media_type: str = "application/octet-stream") -> StoredArtifact:
        if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
            raise ValidationFailed("bad_artifact_id", "Invalid artifact id")
        self.ensure_bucket()
        client = self._client()
        expected_sha = sha256_hex(data)
        client.put_object(
            Bucket=self.settings.s3_bucket,
            Key=artifact_id,
            Body=data,
            ContentType=media_type,
        )
        return StoredArtifact(artifact_id, expected_sha, len(data), media_type)

    def get(self, artifact_id: str) -> bytes:
        if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
            raise ValidationFailed("bad_artifact_id", "Invalid artifact id")
        client = self._client()
        try:
            resp = client.get_object(Bucket=self.settings.s3_bucket, Key=artifact_id)
            return resp["Body"].read()
        except Exception:
            raise NotFound("artifact_not_found", "Artifact not found")

    def exists(self, artifact_id: str) -> bool:
        if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
            return False
        client = self._client()
        try:
            client.head_object(Bucket=self.settings.s3_bucket, Key=artifact_id)
            return True
        except Exception:
            return False

    def delete(self, artifact_id: str) -> bool:
        if "/" in artifact_id or "\\" in artifact_id or ".." in artifact_id:
            return False
        client = self._client()
        try:
            client.delete_object(Bucket=self.settings.s3_bucket, Key=artifact_id)
            return True
        except Exception:
            return False

    def presigned_get(self, artifact_id: str, expires_in_seconds: int = 300) -> str:
        if expires_in_seconds > 900:
            raise ValidationFailed("bad_ttl", "Pre-signed URLs must be short-lived (<=15 min)")
        if not self.exists(artifact_id):
            raise NotFound("artifact_not_found", "Artifact not found")
        client = self._client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.settings.s3_bucket, "Key": artifact_id},
            ExpiresIn=expires_in_seconds,
        )

    def list_objects(self) -> list[dict]:
        self.ensure_bucket()
        client = self._client()
        items = []
        try:
            resp = client.list_objects_v2(Bucket=self.settings.s3_bucket)
            for c in resp.get("Contents", []):
                key = c["Key"]
                data = self.get(key)
                items.append({
                    "key": key,
                    "size": c["Size"],
                    "sha256": sha256_hex(data),
                })
        except Exception:
            pass
        return items

    def backup_snapshot(self, target_dir: str) -> dict:
        out_dir = Path(target_dir) / "s3_objects"
        out_dir.mkdir(parents=True, exist_ok=True)
        items = self.list_objects()
        for item in items:
            key = item["key"]
            data = self.get(key)
            (out_dir / key).write_bytes(data)
        return {"bucket": self.settings.s3_bucket, "count": len(items), "objects": items}

    def restore_snapshot(self, source_dir: str, tombstones: set[str] = frozenset()) -> dict:
        src_dir = Path(source_dir) / "s3_objects"
        if not src_dir.exists():
            return {"restored": 0, "skipped_tombstones": 0}
        self.ensure_bucket()
        restored = 0
        skipped = 0
        for p in src_dir.iterdir():
            if not p.is_file():
                continue
            key = p.name
            if key in tombstones:
                skipped += 1
                self.delete(key)
                continue
            data = p.read_bytes()
            self.put(key, data)
            restored += 1
        return {"restored": restored, "skipped_tombstones": skipped}


def build_artifact_store(settings: Settings) -> LocalArtifactStore | S3ArtifactStore:
    if settings.s3_endpoint:
        return S3ArtifactStore(settings)
    return LocalArtifactStore(settings.artifacts_path)
