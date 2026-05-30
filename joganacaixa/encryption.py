import io
import os
import struct
from pathlib import Path

_MAGIC = b"JGC1"  # 4-byte magic header identifying encrypted archives
CHUNK_SIZE = 64 * 1024  # 64 KB per chunk

# Format: MAGIC(4) | [nonce(12) | ct_len(4) | ciphertext+tag(ct_len)]* | nonce(0 sentinel 12 null bytes) | ct_len(0, 4 bytes)
# Each chunk: random 12-byte nonce, 4-byte length of ciphertext (= plaintext + 16-byte GCM tag), then ciphertext


def generate_key() -> bytes:
    return os.urandom(32)


def derive_key(passphrase: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    if salt is None:
        salt = os.urandom(32)
    kdf = Scrypt(salt=salt, length=32, n=2**18, r=8, p=1)
    key = kdf.derive(passphrase.encode())
    return key, salt


def encrypt_file(src: Path, dst: Path, key: bytes) -> None:
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        encrypt_stream(fin, fout, key)


def decrypt_file(src: Path, dst: Path, key: bytes) -> None:
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        decrypt_stream(fin, fout, key)


def encrypt_stream(in_stream, out_stream, key: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(key)
    out_stream.write(_MAGIC)
    while True:
        chunk = in_stream.read(CHUNK_SIZE)
        if not chunk:
            # Write end-of-stream sentinel: 12 null nonce bytes + 4 zero length
            out_stream.write(b"\x00" * 12)
            out_stream.write(struct.pack(">I", 0))
            break
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, chunk, None)
        out_stream.write(nonce)
        out_stream.write(struct.pack(">I", len(ct)))
        out_stream.write(ct)


def decrypt_stream(in_stream, out_stream, key: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(key)
    magic = in_stream.read(4)
    if magic != _MAGIC:
        raise ValueError(f"Not a JGC encrypted archive (got {magic!r})")
    while True:
        nonce = in_stream.read(12)
        if not nonce or len(nonce) < 12:
            break
        length_bytes = in_stream.read(4)
        if not length_bytes or len(length_bytes) < 4:
            break
        length = struct.unpack(">I", length_bytes)[0]
        if length == 0:
            break  # end-of-stream sentinel
        ct = in_stream.read(length)
        plaintext = aesgcm.decrypt(nonce, ct, None)
        out_stream.write(plaintext)


class DecryptReader:
    """File-like wrapper that decrypts on the fly as data is read.
    Used to pipe decrypt -> extract_from_stream without temp files."""

    def __init__(self, raw_stream, key: bytes) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._aesgcm = AESGCM(key)
        self._raw = raw_stream
        self._buf = b""
        self._done = False
        magic = raw_stream.read(4)
        if magic != _MAGIC:
            raise ValueError(f"Not a JGC encrypted archive (got {magic!r})")

    def read(self, size: int = -1):
        if size == -1:
            out = io.BytesIO()
            while not self._done:
                self._fill()
            out.write(self._buf)
            self._buf = b""
            return out.getvalue()

        while not self._done and len(self._buf) < size:
            self._fill()

        result = self._buf[:size]
        self._buf = self._buf[size:]
        return result

    def _fill(self) -> None:
        if self._done:
            return
        nonce = self._raw.read(12)
        if not nonce or len(nonce) < 12:
            self._done = True
            return
        length_bytes = self._raw.read(4)
        if not length_bytes:
            self._done = True
            return
        length = struct.unpack(">I", length_bytes)[0]
        if length == 0:
            self._done = True
            return
        ct = self._raw.read(length)
        self._buf += self._aesgcm.decrypt(nonce, ct, None)

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass
