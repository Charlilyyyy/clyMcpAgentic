"""Real aioboto3-backed S3 adapter (lazily imported)."""

from __future__ import annotations

from typing import Any


class Aioboto3Backend:
    def __init__(self, endpoint_url: str | None = None) -> None:
        self._endpoint_url = endpoint_url
        self._session: Any = None

    def _get_session(self):
        if self._session is None:
            import aioboto3

            self._session = aioboto3.Session()
        return self._session

    async def get_object(self, bucket: str, key: str, max_bytes: int) -> bytes:
        async with self._get_session().client("s3", endpoint_url=self._endpoint_url) as s3:
            resp = await s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max_bytes - 1}")
            return await resp["Body"].read()

    async def put_object(
        self, bucket: str, key: str, body: bytes, content_type: str, metadata: dict[str, str]
    ) -> None:
        async with self._get_session().client("s3", endpoint_url=self._endpoint_url) as s3:
            await s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                Metadata=metadata,
            )
