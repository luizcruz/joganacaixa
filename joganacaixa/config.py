import os
from pathlib import Path

import yaml

from .compression import Algorithm
from .storage.base import StorageBackend

_DEFAULT_CONFIG: dict = {
    "compression": {
        "algorithm": "zst",
        "exclude": [".git", ".escorregador", ".etiqueta", "__pycache__", "*.pyc", "node_modules"],
    },
    "storage": [],
    "staging_dir": ".escorregador",
    "manifest_dir": ".etiqueta",
}

_CONFIG_CANDIDATES = [
    Path(".joganacaixa.yaml"),
    Path(".joganacaixa.yml"),
    Path(os.environ.get("JOGANACAIXA_CONFIG", "~/.joganacaixa.yaml")).expanduser(),
]


def load_config(path: Path | None = None) -> dict:
    candidates = [path] if path else _CONFIG_CANDIDATES
    for candidate in candidates:
        if candidate and candidate.exists():
            with open(candidate) as f:
                return _deep_merge(_DEFAULT_CONFIG, yaml.safe_load(f) or {})
    return dict(_DEFAULT_CONFIG)


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def build_backends(config: dict) -> list[StorageBackend]:
    from .storage.azure import AzureBackend
    from .storage.gcs import GCSBackend
    from .storage.local import LocalBackend
    from .storage.s3 import S3Backend

    backends: list[StorageBackend] = []
    for entry in config.get("storage", []):
        kind = entry.get("type")
        if kind == "s3":
            backends.append(
                S3Backend(
                    bucket=entry["bucket"],
                    region=entry.get("region", "sa-east-1"),
                    storage_class=entry.get("storage_class", "standard"),
                    prefix=entry.get("prefix", ""),
                )
            )
        elif kind == "gcs":
            backends.append(
                GCSBackend(
                    bucket=entry["bucket"],
                    region=entry.get("region", "southamerica-east1"),
                    storage_class=entry.get("storage_class", "standard"),
                    prefix=entry.get("prefix", ""),
                )
            )
        elif kind == "azure":
            backends.append(
                AzureBackend(
                    container=entry["container"],
                    connection_string=entry["connection_string"],
                    prefix=entry.get("prefix", ""),
                )
            )
        elif kind == "local":
            backends.append(LocalBackend(root=entry["root"]))
        else:
            raise ValueError(f"Unknown storage type: {kind!r}")
    return backends


def get_algorithm(config: dict) -> Algorithm:
    return Algorithm(config.get("compression", {}).get("algorithm", "zst"))


def get_exclude_patterns(config: dict) -> list[str]:
    return config.get("compression", {}).get("exclude", [])
