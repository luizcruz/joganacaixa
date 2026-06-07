"""Tests for the setup wizard (non-interactive paths)."""
import yaml
import pytest

from joganacaixa.setup_wizard import run_setup, _placeholder, _instructions, _BACKEND_PACKAGES


def test_placeholder_local_creates_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    entry = _placeholder("local")
    assert entry["type"] == "local"
    assert "root" in entry


def test_placeholder_s3():
    entry = _placeholder("s3")
    assert entry["type"] == "s3"
    assert entry["bucket"] == "CHANGE_ME"
    assert entry["region"] == "sa-east-1"


def test_placeholder_gcs():
    entry = _placeholder("gcs")
    assert entry["type"] == "gcs"
    assert entry["storage_class"] == "standard"


def test_placeholder_azure():
    entry = _placeholder("azure")
    assert entry["type"] == "azure"
    assert "${" in entry["connection_string"]


def test_instructions_local():
    entry = {"type": "local", "root": "/tmp/backup"}
    text = _instructions("local", entry)
    assert "Nenhuma credencial" in text
    assert "/tmp/backup" in text


def test_instructions_s3():
    entry = {"type": "s3", "region": "us-east-1"}
    text = _instructions("s3", entry)
    assert "aws configure" in text
    assert "us-east-1" in text


def test_instructions_gcs():
    text = _instructions("gcs", {"type": "gcs"})
    assert "GOOGLE_APPLICATION_CREDENTIALS" in text
    assert "service account" in text.lower()


def test_instructions_azure_with_env_var():
    entry = {"type": "azure", "connection_string": "${AZURE_STORAGE_CONNECTION_STRING}"}
    text = _instructions("azure", entry)
    assert "AZURE_STORAGE_CONNECTION_STRING" in text


def test_backend_packages_mapping():
    assert _BACKEND_PACKAGES["s3"] == ["boto3"]
    assert _BACKEND_PACKAGES["gcs"] == ["google-cloud-storage"]
    assert _BACKEND_PACKAGES["azure"] == ["azure-storage-blob"]
    assert _BACKEND_PACKAGES["local"] == []


def test_run_setup_non_interactive_writes_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "myconfig.yaml"
    result = run_setup(
        backend_types=["local"],
        config_path=cfg,
        install=False,
        non_interactive=True,
    )
    assert result == cfg
    assert cfg.exists()
    data = yaml.safe_load(cfg.read_text())
    assert data["storage"][0]["type"] == "local"
    assert data["encryption"]["enabled"] is True
    assert data["compression"]["algorithm"] == "zst"


def test_run_setup_multiple_backends(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "multi.yaml"
    run_setup(
        backend_types=["local", "s3", "gcs"],
        config_path=cfg,
        install=False,
        non_interactive=True,
    )
    data = yaml.safe_load(cfg.read_text())
    types = [s["type"] for s in data["storage"]]
    assert types == ["local", "s3", "gcs"]


def test_run_setup_invalid_backend_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "bad.yaml"
    with pytest.raises(SystemExit):
        run_setup(
            backend_types=["nonexistent"],
            config_path=cfg,
            install=False,
            non_interactive=True,
        )


def test_run_setup_filters_invalid_keeps_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "mixed.yaml"
    run_setup(
        backend_types=["local", "bogus"],
        config_path=cfg,
        install=False,
        non_interactive=True,
    )
    data = yaml.safe_load(cfg.read_text())
    types = [s["type"] for s in data["storage"]]
    assert types == ["local"]
