from pathlib import Path

from .base import StorageBackend

# Maps friendly names to GCS storage class identifiers
_STORAGE_CLASSES = {
    "standard": "STANDARD",
    "nearline": "NEARLINE",
    "coldline": "COLDLINE",
    "archive": "ARCHIVE",  # cheapest, highest retrieval cost — equivalent to Glacier
}


class GCSBackend(StorageBackend):
    def __init__(
        self,
        bucket: str,
        region: str = "southamerica-east1",
        storage_class: str = "standard",
        prefix: str = "",
    ) -> None:
        from google.cloud import storage

        self.name = f"gs://{bucket}"
        self.bucket_name = bucket
        self.prefix = prefix
        self.storage_class = _STORAGE_CLASSES.get(storage_class, "STANDARD")
        self._client = storage.Client()
        self._bucket = self._ensure_bucket(region)

    def _ensure_bucket(self, region: str):
        from google.cloud.exceptions import Conflict, NotFound

        try:
            return self._client.get_bucket(self.bucket_name)
        except NotFound:
            b = self._client.bucket(self.bucket_name)
            b.storage_class = self.storage_class
            try:
                return self._client.create_bucket(b, location=region)
            except Conflict:
                return self._client.get_bucket(self.bucket_name)

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}" if self.prefix else key

    def upload(self, local_path: Path, key: str) -> str:
        blob = self._bucket.blob(self._key(key))
        blob.storage_class = self.storage_class
        blob.upload_from_filename(str(local_path))
        return f"gs://{self.bucket_name}/{self._key(key)}"

    def download(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(self._key(key)).download_to_filename(str(local_path))

    def list_packages(self) -> list[str]:
        keys = []
        for blob in self._client.list_blobs(self.bucket_name, prefix=self.prefix):
            k = blob.name
            if self.prefix:
                k = k[len(self.prefix):]
            keys.append(k)
        return keys

    def delete(self, key: str) -> None:
        self._bucket.blob(self._key(key)).delete()
