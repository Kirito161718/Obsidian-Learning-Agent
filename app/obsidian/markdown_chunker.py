from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from uuid import NAMESPACE_URL, uuid5

from app.obsidian.markdown_parser import MarkdownParser
from app.obsidian.models import MarkdownBlock, ObsidianChunk, ParsedMarkdownNote

SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;\.])(?=\s|[\u3400-\u9fff])")


class StructuredMarkdownChunker:
    def __init__(self, *, min_chars: int, target_chars: int, max_chars: int, hard_max_chars: int) -> None:
        if not 0 < min_chars <= target_chars <= max_chars <= hard_max_chars:
            raise ValueError("Chunk limits must satisfy 0 < min <= target <= max <= hard max")
        self.min_chars = min_chars
        self.target_chars = target_chars
        self.max_chars = max_chars
        self.hard_max_chars = hard_max_chars

    def chunk(self, note: ParsedMarkdownNote, *, note_id: str, note_hash: str) -> list[ObsidianChunk]:
        grouped: OrderedDict[tuple[str, ...], list[MarkdownBlock]] = OrderedDict()
        for block in note.blocks:
            grouped.setdefault(block.heading_path, []).extend(self._split_block(block))

        merged: list[list[MarkdownBlock]] = []
        for blocks in grouped.values():
            current: list[MarkdownBlock] = []
            for block in blocks:
                if not current:
                    current = [block]
                    continue
                combined = len("\n\n".join(item.text for item in current + [block]))
                current_size = len("\n\n".join(item.text for item in current))
                if combined <= self.target_chars or (current_size < self.min_chars and combined <= self.max_chars):
                    current.append(block)
                else:
                    merged.append(current)
                    current = [block]
            if current:
                merged.append(current)

        chunks: list[ObsidianChunk] = []
        for index, blocks in enumerate(merged):
            text = "\n\n".join(block.text.strip() for block in blocks if block.text.strip()).strip()
            if not text:
                continue
            first, last = blocks[0], blocks[-1]
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            heading_key = " > ".join(first.heading_path)
            parent_section_id = str(uuid5(NAMESPACE_URL, f"obsidian-section:{note_id}:{heading_key}"))
            chunk_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"obsidian-chunk:{note_id}:{note_hash}:{heading_key}:{first.line_start}:{last.line_end}:{content_hash}",
                )
            )
            block_types = tuple(dict.fromkeys(block.block_type for block in blocks))
            split_flags = tuple(
                str(block.metadata["split_kind"])
                for block in blocks
                if block.metadata.get("split_kind")
            )
            chunks.append(
                ObsidianChunk(
                    chunk_id=chunk_id,
                    parent_section_id=parent_section_id,
                    note_id=note_id,
                    note_hash=note_hash,
                    relative_path=note.relative_path,
                    file_name=note.file_name,
                    title=note.title,
                    text=text,
                    chunk_index=index,
                    heading_path=first.heading_path,
                    heading_level=first.heading_level,
                    section_title=first.heading_path[-1] if first.heading_path else None,
                    line_start=first.line_start,
                    line_end=last.line_end,
                    block_types=block_types,
                    content_hash=content_hash,
                    tags=tuple(note.tags),
                    links=tuple(MarkdownParser.extract_links(text)),
                    metadata={"split_flags": split_flags},
                )
            )
        return chunks

    def _split_block(self, block: MarkdownBlock) -> list[MarkdownBlock]:
        if len(block.text) <= self.max_chars:
            return [block]
        if block.block_type == "code":
            return self._split_code(block) if len(block.text) > self.hard_max_chars else [block]
        if block.block_type == "table":
            return self._split_table(block)
        if block.block_type in {"list", "callout", "blockquote"} and len(block.text) <= self.hard_max_chars:
            return [block]
        return self._split_text(block)

    def _split_code(self, block: MarkdownBlock) -> list[MarkdownBlock]:
        lines = block.text.splitlines()
        opener = lines[0] if lines and re.match(r"^\s*(`{3,}|~{3,})", lines[0]) else "```"
        marker_match = re.match(r"^\s*(`{3,}|~{3,})", opener)
        marker = marker_match.group(1) if marker_match else "```"
        body = lines[1:-1] if len(lines) > 1 and lines[-1].strip().startswith(marker[0] * 3) else lines[1:]
        budget = max(1, self.hard_max_chars - len(opener) - len(marker) - 2)
        groups = self._pack_lines(body, budget)
        return [
            self._derived(block, f"{opener}\n{'\n'.join(group)}\n{marker}", offset, offset + len(group) - 1, "code_split")
            for offset, group in self._with_offsets(groups)
        ]

    def _split_table(self, block: MarkdownBlock) -> list[MarkdownBlock]:
        lines = block.text.splitlines()
        if len(lines) <= 2:
            return self._split_text(block)
        header = lines[:2]
        budget = max(1, self.max_chars - len("\n".join(header)) - 1)
        groups = self._pack_lines(lines[2:], budget)
        return [
            self._derived(block, "\n".join(header + group), offset + 2, offset + len(group) + 1, "table_split")
            for offset, group in self._with_offsets(groups)
        ]

    def _split_text(self, block: MarkdownBlock) -> list[MarkdownBlock]:
        sentences = [part.strip() for part in SENTENCE_BOUNDARY_RE.split(block.text) if part.strip()]
        if len(sentences) == 1:
            sentences = [block.text[index : index + self.max_chars] for index in range(0, len(block.text), self.max_chars)]
        groups: list[list[str]] = []
        current: list[str] = []
        for sentence in sentences:
            pieces = [sentence[index : index + self.max_chars] for index in range(0, len(sentence), self.max_chars)] or [sentence]
            for piece in pieces:
                combined = len("".join(current)) + len(piece)
                if current and combined > self.max_chars:
                    groups.append(current)
                    current = []
                current.append(piece)
        if current:
            groups.append(current)
        return [
            self._derived(block, "".join(group), offset, offset, "text_split")
            for offset, group in self._with_offsets(groups)
        ]

    @staticmethod
    def _pack_lines(lines: list[str], budget: int) -> list[list[str]]:
        groups: list[list[str]] = []
        current: list[str] = []
        size = 0
        for line in lines:
            pieces = [line[index : index + budget] for index in range(0, len(line), budget)] or [""]
            for piece in pieces:
                extra = len(piece) + (1 if current else 0)
                if current and size + extra > budget:
                    groups.append(current)
                    current, size = [], 0
                current.append(piece)
                size += len(piece) + (1 if size else 0)
        if current:
            groups.append(current)
        return groups or [[""]]

    @staticmethod
    def _with_offsets(groups: list[list[str]]) -> list[tuple[int, list[str]]]:
        result: list[tuple[int, list[str]]] = []
        offset = 0
        for group in groups:
            result.append((offset, group))
            offset += len(group)
        return result

    @staticmethod
    def _derived(block: MarkdownBlock, text: str, line_offset: int, line_end_offset: int, kind: str) -> MarkdownBlock:
        metadata = dict(block.metadata)
        metadata["split_kind"] = kind
        return MarkdownBlock(
            block_type=block.block_type,
            text=text,
            line_start=block.line_start + line_offset,
            line_end=min(block.line_end, block.line_start + line_end_offset),
            heading_path=block.heading_path,
            heading_level=block.heading_level,
            metadata=metadata,
        )
