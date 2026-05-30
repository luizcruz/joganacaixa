import io
import pytest
from pathlib import Path

from joganacaixa.encryption import (
    DecryptReader,
    decrypt_file,
    decrypt_stream,
    encrypt_file,
    encrypt_stream,
    generate_key,
)


def test_generate_key() -> None:
    key = generate_key()
    assert isinstance(key, bytes)
    assert len(key) == 32


def test_encrypt_decrypt_roundtrip(tmp_path: Path) -> None:
    key = generate_key()
    plaintext = b"Hello, joganacaixa! " * 1000

    src = tmp_path / "plain.bin"
    src.write_bytes(plaintext)

    enc = tmp_path / "plain.bin.enc"
    encrypt_file(src, enc, key)

    dec = tmp_path / "plain.dec.bin"
    decrypt_file(enc, dec, key)

    assert dec.read_bytes() == plaintext


def test_wrong_key_raises(tmp_path: Path) -> None:
    key = generate_key()
    wrong_key = generate_key()
    assert key != wrong_key

    src = tmp_path / "data.bin"
    src.write_bytes(b"sensitive data that must stay safe")

    enc = tmp_path / "data.bin.enc"
    encrypt_file(src, enc, key)

    dec = tmp_path / "data.dec.bin"
    with pytest.raises(Exception):
        decrypt_file(enc, dec, wrong_key)


def test_decrypt_reader_streaming(tmp_path: Path) -> None:
    key = generate_key()
    plaintext = b"streaming decryption test " * 500

    enc_buf = io.BytesIO()
    encrypt_stream(io.BytesIO(plaintext), enc_buf, key)
    enc_buf.seek(0)

    reader = DecryptReader(enc_buf, key)
    result = reader.read(-1)
    assert result == plaintext


def test_decrypt_reader_partial_reads(tmp_path: Path) -> None:
    key = generate_key()
    plaintext = b"abcdefghij" * 200  # 2000 bytes

    enc_buf = io.BytesIO()
    encrypt_stream(io.BytesIO(plaintext), enc_buf, key)
    enc_buf.seek(0)

    reader = DecryptReader(enc_buf, key)
    chunks = []
    while True:
        chunk = reader.read(64)
        if not chunk:
            break
        chunks.append(chunk)

    assert b"".join(chunks) == plaintext


def test_encrypt_decrypt_empty(tmp_path: Path) -> None:
    key = generate_key()

    src = tmp_path / "empty.bin"
    src.write_bytes(b"")

    enc = tmp_path / "empty.bin.enc"
    encrypt_file(src, enc, key)

    dec = tmp_path / "empty.dec.bin"
    decrypt_file(enc, dec, key)

    assert dec.read_bytes() == b""


def test_bad_magic_raises() -> None:
    key = generate_key()
    bad_data = io.BytesIO(b"NOPE" + b"\x00" * 100)
    out = io.BytesIO()
    with pytest.raises(ValueError, match="Not a JGC encrypted archive"):
        decrypt_stream(bad_data, out, key)


def test_decrypt_reader_bad_magic() -> None:
    key = generate_key()
    bad_data = io.BytesIO(b"XXXX" + b"\x00" * 50)
    with pytest.raises(ValueError, match="Not a JGC encrypted archive"):
        DecryptReader(bad_data, key)
