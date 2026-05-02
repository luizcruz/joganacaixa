import pytest
from pathlib import Path

from joganacaixa.compression import Algorithm, compress, extract, list_contents


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hello world")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "data.csv").write_text("a,b,c\n1,2,3")
    return tmp_path


@pytest.mark.parametrize("alg", list(Algorithm))
def test_roundtrip(sample_dir: Path, tmp_path: Path, alg: Algorithm) -> None:
    archive = compress(sample_dir, tmp_path / "pkg", alg)
    assert archive.exists()
    assert archive.stat().st_size > 0

    out = tmp_path / "out"
    extract(archive, out)
    assert (out / "hello.txt").read_text() == "hello world"
    assert (out / "sub" / "data.csv").read_text() == "a,b,c\n1,2,3"


@pytest.mark.parametrize("alg", list(Algorithm))
def test_list_contents(sample_dir: Path, tmp_path: Path, alg: Algorithm) -> None:
    archive = compress(sample_dir, tmp_path / "pkg", alg)
    files = list_contents(archive)
    names = " ".join(files)
    assert "hello.txt" in names
    assert "data.csv" in names


def test_exclude_patterns(sample_dir: Path, tmp_path: Path) -> None:
    archive = compress(sample_dir, tmp_path / "pkg", Algorithm.GZIP, exclude_patterns=["*.csv"])
    files = list_contents(archive)
    names = " ".join(files)
    assert "hello.txt" in names
    assert "data.csv" not in names


def test_archive_suffix(tmp_path: Path, sample_dir: Path) -> None:
    for alg in Algorithm:
        archive = compress(sample_dir, tmp_path / f"pkg_{alg.value}", alg)
        assert archive.name.endswith(f".tar.{alg.value}")
