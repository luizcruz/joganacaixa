import pytest
from pathlib import Path

from joganacaixa.storage.local import LocalBackend


@pytest.fixture
def backend(tmp_path: Path) -> LocalBackend:
    return LocalBackend(root=str(tmp_path / "store"))


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    p = tmp_path / "archive.tar.gz"
    p.write_bytes(b"fake-archive-bytes")
    return p


def test_upload_and_download(backend: LocalBackend, sample_file: Path, tmp_path: Path) -> None:
    backend.upload(sample_file, "pkg/archive.tar.gz")
    dest = tmp_path / "recovered.tar.gz"
    backend.download("pkg/archive.tar.gz", dest)
    assert dest.read_bytes() == b"fake-archive-bytes"


def test_list(backend: LocalBackend, sample_file: Path) -> None:
    backend.upload(sample_file, "a.tar.gz")
    backend.upload(sample_file, "b.tar.gz")
    keys = backend.list_packages()
    assert "a.tar.gz" in keys
    assert "b.tar.gz" in keys


def test_delete(backend: LocalBackend, sample_file: Path) -> None:
    backend.upload(sample_file, "to_delete.tar.gz")
    assert "to_delete.tar.gz" in backend.list_packages()
    backend.delete("to_delete.tar.gz")
    assert "to_delete.tar.gz" not in backend.list_packages()


def test_upload_returns_uri(backend: LocalBackend, sample_file: Path) -> None:
    uri = backend.upload(sample_file, "test.tar.gz")
    assert uri.startswith("local://")
    assert "test.tar.gz" in uri


def test_download_missing_raises(backend: LocalBackend, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        backend.download("nonexistent.tar.gz", tmp_path / "out.tar.gz")
