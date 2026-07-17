from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from app.obsidian.models import MarkdownBlock, ParsedMarkdownNote

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
WIKILINK_RE = re.compile(r"(!)?\[\[([^\]]+)]]")
TAG_RE = re.compile(r"(?<![\w`])#([\w\-/\u0080-\uffff]+)", re.UNICODE)


class MarkdownParser:
    def parse_file(self, path: Path, relative_path: str) -> ParsedMarkdownNote:
        return self.parse(path.read_text(encoding="utf-8-sig"), relative_path)

    def parse(self, text: str, relative_path: str) -> ParsedMarkdownNote:
        lines = text.splitlines()
        frontmatter, body_start, warnings = self._frontmatter(lines)
        blocks, first_h1 = self._blocks(lines, body_start)
        aliases = self._string_list(frontmatter.get("aliases") or frontmatter.get("alias"))
        frontmatter_tags = self._string_list(frontmatter.get("tags") or frontmatter.get("tag"), split_hashes=True)
        inline_text = "\n".join(block.text for block in blocks if block.block_type != "code")
        inline_tags = [match.group(1).strip("/") for match in TAG_RE.finditer(inline_text)]
        tags = self._dedupe(frontmatter_tags + inline_tags)
        outbound_links = self.extract_links(inline_text)
        file_name = Path(relative_path).name
        title_value = frontmatter.get("title")
        title = str(title_value).strip() if title_value is not None else ""
        title = title or first_h1 or Path(file_name).stem
        return ParsedMarkdownNote(
            relative_path=relative_path.replace("\\", "/"),
            file_name=file_name,
            title=title,
            frontmatter=frontmatter,
            tags=tags,
            aliases=aliases,
            outbound_links=outbound_links,
            blocks=blocks,
            warnings=warnings,
        )

    def _frontmatter(self, lines: list[str]) -> tuple[dict[str, Any], int, list[str]]:
        if not lines or lines[0].strip() != "---":
            return {}, 0, []
        closing = next((index for index in range(1, len(lines)) if lines[index].strip() in {"---", "..."}), None)
        if closing is None:
            return {}, 0, ["frontmatter_closing_delimiter_missing"]
        try:
            loaded = yaml.safe_load("\n".join(lines[1:closing])) or {}
            if not isinstance(loaded, dict):
                return {}, closing + 1, ["frontmatter_is_not_mapping"]
            return self._json_safe(loaded), closing + 1, []
        except yaml.YAMLError:
            return {}, closing + 1, ["frontmatter_yaml_invalid"]

    def _blocks(self, lines: list[str], start: int) -> tuple[list[MarkdownBlock], str | None]:
        blocks: list[MarkdownBlock] = []
        headings: list[str] = []
        first_h1: str | None = None
        index = start
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                continue
            heading = HEADING_RE.match(line)
            if heading:
                level = len(heading.group(1))
                title = heading.group(2).strip()
                headings = headings[: level - 1]
                while len(headings) < level - 1:
                    headings.append("")
                headings.append(title)
                if level == 1 and first_h1 is None:
                    first_h1 = title
                index += 1
                continue
            fence = FENCE_RE.match(line)
            if fence:
                end = index + 1
                marker = fence.group(1)[0]
                minimum = len(fence.group(1))
                closing_re = re.compile(rf"^\s*{re.escape(marker)}{{{minimum},}}\s*$")
                while end < len(lines) and not closing_re.match(lines[end]):
                    end += 1
                if end < len(lines):
                    end += 1
                blocks.append(self._block("code", lines, index, end, headings, {"fence": fence.group(1), "language": fence.group(2).strip()}))
                index = end
                continue
            if index + 1 < len(lines) and "|" in line and TABLE_SEPARATOR_RE.match(lines[index + 1]):
                end = index + 2
                while end < len(lines) and lines[end].strip() and "|" in lines[end] and not HEADING_RE.match(lines[end]):
                    end += 1
                blocks.append(self._block("table", lines, index, end, headings))
                index = end
                continue
            if LIST_RE.match(line):
                end = index + 1
                while end < len(lines) and lines[end].strip() and (LIST_RE.match(lines[end]) or lines[end].startswith((" ", "\t"))):
                    end += 1
                blocks.append(self._block("list", lines, index, end, headings))
                index = end
                continue
            if line.lstrip().startswith(">"):
                end = index + 1
                while end < len(lines) and (lines[end].lstrip().startswith(">") or not lines[end].strip()):
                    end += 1
                block_type = "callout" if re.match(r"^\s*>\s*\[![^]]+]", line, re.IGNORECASE) else "blockquote"
                blocks.append(self._block(block_type, lines, index, end, headings))
                index = end
                continue
            end = index + 1
            while end < len(lines) and lines[end].strip() and not self._starts_structure(lines, end):
                end += 1
            blocks.append(self._block("paragraph", lines, index, end, headings))
            index = end
        return blocks, first_h1

    def _starts_structure(self, lines: list[str], index: int) -> bool:
        line = lines[index]
        return bool(
            HEADING_RE.match(line)
            or FENCE_RE.match(line)
            or LIST_RE.match(line)
            or line.lstrip().startswith(">")
            or (index + 1 < len(lines) and "|" in line and TABLE_SEPARATOR_RE.match(lines[index + 1]))
        )

    @staticmethod
    def _block(
        block_type: str,
        lines: list[str],
        start: int,
        end: int,
        headings: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> MarkdownBlock:
        return MarkdownBlock(
            block_type=block_type,
            text="\n".join(lines[start:end]).strip(),
            line_start=start + 1,
            line_end=end,
            heading_path=tuple(value for value in headings if value),
            heading_level=len(headings),
            metadata=metadata or {},
        )

    @staticmethod
    def extract_links(text: str) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for match in WIKILINK_RE.finditer(text):
            raw = match.group(2).strip()
            target_and_heading, _, alias = raw.partition("|")
            target, separator, heading = target_and_heading.partition("#")
            item = {
                "target": target.strip(),
                "heading": heading.strip() if separator else None,
                "alias": alias.strip() or None,
                "embed": bool(match.group(1)),
            }
            key = (item["target"], item["heading"], item["alias"], item["embed"])
            if key not in seen:
                links.append(item)
                seen.add(key)
        return links

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _string_list(value: Any, *, split_hashes: bool = False) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, (list, tuple, set)) else [value]
        result: list[str] = []
        for item in values:
            raw_values = re.split(r"[,\s]+", str(item)) if split_hashes else [str(item)]
            result.extend(raw.strip().lstrip("#") for raw in raw_values if raw.strip().lstrip("#"))
        return MarkdownParser._dedupe(result)

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))
