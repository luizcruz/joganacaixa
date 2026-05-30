import shutil
from pathlib import Path

from .base import StorageBackend


class LocalBackend(StorageBackend):
    def __init__(self, root: str) -> None:
        self.name = f"local://{root}"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def upload(self, local_path: Path, key: str) -> str:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)
        return f"local://{dest}"

    def upload_stream(self, fileobj, key: str) -> str:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            while True:
                chunk = fileobj.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        return f"local://{dest}"

    def download_stream(self, key: str):
        path = self.root / key
        return open(path, "rb")

    def download(self, key: str, local_path: Path) -> None:
        src = self.root / key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local_path)

    def list_packages(self) -> list[str]:
        return [
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file()
        ]

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)
