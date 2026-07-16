from dataclasses import dataclass

from app.domain.retrieved_chunk import RetrievedChunk


@dataclass(slots=True)
class ContextBuildResult:
    context_text: str
    context_sources: list[dict]
    chunks: list[RetrievedChunk]


class ContextBuilder:
    def __init__(self, max_chars: int, max_chunks_per_source: int) -> None:
        self.max_chars = max(0, max_chars)
        self.max_chunks_per_source = max(1, max_chunks_per_source)

    def build(self, chunks: list[RetrievedChunk], max_chunks: int) -> ContextBuildResult:
        selected: list[RetrievedChunk] = []
        seen_chunks: set[str] = set()
        source_counts: dict[str, int] = {}
        blocks: list[str] = []
        sources: list[dict] = []
        used_chars = 0

        for chunk in chunks:
            if len(selected) >= max(0, max_chunks):
                break
            if chunk.chunk_id in seen_chunks:
                continue
            if source_counts.get(chunk.source_id, 0) >= self.max_chunks_per_source:
                continue

            citation_index = len(selected) + 1
            header = self._format_header(chunk, citation_index)
            separator_chars = 2 if blocks else 0
            remaining = self.max_chars - used_chars - separator_chars - len(header)
            if remaining <= 0:
                break
            text = chunk.text.strip()[:remaining]
            if not text:
                continue
            block = f"{header}{text}"

            selected.append(chunk)
            seen_chunks.add(chunk.chunk_id)
            source_counts[chunk.source_id] = source_counts.get(chunk.source_id, 0) + 1
            blocks.append(block)
            used_chars += separator_chars + len(block)
            sources.append(
                {
                    "citation_index": citation_index,
                    "citation_label": f"[来源{citation_index}]",
                    "chunk_id": chunk.chunk_id,
                    "source_id": chunk.source_id,
                    "source_type": chunk.source_type,
                    "source_name": chunk.source_name,
                    "section_title": chunk.section_title,
                    "page_no": chunk.page_no,
                    "chunk_index": chunk.chunk_index,
                }
            )

        return ContextBuildResult(context_text="\n\n".join(blocks), context_sources=sources, chunks=selected)

    def _format_header(self, chunk: RetrievedChunk, citation_index: int) -> str:
        section = chunk.section_title or "未标注"
        page = str(chunk.page_no) if chunk.page_no is not None else "未标注"
        return (
            f"[来源{citation_index}]\n"
            f"来源类型：{chunk.source_type}\n"
            f"文件：{chunk.source_name}\n"
            f"章节：{section}\n"
            f"页码：{page}\n"
            "内容："
        )
