from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from app.core.config import Settings
from app.official_sources.errors import official_error

NORMALIZER_VERSION = "official-v1"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    text: str
    normalized_format: str
    content_hash: str
    section_manifest: list[dict]


class OfficialContentNormalizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def normalize(
        self,
        body: bytes,
        *,
        content_type: str,
        source_type: str = "auto",
        content_selector: str | None = None,
    ) -> NormalizedDocument:
        resolved_type = self._resolve_type(content_type, source_type)
        if resolved_type == "html":
            text = self._html(body, content_selector)
        elif resolved_type == "pdf":
            text = self._pdf(body)
        else:
            text = self._decode_utf8(body)
        text = self._normalize_text(text)
        if not text.strip():
            raise official_error("official_content_empty", "Official source content is empty")
        if resolved_type == "html" and len(text) > self.settings.official_html_max_chars:
            raise official_error("official_response_too_large", "Normalized HTML exceeds the configured limit")
        manifest = build_section_manifest(text)
        return NormalizedDocument(
            text=text,
            normalized_format="markdown" if resolved_type in {"html", "markdown", "pdf"} else "text",
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            section_manifest=manifest,
        )

    @staticmethod
    def _resolve_type(content_type: str, source_type: str) -> str:
        detected = {
            "text/html": "html",
            "text/markdown": "markdown",
            "text/plain": "text",
            "application/pdf": "pdf",
        }.get(content_type)
        if detected is None:
            raise official_error("official_content_type_unsupported", "Official source content type is not supported")
        if source_type != "auto" and source_type != detected:
            raise official_error("official_content_type_unsupported", "Official source type does not match the response")
        return detected

    def _html(self, body: bytes, selector: str | None) -> str:
        text = self._decode_utf8(body)
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup.select(
            "script,style,noscript,nav,header,footer,aside,form,svg,canvas,iframe,"
            "[role='navigation'],.nav,.navbar,.sidebar,.advertisement,.ad,.hidden"
        ):
            tag.decompose()
        for tag in list(soup.find_all(True)):
            style = str(tag.get("style") or "").replace(" ", "").casefold()
            if tag.has_attr("hidden") or str(tag.get("aria-hidden") or "").casefold() == "true" or any(
                marker in style for marker in ("display:none", "visibility:hidden")
            ):
                tag.decompose()
        root: Tag | BeautifulSoup
        if selector:
            try:
                selected = soup.select_one(selector)
            except Exception as exc:
                raise official_error("official_content_empty", "Official content selector is invalid") from exc
            if selected is None:
                raise official_error("official_content_empty", "Official content selector matched no content")
            root = selected
        else:
            root = soup.find("main") or soup.find("article") or soup.body or soup
        rendered = self._render_children(root)
        return "\n".join(line for line in rendered if line is not None)

    def _render_children(self, root: Tag | BeautifulSoup) -> list[str]:
        lines: list[str] = []
        for child in root.children:
            if isinstance(child, NavigableString):
                value = " ".join(str(child).split())
                if value:
                    lines.append(value)
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name.casefold()
            content = " ".join(child.get_text(" ", strip=True).split())
            if not content:
                continue
            if name in {f"h{i}" for i in range(1, 7)}:
                lines.extend([f"{'#' * int(name[1])} {content}", ""])
            elif name == "pre":
                lines.extend(["```", child.get_text("\n").strip("\n"), "```", ""])
            elif name == "table":
                rows = []
                for row in child.find_all("tr"):
                    cells = [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.find_all(["th", "td"])]
                    if cells:
                        rows.append(cells)
                if rows:
                    width = max(len(row) for row in rows)
                    rows = [row + [""] * (width - len(row)) for row in rows]
                    lines.append("| " + " | ".join(rows[0]) + " |")
                    lines.append("| " + " | ".join(["---"] * width) + " |")
                    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
                    lines.append("")
            elif name in {"ul", "ol"}:
                for index, item in enumerate(child.find_all("li", recursive=False), start=1):
                    marker = f"{index}." if name == "ol" else "-"
                    lines.append(f"{marker} {' '.join(item.get_text(' ', strip=True).split())}")
                lines.append("")
            elif name in {"p", "blockquote"}:
                prefix = "> " if name == "blockquote" else ""
                lines.extend([prefix + content, ""])
            elif name in {"div", "section", "article", "main", "body"}:
                lines.extend(self._render_children(child))
            else:
                lines.extend([content, ""])
        return lines

    def _pdf(self, body: bytes) -> str:
        self.settings.official_temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.settings.official_temp_dir / f"pdf-{hashlib.sha256(body).hexdigest()[:16]}.pdf"
        try:
            temp_path.write_bytes(body)
            try:
                import pypdfium2 as pdfium

                document = pdfium.PdfDocument(temp_path)
                if len(document) > self.settings.official_pdf_max_pages:
                    raise official_error("official_pdf_unreadable", "Official PDF exceeds the configured page limit")
                pages: list[str] = []
                for index in range(len(document)):
                    page = document[index]
                    text_page = page.get_textpage()
                    value = text_page.get_text_bounded().strip()
                    if value:
                        pages.extend([f"## Page {index + 1}", "", value, ""])
                    text_page.close()
                    page.close()
                document.close()
            except OfficialSourceError:
                raise
            except Exception as exc:
                raise official_error("official_pdf_unreadable", "Official PDF could not be read") from exc
            text = "\n".join(pages)
            if not text.strip():
                raise official_error("official_pdf_unreadable", "Image-only PDFs are not supported")
            return text
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _decode_utf8(body: bytes) -> str:
        try:
            return body.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise official_error("official_fetch_failed", "Official source must use UTF-8 encoding") from exc

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        lines = [line.rstrip() for line in text.split("\n")]
        compact: list[str] = []
        blank = False
        for line in lines:
            if not line.strip():
                if not blank:
                    compact.append("")
                blank = True
            else:
                compact.append(line)
                blank = False
        return "\n".join(compact).strip() + "\n"


def build_section_manifest(text: str) -> list[dict]:
    headings: list[tuple[int, int, str, list[str], str]] = []
    stack: list[str] = []
    counts: dict[str, int] = {}
    position = 0
    for line in text.splitlines(keepends=True):
        match = HEADING_RE.match(line.rstrip("\n"))
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            stack = stack[: level - 1]
            stack.append(title)
            key = "/".join(part.casefold() for part in stack)
            counts[key] = counts.get(key, 0) + 1
            section_id = "section-" + hashlib.sha256(f"{key}#{counts[key]}".encode()).hexdigest()[:16]
            headings.append((position, level, title, list(stack), section_id))
        position += len(line)
    if not headings:
        return [{"section_id": "section-root", "heading_path": [], "level": 0, "start": 0, "end": len(text), "chars": len(text)}]
    manifest: list[dict] = []
    for index, (start, level, title, path, section_id) in enumerate(headings):
        end = len(text)
        for next_start, next_level, *_ in headings[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        manifest.append(
            {"section_id": section_id, "heading_path": path, "title": title, "level": level, "start": start, "end": end, "chars": end - start}
        )
    return manifest


def sections_with_content(text: str, manifest: list[dict] | None = None) -> dict[str, str]:
    manifest = manifest or build_section_manifest(text)
    return {item["section_id"]: text[int(item["start"]): int(item["end"])] for item in manifest}
