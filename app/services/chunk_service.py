from dataclasses import dataclass

from app.services.parser_service import ParsedSection


@dataclass(slots=True)
class Chunk:
    text: str
    page_no: int | None
    section_title: str | None
    chunk_index: int


class ChunkService:
    def __init__(self, chunk_size: int = 900, overlap: int = 120) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, sections: list[ParsedSection]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in sections:
            normalized = "\n".join(line.strip() for line in section.text.splitlines() if line.strip())
            if not normalized:
                continue
            start = 0
            while start < len(normalized):
                end = min(start + self.chunk_size, len(normalized))
                piece = normalized[start:end].strip()
                if piece:
                    chunks.append(
                        Chunk(
                            text=piece,
                            page_no=section.page_no,
                            section_title=section.section_title,
                            chunk_index=len(chunks),
                        )
                    )
                if end == len(normalized):
                    break
                start = max(end - self.overlap, start + 1)
        return chunks
