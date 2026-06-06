# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Joga na caixa** ("Throws in the box") is a Python multi-cloud backup tool. It compresses directories/files using pluggable algorithms, uploads to multiple cloud backends in parallel for redundancy, and tracks contents via local JSON manifests. It exposes both a CLI and a REST API. It also detects and indexes faces in stored images, with a web interface to browse and download images by face.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Install with face recognition support (requires cmake + C++ compiler)
# Ubuntu/Debian: sudo apt-get install cmake build-essential
# macOS:         brew install cmake
pip install -e ".[dev,faces]"

# Run tests
pytest

# Run a single test file
pytest tests/test_compression.py -v

# CLI usage
joganacaixa store [SOURCE]              # compress and upload (default: current dir)
joganacaixa store -a gz .              # override compression algorithm
joganacaixa list                        # list all packages
joganacaixa contents <package_id>      # list files inside a package
joganacaixa search <expr>              # find packages containing a filename
joganacaixa recover <package_id>       # download and extract
joganacaixa recover <id> -b s3://      # prefer a specific backend

# Start the REST API server
joganacaixa serve                      # http://localhost:8000
joganacaixa serve --reload             # dev mode with auto-reload
# API docs: http://localhost:8000/docs
# Web UI:   http://localhost:8000/ui
```

## Architecture

```
joganacaixa/
├── compression.py   # tar + gz/bz2/xz/zst compression; Algorithm enum
├── manifest.py      # JSON package manifests in .etiqueta/; search/list helpers
├── config.py        # YAML config loader; build_backends() factory
├── cli.py           # Click CLI: store, list, contents, search, recover, serve
├── api.py           # FastAPI REST API (same operations as CLI + face endpoints)
├── faces.py         # Face detection, encoding, clustering and index management
└── storage/
    ├── base.py      # StorageBackend ABC: upload/download/list_packages/delete
    ├── local.py     # LocalBackend — filesystem (used in tests)
    ├── s3.py        # S3Backend — AWS S3 + Glacier storage classes
    ├── gcs.py       # GCSBackend — GCS standard/nearline/coldline/archive
    └── azure.py     # AzureBackend — Azure Blob Storage

frontend/
└── index.html       # Single-page web UI: Dashboard, Store, Packages, Search, Faces
```

**Data flow (store):** source → `compress()` → `.escorregador/<timestamp>.tar.<alg>` → parallel upload to all backends → `build_manifest()` → `.etiqueta/<timestamp>.json` → staging archive deleted.

**Data flow (recover):** `.etiqueta/<id>.json` → pick backend → download to `.escorregador/` → `extract()` → staging archive deleted.

**Data flow (face indexing):** trigger via API → download archive → extract images to temp dir → `face_recognition` detects faces → 128-d encodings compared against existing clusters (threshold 0.55) → new cluster created or occurrence appended → thumbnail saved → `.etiqueta/faces/index.json` updated → temp dir deleted.

## Face Index

The face index lives inside `.etiqueta/faces/`:

```
.etiqueta/
└── faces/
    ├── index.json            # all clusters + occurrences + 128-d encodings
    └── face_thumbnails/
        └── face_<id>.jpg     # 160×160 JPEG crop for each cluster
```

`index.json` structure:
```json
{
  "version": 1,
  "clusters": {
    "face_<hex>": {
      "face_id": "face_<hex>",
      "encoding": [128 floats],
      "occurrences": [
        { "package_id": "…", "image_path": "relative/path.jpg", "bbox": [top,right,bottom,left] }
      ]
    }
  },
  "indexed_packages": ["<package_id>", "…"]
}
```

Key parameters in `faces.py`:
- `_MATCH_THRESHOLD = 0.55` — lower is stricter; increase to merge more faces into same cluster
- `_IMAGE_EXTS` — set of extensions considered as images (jpg, jpeg, png, bmp, tiff, webp)

## Configuration

Copy `config.example.yaml` to `.joganacaixa.yaml` in your working directory (or `~/.joganacaixa.yaml` for global config). The config file is optional — without it the tool uses zstd compression and no backends.

Key config fields:
- `compression.algorithm`: `gz` | `bz2` | `xz` | `zst` (default: `zst` — fastest with best ratio)
- `storage[]`: list of backend entries; each has a `type` field (`s3`, `gcs`, `azure`, `local`)
- `storage_class` on S3: `glacier` / `deep_archive` for long-term cold storage
- `storage_class` on GCS: `archive` / `coldline` for equivalent long-term storage

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/packages` | List all packages |
| GET | `/packages/{id}` | Package metadata + full file list |
| GET | `/search?expr=` | Search filenames across all manifests |
| POST | `/store` | Upload a file (multipart); optional `?algorithm=` |
| GET | `/recover/{id}` | Download archive; optional `?backend=s3://` |
| DELETE | `/packages/{id}` | Remove from all backends + delete manifest |
| GET | `/faces` | List all face clusters (id, count, packages) |
| GET | `/faces/{id}` | Cluster details with full occurrence list |
| GET | `/faces/{id}/thumbnail` | JPEG thumbnail of the face |
| POST | `/faces/index/{id}` | Detect and index faces in one package; `?force=true` to re-index |
| POST | `/faces/index` | Index all packages that contain images; `?force=true` to re-index |
| GET | `/faces/{id}/images.zip` | ZIP of all images where this face appears |

## Adding a New Storage Backend

1. Create `joganacaixa/storage/<name>.py` implementing `StorageBackend` (upload, download, list_packages, delete)
2. Register it in `config.py` `build_backends()` with a new `type` value
3. Add the SDK dependency to `pyproject.toml`

## Known Limitations

- The original script was macOS-only; this version is cross-platform
- Glacier/deep-archive retrievals require initiation via the AWS console or SDK before download is possible (this tool only handles upload and regular S3 download)
- `.etiqueta/` manifests are local — if lost, the cloud contents still exist but cannot be searched without re-indexing
- Face indexing requires the `[faces]` optional extra (`face_recognition` + dlib); without it the `/faces/*` endpoints will raise a 500 on index operations (listing/thumbnails still work if the index already exists)
- Face indexing downloads full package archives — for large packages this can be slow and disk-intensive


## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run tests
pytest

# Run a single test file
pytest tests/test_compression.py -v

# CLI usage
joganacaixa store [SOURCE]              # compress and upload (default: current dir)
joganacaixa store -a gz .              # override compression algorithm
joganacaixa list                        # list all packages
joganacaixa contents <package_id>      # list files inside a package
joganacaixa search <expr>              # find packages containing a filename
joganacaixa recover <package_id>       # download and extract
joganacaixa recover <id> -b s3://      # prefer a specific backend

# Start the REST API server
joganacaixa serve                      # http://localhost:8000
joganacaixa serve --reload             # dev mode with auto-reload
# API docs: http://localhost:8000/docs
```

## Architecture

```
joganacaixa/
├── compression.py   # tar + gz/bz2/xz/zst compression; Algorithm enum
├── manifest.py      # JSON package manifests in .etiqueta/; search/list helpers
├── config.py        # YAML config loader; build_backends() factory
├── cli.py           # Click CLI: store, list, contents, search, recover, serve
├── api.py           # FastAPI REST API (same operations as CLI)
└── storage/
    ├── base.py      # StorageBackend ABC: upload/download/list_packages/delete
    ├── local.py     # LocalBackend — filesystem (used in tests)
    ├── s3.py        # S3Backend — AWS S3 + Glacier storage classes
    ├── gcs.py       # GCSBackend — GCS standard/nearline/coldline/archive
    └── azure.py     # AzureBackend — Azure Blob Storage
```

**Data flow (store):** source → `compress()` → `.escorregador/<timestamp>.tar.<alg>` → parallel upload to all backends → `build_manifest()` → `.etiqueta/<timestamp>.json` → staging archive deleted.

**Data flow (recover):** `.etiqueta/<id>.json` → pick backend → download to `.escorregador/` → `extract()` → staging archive deleted.

## Configuration

Copy `config.example.yaml` to `.joganacaixa.yaml` in your working directory (or `~/.joganacaixa.yaml` for global config). The config file is optional — without it the tool uses zstd compression and no backends.

Key config fields:
- `compression.algorithm`: `gz` | `bz2` | `xz` | `zst` (default: `zst` — fastest with best ratio)
- `storage[]`: list of backend entries; each has a `type` field (`s3`, `gcs`, `azure`, `local`)
- `storage_class` on S3: `glacier` / `deep_archive` for long-term cold storage
- `storage_class` on GCS: `archive` / `coldline` for equivalent long-term storage

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/packages` | List all packages |
| GET | `/packages/{id}` | Package metadata + full file list |
| GET | `/search?expr=` | Search filenames across all manifests |
| POST | `/store` | Upload a file (multipart); optional `?algorithm=` |
| GET | `/recover/{id}` | Download archive; optional `?backend=s3://` |
| DELETE | `/packages/{id}` | Remove from all backends + delete manifest |

## Adding a New Storage Backend

1. Create `joganacaixa/storage/<name>.py` implementing `StorageBackend` (upload, download, list_packages, delete)
2. Register it in `config.py` `build_backends()` with a new `type` value
3. Add the SDK dependency to `pyproject.toml`

## Known Limitations

- The original script was macOS-only; this version is cross-platform
- Glacier/deep-archive retrievals require initiation via the AWS console or SDK before download is possible (this tool only handles upload and regular S3 download)
- `.etiqueta/` manifests are local — if lost, the cloud contents still exist but cannot be searched without re-indexing
