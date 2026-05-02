from abc import ABC, abstractmethod
from pathlib import Path


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

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
