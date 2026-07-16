from app.db.models import QAHistory
from app.schemas.rag import AskRequest
from app.services.rag_service import NO_CONTEXT_ANSWER, RagService


class FakeEmbeddingService:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2, 0.3]


class FakeVectorService:
    def __init__(self, candidates: list[dict]) -> None:
        self.candidates = candidates
        self.document_id: str | None = None
        self.top_k: int | None = None

    def search(self, query_vector: list[float], top_k: int, document_id: str | None = None) -> list[dict]:
        self.document_id = document_id
        self.top_k = top_k
        return self.candidates[:top_k]


class FakeRerankService:
    def __init__(self) -> None:
        self.candidate_count: int | None = None
        self.top_k: int | None = None

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        self.candidate_count = len(candidates)
        self.top_k = top_k
        return candidates[:top_k]


class FakeLlmClient:
    def __init__(self, rewritten_query: str = "upload policy limit") -> None:
        self.rewritten_query = rewritten_query
        self.generated = False

    def rewrite_query(self, question: str) -> str:
        return self.rewritten_query

    def generate_answer(self, question: str, contexts: list[dict]) -> str:
        self.generated = True
        return "answer [source1]"


def test_ask_uses_rewritten_query_and_persists_history(db_session):
    embedding_service = FakeEmbeddingService()
    vector_service = FakeVectorService(
        [
            {
                "score": 0.88,
                "payload": {
                    "document_id": "doc-1",
                    "filename": "policy.txt",
                    "page_no": 2,
                    "chunk_index": 3,
                    "text": "Upload size limit is 50 MB.",
                },
            }
        ]
    )
    llm_client = FakeLlmClient()
    rerank_service = FakeRerankService()
    service = RagService(embedding_service, vector_service, rerank_service, llm_client)

    response = service.ask(
        AskRequest(question="  What is the upload size limit?  ", top_k=5, document_id="doc-1"),
        db_session,
    )

    assert embedding_service.queries == ["upload policy limit"]
    assert vector_service.document_id == "doc-1"
    assert vector_service.top_k == 25
    assert rerank_service.candidate_count == 1
    assert rerank_service.top_k == 5
    assert llm_client.generated is True
    assert response.answer == "answer [source1]"
    assert response.sources[0].filename == "policy.txt"

    history = db_session.query(QAHistory).one()
    assert history.question == "  What is the upload size limit?  "
    assert history.rewritten_query == "upload policy limit"
    assert history.source_chunks[0]["filename"] == "policy.txt"


def test_ask_skips_generation_when_retrieval_has_no_context(db_session):
    embedding_service = FakeEmbeddingService()
    vector_service = FakeVectorService([])
    llm_client = FakeLlmClient()
    service = RagService(embedding_service, vector_service, FakeRerankService(), llm_client)

    response = service.ask(AskRequest(question="missing answer?", top_k=5), db_session)

    assert response.answer == NO_CONTEXT_ANSWER
    assert response.sources == []
    assert llm_client.generated is False


def test_ask_retrieves_more_candidates_than_final_top_k(db_session):
    embedding_service = FakeEmbeddingService()
    candidates = [
        {
            "score": 1.0 - index / 100,
            "payload": {
                "document_id": f"doc-{index}",
                "filename": "demo.txt",
                "page_no": 1,
                "chunk_index": index,
                "text": f"chunk {index}",
            },
        }
        for index in range(30)
    ]
    vector_service = FakeVectorService(candidates)
    rerank_service = FakeRerankService()
    llm_client = FakeLlmClient()
    service = RagService(embedding_service, vector_service, rerank_service, llm_client)

    response = service.ask(AskRequest(question="question", top_k=5), db_session)

    assert vector_service.top_k == 25
    assert rerank_service.candidate_count == 25
    assert rerank_service.top_k == 5
    assert len(response.sources) == 5


def test_retrieve_does_not_generate_answer():
    embedding_service = FakeEmbeddingService()
    vector_service = FakeVectorService(
        [
            {
                "score": 0.88,
                "payload": {
                    "chunk_id": "doc-1_0",
                    "document_id": "doc-1",
                    "filename": "policy.txt",
                    "file_type": "txt",
                    "page_no": 2,
                    "section_title": "Policy",
                    "chunk_index": 0,
                    "text": "Upload size limit is 50 MB.",
                },
            }
        ]
    )
    llm_client = FakeLlmClient()
    service = RagService(embedding_service, vector_service, FakeRerankService(), llm_client)

    response = service.retrieve(AskRequest(question="What is the upload size limit?", top_k=5, document_id="doc-1"))

    assert response.question == "What is the upload size limit?"
    assert response.rewritten_query == "upload policy limit"
    assert response.document_id == "doc-1"
    assert response.retrieved_count == 1
    assert response.chunks[0].chunk_id == "doc-1_0"
    assert response.chunks[0].text == "Upload size limit is 50 MB."
    assert llm_client.generated is False


def test_rewrite_query_falls_back_to_normalized_question_on_error():
    class FailingLlmClient:
        def rewrite_query(self, question: str) -> str:
            raise RuntimeError("rewrite unavailable")

    service = RagService(FakeEmbeddingService(), FakeVectorService([]), FakeRerankService(), FailingLlmClient())

    assert service.rewrite_query("  hello   world  ") == "hello world"
