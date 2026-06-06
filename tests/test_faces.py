"""Tests for face index helpers (no actual face_recognition needed)."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from joganacaixa.faces import (
    face_index_dir,
    load_index,
    save_index,
    list_faces,
    get_face,
    thumbnail_path,
    _IMAGE_EXTS,
)


@pytest.fixture()
def manifest_dir(tmp_path):
    d = tmp_path / ".etiqueta"
    d.mkdir()
    return d


def test_load_index_empty(manifest_dir):
    data = load_index(manifest_dir)
    assert data["version"] == 1
    assert data["clusters"] == {}
    assert data["indexed_packages"] == []


def test_save_and_load_roundtrip(manifest_dir):
    data = {
        "version": 1,
        "clusters": {
            "face_abc": {
                "face_id": "face_abc",
                "encoding": [0.1] * 128,
                "occurrences": [
                    {"package_id": "pkg1", "image_path": "a.jpg", "bbox": [10, 50, 60, 5]}
                ],
            }
        },
        "indexed_packages": ["pkg1"],
    }
    save_index(data, manifest_dir)
    loaded = load_index(manifest_dir)
    assert loaded["clusters"]["face_abc"]["face_id"] == "face_abc"
    assert loaded["indexed_packages"] == ["pkg1"]


def test_list_faces_empty(manifest_dir):
    assert list_faces(manifest_dir) == []


def test_list_faces(manifest_dir):
    data = {
        "version": 1,
        "clusters": {
            "face_abc": {
                "face_id": "face_abc",
                "encoding": [],
                "occurrences": [
                    {"package_id": "pkg1", "image_path": "a.jpg", "bbox": []},
                    {"package_id": "pkg2", "image_path": "b.jpg", "bbox": []},
                ],
            }
        },
        "indexed_packages": ["pkg1", "pkg2"],
    }
    save_index(data, manifest_dir)
    faces = list_faces(manifest_dir)
    assert len(faces) == 1
    f = faces[0]
    assert f["face_id"] == "face_abc"
    assert f["occurrence_count"] == 2
    assert set(f["packages"]) == {"pkg1", "pkg2"}


def test_get_face_not_found(manifest_dir):
    assert get_face("nonexistent", manifest_dir) is None


def test_get_face(manifest_dir):
    data = {
        "version": 1,
        "clusters": {
            "face_xyz": {
                "face_id": "face_xyz",
                "encoding": [],
                "occurrences": [{"package_id": "p1", "image_path": "x.png", "bbox": []}],
            }
        },
        "indexed_packages": ["p1"],
    }
    save_index(data, manifest_dir)
    f = get_face("face_xyz", manifest_dir)
    assert f is not None
    assert f["face_id"] == "face_xyz"
    assert f["packages"] == ["p1"]


def test_image_exts_coverage():
    assert ".jpg" in _IMAGE_EXTS
    assert ".png" in _IMAGE_EXTS
    assert ".txt" not in _IMAGE_EXTS


def test_face_index_dir(manifest_dir):
    d = face_index_dir(manifest_dir)
    assert d == manifest_dir / "faces"
