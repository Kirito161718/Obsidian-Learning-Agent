from app.domain.retrieved_chunk import RetrievedChunk
from app.services.context_builder import ContextBuilder


def make_chunk(index: int, source_id: str = "doc-1", text: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{source_id}_{index}",
        source_id=source_id,
        source_name=f"{source_id}.txt",
        section_title="Section",
        page_no=index + 1,
        chunk_index=index,
        text=text or f"content {index}",
        score=1.0 - index / 100,
    )


def test_context_builder_limits_each_source_to_three_chunks():
    builder = ContextBuilder(max_chars=10000, max_chunks_per_source=3)

    result = builder.build([make_chunk(index) for index in range(5)], max_chunks=10)

    assert len(result.chunks) == 3
    assert [source["citation_label"] for source in result.context_sources] == [
        "[来源1]",
        "[来源2]",
        "[来源3]",
    ]


def test_context_builder_enforces_total_character_limit():
    builder = ContextBuilder(max_chars=180, max_chunks_per_source=3)

    result = builder.build(
        [make_chunk(0, "doc-1", "A" * 500), make_chunk(0, "doc-2", "B" * 500)],
        max_chunks=5,
    )

    assert 0 < len(result.context_text) <= 180
    assert len(result.chunks) == 1
    assert "C:\\" not in result.context_text


def test_context_builder_deduplicates_chunk_ids_and_preserves_order():
    builder = ContextBuilder(max_chars=10000, max_chunks_per_source=3)
    first = make_chunk(0)

    result = builder.build([first, first, make_chunk(1)], max_chunks=5)

    assert [chunk.chunk_id for chunk in result.chunks] == ["doc-1_0", "doc-1_1"]
