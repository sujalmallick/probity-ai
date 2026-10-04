"""Upload storage: local disk (dev) or any S3-compatible bucket (AWS S3, Cloudflare R2, MinIO).

Keys are prefixed by workspace (Security.md §3). Files are content-addressed by SHA-256. The API and the
worker both read through this module, so production must use S3 (the API and worker are separate services).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from probity.config import get_settings


def _key(workspace_id: str, sha256: str) -> str:
    return f"{workspace_id}/{sha256}"


@lru_cache
def _s3():  # type: ignore[no-untyped-def]
    import boto3

    st = get_settings()
    return boto3.client(
        "s3", endpoint_url=st.s3_endpoint_url, region_name=st.s3_region,
        aws_access_key_id=st.s3_access_key_id, aws_secret_access_key=st.s3_secret_access_key,
    )


def put(workspace_id: str, sha256: str, data: bytes, mime: str) -> str:
    st = get_settings()
    key = _key(workspace_id, sha256)
    if st.storage_backend == "s3":
        _s3().put_object(Bucket=st.s3_bucket, Key=key, Body=data, ContentType=mime, ServerSideEncryption="AES256")
        return f"s3://{st.s3_bucket}/{key}"
    path = Path(st.storage_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"local:{path}"


def get(ref: str) -> bytes:
    if ref.startswith("s3://"):
        bucket, key = ref[5:].split("/", 1)
        return _s3().get_object(Bucket=bucket, Key=key)["Body"].read()
    path = ref.removeprefix("local:")
    return Path(path).read_bytes()


def delete(ref: str) -> None:
    """Remove a stored object (used by the integration check's round trip)."""
    if ref.startswith("s3://"):
        bucket, key = ref[5:].split("/", 1)
        _s3().delete_object(Bucket=bucket, Key=key)
        return
    Path(ref.removeprefix("local:")).unlink(missing_ok=True)
