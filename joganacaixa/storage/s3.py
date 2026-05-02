from pathlib import Path

from .base import StorageBackend

# Maps friendly names to S3 storage class identifiers
_STORAGE_CLASSES = {
    "standard": "STANDARD",
    "glacier": "GLACIER",
    "deep_archive": "DEEP_ARCHIVE",
    "glacier_ir": "GLACIER_IR",
    "intelligent_tiering": "INTELLIGENT_TIERING",
}


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
        self._s3.upload_file(
            str(local_path),
            self.bucket,
            self._key(key),
            ExtraArgs={"StorageClass": self.storage_class},
        )
        return f"s3://{self.bucket}/{self._key(key)}"

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
