from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

from app.obsidian.markdown_chunker import StructuredMarkdownChunker
from app.obsidian.markdown_parser import LIST_RE, MarkdownParser
from app.obsidian.models import MarkdownBlock, ObsidianChunk, ParsedMarkdownNote

CHUNKING_VERSION = "markdown-structural-v2"


class MarkdownStructuralV2Chunker(StructuredMarkdownChunker):
    """Evidence-driven structural chunking for isolated Stage 7 experiments."""

    def chunk(
        self,
        note: ParsedMarkdownNote,
        *,
        note_id: str,
        note_hash: str,
    ) -> list[ObsidianChunk]:
        sections = self._consecutive_sections(note.blocks)
        packed_sections = [
            (heading_path, self._pack_section(blocks))
            for heading_path, blocks in sections
        ]
        merged_sections = self._merge_short_sibling_sections(packed_sections)

        chunks: list[ObsidianChunk] = []
        parent_ordinals: defaultdict[str, int] = defaultdict(int)
        for blocks in merged_sections:
            text = "\n\n".join(
                block.text.strip() for block in blocks if block.text.strip()
            ).strip()
            if not text:
                continue
            first, last = blocks[0], blocks[-1]
            heading_paths = list(
                dict.fromkeys(block.heading_path for block in blocks)
            )
            heading_key = " || ".join(
                " > ".join(path) or "<root>" for path in heading_paths
            )
            parent_section_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"obsidian-section-v2:{note_id}:{heading_key}",
                )
            )
            ordinal = parent_ordinals[parent_section_id]
            parent_ordinals[parent_section_id] += 1
            structural_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"obsidian-structural-v2:{parent_section_id}:{ordinal}",
                )
            )
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunk_id = str(
                uuid5(
                    NAMESPACE_URL,
                    (
                        f"obsidian-chunk-v2:{note_id}:{note_hash}:"
                        f"{structural_id}:{first.line_start}:{last.line_end}:"
                        f"{content_hash}"
                    ),
                )
            )
            split_flags = tuple(
                dict.fromkeys(
                    str(block.metadata["split_kind"])
                    for block in blocks
                    if block.metadata.get("split_kind")
                )
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
                    chunk_index=len(chunks),
                    heading_path=first.heading_path,
                    heading_level=first.heading_level,
                    section_title=first.heading_path[-1]
                    if first.heading_path
                    else None,
                    line_start=first.line_start,
                    line_end=last.line_end,
                    block_types=tuple(
                        dict.fromkeys(block.block_type for block in blocks)
                    ),
                    content_hash=content_hash,
                    tags=tuple(note.tags),
                    links=tuple(MarkdownParser.extract_links(text)),
                    metadata={
                        "split_flags": split_flags,
                        "merged_heading_paths": [
                            list(path) for path in heading_paths
                        ],
                        "structure_position": {
                            "line_start": first.line_start,
                            "line_end": last.line_end,
                        },
                    },
                    chunking_version=CHUNKING_VERSION,
                    structural_id=structural_id,
                )
            )
        self._link_siblings(chunks)
        return chunks

    @staticmethod
    def _consecutive_sections(
        blocks: list[MarkdownBlock],
    ) -> list[tuple[tuple[str, ...], list[MarkdownBlock]]]:
        sections: list[tuple[tuple[str, ...], list[MarkdownBlock]]] = []
        for block in blocks:
            if not sections or sections[-1][0] != block.heading_path:
                sections.append((block.heading_path, [block]))
            else:
                sections[-1][1].append(block)
        return sections

    def _pack_section(self, blocks: list[MarkdownBlock]) -> list[list[MarkdownBlock]]:
        split_blocks = [
            split
            for block in blocks
            for split in self._split_block(block)
        ]
        groups: list[list[MarkdownBlock]] = []
        current: list[MarkdownBlock] = []
        for block in split_blocks:
            if not current:
                current = [block]
                continue
            current_size = self._text_size(current)
            combined = self._text_size([*current, block])
            if combined <= self.target_chars or (
                current_size < self.min_chars and combined <= self.max_chars
            ):
                current.append(block)
            else:
                groups.append(current)
                current = [block]
        if current:
            groups.append(current)
        return groups

    def _merge_short_sibling_sections(
        self,
        sections: list[
            tuple[tuple[str, ...], list[list[MarkdownBlock]]]
        ],
    ) -> list[list[MarkdownBlock]]:
        output: list[list[MarkdownBlock]] = []
        index = 0
        while index < len(sections):
            heading_path, groups = sections[index]
            if (
                len(groups) == 1
                and self._text_size(groups[0]) < self.min_chars
                and index + 1 < len(sections)
            ):
                next_path, next_groups = sections[index + 1]
                same_level = len(heading_path) == len(next_path)
                same_h1 = bool(heading_path and next_path) and (
                    heading_path[0] == next_path[0]
                )
                next_size = (
                    self._text_size(next_groups[0])
                    if len(next_groups) == 1
                    else self.target_chars + 1
                )
                combined_size = self._text_size(
                    [*groups[0], *next_groups[0]]
                )
                combined = (
                    len(next_groups) == 1
                    and next_size < self.min_chars
                    and self.min_chars <= combined_size <= self.target_chars
                )
                if same_level and same_h1 and combined:
                    output.append([*groups[0], *next_groups[0]])
                    index += 2
                    continue
            output.extend(groups)
            index += 1
        return output

    def _split_block(self, block: MarkdownBlock) -> list[MarkdownBlock]:
        if block.block_type == "list" and len(block.text) > self.hard_max_chars:
            return self._split_list(block)
        return super()._split_block(block)

    def _split_list(self, block: MarkdownBlock) -> list[MarkdownBlock]:
        lines = block.text.splitlines()
        items: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if LIST_RE.match(line) and current:
                items.append(current)
                current = []
            current.append(line)
        if current:
            items.append(current)

        groups: list[list[str]] = []
        current_lines: list[str] = []
        for item in items:
            item_text = "\n".join(item)
            if len(item_text) > self.hard_max_chars:
                if current_lines:
                    groups.append(current_lines)
                    current_lines = []
                groups.extend(
                    [
                        item_text[start : start + self.max_chars].splitlines()
                        for start in range(0, len(item_text), self.max_chars)
                    ]
                )
                continue
            candidate = "\n".join([*current_lines, *item])
            if current_lines and len(candidate) > self.max_chars:
                groups.append(current_lines)
                current_lines = []
            current_lines.extend(item)
        if current_lines:
            groups.append(current_lines)

        result: list[MarkdownBlock] = []
        line_offset = 0
        for group in groups:
            line_count = max(1, len(group))
            result.append(
                self._derived(
                    block,
                    "\n".join(group),
                    line_offset,
                    line_offset + line_count - 1,
                    "list_split",
                )
            )
            line_offset += line_count
        return result

    @staticmethod
    def _text_size(blocks: list[MarkdownBlock]) -> int:
        return len(
            "\n\n".join(block.text.strip() for block in blocks if block.text.strip())
        )

    @staticmethod
    def _link_siblings(chunks: list[ObsidianChunk]) -> None:
        by_parent: defaultdict[str, list[ObsidianChunk]] = defaultdict(list)
        for chunk in chunks:
            by_parent[chunk.parent_section_id].append(chunk)
        for siblings in by_parent.values():
            for index, chunk in enumerate(siblings):
                chunk.previous_sibling_id = (
                    siblings[index - 1].structural_id if index else None
                )
                chunk.next_sibling_id = (
                    siblings[index + 1].structural_id
                    if index + 1 < len(siblings)
                    else None
                )
