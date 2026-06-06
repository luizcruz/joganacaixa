import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


class _IterReader:
    """Wraps a chunk iterator and provides a file-like read() interface."""

    def __init__(self, iterator):
        self._iterator = iterator
        self._buffer = b""
        self._done = False

    def read(self, size: int = -1):
        if size == -1:
            chunks = [self._buffer]
            for chunk in self._iterator:
                chunks.append(chunk)
            self._buffer = b""
            self._done = True
            return b"".join(chunks)

        while not self._done and len(self._buffer) < size:
            try:
                self._buffer += next(self._iterator)
            except StopIteration:
                self._done = True
                break

        result = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return result

    def readable(self) -> bool:
        return True


class StorageBackend(ABC):
    name: str

    @abstractmethod
    def upload(self, local_path: Path, key: str) -> str:
        """Upload file and return its remote URI."""

    @abstractmethod
    def download(self, key: str, local_path: Path) -> None:
        """Download file to local_path."""

    @abstractmethod
    def list_packages(self) -> list[str]:
        """Return list of stored package keys."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a package by key."""

    def upload_stream(self, data, key: str) -> str:
        """Upload from an iterable of bytes chunks or a file-like object.

        Default: collect into a temp file then call upload().
        """
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
            if hasattr(data, "read"):
                while True:
                    chunk = data.read(65536)
                    if not chunk:
                        break
                    tmp.write(chunk)
            else:
                for chunk in data:
                    tmp.write(chunk)
        try:
            return self.upload(tmp_path, key)
        finally:
            tmp_path.unlink(missing_ok=True)

    def download_stream(self, key: str, offset: int = 0):
        """Download *key* and yield bytes chunks.

        If *offset* > 0, skip the first *offset* bytes (resume support).
        Default implementation downloads to a temp file.
        """
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
            tmp_path = Path(tmp.name)
        self.download(key, tmp_path)
        fh = open(tmp_path, "rb")
        if offset > 0:
            fh.seek(offset)

        def _iter():
            try:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                fh.close()
                tmp_path.unlink(missing_ok=True)

        return _iter()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
