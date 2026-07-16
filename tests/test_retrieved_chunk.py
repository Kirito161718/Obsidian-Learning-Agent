from app.domain.retrieved_chunk import RetrievedChunk


def test_qdrant_payload_without_source_type_maps_to_uploaded_document():
    chunk = RetrievedChunk.from_search_result(
        {
            "score": 0.8,
            "payload": {
                "document_id": "doc-1",
                "filename": "demo.txt",
                "chunk_index": 2,
                "text": "content",
            },
        }
    )

    assert chunk.chunk_id == "doc-1_2"
    assert chunk.source_id == "doc-1"
    assert chunk.source_type == "uploaded_document"
    assert chunk.source_name == "demo.txt"


def test_retrieved_chunk_rerank_round_trip_preserves_score_semantics():
    original = RetrievedChunk(
        chunk_id="doc-1_0",
        source_id="doc-1",
        source_name="demo.txt",
        text="content",
        score=0.75,
        rerank_score=0.6,
    )

    restored = RetrievedChunk.from_search_result(original.to_rerank_item())

    assert restored.score == 0.75
    assert restored.rerank_score == 0.6
