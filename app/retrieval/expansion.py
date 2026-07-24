from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Iterable

from app.domain.retrieved_chunk import RetrievedChunk
from app.services.vector_service import VectorService


class RetrievalExpansionService:
    """Bounded, collection-local one-hop and sibling expansion."""

    def __init__(self, vector_service: VectorService) -> None:
        self.vector_service = vector_service

    def expand_links(
        self,
        initial: list[RetrievedChunk],
        *,
        max_links_per_origin_note: int,
        max_chunks_per_target_note: int = 3,
    ) -> list[RetrievedChunk]:
        if not initial or max_links_per_origin_note <= 0:
            return list(initial)
        catalog = self.vector_service.list_all_obsidian_chunks()
        by_note, by_target = self._catalog_indexes(catalog)
        expanded = list(initial)
        seen = {chunk.chunk_id for chunk in initial}
        origin_ids = list(
            dict.fromkeys(
                chunk.source_id
                for chunk in initial
                if chunk.source_type == "obsidian_note"
            )
        )
        for origin_id in origin_ids:
            target_specs: list[tuple[str, str | None]] = []
            seen_targets: set[tuple[str, str | None]] = set()
            for payload in by_note.get(origin_id, []):
                for link in payload.get("links") or []:
                    target = str(link.get("target") or "").strip()
                    heading = str(link.get("heading") or "").strip() or None
                    key = (target.casefold(), heading.casefold() if heading else None)
                    if target and key not in seen_targets:
                        target_specs.append((target, heading))
                        seen_targets.add(key)
                    if len(target_specs) >= max_links_per_origin_note:
                        break
                if len(target_specs) >= max_links_per_origin_note:
                    break

            for target, heading in target_specs:
                candidates = list(by_target.get(self._target_key(target), []))
                if heading:
                    heading_key = heading.casefold()
                    heading_matches = [
                        payload
                        for payload in candidates
                        if heading_key
                        in {
                            str(value).casefold()
                            for value in payload.get("heading_path") or []
                        }
                    ]
                    candidates = heading_matches or candidates
                for payload in candidates[:max_chunks_per_target_note]:
                    candidate = RetrievedChunk.from_search_result(
                        {"score": None, "payload": payload}
                    )
                    if candidate.chunk_id in seen or candidate.source_id == origin_id:
                        continue
                    metadata = dict(candidate.metadata)
                    metadata.update(
                        {
                            "expansion_type": "link",
                            "expansion_reason": "direct_wikilink_one_hop",
                            "link_origin_note_id": origin_id,
                            "link_target": target,
                        }
                    )
                    expanded.append(
                        candidate.model_copy(
                            update={
                                "score": None,
                                "rerank_score": None,
                                "metadata": metadata,
                            }
                        )
                    )
                    seen.add(candidate.chunk_id)
        return expanded

    def expand_neighbors(
        self,
        reranked: list[RetrievedChunk],
        *,
        window: int,
    ) -> list[RetrievedChunk]:
        if not reranked or window <= 0:
            return list(reranked)
        catalog = self.vector_service.list_all_obsidian_chunks()
        by_note: defaultdict[str, dict[int, dict]] = defaultdict(dict)
        for payload in catalog:
            source_id = str(payload.get("source_id") or "")
            if source_id:
                by_note[source_id][int(payload.get("chunk_index") or 0)] = payload

        expanded: list[RetrievedChunk] = []
        seen: set[str] = set()
        for origin in reranked:
            if origin.chunk_id not in seen:
                expanded.append(origin)
                seen.add(origin.chunk_id)
            if origin.source_type != "obsidian_note" or origin.chunk_index is None:
                continue
            candidates: list[tuple[bool, int, int, dict]] = []
            origin_heading = tuple(origin.metadata.get("heading_path") or [])
            for delta in range(-window, window + 1):
                if delta == 0:
                    continue
                index = origin.chunk_index + delta
                payload = by_note.get(origin.source_id, {}).get(index)
                if payload is None:
                    continue
                candidate_heading = tuple(payload.get("heading_path") or [])
                candidates.append(
                    (
                        candidate_heading != origin_heading,
                        abs(delta),
                        index,
                        payload,
                    )
                )
            for _, _, _, payload in sorted(candidates, key=lambda item: item[:3]):
                candidate = RetrievedChunk.from_search_result(
                    {"score": None, "payload": payload}
                )
                if candidate.chunk_id in seen:
                    continue
                metadata = dict(candidate.metadata)
                metadata.update(
                    {
                        "expansion_type": "neighbor",
                        "expansion_reason": "same_note_adjacent_chunk",
                        "neighbor_origin_chunk_id": origin.chunk_id,
                    }
                )
                expanded.append(
                    candidate.model_copy(
                        update={
                            "score": None,
                            "rerank_score": None,
                            "metadata": metadata,
                        }
                    )
                )
                seen.add(candidate.chunk_id)
        return expanded

    @classmethod
    def _catalog_indexes(
        cls,
        catalog: Iterable[dict],
    ) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        by_note: defaultdict[str, list[dict]] = defaultdict(list)
        by_target: defaultdict[str, list[dict]] = defaultdict(list)
        for payload in catalog:
            source_id = str(payload.get("source_id") or "")
            if not source_id:
                continue
            by_note[source_id].append(payload)
            keys = {
                cls._target_key(str(payload.get("source_name") or "")),
                cls._target_key(str(payload.get("filename") or "")),
                cls._target_key(str(payload.get("source_path") or "")),
            }
            for key in keys:
                if key:
                    by_target[key].append(payload)
        for payloads in by_note.values():
            payloads.sort(key=lambda item: int(item.get("chunk_index") or 0))
        for payloads in by_target.values():
            payloads.sort(key=lambda item: int(item.get("chunk_index") or 0))
        return dict(by_note), dict(by_target)

    @staticmethod
    def _target_key(value: str) -> str:
        normalized = value.replace("\\", "/").strip().removesuffix(".md")
        return PurePosixPath(normalized).name.casefold()
