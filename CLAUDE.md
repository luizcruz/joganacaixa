# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Joga na caixa** ("Throws in the box") is a Python multi-cloud backup tool. It compresses directories/files using pluggable algorithms, uploads to multiple cloud backends in parallel for redundancy, and tracks contents via local JSON manifests. It exposes both a CLI and a REST API.

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
