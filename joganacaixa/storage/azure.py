from pathlib import Path

from .base import StorageBackend, _IterReader


class AzureBackend(StorageBackend):
    def __init__(self, container: str, connection_string: str, prefix: str = "") -> None:
        from azure.storage.blob import BlobServiceClient

        self.name = f"azure://{container}"
        self.container = container
        self.prefix = prefix
        self._client = BlobServiceClient.from_connection_string(connection_string)
        self._ensure_container()

    def _ensure_container(self) -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._client.create_container(self.container)
        except ResourceExistsError:
            pass

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}" if self.prefix else key

    def upload(self, local_path: Path, key: str) -> str:
        blob = self._client.get_blob_client(container=self.container, blob=self._key(key))
        with open(local_path, "rb") as f:
            blob.upload_blob(f, overwrite=True)
        return f"azure://{self.container}/{self._key(key)}"

    def upload_stream(self, fileobj, key: str) -> str:
        blob_client = self._client.get_blob_client(container=self.container, blob=self._key(key))
        blob_client.upload_blob(fileobj, overwrite=True)
        return f"azure://{self.container}/{self._key(key)}"

    def download_stream(self, key: str):
        blob_client = self._client.get_blob_client(container=self.container, blob=self._key(key))
        downloader = blob_client.download_blob()
        return _IterReader(downloader.chunks())

    def download(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob = self._client.get_blob_client(container=self.container, blob=self._key(key))
        with open(local_path, "wb") as f:
            f.write(blob.download_blob().readall())

    def list_packages(self) -> list[str]:
        cc = self._client.get_container_client(self.container)
        keys = []
        for blob in cc.list_blobs(name_starts_with=self.prefix):
            k = blob.name
            if self.prefix:
                k = k[len(self.prefix):]
            keys.append(k)
        return keys

    def delete(self, key: str) -> None:
        self._client.get_blob_client(
            container=self.container, blob=self._key(key)
        ).delete_blob()
