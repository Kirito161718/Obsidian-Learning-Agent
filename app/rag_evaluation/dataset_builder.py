from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import ObsidianNote
from app.obsidian.markdown_parser import MarkdownParser
from app.obsidian.models import MarkdownBlock
from app.obsidian.scanner import ObsidianVaultScanner
from app.rag_evaluation.schemas import (
    ConversationTurn,
    EvaluationCase,
    EvaluationDataset,
    RelevanceJudgment,
    SourceScope,
)


@dataclass(slots=True)
class _NoteMaterial:
    note: ObsidianNote
    blocks: list[MarkdownBlock]

    @property
    def primary_block(self) -> MarkdownBlock:
        return next(
            (
                block
                for block in self.blocks
                if block.block_type == "paragraph" and len(block.text.strip()) >= 80
            ),
            next(
                (
                    block
                    for block in self.blocks
                    if block.block_type == "paragraph" and block.text.strip()
                ),
                self.blocks[0],
            ),
        )


class RealDatasetBuilder:
    """Build a reviewed local dataset from the configured Vault without modifying it."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build(self, db: Session) -> EvaluationDataset:
        materials = self._materials(db)
        if len(materials) < 12:
            raise ValueError("At least 12 indexed Markdown notes are required")
        cases: list[EvaluationCase] = []

        for material in materials[:6]:
            block = material.primary_block
            cases.append(
                self._answerable_case(
                    case_id=f"rag-case-{len(cases) + 1:03d}",
                    category="single_note",
                    question=(
                        f"根据《{material.note.title or material.note.file_name}》，"
                        f"{self._heading_label(block)}这一部分的核心要点是什么？"
                    ),
                    materials=[(material, block, 3)],
                    tags=["single-note"],
                )
            )

        for index in range(5):
            left = materials[6 + index * 2]
            right = materials[7 + index * 2]
            cases.append(
                self._answerable_case(
                    case_id=f"rag-case-{len(cases) + 1:03d}",
                    category="multi_note",
                    question=(
                        f"综合《{left.note.title or left.note.file_name}》与"
                        f"《{right.note.title or right.note.file_name}》，分别概括两者"
                        "记录的关键做法。"
                    ),
                    materials=[
                        (left, left.primary_block, 3),
                        (right, right.primary_block, 3),
                    ],
                    tags=["multi-note"],
                )
            )

        for material in materials[16:21]:
            block = material.primary_block
            title = material.note.title or material.note.file_name
            case = self._answerable_case(
                case_id=f"rag-case-{len(cases) + 1:03d}",
                category="conversational_rewrite",
                question=f"那它在{self._heading_label(block)}里具体是怎么说明的？",
                materials=[(material, block, 3)],
                tags=["rewrite", "multi-turn"],
            )
            case.requires_rewrite = True
            case.conversation_context = [
                ConversationTurn(role="user", content=f"请先介绍《{title}》的主题。"),
                ConversationTurn(
                    role="assistant",
                    content="我会以这篇笔记为当前讨论对象，并只依据知识库说明。",
                ),
            ]
            cases.append(case)

        link_pairs = self._link_pairs(materials)
        for source, target in link_pairs[:5]:
            source_block = source.primary_block
            cases.append(
                self._answerable_case(
                    case_id=f"rag-case-{len(cases) + 1:03d}",
                    category="internal_link",
                    question=(
                        f"《{source.note.title or source.note.file_name}》关联到"
                        f"《{target.note.title or target.note.file_name}》时，"
                        "两边分别提供了哪些上下文？"
                    ),
                    materials=[
                        (source, source_block, 3),
                        (target, target.primary_block, 2),
                    ],
                    tags=["internal-link"],
                )
            )

        while sum(case.category == "internal_link" for case in cases) < 5:
            offset = sum(case.category == "internal_link" for case in cases)
            source = materials[21 + offset * 2]
            target = materials[22 + offset * 2]
            cases.append(
                self._answerable_case(
                    case_id=f"rag-case-{len(cases) + 1:03d}",
                    category="internal_link",
                    question=(
                        f"要把《{source.note.title or source.note.file_name}》与"
                        f"《{target.note.title or target.note.file_name}》串联理解，"
                        "需要同时找回哪些要点？"
                    ),
                    materials=[
                        (source, source.primary_block, 3),
                        (target, target.primary_block, 2),
                    ],
                    tags=["internal-link", "cross-note"],
                )
            )

        no_answer_questions = (
            "这些笔记是否给出了火星基地在 2099 年的最终预算数字？",
            "知识库是否记录了尚未发布的软件版本的确定发布日期？",
            "现有资料能否证明一个未被记录的第三方账户密码？",
            "笔记是否包含对未来市场价格的确定性预测？",
        )
        for question in no_answer_questions:
            cases.append(
                EvaluationCase(
                    id=f"rag-case-{len(cases) + 1:03d}",
                    category="no_answer",
                    question=question,
                    should_answer=False,
                    requires_rewrite=False,
                    source_scope=SourceScope(note_ids=[]),
                    relevance_judgments=[],
                    expected_answer_points=[],
                    acceptable_citation_note_ids=[],
                    forbidden_citation_note_ids=[],
                    tags=["no-answer", "public-safe"],
                    share_with_langsmith=True,
                    review_status="reviewed",
                )
            )

        for index in range(4):
            left = materials[-(index * 2 + 1)]
            right = materials[-(index * 2 + 2)]
            cases.append(
                self._answerable_case(
                    case_id=f"rag-case-{len(cases) + 1:03d}",
                    category="conflicting_sources",
                    question=(
                        f"对照《{left.note.title or left.note.file_name}》与"
                        f"《{right.note.title or right.note.file_name}》，"
                        "两份资料的表述是否存在差异或不确定之处？请分别说明依据。"
                    ),
                    materials=[
                        (left, left.primary_block, 3),
                        (right, right.primary_block, 3),
                    ],
                    tags=["conflict", "uncertainty"],
                )
            )

        return EvaluationDataset(
            name="obsidian-learning-agent-rag-eval",
            version="obsidian-real-v1",
            description=(
                "Private local evaluation dataset derived from the configured Vault. "
                "Do not commit this file."
            ),
            cases=cases,
        )

    def write(
        self,
        dataset: EvaluationDataset,
        *,
        file_name: str = "obsidian-real-v1.yaml",
        overwrite: bool = False,
    ) -> Path:
        if Path(file_name).name != file_name or Path(file_name).suffix not in {
            ".yaml",
            ".yml",
        }:
            raise ValueError("Dataset output must be a YAML file name")
        root = self.settings.rag_evaluation_dataset_dir
        root.mkdir(parents=True, exist_ok=True)
        path = root / file_name
        if path.exists() and not overwrite:
            raise FileExistsError("Dataset already exists; use a new version or --force")
        path.write_text(
            yaml.safe_dump(
                dataset.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
                width=100,
            ),
            encoding="utf-8",
        )
        return path

    def _materials(self, db: Session) -> list[_NoteMaterial]:
        scanner = ObsidianVaultScanner(
            self.settings.obsidian_vault_path,
            include_patterns=self.settings.obsidian_include_pattern_list,
            exclude_dirs=self.settings.obsidian_exclude_dir_list,
            exclude_paths=self.settings.obsidian_exclude_path_list,
            max_file_bytes=self.settings.obsidian_max_file_bytes,
        )
        parser = MarkdownParser()
        result: list[_NoteMaterial] = []
        notes = (
            db.query(ObsidianNote)
            .filter(ObsidianNote.index_status == "indexed")
            .order_by(ObsidianNote.relative_path)
            .all()
        )
        for note in notes:
            try:
                parsed = parser.parse_file(
                    scanner.resolve_note_path(note.relative_path),
                    note.relative_path,
                )
            except (OSError, UnicodeError, ValueError):
                continue
            usable = [
                block
                for block in parsed.blocks
                if block.text.strip() and block.block_type != "code"
            ]
            if usable and sum(len(block.text) for block in usable) >= 300:
                result.append(_NoteMaterial(note=note, blocks=usable))
        return result

    @staticmethod
    def _link_pairs(
        materials: list[_NoteMaterial],
    ) -> list[tuple[_NoteMaterial, _NoteMaterial]]:
        by_key: dict[str, _NoteMaterial] = {}
        for material in materials:
            note = material.note
            keys = {
                Path(note.relative_path).stem,
                str(note.title or ""),
                *(note.aliases or []),
            }
            for key in keys:
                normalized = key.strip().casefold()
                if normalized:
                    by_key.setdefault(normalized, material)
        pairs: list[tuple[_NoteMaterial, _NoteMaterial]] = []
        seen: set[tuple[str, str]] = set()
        for source in materials:
            for link in source.note.outbound_links or []:
                target_key = Path(str(link.get("target") or "")).stem.casefold()
                target = by_key.get(target_key)
                if target is None or target.note.id == source.note.id:
                    continue
                key = source.note.id, target.note.id
                if key not in seen:
                    pairs.append((source, target))
                    seen.add(key)
        return pairs

    def _answerable_case(
        self,
        *,
        case_id: str,
        category: str,
        question: str,
        materials: list[tuple[_NoteMaterial, MarkdownBlock, int]],
        tags: list[str],
    ) -> EvaluationCase:
        note_ids = [material.note.id for material, _, _ in materials]
        return EvaluationCase(
            id=case_id,
            category=category,
            question=question,
            should_answer=True,
            source_scope=SourceScope(note_ids=note_ids),
            relevance_judgments=[
                RelevanceJudgment(
                    note_id=material.note.id,
                    heading_path=list(block.heading_path),
                    relevance_grade=grade,
                )
                for material, block, grade in materials
            ],
            expected_answer_points=[
                self._reference_point(block.text) for _, block, _ in materials
            ],
            acceptable_citation_note_ids=note_ids,
            forbidden_citation_note_ids=[],
            tags=tags,
            share_with_langsmith=False,
            review_status="reviewed",
        )

    @staticmethod
    def _heading_label(block: MarkdownBlock) -> str:
        return f"“{block.heading_path[-1]}”" if block.heading_path else "正文"

    @staticmethod
    def _reference_point(text: str) -> str:
        cleaned = re.sub(r"[#>*_`|~-]+", " ", text)
        cleaned = " ".join(cleaned.split())
        sentence = re.split(r"(?<=[。！？.!?])\s*", cleaned, maxsplit=1)[0]
        fallback = " ".join(text.split())
        return (sentence or cleaned or fallback or "该章节的结构化说明")[:180]
