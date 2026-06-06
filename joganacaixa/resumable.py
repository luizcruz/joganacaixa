"""Resumable upload and download helpers.

Upload: streams the archive file to each backend in a loop that yields
control back to the Operation on every chunk, honouring pause/cancel.

Download: uses HTTP Range headers (or backend equivalents) so an
interrupted download can continue from the last byte written.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .operations import Operation
    from .storage.base import StorageBackend

_CHUNK = 512 * 1024   # 512 KB progress-reporting granularity


def upload_with_progress(
    backend: "StorageBackend",
    archive: Path,
    key: str,
    op: "Operation",
) -> str:
    """Upload *archive* to *backend* with pause/cancel support.

    Returns the backend location string on success.
    Raises RuntimeError on network failure (caller should catch and
    call op.mark_no_connection() then retry).
    """
    total = archive.stat().st_size
    sent = 0
    op.update_progress(sent, total)

    def _chunk_iter():
        nonlocal sent
        with open(archive, "rb") as fh:
            while True:
                if not op.check():
                    raise InterruptedError("operation cancelled")
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                yield chunk
                sent += len(chunk)
                op.update_progress(sent, total)

    # Use upload_stream so we can wrap it with our iterator
    loc = backend.upload_stream(_chunk_iter(), key)
    return loc


def download_with_progress(
    backend: "StorageBackend",
    key: str,
    dest: Path,
    op: "Operation",
) -> None:
    """Download *key* from *backend* to *dest* with pause/cancel support.

    Supports resume: if *dest* already exists and the backend supports
    Range requests, continues from the last byte.
    """
    existing = dest.stat().st_size if dest.exists() else 0
    op.update_progress(existing)

    offset = existing if existing > 0 else 0

    try:
        stream = backend.download_stream(key, offset=offset)
    except TypeError:
        # Backend doesn't support offset parameter – start from zero
        offset = 0
        if dest.exists():
            dest.unlink()
        stream = backend.download_stream(key)

    mode = "ab" if offset > 0 else "wb"
    received = offset

    with open(dest, mode) as fh:
        for chunk in stream:
            if not op.check():
                raise InterruptedError("operation cancelled")
            fh.write(chunk)
            received += len(chunk)
            op.update_progress(received)


def upload_to_backends(
    backends: list["StorageBackend"],
    archive: Path,
    key: str,
    op: "Operation",
    max_attempts: int = 8,
) -> list[str]:
    """Upload *archive* to all *backends* with per-backend retry/resume.

    Each failed backend is retried with exponential backoff.  Network
    errors set the operation to NO_CONNECTION; the connection monitor
    (operations.py) will call op.resume() when connectivity returns,
    at which point the loop unblocks and retries.

    Returns list of successful location strings.
    """
    from .operations import Status

    locations: list[str] = []
    pending = list(backends)
    attempt = 0

    while pending and attempt < max_attempts:
        if not op.check():
            break

        failed_again: list["StorageBackend"] = []
        for backend in pending:
            if not op.check():
                break
            try:
                loc = upload_with_progress(backend, archive, key, op)
                locations.append(loc)
            except InterruptedError:
                break
            except Exception:
                failed_again.append(backend)

        if failed_again and op.status not in {Status.CANCELLED, Status.COMPLETED}:
            pending = failed_again
            op.mark_no_connection()
            # Wait until resumed (by monitor or user) or cancelled
            op._pause.wait()
            if op._cancel.is_set():
                break
            op.mark_running()
            delay = min(2 ** attempt, 60)
            time.sleep(delay)
            attempt += 1
        else:
            pending = failed_again   # empty if all succeeded
            break

    return locations
