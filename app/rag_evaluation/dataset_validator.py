from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import ObsidianNote
from app.obsidian.markdown_parser import MarkdownParser
from app.obsidian.scanner import ObsidianVaultScanner
from app.rag_evaluation.dataset_loader import dataset_content_hash
from app.rag_evaluation.schemas import (
    DatasetIssue,
    DatasetValidationResult,
    EvaluationDataset,
)


@dataclass(slots=True)
class VaultCatalogEntry:
    status: str
    headings: set[tuple[str, ...]] = field(default_factory=set)


class DatasetValidator:
    def __init__(self, catalog: dict[str, VaultCatalogEntry]) -> None:
        self.catalog = catalog

    def validate(self, dataset: EvaluationDataset) -> DatasetValidationResult:
        issues: list[DatasetIssue] = []
        stale_cases: set[str] = set()
        if dataset.schema_version == "rag-eval-v2":
            for split in ("calibration", "holdout"):
                split_cases = [
                    case for case in dataset.cases if case.safety_split == split
                ]
                policies = {case.expected_policy for case in split_cases}
                for required in (
                    "answer",
                    "abstain",
                    "answer_with_uncertainty",
                ):
                    if required not in policies:
                        issues.append(
                            DatasetIssue(
                                case_id=f"split:{split}",
                                code="safety_split_incomplete",
                                message=(
                                    f"{split} split is missing policy {required}"
                                ),
                            )
                        )
        for case in dataset.cases:
            if case.review_status == "stale":
                stale_cases.add(case.id)
                issues.append(
                    DatasetIssue(
                        case_id=case.id,
                        code="case_marked_stale",
                        message="Case is explicitly marked stale and will not run",
                        stale=True,
                    )
                )
            referenced_ids = set(case.source_scope.note_ids)
            referenced_ids.update(item.note_id for item in case.relevance_judgments)
            referenced_ids.update(case.acceptable_citation_note_ids)
            referenced_ids.update(case.forbidden_citation_note_ids)
            for note_id in sorted(referenced_ids):
                note = self.catalog.get(note_id)
                if note is None:
                    stale_cases.add(case.id)
                    issues.append(
                        DatasetIssue(
                            case_id=case.id,
                            code="note_missing",
                            message=f"Referenced note ID is missing: {note_id}",
                            stale=True,
                        )
                    )
                elif note.status == "missing":
                    stale_cases.add(case.id)
                    issues.append(
                        DatasetIssue(
                            case_id=case.id,
                            code="note_stale",
                            message=f"Referenced note is marked missing: {note_id}",
                            stale=True,
                        )
                    )
            for judgment in case.relevance_judgments:
                note = self.catalog.get(judgment.note_id)
                heading = tuple(judgment.heading_path)
                if note is not None and heading and heading not in note.headings:
                    stale_cases.add(case.id)
                    issues.append(
                        DatasetIssue(
                            case_id=case.id,
                            code="heading_stale",
                            message=(
                                "Referenced heading no longer exists for note "
                                f"{judgment.note_id}"
                            ),
                            stale=True,
                        )
                    )
            if case.share_with_langsmith and case.review_status != "reviewed":
                issues.append(
                    DatasetIssue(
                        case_id=case.id,
                        code="unsafe_sync_status",
                        message="Only reviewed cases may be shared with LangSmith",
                    )
                )
        safe_count = sum(
            case.share_with_langsmith
            and case.review_status == "reviewed"
            and case.id not in stale_cases
            for case in dataset.cases
        )
        blocking = [issue for issue in issues if not issue.stale]
        return DatasetValidationResult(
            valid=not blocking and not stale_cases,
            dataset_hash=dataset_content_hash(dataset),
            case_count=len(dataset.cases),
            stale_case_count=len(stale_cases),
            safe_case_count=safe_count,
            category_counts=dict(Counter(case.category for case in dataset.cases)),
            issues=issues,
        )


def build_vault_catalog(
    db: Session,
    settings: Settings | None = None,
) -> dict[str, VaultCatalogEntry]:
    settings = settings or get_settings()
    notes = db.query(ObsidianNote).all()
    catalog = {
        note.id: VaultCatalogEntry(status=note.index_status, headings=set())
        for note in notes
    }
    if not settings.obsidian_vault_path:
        return catalog
    scanner = ObsidianVaultScanner(
        settings.obsidian_vault_path,
        include_patterns=settings.obsidian_include_pattern_list,
        exclude_dirs=settings.obsidian_exclude_dir_list,
        exclude_paths=settings.obsidian_exclude_path_list,
        max_file_bytes=settings.obsidian_max_file_bytes,
    )
    parser = MarkdownParser()
    for note in notes:
        try:
            path = scanner.resolve_note_path(note.relative_path)
            parsed = parser.parse_file(path, note.relative_path)
        except (OSError, UnicodeError, ValueError):
            catalog[note.id].status = "missing"
            continue
        headings = {tuple(block.heading_path) for block in parsed.blocks}
        headings.add(())
        catalog[note.id].headings = headings
    return catalog
