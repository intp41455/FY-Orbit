"""Private S3-compatible object storage service (FROZEN_CONTRACT §5.3, §8.3, §10.2).

Provides an in-process and daemon S3-compatible REST API matching AWS S3 / MinIO protocol:
* SigV4 signature verification (both Authorization header and presigned GET query params).
* Strictly private buckets: unauthenticated or invalidly signed requests receive HTTP 403 AccessDenied.
* Pre-signed URL expiration enforcement: expired URLs receive HTTP 403.
* Bucket and object lifecycle: PUT, GET, HEAD, DELETE, ListObjectsV2.
* Local persistent storage directory with SHA-256 and MD5 integrity tracking.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import shutil
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape


def _build_canonical_query(params: dict[str, str], exclude_sig: bool = False) -> str:
    encoded = []
    for k, v in params.items():
        if exclude_sig and k == "X-Amz-Signature":
            continue
        ek = urllib.parse.quote(k, safe="-_.~")
        ev = urllib.parse.quote(v, safe="-_.~")
        encoded.append((ek, ev))
    encoded.sort(key=lambda x: x[0])
    return "&".join(f"{k}={v}" for k, v in encoded)

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region_name)
    k_service = _sign(k_region, service_name)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


def _build_error_xml(code: str, message: str, resource: str = "") -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Error>\n"
        f"  <Code>{escape(code)}</Code>\n"
        f"  <Message>{escape(message)}</Message>\n"
        f"  <Resource>{escape(resource)}</Resource>\n"
        f"</Error>"
    )


class S3ServiceApp:
    def __init__(
        self,
        storage_dir: str,
        access_key: str = "fy-minio",
        secret_key: str = "minio_dev_change_me_not_for_prod",
        region: str = "us-east-1",
    ):
        self.storage_root = Path(storage_dir).resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.app = FastAPI(title="Private S3 Compatible Service", docs_url=None, redoc_url=None)
        self._register_routes()

    def _bucket_path(self, bucket: str) -> Path:
        if "/" in bucket or "\\" in bucket or ".." in bucket:
            raise HTTPException(status_code=400, detail="Invalid bucket name")
        return self.storage_root / bucket

    def _object_path(self, bucket: str, key: str) -> Path:
        if ".." in key or key.startswith("/") or key.startswith("\\"):
            raise HTTPException(status_code=400, detail="Invalid object key")
        p = (self._bucket_path(bucket) / key).resolve()
        # Prevent traversal outside bucket directory
        if not str(p).startswith(str(self._bucket_path(bucket))):
            raise HTTPException(status_code=400, detail="Path traversal rejected")
        return p

    def _verify_auth(self, request: Request, body_bytes: bytes) -> None:
        """Verifies AWS SigV4 authentication. Rejects unauthenticated requests with 403."""
        auth_header = request.headers.get("authorization", "")
        params = dict(request.query_params)

        # 1. Header-based SigV4
        if auth_header.startswith("AWS4-HMAC-SHA256"):
            parts = dict(
                item.strip().split("=", 1)
                for item in auth_header[len("AWS4-HMAC-SHA256 ") :].split(",")
                if "=" in item
            )
            credential = parts.get("Credential", "")
            cred_parts = credential.split("/")
            if len(cred_parts) < 5:
                raise HTTPException(
                    status_code=403,
                    headers={"Content-Type": "application/xml"},
                    detail=_build_error_xml("AccessDenied", "Invalid Credential format", request.url.path),
                )
            req_access_key, date_stamp, req_region, service = cred_parts[0], cred_parts[1], cred_parts[2], cred_parts[3]
            if req_access_key != self.access_key:
                raise HTTPException(
                    status_code=403,
                    headers={"Content-Type": "application/xml"},
                    detail=_build_error_xml("InvalidAccessKeyId", "The access key ID provided does not exist", request.url.path),
                )
            # Signature check: calculate expected signature
            signature = parts.get("Signature", "")
            signed_headers_list = [h.strip().lower() for h in parts.get("SignedHeaders", "").split(";") if h.strip()]

            # Build canonical headers
            canonical_headers_parts = []
            for h in signed_headers_list:
                val = request.headers.get(h, "").strip()
                canonical_headers_parts.append(f"{h}:{val}\n")
            canonical_headers = "".join(canonical_headers_parts)
            signed_headers = ";".join(signed_headers_list)

            # Query string
            canonical_query = _build_canonical_query(params)

            # Payload hash
            payload_hash = request.headers.get("x-amz-content-sha256")
            if not payload_hash or payload_hash == "UNSIGNED-PAYLOAD":
                payload_hash = _sha256_hex(body_bytes)

            canonical_request = (
                f"{request.method}\n"
                f"{request.url.path}\n"
                f"{canonical_query}\n"
                f"{canonical_headers}\n"
                f"{signed_headers}\n"
                f"{payload_hash}"
            )

            amz_date = request.headers.get("x-amz-date", "")
            credential_scope = f"{date_stamp}/{req_region}/{service}/aws4_request"
            string_to_sign = (
                f"AWS4-HMAC-SHA256\n"
                f"{amz_date}\n"
                f"{credential_scope}\n"
                f"{_sha256_hex(canonical_request.encode('utf-8'))}"
            )

            signing_key = _get_signature_key(self.secret_key, date_stamp, req_region, service)
            expected_signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

            if not hmac.compare_digest(signature, expected_signature):
                raise HTTPException(
                    status_code=403,
                    headers={"Content-Type": "application/xml"},
                    detail=_build_error_xml("SignatureDoesNotMatch", "The request signature we calculated does not match", request.url.path),
                )
            return

        # 2. Query-param based presigned URL (X-Amz-Algorithm=AWS4-HMAC-SHA256)
        if params.get("X-Amz-Algorithm") == "AWS4-HMAC-SHA256":
            cred = params.get("X-Amz-Credential", "")
            cred_parts = cred.split("/")
            if len(cred_parts) < 5:
                raise HTTPException(
                    status_code=403,
                    headers={"Content-Type": "application/xml"},
                    detail=_build_error_xml("AccessDenied", "Invalid Credential format in URL", request.url.path),
                )
            req_access_key, date_stamp, req_region, service = cred_parts[0], cred_parts[1], cred_parts[2], cred_parts[3]
            if req_access_key != self.access_key:
                raise HTTPException(
                    status_code=403,
                    headers={"Content-Type": "application/xml"},
                    detail=_build_error_xml("InvalidAccessKeyId", "The access key ID provided does not exist", request.url.path),
                )

            # Check expiration
            amz_date = params.get("X-Amz-Date", "")
            expires_sec = int(params.get("X-Amz-Expires", "300"))
            try:
                dt = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - dt).total_seconds()
                if age > expires_sec:
                    raise HTTPException(
                        status_code=403,
                        headers={"Content-Type": "application/xml"},
                        detail=_build_error_xml("RequestTimeTooSkewed", "The pre-signed URL has expired", request.url.path),
                    )
            except ValueError:
                raise HTTPException(
                    status_code=403,
                    headers={"Content-Type": "application/xml"},
                    detail=_build_error_xml("AccessDenied", "Invalid X-Amz-Date", request.url.path),
                )

            signature = params.get("X-Amz-Signature", "")
            signed_headers_list = [h.strip().lower() for h in params.get("X-Amz-SignedHeaders", "").split(";") if h.strip()]

            # Build canonical headers
            canonical_headers_parts = []
            for h in signed_headers_list:
                val = request.headers.get(h, "").strip()
                canonical_headers_parts.append(f"{h}:{val}\n")
            canonical_headers = "".join(canonical_headers_parts)
            signed_headers = ";".join(signed_headers_list)

            # Query params except signature
            canonical_query = _build_canonical_query(params, exclude_sig=True)

            payload_hash = "UNSIGNED-PAYLOAD"
            canonical_request = (
                f"{request.method}\n"
                f"{request.url.path}\n"
                f"{canonical_query}\n"
                f"{canonical_headers}\n"
                f"{signed_headers}\n"
                f"{payload_hash}"
            )

            credential_scope = f"{date_stamp}/{req_region}/{service}/aws4_request"
            string_to_sign = (
                f"AWS4-HMAC-SHA256\n"
                f"{amz_date}\n"
                f"{credential_scope}\n"
                f"{_sha256_hex(canonical_request.encode('utf-8'))}"
            )

            signing_key = _get_signature_key(self.secret_key, date_stamp, req_region, service)
            expected_signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

            if not hmac.compare_digest(signature, expected_signature):
                raise HTTPException(
                    status_code=403,
                    headers={"Content-Type": "application/xml"},
                    detail=_build_error_xml("SignatureDoesNotMatch", "Signature does not match", request.url.path),
                )
            return

        # 3. No auth at all: Private bucket enforcement!
        raise HTTPException(
            status_code=403,
            headers={"Content-Type": "application/xml"},
            detail=_build_error_xml("AccessDenied", "Private bucket: anonymous public access denied", request.url.path),
        )

    def _register_routes(self):
        @self.app.get("/minio/health/live")
        @self.app.get("/health/live")
        async def health():
            return {"status": "ok"}

        # Bucket operations
        @self.app.put("/{bucket}")
        async def create_bucket(bucket: str, request: Request):
            body = await request.body()
            self._verify_auth(request, body)
            bp = self._bucket_path(bucket)
            bp.mkdir(parents=True, exist_ok=True)
            return Response(status_code=200, headers={"Location": f"/{bucket}"})

        @self.app.head("/{bucket}")
        async def head_bucket(bucket: str, request: Request):
            self._verify_auth(request, b"")
            bp = self._bucket_path(bucket)
            if not bp.exists():
                return Response(status_code=404)
            return Response(status_code=200, headers={"x-amz-bucket-region": self.region})

        @self.app.get("/{bucket}")
        async def list_objects(bucket: str, request: Request):
            self._verify_auth(request, b"")
            bp = self._bucket_path(bucket)
            if not bp.exists():
                return Response(
                    status_code=404,
                    media_type="application/xml",
                    content=_build_error_xml("NoSuchBucket", "The specified bucket does not exist", bucket),
                )
            files = [p for p in bp.rglob("*") if p.is_file() and not p.name.endswith(".meta")]
            contents_xml = []
            for f in files:
                rel = f.relative_to(bp).as_posix()
                size = f.stat().st_size
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                etag = f'"{_sha256_hex(f.read_bytes())[:32]}"'
                contents_xml.append(
                    f"  <Contents>\n"
                    f"    <Key>{escape(rel)}</Key>\n"
                    f"    <LastModified>{mtime}</LastModified>\n"
                    f"    <ETag>{etag}</ETag>\n"
                    f"    <Size>{size}</Size>\n"
                    f"    <StorageClass>STANDARD</StorageClass>\n"
                    f"  </Contents>"
                )
            contents_str = "\n".join(contents_xml)
            xml = (
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">\n'
                f"  <Name>{escape(bucket)}</Name>\n"
                f"  <Prefix></Prefix>\n"
                f"  <KeyCount>{len(files)}</KeyCount>\n"
                f"  <MaxKeys>1000</MaxKeys>\n"
                f"  <IsTruncated>false</IsTruncated>\n"
                f"{contents_str}\n"
                f"</ListBucketResult>"
            )
            return Response(content=xml, media_type="application/xml")

        # Object operations
        @self.app.put("/{bucket}/{key:path}")
        async def put_object(bucket: str, key: str, request: Request):
            body = await request.body()
            self._verify_auth(request, body)
            bp = self._bucket_path(bucket)
            bp.mkdir(parents=True, exist_ok=True)
            op = self._object_path(bucket, key)
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_bytes(body)

            # Save media type meta
            ct = request.headers.get("content-type", "application/octet-stream")
            meta_path = op.with_suffix(op.suffix + ".meta")
            meta_path.write_text(ct, encoding="utf-8")

            etag = f'"{hashlib.md5(body).hexdigest()}"'
            return Response(
                status_code=200,
                headers={
                    "ETag": etag,
                    "x-amz-version-id": "null",
                },
            )

        @self.app.get("/{bucket}/{key:path}")
        async def get_object(bucket: str, key: str, request: Request):
            self._verify_auth(request, b"")
            op = self._object_path(bucket, key)
            if not op.exists() or not op.is_file():
                return Response(
                    status_code=404,
                    media_type="application/xml",
                    content=_build_error_xml("NoSuchKey", "The specified key does not exist", key),
                )
            data = op.read_bytes()
            meta_path = op.with_suffix(op.suffix + ".meta")
            ct = meta_path.read_text(encoding="utf-8") if meta_path.exists() else "application/octet-stream"
            etag = f'"{hashlib.md5(data).hexdigest()}"'
            return Response(
                content=data,
                media_type=ct,
                headers={
                    "Content-Length": str(len(data)),
                    "ETag": etag,
                    "Last-Modified": datetime.fromtimestamp(op.stat().st_mtime, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
                },
            )

        @self.app.head("/{bucket}/{key:path}")
        async def head_object(bucket: str, key: str, request: Request):
            self._verify_auth(request, b"")
            op = self._object_path(bucket, key)
            if not op.exists() or not op.is_file():
                return Response(status_code=404)
            size = op.stat().st_size
            meta_path = op.with_suffix(op.suffix + ".meta")
            ct = meta_path.read_text(encoding="utf-8") if meta_path.exists() else "application/octet-stream"
            return Response(
                status_code=200,
                headers={
                    "Content-Length": str(size),
                    "Content-Type": ct,
                    "Last-Modified": datetime.fromtimestamp(op.stat().st_mtime, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
                },
            )

        @self.app.delete("/{bucket}/{key:path}")
        async def delete_object(bucket: str, key: str, request: Request):
            self._verify_auth(request, b"")
            op = self._object_path(bucket, key)
            if op.exists():
                op.unlink()
            meta_path = op.with_suffix(op.suffix + ".meta")
            if meta_path.exists():
                meta_path.unlink()
            return Response(status_code=204)


def create_s3_app(
    storage_dir: str = ".runtime/s3_data",
    access_key: str = "fy-minio",
    secret_key: str = "minio_dev_change_me_not_for_prod",
    region: str = "us-east-1",
) -> FastAPI:
    return S3ServiceApp(storage_dir, access_key, secret_key, region).app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Find Yourself S3-Compatible Storage Service")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="Bind port (default: 9000)")
    parser.add_argument("--dir", default=".runtime/s3_data", help="Storage directory")
    args = parser.parse_args()

    app = create_s3_app(storage_dir=args.dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
