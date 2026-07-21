from __future__ import annotations

import gzip
import os
import tempfile
from pathlib import Path

from app.core.config import BASE_DIR, Settings
from app.official_sources.errors import official_error


class SnapshotStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def save(self, source_id: str, snapshot_id: str, text: str) -> str:
        directory = self.settings.official_snapshot_dir / source_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{snapshot_id}.md.gz"
        fd, raw_temp = tempfile.mkstemp(prefix=f".{snapshot_id}-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                    compressed.write(text.encode("utf-8"))
                raw.flush()
                os.fsync(raw.fileno())
            os.replace(raw_temp, target)
        finally:
            Path(raw_temp).unlink(missing_ok=True)
        return target.relative_to(BASE_DIR).as_posix()

    def load(self, storage_path: str) -> str:
        target = self.resolve(storage_path)
        try:
            with gzip.open(target, "rt", encoding="utf-8", errors="strict") as handle:
                return handle.read()
        except (OSError, UnicodeError) as exc:
            raise official_error("official_fetch_failed", "Official snapshot is unavailable", status_code=500) from exc

    def preview(self, storage_path: str, max_chars: int) -> str:
        return self.load(storage_path)[:max_chars]

    def delete(self, storage_path: str) -> None:
        self.resolve(storage_path).unlink(missing_ok=True)

    def resolve(self, storage_path: str) -> Path:
        candidate = (BASE_DIR / storage_path).resolve(strict=False)
        root = self.settings.official_snapshot_dir.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise official_error("official_fetch_failed", "Official snapshot path is invalid", status_code=500) from exc
        if candidate.suffix != ".gz":
            raise official_error("official_fetch_failed", "Official snapshot path is invalid", status_code=500)
        return candidate
