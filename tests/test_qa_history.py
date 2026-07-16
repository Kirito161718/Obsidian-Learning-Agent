from app.db.models import QAHistory


def make_history(index: int, document_id: str = "doc-1") -> QAHistory:
    return QAHistory(
        question=f"question {index}",
        rewritten_query=f"rewritten {index}",
        answer=f"answer {index}",
        source_chunks=[
            {
                "chunk_id": f"{document_id}_{index}",
                "document_id": document_id,
                "filename": "demo.txt",
                "page_no": 1,
                "chunk_index": index,
                "score": 0.5,
                "rerank_score": 0.1,
                "text": f"source text {index}",
            }
        ],
    )


def test_qa_history_returns_200(client, db_session):
    db_session.add(make_history(1))
    db_session.commit()

    response = client.get("/qa/history")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["items"][0]["source_count"] == 1
    assert "source_chunks" not in body["items"][0]


def test_qa_history_supports_pagination(client, db_session):
    for index in range(3):
        db_session.add(make_history(index))
    db_session.commit()

    response = client.get("/qa/history", params={"page": 2, "page_size": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert body["page_size"] == 1
    assert len(body["items"]) == 1


def test_qa_history_filters_by_keyword_and_document_id(client, db_session):
    db_session.add(make_history(1, document_id="doc-1"))
    db_session.add(make_history(2, document_id="doc-2"))
    db_session.commit()

    response = client.get("/qa/history", params={"keyword": "answer 2", "document_id": "doc-2"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["question"] == "question 2"


def test_qa_history_detail_not_found(client):
    response = client.get("/qa/history/missing-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "QA history not found"


def test_qa_history_detail_returns_sources(client, db_session):
    history = make_history(1)
    db_session.add(history)
    db_session.commit()
    db_session.refresh(history)

    response = client.get(f"/qa/history/{history.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == history.id
    assert body["question"] == "question 1"
    assert body["source_chunks"][0]["document_id"] == "doc-1"
    assert "file_path" not in body["source_chunks"][0]


def test_qa_history_invalid_source_chunks_does_not_crash(client, db_session):
    history = QAHistory(
        question="bad json",
        rewritten_query="bad json",
        answer="answer",
        source_chunks="{not valid json",
    )
    db_session.add(history)
    db_session.commit()
    db_session.refresh(history)

    response = client.get(f"/qa/history/{history.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["source_chunks"] == []
