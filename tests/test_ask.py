from app.routers import rag
from app.schemas.rag import AskResponse, RetrieveResponse, SourceChunk
from app.services.llm_service import LLMServiceUnavailable


class FakeRagService:
    def ask(self, request, db):
        return AskResponse(
            answer="可以上传文档后进行检索问答。[来源1]",
            sources=[
                SourceChunk(
                    document_id="doc-1",
                    filename="demo.txt",
                    page_no=1,
                    chunk_index=0,
                    score=0.9,
                    text="企业知识库支持上传文档并进行问答。",
                )
            ],
        )


class FakeRetrieveOnlyService:
    answer_generation_called = False

    def retrieve(self, request):
        return RetrieveResponse(
            question=request.question,
            rewritten_query=request.question,
            document_id=request.document_id,
            top_k=request.top_k,
            retrieved_count=0,
            chunks=[],
        )


def test_ask(client, monkeypatch):
    monkeypatch.setattr(rag, "get_rag_service", lambda: FakeRagService())

    response = client.post("/rag/ask", json={"question": "支持什么功能？", "top_k": 5})

    assert response.status_code == 200
    body = response.json()
    assert "answer" in body
    assert len(body["sources"]) == 1
    assert body["sources"][0]["filename"] == "demo.txt"


def test_ask_empty_question_returns_422(client):
    response = client.post("/rag/ask", json={"question": "", "top_k": 5})

    assert response.status_code == 422


def test_ask_blank_question_returns_422(client):
    response = client.post("/rag/ask", json={"question": "   ", "top_k": 5})

    assert response.status_code == 422


def test_ask_returns_503_when_rag_service_is_unavailable(client, monkeypatch):
    def fail_to_get_service():
        raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(rag, "get_rag_service", fail_to_get_service)

    response = client.post("/rag/ask", json={"question": "test", "top_k": 5})

    assert response.status_code == 503
    assert response.json()["detail"] == rag.RAG_UNAVAILABLE_DETAIL


def test_ask_returns_friendly_error_when_llm_is_unavailable(client, monkeypatch):
    class FailingRagService:
        def ask(self, request, db):
            raise LLMServiceUnavailable("upstream timeout")

    monkeypatch.setattr(rag, "get_rag_service", lambda: FailingRagService())

    response = client.post("/rag/ask", json={"question": "test", "top_k": 5})

    assert response.status_code == 503
    assert response.json()["detail"] == "LLM service is temporarily unavailable"


def test_retrieve_empty_question_returns_422(client):
    response = client.post("/rag/retrieve", json={"question": "", "top_k": 5})

    assert response.status_code == 422


def test_retrieve_no_results_returns_empty_chunks_without_answer_generation(client, monkeypatch):
    service = FakeRetrieveOnlyService()
    monkeypatch.setattr(rag, "get_rag_service", lambda: service)

    response = client.post("/rag/retrieve", json={"question": "missing topic", "top_k": 5})

    assert response.status_code == 200
    body = response.json()
    assert body["question"] == "missing topic"
    assert body["rewritten_query"] == "missing topic"
    assert body["retrieved_count"] == 0
    assert body["chunks"] == []
    assert service.answer_generation_called is False
