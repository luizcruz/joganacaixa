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


def test_upload_stream_accepts_generator(backend: LocalBackend) -> None:
    """resumable.py feeds a generator of byte chunks, not a file object."""
    def chunks():
        yield b"hello "
        yield b"world"

    loc = backend.upload_stream(chunks(), "gen.bin")
    assert loc.startswith("local://")
    dest = backend.root / "gen.bin"
    assert dest.read_bytes() == b"hello world"


def test_upload_stream_accepts_file_object(backend: LocalBackend, sample_file: Path) -> None:
    with open(sample_file, "rb") as fh:
        backend.upload_stream(fh, "fromfile.bin")
    assert (backend.root / "fromfile.bin").read_bytes() == b"fake-archive-bytes"


def test_download_stream_offset_resumes(backend: LocalBackend) -> None:
    backend.upload_stream((c for c in [b"hello world"]), "off.bin")
    stream = backend.download_stream("off.bin", offset=6)
    try:
        assert stream.read() == b"world"
    finally:
        stream.close()
