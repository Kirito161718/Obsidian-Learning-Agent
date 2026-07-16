from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ParsedSection:
    text: str
    page_no: int | None = None
    section_title: str | None = None


class ParserService:
    def parse(self, file_path: Path, file_type: str) -> list[ParsedSection]:
        if file_type in {"txt", "csv"}:
            return self._parse_plain_text(file_path)
        return self._parse_with_docling(file_path)

    def _parse_plain_text(self, file_path: Path) -> list[ParsedSection]:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        return [ParsedSection(text=text, page_no=1)]

    def _parse_with_docling(self, file_path: Path) -> list[ParsedSection]:
        try:
            from docling.document_converter import DocumentConverter
        except Exception as exc:  # pragma: no cover - import depends on optional native deps
            raise RuntimeError("Docling is not installed or failed to import") from exc

        converter = DocumentConverter()
        # 需要根据当前官方文档核验：Docling 2.x 的 convert/export API 仍可能调整。
        result = converter.convert(file_path)
        document = result.document
        text = document.export_to_markdown()
        return [ParsedSection(text=text, page_no=1)]
