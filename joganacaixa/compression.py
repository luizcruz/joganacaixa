import fnmatch
import tarfile
from enum import Enum
from pathlib import Path


class Algorithm(str, Enum):
    GZIP = "gz"
    BZIP2 = "bz2"
    XZ = "xz"
    ZSTD = "zst"


def compress(
    source: Path,
    dest: Path,
    algorithm: Algorithm = Algorithm.ZSTD,
    exclude_patterns: list[str] | None = None,
) -> Path:
    """Compress source into a .tar.<alg> archive, returning the archive path."""
    exclude_patterns = exclude_patterns or []
    suffix = f".tar.{algorithm.value}"
    if not str(dest).endswith(suffix):
        dest = Path(str(dest) + suffix)

    filter_fn = _make_exclude_filter(exclude_patterns) if exclude_patterns else None

    if algorithm == Algorithm.ZSTD:
        _compress_zstd(source, dest, filter_fn)
    else:
        with tarfile.open(dest, f"w:{algorithm.value}") as tar:
            tar.add(source, arcname=".", filter=filter_fn)

    return dest


def _compress_zstd(source: Path, dest: Path, filter_fn) -> None:
    import zstandard as zstd

    cctx = zstd.ZstdCompressor(level=10, threads=-1)
    with open(dest, "wb") as f:
        with cctx.stream_writer(f, closefd=False) as compressor:
            with tarfile.open(fileobj=compressor, mode="w|") as tar:
                tar.add(source, arcname=".", filter=filter_fn)


def list_contents(archive: Path) -> list[str]:
    """Return the list of member names inside an archive."""
    if str(archive).endswith(".tar.zst"):
        return _list_zstd(archive)
    with tarfile.open(archive) as tar:
        return tar.getnames()


def _list_zstd(archive: Path) -> list[str]:
    import zstandard as zstd

    dctx = zstd.ZstdDecompressor()
    with open(archive, "rb") as f:
        with dctx.stream_reader(f) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                return [m.name for m in tar.getmembers()]


def extract(archive: Path, dest: Path) -> None:
    """Extract archive into dest directory."""
    dest.mkdir(parents=True, exist_ok=True)
    if str(archive).endswith(".tar.zst"):
        _extract_zstd(archive, dest)
        return
    with tarfile.open(archive) as tar:
        tar.extractall(dest)


def _extract_zstd(archive: Path, dest: Path) -> None:
    import zstandard as zstd

    dctx = zstd.ZstdDecompressor()
    with open(archive, "rb") as f:
        with dctx.stream_reader(f) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                tar.extractall(dest)


def _make_exclude_filter(patterns: list[str]):
    def filter_fn(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = tarinfo.name
        basename = Path(name).name
        for pattern in patterns:
            if fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(name, pattern):
                return None
            if pattern in name:
                return None
        return tarinfo

    return filter_fn
