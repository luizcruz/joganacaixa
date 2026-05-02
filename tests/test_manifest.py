import pytest
from pathlib import Path

from joganacaixa.compression import Algorithm, compress
from joganacaixa.manifest import Manifest, build_manifest


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "readme.txt").write_text("test")
    (src / "data" ).mkdir()
    (src / "data" / "file.csv").write_text("x,y")
    return compress(src, tmp_path / "pkg", Algorithm.GZIP)


def test_build_and_save_load(tmp_path: Path, archive: Path) -> None:
    mdir = tmp_path / "manifests"
    m = build_manifest("42", archive, Algorithm.GZIP, ["s3://bucket/42.tar.gz"])
    m.save(mdir)

    loaded = Manifest.load("42", mdir)
    assert loaded.package_id == "42"
    assert loaded.algorithm == "gz"
    assert "s3://bucket/42.tar.gz" in loaded.locations
    assert any("readme.txt" in f for f in loaded.files)


def test_all(tmp_path: Path, archive: Path) -> None:
    mdir = tmp_path / "manifests"
    for pid in ["100", "200", "300"]:
        build_manifest(pid, archive, Algorithm.GZIP, []).save(mdir)

    manifests = Manifest.all(mdir)
    ids = [m.package_id for m in manifests]
    assert ids == ["100", "200", "300"]


def test_search(tmp_path: Path, archive: Path) -> None:
    mdir = tmp_path / "manifests"
    build_manifest("999", archive, Algorithm.GZIP, []).save(mdir)

    results = Manifest.search("file.csv", mdir)
    assert len(results) == 1
    manifest, matches = results[0]
    assert manifest.package_id == "999"
    assert any("file.csv" in m for m in matches)


def test_search_no_match(tmp_path: Path, archive: Path) -> None:
    mdir = tmp_path / "manifests"
    build_manifest("1", archive, Algorithm.GZIP, []).save(mdir)
    assert Manifest.search("nonexistent.xyz", mdir) == []


def test_missing_package_raises(tmp_path: Path) -> None:
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    with pytest.raises(FileNotFoundError):
        Manifest.load("missing", mdir)
