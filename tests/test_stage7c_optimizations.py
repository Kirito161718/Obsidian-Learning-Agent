from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.domain.retrieved_chunk import RetrievedChunk
from app.graphs.rag_graph import RagWorkflow
from app.obsidian.markdown_chunker_v2 import (
    CHUNKING_VERSION,
    MarkdownStructuralV2Chunker,
)
from app.obsidian.markdown_parser import MarkdownParser
from app.obsidian.models import ScanItem, ScanReport, VaultFile
from app.rag_evaluation.chunk_analysis import analyze_chunk_payloads
from app.rag_evaluation.cuda_compat_cli import configure_cuda_runtime
from app.rag_evaluation.metrics import relevance_grade, retrieval_metrics
from app.rag_evaluation.schemas import RelevanceJudgment
from app.rag_evaluation.shadow_collection import ShadowCollectionService
from app.retrieval.configuration import (
    RetrievalProfileConfiguration,
    candidate_configurations,
)
from app.retrieval.expansion import RetrievalExpansionService


def chunk_v2(
    markdown: str,
    *,
    minimum: int = 20,
    target: int = 80,
    maximum: int = 120,
    hard: int = 200,
):
    note = MarkdownParser().parse(markdown, "Folder/Note.md")
    return MarkdownStructuralV2Chunker(
        min_chars=minimum,
        target_chars=target,
        max_chars=maximum,
        hard_max_chars=hard,
    ).chunk(note, note_id="note-1", note_hash="a" * 64)


def payload(
    *,
    chunk_id: str,
    note_id: str,
    index: int,
    title: str,
    heading: list[str],
    links: list[dict] | None = None,
    text: str = "text",
) -> dict:
    return {
        "chunk_id": chunk_id,
        "source_id": note_id,
        "source_type": "obsidian_note",
        "source_name": title,
        "filename": f"{title}.md",
        "source_path": f"Folder/{title}.md",
        "chunk_index": index,
        "heading_path": heading,
        "links": links or [],
        "text": text,
    }


class FakeVector:
    def __init__(self, catalog: list[dict]) -> None:
        self.catalog = catalog

    def list_all_obsidian_chunks(self):
        return [dict(item) for item in self.catalog]


def test_chunk_v2_merges_only_short_sibling_sections():
    merged = chunk_v2(
        "# Root\n## A\nfirst-short\n## B\nsecond-short",
        minimum=20,
    )
    assert len(merged) == 1
    assert merged[0].heading_path == ("Root", "A")
    assert merged[0].metadata["merged_heading_paths"] == [
        ["Root", "A"],
        ["Root", "B"],
    ]

    separate = chunk_v2("# A\none\n# B\ntwo", minimum=20)
    assert len(separate) == 2
    assert [item.heading_path for item in separate] == [("A",), ("B",)]


def test_chunk_v2_preserves_code_table_and_list_structure():
    code_body = "\n".join("print('line')" for _ in range(30))
    code_chunks = chunk_v2(
        f"# Code\n```python\n{code_body}\n```",
        maximum=100,
        hard=150,
    )
    assert len(code_chunks) > 1
    assert all(
        item.text.startswith("```python") and item.text.endswith("```")
        for item in code_chunks
    )

    rows = "\n".join(f"| {index} | value |" for index in range(30))
    table_chunks = chunk_v2(
        f"# Table\n| N | V |\n|---|---|\n{rows}",
        maximum=100,
        hard=150,
    )
    assert len(table_chunks) > 1
    assert all(item.text.startswith("| N | V |\n|---|---|") for item in table_chunks)

    items = "\n".join(f"- item {index} value" for index in range(30))
    list_chunks = chunk_v2(
        f"# List\n{items}",
        maximum=100,
        hard=140,
    )
    assert len(list_chunks) > 1
    assert all(item.text.lstrip().startswith("- ") for item in list_chunks)


def test_chunk_v2_records_version_position_and_stable_siblings():
    markdown = "# A\n" + "句子。" * 100
    first = chunk_v2(markdown, maximum=80, hard=120)
    second = chunk_v2(markdown, maximum=80, hard=120)
    assert len(first) > 1
    assert all(item.chunking_version == CHUNKING_VERSION for item in first)
    assert all(item.metadata["structure_position"]["line_start"] >= 2 for item in first)
    assert [item.structural_id for item in first] == [
        item.structural_id for item in second
    ]
    assert first[0].next_sibling_id == first[1].structural_id
    assert first[1].previous_sibling_id == first[0].structural_id
    assert len({item.parent_section_id for item in first}) == 1


def test_link_expansion_is_one_hop_deduplicated_and_unscored():
    origin = payload(
        chunk_id="origin-0",
        note_id="origin",
        index=0,
        title="Origin",
        heading=["Origin"],
        links=[{"target": "Target", "heading": "Useful"}],
    )
    target = payload(
        chunk_id="target-0",
        note_id="target",
        index=0,
        title="Target",
        heading=["Useful"],
        links=[{"target": "Recursive"}],
    )
    recursive = payload(
        chunk_id="recursive-0",
        note_id="recursive",
        index=0,
        title="Recursive",
        heading=["Recursive"],
    )
    service = RetrievalExpansionService(FakeVector([origin, target, recursive]))
    initial = [RetrievedChunk.from_search_result({"score": 0.8, "payload": origin})]
    result = service.expand_links(
        initial,
        max_links_per_origin_note=1,
        max_chunks_per_target_note=2,
    )
    assert [item.chunk_id for item in result] == ["origin-0", "target-0"]
    assert result[1].score is None
    assert result[1].metadata["link_origin_note_id"] == "origin"
    assert result[1].metadata["expansion_reason"] == "direct_wikilink_one_hop"


def test_neighbor_expansion_stays_in_note_and_prioritizes_same_heading():
    catalog = [
        payload(
            chunk_id="a-0",
            note_id="a",
            index=0,
            title="A",
            heading=["Same"],
        ),
        payload(
            chunk_id="a-1",
            note_id="a",
            index=1,
            title="A",
            heading=["Same"],
        ),
        payload(
            chunk_id="a-2",
            note_id="a",
            index=2,
            title="A",
            heading=["Other"],
        ),
        payload(
            chunk_id="b-0",
            note_id="b",
            index=0,
            title="B",
            heading=["Same"],
        ),
    ]
    origin = RetrievedChunk.from_search_result(
        {"score": 0.9, "payload": catalog[1]}
    )
    result = RetrievalExpansionService(FakeVector(catalog)).expand_neighbors(
        [origin],
        window=1,
    )
    assert [item.chunk_id for item in result] == ["a-1", "a-0", "a-2"]
    assert all(item.source_id == "a" for item in result)
    assert all(item.score is None for item in result[1:])
    assert result[1].metadata["neighbor_origin_chunk_id"] == "a-1"


def test_graph_applies_link_rerank_then_neighbor_expansion():
    origin = payload(
        chunk_id="origin-1",
        note_id="origin",
        index=1,
        title="Origin",
        heading=["Origin"],
        links=[{"target": "Target"}],
    )
    before = payload(
        chunk_id="origin-0",
        note_id="origin",
        index=0,
        title="Origin",
        heading=["Origin"],
    )
    target = payload(
        chunk_id="target-0",
        note_id="target",
        index=0,
        title="Target",
        heading=["Target"],
    )

    class GraphEmbedding:
        def embed_query(self, _text):
            return [0.1, 0.2]

    class GraphVector(FakeVector):
        def search(self, *_args, **_kwargs):
            return [{"score": 0.8, "payload": origin}]

    class GraphRerank:
        def rerank(self, _query, chunks, top_n):
            return sorted(
                chunks,
                key=lambda item: item["payload"]["chunk_id"] == "target-0",
                reverse=True,
            )[:top_n]

    class GraphLlm:
        def rewrite_query(self, question):
            return question

        def generate_answer(self, _question, _context):
            return "answer"

    settings = get_settings().model_copy(
        update={
            "rag_rewrite_enabled": False,
            "rag_max_chunks_per_source": 3,
        }
    )
    graph = RagWorkflow(
        settings=settings,
        embedding_service=GraphEmbedding(),
        vector_service=GraphVector([before, origin, target]),
        rerank_service=GraphRerank(),
        llm_client=GraphLlm(),
    )
    profile = RetrievalProfileConfiguration(
        final_top_k=2,
        original_top_k=2,
        rewritten_top_k=2,
        rerank_enabled=True,
        rerank_top_k=2,
        rewrite_enabled=False,
        dual_retrieval_enabled=False,
        link_expansion_enabled=True,
        link_expansion_limit=1,
        neighbor_expansion_enabled=True,
        neighbor_window=1,
    )
    state = graph.invoke(
        mode="retrieve",
        question="query",
        document_id=None,
        top_k=2,
        source_types=["obsidian_note"],
        retrieval_profile=profile.canonical_dict(),
    )
    assert state["evaluation_result"]["link_expansion_applied"] is True
    assert state["evaluation_result"]["neighbor_expansion_applied"] is True
    assert state["expanded_results"][-1].chunk_id == "target-0"
    assert any(
        item.metadata.get("expansion_type") == "neighbor"
        for item in state["context_chunks"]
    )


def test_chunk_analysis_reports_required_distributions():
    payloads = [
        {
            **payload(
                chunk_id="a-0",
                note_id="a",
                index=0,
                title="A",
                heading=[],
                text="x" * 5,
            ),
            "content_hash": "same",
        },
        {
            **payload(
                chunk_id="a-1",
                note_id="a",
                index=1,
                title="A",
                heading=["H"],
                text="x" * 50,
            ),
            "content_hash": "same",
            "block_types": ["list"],
            "chunk_metadata": {"split_flags": ["list_split"]},
        },
    ]
    stats = analyze_chunk_payloads(payloads, min_chars=10, max_chars=40)
    assert stats["chunk_total"] == 2
    assert stats["chunks_per_note"]["mean"] == 2
    assert stats["short_chunk_ratio"] == 0.5
    assert stats["long_chunk_ratio"] == 0.5
    assert stats["no_heading_ratio"] == 0.5
    assert stats["duplicate_chunk_ratio"] == 0.5
    assert stats["list_split_ratio"] == 1.0


def test_metrics_credit_a_preserved_merged_heading_path():
    item = {
        "score": 0.8,
        "payload": {
            "source_id": "note-1",
            "heading_path": ["Root", "A"],
            "chunk_metadata": {
                "merged_heading_paths": [
                    ["Root", "A"],
                    ["Root", "B"],
                ]
            },
        },
    }
    judgment = RelevanceJudgment(
        note_id="note-1",
        heading_path=["Root", "B"],
        relevance_grade=3,
    )
    assert relevance_grade(item, [judgment]) == 3
    assert retrieval_metrics([item], [judgment])["recall@10"] == 1.0


def test_candidate_profiles_keep_shadow_and_production_separate():
    baseline = RetrievalProfileConfiguration(collection_name="rag_chunks")
    candidates = candidate_configurations(
        baseline,
        shadow_collection="rag_eval_7c_chunk_v2",
    )
    assert candidates["chunk-v2"].collection_name == "rag_eval_7c_chunk_v2"
    assert candidates["chunk-v2"].chunking_version == CHUNKING_VERSION
    assert candidates["link-v2"].collection_name == "rag_chunks"
    assert candidates["link-v2"].link_expansion_enabled is True
    assert candidates["neighbor"].neighbor_window == 1
    assert candidates["chunk-v2-link"].link_expansion_enabled is True
    assert candidates["chunk-v2-link"].neighbor_expansion_enabled is False
    assert candidates["chunk-v2-no-rerank"].rerank_enabled is False
    assert baseline.collection_name == "rag_chunks"


def test_shadow_build_failure_removes_only_incomplete_collection(
    monkeypatch,
    tmp_path: Path,
):
    from app.rag_evaluation import shadow_collection as shadow_module

    shadow_name = "rag_eval_failed_build"
    collections: set[str] = set()
    deleted: list[str] = []

    class FakeClient:
        def get_collections(self):
            return SimpleNamespace(
                collections=[SimpleNamespace(name=name) for name in collections]
            )

        def delete_collection(self, *, collection_name: str):
            deleted.append(collection_name)
            collections.discard(collection_name)

    class FailingVector:
        def __init__(self, *, collection_name: str, **_kwargs):
            collections.add(collection_name)

        def upsert_obsidian_chunks(self, **_kwargs):
            raise RuntimeError("simulated indexing failure")

    note_path = tmp_path / "Note.md"
    note_path.write_text("# Note\nbody", encoding="utf-8")
    note = SimpleNamespace(
        id="note-1",
        relative_path="Note.md",
        indexed_hash="h" * 64,
        index_status="indexed",
    )

    class FakeQuery:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return [note]

    class FakeDB:
        def query(self, *_args):
            return FakeQuery()

    class FakeScanner:
        def __init__(self, *_args, **_kwargs):
            pass

        def scan(self, _existing):
            return ScanReport(
                [
                    ScanItem(
                        kind="unchanged",
                        relative_path="Note.md",
                        observed_hash="h" * 64,
                        indexed_hash="h" * 64,
                        vault_file=VaultFile(
                            relative_path="Note.md",
                            absolute_path=note_path,
                            file_name="Note.md",
                            size_bytes=note_path.stat().st_size,
                            modified_at=datetime.now(UTC),
                            observed_hash="h" * 64,
                        ),
                    )
                ]
            )

    fake_embedding = SimpleNamespace(
        vector_size=2,
        embed_texts=lambda texts: [[0.1, 0.2] for _ in texts],
    )
    settings = get_settings().model_copy(
        update={"obsidian_vault_path": tmp_path}
    )
    service = ShadowCollectionService(
        settings,
        embedding_service=fake_embedding,
    )
    monkeypatch.setattr(service, "_client", lambda: FakeClient())
    monkeypatch.setattr(shadow_module, "VectorService", FailingVector)
    monkeypatch.setattr(shadow_module, "ObsidianVaultScanner", FakeScanner)

    with pytest.raises(RuntimeError, match="simulated indexing failure"):
        service.prepare(FakeDB(), collection_name=shadow_name)
    assert deleted == [shadow_name]
    assert "rag_chunks" not in deleted


def test_shadow_collection_rejects_production_name_before_connecting():
    settings = get_settings()
    service = ShadowCollectionService(settings)
    with pytest.raises(ValueError, match="evaluation prefix"):
        service.prepare(
            SimpleNamespace(),
            collection_name=settings.qdrant_collection,
        )


def test_cuda_compat_runtime_requires_explicit_existing_site_packages(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv(
        "RAG_EVALUATION_CUDA_SITE_PACKAGES",
        str(tmp_path / "missing"),
    )
    with pytest.raises(RuntimeError, match="existing site-packages"):
        configure_cuda_runtime()


def test_shadow_replace_waits_for_deleted_name(monkeypatch, tmp_path: Path):
    from app.rag_evaluation import shadow_collection as shadow_module

    shadow_name = "rag_eval_replace"
    calls = 0

    class EventuallyConsistentClient:
        def get_collections(self):
            nonlocal calls
            calls += 1
            names = [shadow_name] if calls < 3 else []
            return SimpleNamespace(
                collections=[SimpleNamespace(name=name) for name in names]
            )

        def delete_collection(self, *, collection_name: str):
            assert collection_name == shadow_name

    settings = get_settings().model_copy(
        update={"obsidian_vault_path": tmp_path}
    )
    service = ShadowCollectionService(
        settings,
        embedding_service=SimpleNamespace(vector_size=2),
    )
    monkeypatch.setattr(
        service,
        "_client",
        lambda: EventuallyConsistentClient(),
    )
    monkeypatch.setattr(shadow_module.time, "sleep", lambda _seconds: None)

    class StopAfterCreate:
        def __init__(self, **_kwargs):
            raise RuntimeError("created after deletion")

    monkeypatch.setattr(shadow_module, "VectorService", StopAfterCreate)
    with pytest.raises(RuntimeError, match="created after deletion"):
        service.prepare(
            SimpleNamespace(),
            collection_name=shadow_name,
            replace=True,
        )
    assert calls >= 3
