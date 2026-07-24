from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from statistics import mean
from typing import Any


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def _split_flags(payload: dict[str, Any]) -> set[str]:
    metadata = payload.get("chunk_metadata") or {}
    return {
        str(value)
        for value in metadata.get("split_flags") or payload.get("split_flags") or []
    }


def _terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", text.casefold())
    if len(normalized) < 3:
        return {normalized} if normalized else set()
    return {normalized[index : index + 3] for index in range(len(normalized) - 2)}


def analyze_chunk_payloads(
    payloads: list[dict[str, Any]],
    *,
    min_chars: int,
    max_chars: int,
) -> dict[str, Any]:
    obsidian = [
        payload
        for payload in payloads
        if payload.get("source_type") == "obsidian_note"
    ]
    lengths = [len(str(payload.get("text") or "")) for payload in obsidian]
    per_note: Counter[str] = Counter(
        str(payload.get("source_id") or "") for payload in obsidian
    )
    content_hashes = [
        str(payload.get("content_hash") or "")
        for payload in obsidian
        if payload.get("content_hash")
    ]
    duplicate_count = len(content_hashes) - len(set(content_hashes))
    no_heading = sum(not (payload.get("heading_path") or []) for payload in obsidian)
    cross_heading = sum(
        len(
            (payload.get("chunk_metadata") or {}).get("merged_heading_paths")
            or []
        )
        > 1
        for payload in obsidian
    )

    atomic_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    for payload in obsidian:
        types = {str(value) for value in payload.get("block_types") or []}
        flags = _split_flags(payload)
        for block_type, split_flag in (
            ("code", "code_split"),
            ("table", "table_split"),
            ("list", "list_split"),
        ):
            if block_type in types:
                atomic_counts[block_type] += 1
                if split_flag in flags:
                    split_counts[block_type] += 1

    by_note: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in obsidian:
        by_note[str(payload.get("source_id") or "")].append(payload)
    overlaps: list[float] = []
    for chunks in by_note.values():
        chunks.sort(key=lambda item: int(item.get("chunk_index") or 0))
        for left, right in zip(chunks, chunks[1:]):
            left_terms = _terms(str(left.get("text") or ""))
            right_terms = _terms(str(right.get("text") or ""))
            union = left_terms | right_terms
            overlaps.append(
                len(left_terms & right_terms) / len(union) if union else 0.0
            )

    note_counts = [float(value) for value in per_note.values()]
    total = len(obsidian)
    ratio = lambda count: count / total if total else 0.0
    return {
        "chunk_total": total,
        "note_total": len(per_note),
        "chunks_per_note": {
            "min": min(note_counts, default=0.0),
            "mean": mean(note_counts) if note_counts else 0.0,
            "p50": _percentile(note_counts, 0.50),
            "p90": _percentile(note_counts, 0.90),
            "p95": _percentile(note_counts, 0.95),
            "max": max(note_counts, default=0.0),
        },
        "length_chars": {
            "min": min(lengths, default=0),
            "mean": mean(lengths) if lengths else 0.0,
            "p50": _percentile([float(value) for value in lengths], 0.50),
            "p90": _percentile([float(value) for value in lengths], 0.90),
            "p95": _percentile([float(value) for value in lengths], 0.95),
            "max": max(lengths, default=0),
        },
        "short_chunk_ratio": ratio(sum(value < min_chars for value in lengths)),
        "long_chunk_ratio": ratio(sum(value > max_chars for value in lengths)),
        "no_heading_ratio": ratio(no_heading),
        "cross_heading_ratio": ratio(cross_heading),
        "code_split_ratio": (
            split_counts["code"] / atomic_counts["code"]
            if atomic_counts["code"]
            else 0.0
        ),
        "table_split_ratio": (
            split_counts["table"] / atomic_counts["table"]
            if atomic_counts["table"]
            else 0.0
        ),
        "list_split_ratio": (
            split_counts["list"] / atomic_counts["list"]
            if atomic_counts["list"]
            else 0.0
        ),
        "duplicate_chunk_ratio": ratio(duplicate_count),
        "neighbor_overlap_ratio_mean": mean(overlaps) if overlaps else 0.0,
    }
