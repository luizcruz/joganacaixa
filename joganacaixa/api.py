import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .compression import Algorithm, compress, extract, extract_from_stream, list_contents, sha256_file
from .config import build_backends, get_algorithm, get_encryption_key, get_exclude_patterns, get_retries, get_zstd_level, load_config
from .manifest import Manifest, build_manifest
from .reliability import retry
from . import faces as face_lib
from .operations import registry, Status
from . import resumable as resumable_lib

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


class FaceSummary(BaseModel):
    face_id: str
    occurrence_count: int
    packages: list[str]
    has_thumbnail: bool


class FaceDetail(BaseModel):
    face_id: str
    occurrences: list[dict]
    packages: list[str]
    has_thumbnail: bool


class FaceIndexResult(BaseModel):
    package_id: str
    status: str
    images_scanned: int
    new_faces: int


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
    encrypt: bool | None = Query(default=None, description="Override encryption (true/false)"),
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

    # List contents while archive is still a plain tar (before encryption)
    archive_files = list_contents(archive)

    # encrypt=False explicitly disables; encrypt=True or None uses config default
    enc_key = None if encrypt is False else get_encryption_key(_config)
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

    manifest = build_manifest(package_id, archive, alg, locations, checksum=checksum, encrypted=encrypted, files=archive_files)
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


# ---------------------------------------------------------------------------
# Face recognition endpoints
# ---------------------------------------------------------------------------

@app.get("/faces", response_model=list[FaceSummary])
def list_faces() -> list[FaceSummary]:
    """List all detected face clusters."""
    return [FaceSummary(**f) for f in face_lib.list_faces(_manifest_dir())]


@app.get("/faces/{face_id}", response_model=FaceDetail)
def get_face(face_id: str) -> FaceDetail:
    """Get details for a face cluster."""
    f = face_lib.get_face(face_id, _manifest_dir())
    if f is None:
        raise HTTPException(status_code=404, detail=f"Face {face_id!r} not found")
    return FaceDetail(**f)


@app.get("/faces/{face_id}/thumbnail")
def face_thumbnail(face_id: str) -> Response:
    """Serve the thumbnail image for a face cluster."""
    path = face_lib.thumbnail_path(face_id, _manifest_dir())
    if not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return Response(content=path.read_bytes(), media_type="image/jpeg")


@app.post("/faces/index/{package_id}", response_model=FaceIndexResult)
def index_faces(
    package_id: str,
    force: bool = Query(default=False, description="Re-index even if already indexed"),
) -> FaceIndexResult:
    """Detect and index faces in a stored package."""
    try:
        _get_manifest(package_id)
    except HTTPException:
        raise

    backends = build_backends(_config)
    if not backends:
        raise HTTPException(status_code=503, detail="No storage backends configured")

    archive = face_lib._download_package(package_id, _manifest_dir(), backends)
    if archive is None:
        raise HTTPException(status_code=502, detail="Could not download package from any backend")

    try:
        result = face_lib.index_package(package_id, archive, _manifest_dir(), force=force)
    finally:
        archive.unlink(missing_ok=True)

    return FaceIndexResult(**result)


@app.post("/faces/index", response_model=list[FaceIndexResult])
def index_all_faces(
    force: bool = Query(default=False, description="Re-index already-indexed packages"),
) -> list[FaceIndexResult]:
    """Index faces in all packages that contain images."""
    backends = build_backends(_config)
    if not backends:
        raise HTTPException(status_code=503, detail="No storage backends configured")

    results = []
    for manifest in Manifest.all(_manifest_dir()):
        has_images = any(
            Path(f).suffix.lower() in face_lib._IMAGE_EXTS for f in manifest.files
        )
        if not has_images:
            continue
        archive = face_lib._download_package(manifest.package_id, _manifest_dir(), backends)
        if archive is None:
            continue
        try:
            r = face_lib.index_package(manifest.package_id, archive, _manifest_dir(), force=force)
            results.append(FaceIndexResult(**r))
        finally:
            archive.unlink(missing_ok=True)

    return results


@app.get("/faces/{face_id}/images.zip")
def download_face_images(face_id: str) -> StreamingResponse:
    """Download a ZIP archive containing all images where this face appears."""
    f = face_lib.get_face(face_id, _manifest_dir())
    if f is None:
        raise HTTPException(status_code=404, detail=f"Face {face_id!r} not found")

    backends = build_backends(_config)

    def generate():
        try:
            data = face_lib.build_images_zip(face_id, _manifest_dir(), backends)
            yield data
        except KeyError:
            raise HTTPException(status_code=404, detail="Face not found")

    return StreamingResponse(
        generate(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{face_id}_images.zip"'},
    )


# ---------------------------------------------------------------------------
# Resumable operations
# ---------------------------------------------------------------------------

class OperationOut(BaseModel):
    id: str
    type: str
    label: str
    status: str
    progress: int
    transferred: int
    total: int
    error: str | None = None
    created_at: float
    updated_at: float


class OperationStarted(BaseModel):
    operation_id: str


def _run_store(op_id: str, archive: Path, key: str, alg: Algorithm, checksum: str, encrypted: bool, files: list[str]) -> None:
    """Background thread: upload archive with pause/resume, then build manifest."""
    op = registry.get(op_id)
    if op is None:
        return

    backends = build_backends(_config)
    op.mark_running()

    locations = resumable_lib.upload_to_backends(backends, archive, key, op)

    if not locations or op.status is Status.CANCELLED:
        archive.unlink(missing_ok=True)
        if op.status is not Status.CANCELLED:
            op.mark_failed("Upload failed on all backends")
        return

    # Build and save manifest (files already listed before encryption)
    package_id = key.split(".")[0]
    manifest = build_manifest(package_id, archive, alg, locations, checksum=checksum, encrypted=encrypted, files=files)
    manifest_path = manifest.save(_manifest_dir())
    archive.unlink(missing_ok=True)

    # Back up manifest to all backends (best-effort)
    manifest_key = f"{package_id}.manifest.json"
    for b in backends:
        try:
            b.upload(manifest_path, manifest_key)
        except Exception:
            pass

    op.mark_completed(result={
        "package_id": package_id,
        "locations": locations,
        "file_count": len(manifest.files),
    })


def _run_recover(op_id: str, package_id: str, backend_prefix: str | None) -> None:
    """Background thread: download archive with pause/resume."""
    op = registry.get(op_id)
    if op is None:
        return

    manifest = Manifest.load(package_id, _manifest_dir())
    backends = build_backends(_config)
    chosen = [b for b in backends if not backend_prefix or b.name.startswith(backend_prefix)]
    if not chosen:
        op.mark_failed("No matching backend")
        return

    alg = Algorithm(manifest.algorithm)
    key = f"{package_id}.tar.{alg.value}" + (".enc" if manifest.encrypted else "")
    dest = _staging_dir() / key

    op.mark_running()

    # Try backends in order until one succeeds
    success = False
    for backend in chosen:
        if not op.check():
            break
        try:
            resumable_lib.download_with_progress(backend, key, dest, op)
            success = True
            break
        except InterruptedError:
            break
        except Exception:
            op.mark_no_connection()
            op._pause.wait()
            if op._cancel.is_set():
                break
            op.mark_running()

    if not success or op.status is Status.CANCELLED:
        dest.unlink(missing_ok=True)
        if op.status is not Status.CANCELLED:
            op.mark_failed("Download failed from all backends")
        return

    if manifest.encrypted:
        enc_key = get_encryption_key(_config)
        if not enc_key:
            dest.unlink(missing_ok=True)
            op.mark_failed("Package is encrypted but no key configured")
            return
        from .encryption import decrypt_file
        plain_key = f"{package_id}.tar.{alg.value}"
        plain = _staging_dir() / plain_key
        decrypt_file(dest, plain, enc_key)
        dest.unlink(missing_ok=True)
        dest = plain

    op.mark_completed(result={"package_id": package_id, "archive": str(dest)})


@app.post("/store/resumable", response_model=OperationStarted, status_code=202)
async def store_resumable(
    file: UploadFile = File(...),
    algorithm: str | None = Query(default=None),
    encrypt: bool | None = Query(default=None),
) -> OperationStarted:
    """Start a resumable upload. Returns an operation_id to track progress."""
    backends = build_backends(_config)
    if not backends:
        raise HTTPException(status_code=503, detail="No storage backends configured")

    alg = Algorithm(algorithm) if algorithm else get_algorithm(_config)
    staging = _staging_dir()
    package_id = str(int(time.time()))
    level = get_zstd_level(_config)

    # Save uploaded file and compress it (fast, in-request)
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

    # List contents before encryption
    archive_files = list_contents(archive)

    enc_key = None if encrypt is False else get_encryption_key(_config)
    if enc_key:
        from .encryption import encrypt_file
        enc_archive = Path(str(archive) + ".enc")
        encrypt_file(archive, enc_archive, enc_key)
        archive.unlink()
        archive = enc_archive

    key = archive.name
    op = registry.create("store", label=file.filename or package_id)

    thread = threading.Thread(
        target=_run_store,
        args=(op.id, archive, key, alg, checksum, enc_key is not None, archive_files),
        daemon=True,
        name=f"store-{op.id}",
    )
    thread.start()

    return OperationStarted(operation_id=op.id)


@app.post("/recover/{package_id}/resumable", response_model=OperationStarted, status_code=202)
def recover_resumable(
    package_id: str,
    backend: str | None = Query(default=None),
) -> OperationStarted:
    """Start a resumable download. Returns an operation_id to track progress."""
    _get_manifest(package_id)   # 404 early if not found
    op = registry.create("recover", label=package_id)

    thread = threading.Thread(
        target=_run_recover,
        args=(op.id, package_id, backend),
        daemon=True,
        name=f"recover-{op.id}",
    )
    thread.start()

    return OperationStarted(operation_id=op.id)


@app.get("/operations", response_model=list[OperationOut])
def list_operations() -> list[OperationOut]:
    """List all active and recent operations."""
    return [OperationOut(**op.to_dict()) for op in registry.list_all()]


@app.get("/operations/{op_id}", response_model=OperationOut)
def get_operation(op_id: str) -> OperationOut:
    op = registry.get(op_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    return OperationOut(**op.to_dict())


@app.post("/operations/{op_id}/pause", response_model=OperationOut)
def pause_operation(op_id: str) -> OperationOut:
    op = registry.get(op_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    op.pause()
    return OperationOut(**op.to_dict())


@app.post("/operations/{op_id}/resume", response_model=OperationOut)
def resume_operation(op_id: str) -> OperationOut:
    op = registry.get(op_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    op.resume()
    return OperationOut(**op.to_dict())


@app.delete("/operations/{op_id}", response_model=OperationOut)
def cancel_operation(op_id: str) -> OperationOut:
    op = registry.get(op_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    op.cancel()
    return OperationOut(**op.to_dict())


@app.get("/operations/{op_id}/events")
async def operation_events(op_id: str) -> StreamingResponse:
    """Server-Sent Events stream for real-time operation progress."""
    op = registry.get(op_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")

    loop = asyncio.get_running_loop()
    q = op.subscribe(loop)

    async def _stream():
        # Send current state immediately
        yield f"data: {json.dumps(op.to_dict())}\n\n"
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                    if payload.get("status") in ("completed", "failed", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            op.unsubscribe(q)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
