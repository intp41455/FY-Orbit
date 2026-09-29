"""Private object/artifact storage (FROZEN_CONTRACT §5.3, §8.3, §10.2).

Two backends:

* ``LocalArtifactStore`` — bytes stored under a controlled directory, keyed by
  artifact id, served only after the API enforces owner authz. Never mapped to a
  public route.
* ``S3ArtifactStore`` — private bucket access via short-lived pre-signed GET
  URLs. No public-read ACL is ever set. When ``s3_endpoint`` is not configured
  the store reports ``available=False``; absence of MinIO is reported honestly
  rather than claimed as an S3 integration.
"""

from __future__ import annotations

import hashlib
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

    def put(self, artifact_id: str, data: bytes, media_type: str = "application/octet-stream") -> StoredArtifact:
        p = self._path(artifact_id)
        p.write_bytes(data)
        return StoredArtifact(artifact_id, sha256_hex(data), len(data), media_type)

    def get(self, artifact_id: str) -> bytes:
        p = self._path(artifact_id)
        if not p.exists():
            raise NotFound("artifact_not_found", "Artifact not found")
        return p.read_bytes()

    def exists(self, artifact_id: str) -> bool:
        return self._path(artifact_id).exists()


class S3ArtifactStore:
    """Private S3-compatible store with short-lived pre-signed GET URLs."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.s3_endpoint)

    def _client(self):
        if not self.available:
            raise NotFound("storage_unavailable", "Object storage is not configured")
        import boto3  # local import: boto3 is only needed when S3 is configured
        return boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint,
            region_name=self.settings.s3_region,
            aws_access_key_id=self.settings.model_api_key or None,
            aws_secret_access_key=self.settings.model_base_url or None,
        )

    def presigned_get(self, artifact_id: str, expires_in_seconds: int = 300) -> str:
        if expires_in_seconds > 900:
            raise ValidationFailed("bad_ttl", "Pre-signed URLs must be short-lived (<=15 min)")
        client = self._client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.settings.s3_bucket, "Key": artifact_id},
            ExpiresIn=expires_in_seconds,
        )


def build_artifact_store(settings: Settings) -> LocalArtifactStore:
    return LocalArtifactStore(settings.artifacts_path)
