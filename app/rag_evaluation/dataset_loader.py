from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from app.core.config import BASE_DIR, Settings, get_settings
from app.rag_evaluation.errors import DatasetPathError, DatasetValidationError
from app.rag_evaluation.schemas import EvaluationDataset


def canonical_dataset_bytes(dataset: EvaluationDataset) -> bytes:
    return json.dumps(
        dataset.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def dataset_content_hash(dataset: EvaluationDataset) -> str:
    return hashlib.sha256(canonical_dataset_bytes(dataset)).hexdigest()


class DatasetLoader:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = self.settings.rag_evaluation_dataset_dir.resolve(strict=False)

    def list_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            (
                path
                for path in self.root.iterdir()
                if path.is_file()
                and path.suffix.casefold() in {".json", ".yaml", ".yml"}
                and not path.name.startswith(".")
            ),
            key=lambda path: path.name.casefold(),
        )

    def resolve(self, dataset_file: str | Path) -> Path:
        value = Path(dataset_file)
        if value.is_absolute() or len(value.parts) != 1 or value.name != str(value):
            raise DatasetPathError("Dataset must be selected by file name")
        candidate = (self.root / value.name).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise DatasetPathError("Dataset path escapes the configured directory") from exc
        if candidate.suffix.casefold() not in {".json", ".yaml", ".yml"}:
            raise DatasetPathError("Dataset must be JSON or YAML")
        if not candidate.is_file():
            raise DatasetPathError("Dataset file not found")
        return candidate

    def load(self, dataset_file: str | Path) -> EvaluationDataset:
        path = self.resolve(dataset_file)
        try:
            text = path.read_text(encoding="utf-8-sig")
            raw = (
                json.loads(text)
                if path.suffix.casefold() == ".json"
                else yaml.safe_load(text)
            )
            return EvaluationDataset.model_validate(raw)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise DatasetValidationError(
                f"Dataset could not be loaded: {type(exc).__name__}"
            ) from exc

    @staticmethod
    def project_relative_path(path: Path) -> str:
        resolved = path.resolve(strict=False)
        try:
            return resolved.relative_to(BASE_DIR).as_posix()
        except ValueError as exc:
            raise DatasetPathError("Dataset must be stored inside the project") from exc
