from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.official_sources.errors import official_error

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class NoteDocument:
    path: Path
    raw: bytes
    text: str
    normalized_text: str
    newline: str
    bom: bool
    content_hash: str
    frontmatter: str


@dataclass(frozen=True, slots=True)
class PatchResult:
    content: bytes
    normalized_text: str
    content_hash: str
    unified_diff: str
    change_ratio: float


class NotePatchService:
    def read(self, path: Path) -> NoteDocument:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise official_error("official_note_missing", "Obsidian note is not valid UTF-8") from exc
        newline = "\r\n" if b"\r\n" in raw else "\n"
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return NoteDocument(
            path=path,
            raw=raw,
            text=text,
            normalized_text=normalized,
            newline=newline,
            bom=raw.startswith(b"\xef\xbb\xbf"),
            content_hash=hashlib.sha256(raw).hexdigest(),
            frontmatter=self._frontmatter(normalized),
        )

    def apply(self, document: NoteDocument, operations: list[dict]) -> PatchResult:
        updated = document.normalized_text
        for operation in operations:
            updated = self._apply_one(updated, operation)
        if self._frontmatter(updated) != document.frontmatter:
            raise official_error("official_proposal_invalid", "Proposal attempts to modify Frontmatter")
        self._validate_markdown(document.normalized_text, updated)
        if updated == document.normalized_text:
            content = document.raw
        else:
            rendered = updated.replace("\n", document.newline)
            content = rendered.encode("utf-8")
            if document.bom:
                content = b"\xef\xbb\xbf" + content
        unified = "\n".join(
            difflib.unified_diff(
                document.normalized_text.splitlines(),
                updated.splitlines(),
                fromfile=document.path.name,
                tofile=document.path.name,
                lineterm="",
            )
        )
        ratio = 1.0 - difflib.SequenceMatcher(None, document.normalized_text, updated, autojunk=False).ratio()
        return PatchResult(
            content=content,
            normalized_text=updated,
            content_hash=hashlib.sha256(content).hexdigest(),
            unified_diff=unified,
            change_ratio=max(0.0, min(1.0, ratio)),
        )

    def _apply_one(self, text: str, operation: dict) -> str:
        operation_type = str(operation["operation_type"])
        heading_path = [str(item) for item in operation.get("target_heading_path") or []]
        replacement = str(operation.get("replacement_markdown") or "").replace("\r\n", "\n").replace("\r", "\n")
        if operation_type == "append_section":
            start, end = self._section_span(text, heading_path)
            insertion = replacement.strip("\n") + "\n"
            prefix = "\n" if end > start and not text[:end].endswith("\n\n") else ""
            return text[:end] + prefix + insertion + text[end:]

        target = str(operation.get("target_excerpt") or "").replace("\r\n", "\n").replace("\r", "\n")
        if not target:
            raise official_error("official_proposal_invalid", "Edit operation target excerpt is required")
        start, end = self._section_span(text, heading_path)
        section = text[start:end]
        matches = [match.start() for match in re.finditer(re.escape(target), section)]
        if not matches:
            raise official_error("official_proposal_stale", "Edit target no longer matches the note", status_code=409)
        if len(matches) != 1:
            raise official_error("official_proposal_invalid", "Edit target is ambiguous")
        absolute = start + matches[0]
        if operation_type == "replace":
            return text[:absolute] + replacement + text[absolute + len(target) :]
        if operation_type == "insert_before":
            return text[:absolute] + replacement + text[absolute:]
        if operation_type == "insert_after":
            return text[: absolute + len(target)] + replacement + text[absolute + len(target) :]
        raise official_error("official_proposal_invalid", "Edit operation type is not allowed")

    @staticmethod
    def _frontmatter(text: str) -> str:
        if not text.startswith("---\n"):
            return ""
        end = text.find("\n---\n", 4)
        return text[: end + 5] if end >= 0 else text

    def _section_span(self, text: str, heading_path: list[str]) -> tuple[int, int]:
        body_start = len(self._frontmatter(text))
        if not heading_path:
            return body_start, len(text)
        stack: list[str] = []
        candidates: list[tuple[int, int, int]] = []
        matches = list(HEADING_RE.finditer(text, body_start))
        for index, match in enumerate(matches):
            level = len(match.group(1))
            title = match.group(2).strip()
            stack = stack[: level - 1]
            stack.append(title)
            if stack == heading_path:
                end = len(text)
                for following in matches[index + 1 :]:
                    if len(following.group(1)) <= level:
                        end = following.start()
                        break
                candidates.append((match.start(), end, level))
        if not candidates:
            raise official_error("official_proposal_invalid", "Target heading path was not found")
        if len(candidates) != 1:
            raise official_error("official_proposal_invalid", "Target heading path is ambiguous")
        return candidates[0][0], candidates[0][1]

    @staticmethod
    def _validate_markdown(before: str, after: str) -> None:
        for marker in ("```", "~~~"):
            before_count = sum(1 for line in before.splitlines() if line.lstrip().startswith(marker))
            after_count = sum(1 for line in after.splitlines() if line.lstrip().startswith(marker))
            if before_count % 2 or after_count % 2:
                raise official_error("official_proposal_invalid", "Proposal breaks a Markdown code fence")
        if "|" in before and re.search(r"^\s*\|?(?:\s*:?-+:?\s*\|)+", before, re.MULTILINE):
            if not re.search(r"^\s*\|?(?:\s*:?-+:?\s*\|)+", after, re.MULTILINE):
                raise official_error("official_proposal_invalid", "Proposal breaks a Markdown table")
