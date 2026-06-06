"""Face detection, encoding and clustering for stored image packages."""
from __future__ import annotations

import io
import json
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

_INDEX_FILE = "faces_index.json"
_THUMBNAILS = "face_thumbnails"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
_MATCH_THRESHOLD = 0.55


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def face_index_dir(manifest_dir: Path) -> Path:
    return manifest_dir / "faces"


def load_index(manifest_dir: Path) -> dict:
    path = face_index_dir(manifest_dir) / _INDEX_FILE
    if path.exists():
        return json.loads(path.read_text())
    return {"version": 1, "clusters": {}, "indexed_packages": []}


def save_index(data: dict, manifest_dir: Path) -> None:
    d = face_index_dir(manifest_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / _THUMBNAILS).mkdir(exist_ok=True)
    (d / _INDEX_FILE).write_text(json.dumps(data, indent=2))


def thumbnail_path(face_id: str, manifest_dir: Path) -> Path:
    return face_index_dir(manifest_dir) / _THUMBNAILS / f"{face_id}.jpg"


def list_faces(manifest_dir: Path) -> list[dict]:
    """Return all face clusters (without raw encodings)."""
    data = load_index(manifest_dir)
    result = []
    for cluster in data["clusters"].values():
        result.append({
            "face_id": cluster["face_id"],
            "occurrence_count": len(cluster["occurrences"]),
            "packages": list({o["package_id"] for o in cluster["occurrences"]}),
            "has_thumbnail": thumbnail_path(cluster["face_id"], manifest_dir).exists(),
        })
    return result


def get_face(face_id: str, manifest_dir: Path) -> dict | None:
    data = load_index(manifest_dir)
    cluster = data["clusters"].get(face_id)
    if cluster is None:
        return None
    return {
        "face_id": cluster["face_id"],
        "occurrences": cluster["occurrences"],
        "packages": list({o["package_id"] for o in cluster["occurrences"]}),
        "has_thumbnail": thumbnail_path(face_id, manifest_dir).exists(),
    }


def index_package(
    package_id: str,
    archive_path: Path,
    manifest_dir: Path,
    force: bool = False,
) -> dict:
    """Detect and cluster faces in all images inside *archive_path*.

    Returns a summary dict with counts of faces found / updated.
    """
    import face_recognition
    import numpy as np
    from PIL import Image

    data = load_index(manifest_dir)
    already = data.get("indexed_packages", [])

    if package_id in already and not force:
        return {"package_id": package_id, "status": "already_indexed", "new_faces": 0, "images_scanned": 0}

    new_faces = 0
    images_scanned = 0

    with tempfile.TemporaryDirectory(prefix="jnc_faces_") as tmpdir:
        tmp = Path(tmpdir)
        _extract_images(archive_path, tmp)

        for img_file in sorted(tmp.rglob("*")):
            if img_file.suffix.lower() not in _IMAGE_EXTS:
                continue
            try:
                img = face_recognition.load_image_file(str(img_file))
                locations = face_recognition.face_locations(img, model="hog")
                encodings = face_recognition.face_encodings(img, locations)
                images_scanned += 1
                rel = str(img_file.relative_to(tmp))
                for bbox, enc in zip(locations, encodings):
                    created = _upsert_face(data, enc, bbox, img, rel, package_id, manifest_dir)
                    if created:
                        new_faces += 1
            except Exception:
                continue

    indexed = list(set(data.get("indexed_packages", []) + [package_id]))
    data["indexed_packages"] = indexed
    save_index(data, manifest_dir)

    return {
        "package_id": package_id,
        "status": "indexed",
        "images_scanned": images_scanned,
        "new_faces": new_faces,
    }


def build_images_zip(face_id: str, manifest_dir: Path, backends: list) -> bytes:
    """Download the relevant images from backends and return a zip as bytes."""
    import face_recognition
    from PIL import Image

    cluster = get_face(face_id, manifest_dir)
    if cluster is None:
        raise KeyError(face_id)

    # Group occurrences by package so we download each archive only once
    by_pkg: dict[str, list[dict]] = {}
    for occ in cluster["occurrences"]:
        by_pkg.setdefault(occ["package_id"], []).append(occ)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for pkg_id, occs in by_pkg.items():
            archive = _download_package(pkg_id, manifest_dir, backends)
            if archive is None:
                continue
            try:
                with tempfile.TemporaryDirectory(prefix="jnc_zip_") as tmpdir:
                    tmp = Path(tmpdir)
                    _extract_images(archive, tmp)
                    for occ in occs:
                        img_path = tmp / occ["image_path"]
                        if img_path.exists():
                            zf.write(img_path, f"{pkg_id}/{occ['image_path']}")
            finally:
                archive.unlink(missing_ok=True)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_images(archive_path: Path, dest: Path) -> None:
    """Extract only image files from a tar archive."""
    name = archive_path.name
    if name.endswith(".zst") or name.endswith(".zst.enc"):
        import zstandard as zstd
        raw_name = name.replace(".enc", "")  # strip .enc suffix if present
        inner_name = raw_name[: raw_name.rfind(".zst")]  # strip .zst
        with open(archive_path, "rb") as fh:
            dctx = zstd.ZstdDecompressor()
            raw = io.BytesIO(dctx.read(fh))
        tf = tarfile.open(fileobj=raw)
    else:
        tf = tarfile.open(str(archive_path))

    with tf:
        members = [
            m for m in tf.getmembers()
            if Path(m.name).suffix.lower() in _IMAGE_EXTS and m.isfile()
        ]
        for m in members:
            m_path = dest / m.name
            m_path.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src:
                m_path.write_bytes(src.read())


def _upsert_face(
    data: dict,
    encoding,  # np.ndarray
    bbox: tuple,
    img,       # np.ndarray (RGB)
    rel_path: str,
    package_id: str,
    manifest_dir: Path,
) -> bool:
    """Add face to best matching cluster or create a new one. Returns True if new cluster."""
    import face_recognition
    import numpy as np
    from PIL import Image

    clusters = data.setdefault("clusters", {})
    best_id: str | None = None
    best_dist = _MATCH_THRESHOLD

    for fid, cl in clusters.items():
        if "encoding" not in cl:
            continue
        enc = np.array(cl["encoding"])
        dist = float(face_recognition.face_distance([enc], encoding)[0])
        if dist < best_dist:
            best_dist = dist
            best_id = fid

    is_new = best_id is None
    if is_new:
        face_id = f"face_{uuid.uuid4().hex[:10]}"
        _save_thumbnail(face_id, img, bbox, manifest_dir)
        clusters[face_id] = {
            "face_id": face_id,
            "encoding": encoding.tolist(),
            "occurrences": [],
        }
        best_id = face_id

    occ = {"package_id": package_id, "image_path": rel_path, "bbox": list(bbox)}
    existing = clusters[best_id]["occurrences"]
    if occ not in existing:
        existing.append(occ)

    return is_new


def _save_thumbnail(face_id: str, img, bbox: tuple, manifest_dir: Path) -> None:
    from PIL import Image

    top, right, bottom, left = bbox
    h, w = img.shape[:2]
    margin = max(20, int((bottom - top) * 0.25))
    y1, y2 = max(0, top - margin), min(h, bottom + margin)
    x1, x2 = max(0, left - margin), min(w, right + margin)
    crop = img[y1:y2, x1:x2]
    pil = Image.fromarray(crop)
    pil.thumbnail((160, 160))
    tdir = face_index_dir(manifest_dir) / _THUMBNAILS
    tdir.mkdir(parents=True, exist_ok=True)
    pil.save(str(tdir / f"{face_id}.jpg"), "JPEG", quality=85)


def _download_package(package_id: str, manifest_dir: Path, backends: list) -> Path | None:
    """Download the archive for *package_id* from the first available backend."""
    from .manifest import Manifest
    from .compression import Algorithm

    try:
        m = Manifest.load(package_id, manifest_dir)
    except FileNotFoundError:
        return None

    alg = Algorithm(m.algorithm)
    key = f"{package_id}.tar.{alg.value}"
    staging = manifest_dir.parent / ".escorregador"
    staging.mkdir(exist_ok=True)
    dest = staging / f"face_{key}"

    for backend in backends:
        try:
            backend.download(key, dest)
            return dest
        except Exception:
            continue
    return None
