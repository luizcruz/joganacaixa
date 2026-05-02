import json
from datetime import datetime, timezone
from pathlib import Path

from .compression import Algorithm, list_contents


class Manifest:
    def __init__(
        self,
        package_id: str,
        algorithm: str,
        files: list[str],
        locations: list[str],
        created_at: str | None = None,
    ) -> None:
        self.package_id = package_id
        self.algorithm = algorithm
        self.files = files
        self.locations = locations
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "package_id": self.package_id,
            "algorithm": self.algorithm,
            "files": self.files,
            "locations": self.locations,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        return cls(**data)

    def save(self, manifest_dir: Path) -> Path:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        path = manifest_dir / f"{self.package_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, package_id: str, manifest_dir: Path) -> "Manifest":
        path = manifest_dir / f"{package_id}.json"
        return cls.from_dict(json.loads(path.read_text()))

    @classmethod
    def all(cls, manifest_dir: Path) -> list["Manifest"]:
        if not manifest_dir.exists():
            return []
        manifests = []
        for path in sorted(manifest_dir.glob("*.json")):
            try:
                manifests.append(cls.from_dict(json.loads(path.read_text())))
            except (json.JSONDecodeError, KeyError):
                continue
        return manifests

    @classmethod
    def search(cls, expr: str, manifest_dir: Path) -> list[tuple["Manifest", list[str]]]:
        results = []
        for manifest in cls.all(manifest_dir):
            matches = [f for f in manifest.files if expr in f]
            if matches:
                results.append((manifest, matches))
        return results


def build_manifest(
    package_id: str,
    archive: Path,
    algorithm: Algorithm,
    locations: list[str],
) -> Manifest:
    return Manifest(
        package_id=package_id,
        algorithm=algorithm.value,
        files=list_contents(archive),
        locations=locations,
    )
