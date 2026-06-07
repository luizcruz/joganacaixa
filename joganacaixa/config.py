import os
from pathlib import Path

import yaml

from .compression import Algorithm
from .storage.base import StorageBackend

_DEFAULT_CONFIG: dict = {
    "compression": {
        "algorithm": "zst",
        "level": 3,
        "exclude": [".git", ".escorregador", ".etiqueta", "__pycache__", "*.pyc", "node_modules"],
    },
    "storage": [],
    "staging_dir": ".escorregador",
    "manifest_dir": ".etiqueta",
    "retries": 3,
    "encryption": {
        "enabled": True,
        "key_file": "~/.joganacaixa.key",
    },
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
        try:
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
        except Exception as exc:
            raise RuntimeError(
                f"Failed to initialise {kind!r} backend "
                f"(bucket/container: {entry.get('bucket') or entry.get('container') or entry.get('root')!r}): "
                f"{exc}\n\n"
                f"Run `joganacaixa diagnose` to check your configuration."
            ) from exc
    return backends


def get_algorithm(config: dict) -> Algorithm:
    return Algorithm(config.get("compression", {}).get("algorithm", "zst"))


def get_exclude_patterns(config: dict) -> list[str]:
    return config.get("compression", {}).get("exclude", [])


def get_zstd_level(config: dict) -> int:
    return config.get("compression", {}).get("level", 3)


def get_retries(config: dict) -> int:
    return config.get("retries", 3)


def get_encryption_key(config: dict) -> bytes | None:
    """Return the AES-256 encryption key, or None if encryption is disabled."""
    enc_cfg = config.get("encryption", {})
    if not enc_cfg.get("enabled", True):
        return None

    passphrase_env = enc_cfg.get("passphrase_env")
    if passphrase_env:
        passphrase = os.environ.get(passphrase_env)
        if not passphrase:
            raise ValueError(f"Encryption passphrase env var {passphrase_env!r} is not set")
        salt_path = Path("~/.joganacaixa.salt").expanduser()
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(32)
            salt_path.write_bytes(salt)
            salt_path.chmod(0o600)
        from .encryption import derive_key
        key, _ = derive_key(passphrase, salt)
        return key

    key_file = Path(enc_cfg.get("key_file", "~/.joganacaixa.key")).expanduser()
    if key_file.exists():
        return key_file.read_bytes()

    # Auto-generate a new key on first use
    from .encryption import generate_key
    key = generate_key()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return key
