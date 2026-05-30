import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .compression import Algorithm, compress, extract, extract_from_stream, sha256_file
from .config import build_backends, get_algorithm, get_encryption_key, get_exclude_patterns, get_retries, get_zstd_level, load_config
from .manifest import Manifest, build_manifest
from .reliability import retry

app = FastAPI(
    title="Joga na Caixa",
    version="2.0.0",
    description="Multi-cloud backup API with redundant storage and robust compression",
)

_config = load_config()

# Serve the web UI from /ui (html=True serves index.html for directory requests)
_frontend = Path(__file__).parent.parent / "frontend"
if _frontend.exists():
    app.mount("/ui", StaticFiles(directory=str(_frontend), html=True), name="ui")


# --- Response models ---

class PackageSummary(BaseModel):
    package_id: str
    created_at: str
    algorithm: str
    file_count: int
    locations: list[str]


class PackageDetail(BaseModel):
    package_id: str
    created_at: str
    algorithm: str
    files: list[str]
    locations: list[str]


class StoreResult(BaseModel):
    package_id: str
    locations: list[str]
    file_count: int


class SearchMatch(BaseModel):
    package_id: str
    created_at: str
    matches: list[str]


class DeleteResult(BaseModel):
    deleted: str
    errors: list[str]


# --- Helpers ---

def _try_upload(backend, path: Path, key: str) -> None:
    try:
        backend.upload(path, key)
    except Exception:
        pass


def _manifest_dir() -> Path:
    return Path(_config.get("manifest_dir", ".etiqueta"))


def _staging_dir() -> Path:
    d = Path(_config.get("staging_dir", ".escorregador"))
    d.mkdir(exist_ok=True)
    return d


def _get_manifest(package_id: str) -> Manifest:
    try:
        return Manifest.load(package_id, _manifest_dir())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Package {package_id!r} not found")


# --- Endpoints ---

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui")


@app.get("/packages", response_model=list[PackageSummary])
def list_packages() -> list[PackageSummary]:
    """List all stored packages."""
    return [
        PackageSummary(
            package_id=m.package_id,
            created_at=m.created_at,
            algorithm=m.algorithm,
            file_count=len(m.files),
            locations=m.locations,
        )
        for m in Manifest.all(_manifest_dir())
    ]


@app.get("/packages/{package_id}", response_model=PackageDetail)
def get_package(package_id: str) -> PackageDetail:
    """Get a package's metadata and full file listing."""
    m = _get_manifest(package_id)
    return PackageDetail(
        package_id=m.package_id,
        created_at=m.created_at,
        algorithm=m.algorithm,
        files=m.files,
        locations=m.locations,
    )


@app.get("/search", response_model=list[SearchMatch])
def search(expr: str = Query(..., description="Substring to match against archived file paths")) -> list[SearchMatch]:
    """Search for a filename expression across all manifests."""
    return [
        SearchMatch(package_id=m.package_id, created_at=m.created_at, matches=hits)
        for m, hits in Manifest.search(expr, _manifest_dir())
    ]


@app.post("/store", response_model=StoreResult, status_code=201)
async def store(
    file: UploadFile = File(...),
    algorithm: str | None = Query(default=None, description="gz | bz2 | xz | zst"),
) -> StoreResult:
    """Upload a file, compress it, and store it across all configured backends in parallel."""
    backends = build_backends(_config)
    if not backends:
        raise HTTPException(status_code=503, detail="No storage backends configured")

    alg = Algorithm(algorithm) if algorithm else get_algorithm(_config)
    staging = _staging_dir()
    package_id = str(int(time.time()))
    attempts = get_retries(_config)
    level = get_zstd_level(_config)

    tmp = staging / (file.filename or package_id)
    with open(tmp, "wb") as f:
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            f.write(chunk)

    archive = compress(tmp, staging / package_id, alg, get_exclude_patterns(_config), level=level)
    tmp.unlink(missing_ok=True)
    checksum = sha256_file(archive)

    enc_key = get_encryption_key(_config)
    if enc_key:
        from .encryption import encrypt_file
        enc_archive = Path(str(archive) + ".enc")
        encrypt_file(archive, enc_archive, enc_key)
        archive.unlink()
        archive = enc_archive
    encrypted = enc_key is not None

    locations: list[str] = []
    with ThreadPoolExecutor(max_workers=len(backends)) as pool:
        futures = {
            pool.submit(retry, lambda b=b: b.upload(archive, archive.name), attempts): b
            for b in backends
        }
        for future in as_completed(futures):
            try:
                locations.append(future.result())
            except Exception:
                pass

    if not locations:
        archive.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail="Upload failed on all backends")

    # Build manifest before deleting the archive (list_contents reads the file)
    manifest = build_manifest(package_id, archive, alg, locations, checksum=checksum, encrypted=encrypted)
    archive.unlink(missing_ok=True)
    manifest_path = manifest.save(_manifest_dir())

    # Back up manifest JSON to all backends
    manifest_key = f"{package_id}.manifest.json"
    with ThreadPoolExecutor(max_workers=len(backends)) as pool:
        for b in backends:
            pool.submit(_try_upload, b, manifest_path, manifest_key)

    return StoreResult(package_id=package_id, locations=locations, file_count=len(manifest.files))


@app.get("/recover/{package_id}")
def recover(
    package_id: str,
    background_tasks: BackgroundTasks,
    backend: str | None = Query(default=None, description="Backend name prefix to prefer, e.g. 's3://'"),
) -> FileResponse:
    """Download a package archive from the first available backend."""
    manifest = _get_manifest(package_id)
    backends = build_backends(_config)
    chosen = [b for b in backends if not backend or b.name.startswith(backend)]
    if not chosen:
        raise HTTPException(status_code=404, detail="No matching backend")

    alg = Algorithm(manifest.algorithm)
    key = f"{package_id}.tar.{alg.value}" + (".enc" if manifest.encrypted else "")
    archive = _staging_dir() / key

    import threading

    result: list = []
    done = threading.Event()

    def _attempt(b):
        try:
            b.download(key, archive)
            if not done.is_set():
                done.set()
                result.append(True)
        except Exception:
            pass

    threads = [threading.Thread(target=_attempt, args=(b,), daemon=True) for b in chosen]
    for t in threads:
        t.start()
    done.wait(timeout=30)

    if not result:
        raise HTTPException(status_code=502, detail="Download failed from all backends")

    if manifest.encrypted:
        enc_key = get_encryption_key(_config)
        if not enc_key:
            archive.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Package is encrypted but no key configured")
        from .encryption import decrypt_file
        plain_key = f"{package_id}.tar.{alg.value}"
        plain_archive = _staging_dir() / plain_key
        decrypt_file(archive, plain_archive, enc_key)
        archive.unlink(missing_ok=True)
        background_tasks.add_task(plain_archive.unlink, missing_ok=True)
        return FileResponse(str(plain_archive), filename=plain_key, media_type="application/octet-stream")

    background_tasks.add_task(archive.unlink, missing_ok=True)
    return FileResponse(str(archive), filename=key, media_type="application/octet-stream")


@app.delete("/packages/{package_id}", response_model=DeleteResult)
def delete_package(package_id: str) -> DeleteResult:
    """Delete a package from all backends and remove its local manifest."""
    manifest = _get_manifest(package_id)
    backends = build_backends(_config)
    alg = Algorithm(manifest.algorithm)
    key = f"{package_id}.tar.{alg.value}"

    errors: list[str] = []
    for b in backends:
        try:
            b.delete(key)
        except Exception as exc:
            errors.append(f"{b.name}: {exc}")

    (_manifest_dir() / f"{package_id}.json").unlink(missing_ok=True)
    return DeleteResult(deleted=package_id, errors=errors)
