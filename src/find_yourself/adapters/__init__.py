"""Adapters: object storage and external integrations (Runtime shard).

Local artifact storage is authed and lives under a controlled directory.
S3-compatible storage is private and exposed only through short-lived
pre-signed GET URLs; buckets are never public. When no S3 endpoint (and no
MinIO) is configured the adapter reports itself unavailable rather than
claiming an S3 integration.
"""
