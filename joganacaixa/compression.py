import fnmatch
import hashlib
import tarfile
from enum import Enum
from pathlib import Path


class Algorithm(str, Enum):
    GZIP = "gz"
    BZIP2 = "bz2"
    XZ = "xz"
    ZSTD = "zst"


def _arcname(source: Path) -> str:
    """Archive name for the top-level entry.

    Directories use '.' so their *contents* extract directly into the
    destination. Single files keep their own name, otherwise a lone file
    would be stored as '.' and clash with the destination directory on
    extraction (IsADirectoryError).
    """
    return "." if source.is_dir() else source.name


def compress(
    source: Path,
    dest: Path,
    algorithm: Algorithm = Algorithm.ZSTD,
    exclude_patterns: list[str] | None = None,
    level: int = 3,
) -> Path:
    """Compress source into a .tar.<alg> archive, returning the archive path."""
    exclude_patterns = exclude_patterns or []
    suffix = f".tar.{algorithm.value}"
    if not str(dest).endswith(suffix):
        dest = Path(str(dest) + suffix)

    filter_fn = _make_exclude_filter(exclude_patterns) if exclude_patterns else None
    arcname = _arcname(source)

    if algorithm == Algorithm.ZSTD:
        _compress_zstd(source, dest, filter_fn, level=level, arcname=arcname)
    else:
        with tarfile.open(dest, f"w:{algorithm.value}") as tar:
            tar.add(source, arcname=arcname, filter=filter_fn)

    return dest


def _compress_zstd(source: Path, dest: Path, filter_fn, level: int = 3, arcname: str = ".") -> None:
    import zstandard as zstd

    cctx = zstd.ZstdCompressor(level=level, threads=-1)
    with open(dest, "wb") as f:
        with cctx.stream_writer(f, closefd=False) as compressor:
            with tarfile.open(fileobj=compressor, mode="w|") as tar:
                tar.add(source, arcname=arcname, filter=filter_fn)


def sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file, reading in 64 KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def extract_from_stream(stream, dest: Path, algorithm: Algorithm) -> None:
    """Extract an archive from a file-like stream without writing a temp file."""
    dest.mkdir(parents=True, exist_ok=True)
    if algorithm == Algorithm.ZSTD:
        import zstandard as zstd

        dctx = zstd.ZstdDecompressor()
        with dctx.stream_reader(stream) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                tar.extractall(dest)
    elif algorithm == Algorithm.GZIP:
        import gzip
        import io

        with gzip.GzipFile(fileobj=stream) as gz:
            with tarfile.open(fileobj=gz, mode="r|") as tar:
                tar.extractall(dest)
    elif algorithm == Algorithm.BZIP2:
        import bz2
        import io

        with bz2.BZ2File(stream) as bz:
            with tarfile.open(fileobj=bz, mode="r|") as tar:
                tar.extractall(dest)
    elif algorithm == Algorithm.XZ:
        import lzma

        with lzma.open(stream) as xz:
            with tarfile.open(fileobj=xz, mode="r|") as tar:
                tar.extractall(dest)
    else:
        with tarfile.open(fileobj=stream, mode="r|") as tar:
            tar.extractall(dest)


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
