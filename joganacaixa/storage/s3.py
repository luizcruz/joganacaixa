from pathlib import Path

from .base import StorageBackend

_STORAGE_CLASSES = {
    "standard": "STANDARD",
    "glacier": "GLACIER",
    "deep_archive": "DEEP_ARCHIVE",
    "glacier_ir": "GLACIER_IR",
    "intelligent_tiering": "INTELLIGENT_TIERING",
}

try:
    from boto3.s3.transfer import TransferConfig
    _MULTIPART_CONFIG = TransferConfig(
        multipart_threshold=16 * 1024 * 1024,
        multipart_chunksize=16 * 1024 * 1024,
        max_concurrency=4,
    )
except ImportError:
    _MULTIPART_CONFIG = None  # type: ignore[assignment]


class S3Backend(StorageBackend):
    def __init__(
        self,
        bucket: str,
        region: str = "sa-east-1",
        storage_class: str = "standard",
        prefix: str = "",
    ) -> None:
        import boto3

        self.name = f"s3://{bucket}"
        self.bucket = bucket
        self._bucket = bucket
        self.prefix = prefix
        self.storage_class = _STORAGE_CLASSES.get(storage_class, "STANDARD")
        self._s3 = boto3.client("s3", region_name=region)
        self._ensure_bucket(region)

    def _ensure_bucket(self, region: str) -> None:
        try:
            self._s3.head_bucket(Bucket=self.bucket)
        except Exception:
            kwargs: dict = {"Bucket": self.bucket}
            if region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
            self._s3.create_bucket(**kwargs)

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}" if self.prefix else key

    def upload(self, local_path: Path, key: str) -> str:
        extra_args = {"StorageClass": self.storage_class}
        kwargs = dict(
            Filename=str(local_path),
            Bucket=self.bucket,
            Key=self._key(key),
            ExtraArgs=extra_args,
        )
        if _MULTIPART_CONFIG is not None:
            kwargs["Config"] = _MULTIPART_CONFIG
        self._s3.upload_file(**kwargs)
        return f"s3://{self.bucket}/{self._key(key)}"

    def upload_stream(self, fileobj, key: str) -> str:
        extra_args = {"StorageClass": self.storage_class}
        kwargs = dict(
            Fileobj=fileobj,
            Bucket=self.bucket,
            Key=self._key(key),
            ExtraArgs=extra_args,
        )
        if _MULTIPART_CONFIG is not None:
            kwargs["Config"] = _MULTIPART_CONFIG
        self._s3.upload_fileobj(**kwargs)
        return f"s3://{self.bucket}/{self._key(key)}"

    def download_stream(self, key: str):
        full_key = self._key(key)
        return self._s3.get_object(Bucket=self.bucket, Key=full_key)["Body"]

    def download(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self.bucket, self._key(key), str(local_path))

    def list_packages(self) -> list[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if self.prefix:
                    k = k[len(self.prefix):]
                keys.append(k)
        return keys

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=self._key(key))
