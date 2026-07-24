from types import SimpleNamespace

from app.core.config import Settings
from app.db.models import QAHistory
from app.graphs.rag_graph import RagWorkflow
from app.graphs.rag_state import RagState


def raw_chunk(chunk_id: str, score: float, document_id: str = "doc-1", text: str = "content") -> dict:
    return {
        "score": score,
        "payload": {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "filename": f"{document_id}.txt",
            "file_type": "txt",
            "section_title": "Section",
            "page_no": 1,
            "chunk_index": int(chunk_id.rsplit("_", 1)[-1]),
            "text": text,
        },
    }


class FakeEmbeddingService:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str):
        self.queries.append(text)
        return text


class FakeVectorService:
    def __init__(self, responses: dict[str, list[dict]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def search(self, query_vector, top_k: int, document_id: str | None = None) -> list[dict]:
        self.queries.append(query_vector)
        return self.responses.get(query_vector, [])[:top_k]


class FakeRerankService:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def rerank(self, query: str, chunks: list[dict], top_n: int):
        self.calls += 1
        if self.fail:
            raise RuntimeError("rerank failed")
        return chunks[:top_n]


class FakeLlmClient:
    def __init__(
        self,
        rewritten: str = "rewritten query",
        answer: str = "answer [来源1]",
        rewrite_fails: bool = False,
    ) -> None:
        self.rewritten = rewritten
        self.answer = answer
        self.rewrite_fails = rewrite_fails
        self.rewrite_calls = 0
        self.answer_calls = 0

    def rewrite_query(self, question: str) -> str:
        self.rewrite_calls += 1
        if self.rewrite_fails:
            raise RuntimeError("rewrite failed")
        return self.rewritten

    def generate_answer(self, question: str, context_text: str) -> str:
        self.answer_calls += 1
        assert "[来源1]" in context_text
        return self.answer


def settings(**overrides) -> Settings:
    values = {
        "rag_rewrite_enabled": True,
        "rag_rewrite_score_threshold": 0.45,
        "rag_rewrite_min_results": 2,
        "rag_rewrite_short_query_length": 4,
        "rag_context_max_chars": 12000,
        "rag_max_chunks_per_source": 3,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def workflow(responses, *, llm=None, rerank=None, config=None):
    embedding = FakeEmbeddingService()
    vector = FakeVectorService(responses)
    llm = llm or FakeLlmClient()
    rerank = rerank or FakeRerankService()
    graph = RagWorkflow(
        settings=config or settings(),
        embedding_service=embedding,
        vector_service=vector,
        rerank_service=rerank,
        llm_client=llm,
    )
    return graph, embedding, vector, rerank, llm


def test_sufficient_original_results_do_not_call_rewrite():
    question = "clear detailed question"
    graph, embedding, _, _, llm = workflow(
        {question: [raw_chunk("doc-1_0", 0.9), raw_chunk("doc-2_0", 0.8, "doc-2")]}
    )

    state = graph.invoke(mode="retrieve", question=question, document_id=None, top_k=5)

    assert embedding.queries == [question]
    assert llm.rewrite_calls == 0
    assert state["rewrite_applied"] is False


def test_empty_original_results_trigger_rewrite():
    question = "missing detailed topic"
    llm = FakeLlmClient(rewritten="specific topic")
    graph, embedding, _, _, _ = workflow(
        {question: [], "specific topic": [raw_chunk("doc-1_0", 0.9)]}, llm=llm
    )

    state = graph.invoke(mode="retrieve", question=question, document_id=None, top_k=5)

    assert embedding.queries == [question, "specific topic"]
    assert llm.rewrite_calls == 1
    assert state["rewrite_applied"] is True


def test_referential_question_triggers_rewrite_even_with_good_results():
    question = "这个配置具体是什么意思"
    graph, _, _, _, llm = workflow(
        {question: [raw_chunk("doc-1_0", 0.9), raw_chunk("doc-2_0", 0.8, "doc-2")]}
    )

    state = graph.invoke(mode="retrieve", question=question, document_id=None, top_k=5)

    assert llm.rewrite_calls == 1
    assert state["retrieval_quality"]["reason"] == "referential_query"


def test_disabled_rewrite_never_calls_model():
    question = "missing topic"
    graph, embedding, _, _, llm = workflow(
        {question: []}, config=settings(rag_rewrite_enabled=False)
    )

    state = graph.invoke(mode="retrieve", question=question, document_id=None, top_k=5)

    assert embedding.queries == [question]
    assert llm.rewrite_calls == 0
    assert state["rewrite_reason"] == "rewrite_disabled"


def test_rewrite_failure_keeps_original_results():
    question = "one result topic"
    llm = FakeLlmClient(rewrite_fails=True)
    graph, _, _, _, _ = workflow({question: [raw_chunk("doc-1_0", 0.9)]}, llm=llm)

    state = graph.invoke(mode="retrieve", question=question, document_id=None, top_k=5)

    assert [chunk.chunk_id for chunk in state["merged_results"]] == ["doc-1_0"]
    assert state["rewrite_applied"] is False
    assert state["rewrite_reason"] == "rewrite_failed"


def test_original_and_rewritten_results_merge_by_chunk_id_and_record_both():
    question = "one result topic"
    graph, _, _, _, _ = workflow(
        {
            question: [raw_chunk("doc-1_0", 0.6)],
            "rewritten query": [raw_chunk("doc-1_0", 0.9), raw_chunk("doc-2_0", 0.8, "doc-2")],
        }
    )

    state = graph.invoke(mode="retrieve", question=question, document_id=None, top_k=5)
    merged = {chunk.chunk_id: chunk for chunk in state["merged_results"]}

    assert len(merged) == 2
    assert merged["doc-1_0"].score == 0.9
    assert merged["doc-1_0"].metadata["retrieval_source"] == "both"
    assert merged["doc-2_0"].metadata["retrieval_source"] == "rewritten"


def test_rerank_failure_falls_back_to_merged_order():
    question = "clear detailed question"
    graph, _, _, rerank, _ = workflow(
        {question: [raw_chunk("doc-1_0", 0.9), raw_chunk("doc-2_0", 0.8, "doc-2")]},
        rerank=FakeRerankService(fail=True),
    )

    state = graph.invoke(mode="retrieve", question=question, document_id=None, top_k=5)

    assert rerank.calls == 1
    assert [chunk.chunk_id for chunk in state["reranked_results"]] == ["doc-1_0", "doc-2_0"]
    assert state["error_code"] == "rerank_failed"


def test_retrieve_mode_never_generates_answer_or_writes_history(db_session):
    question = "clear detailed question"
    graph, _, _, _, llm = workflow(
        {question: [raw_chunk("doc-1_0", 0.9), raw_chunk("doc-2_0", 0.8, "doc-2")]}
    )

    state = graph.invoke(mode="retrieve", question=question, document_id=None, top_k=5, db=db_session)

    assert state.get("answer", "") == ""
    assert llm.answer_calls == 0
    assert db_session.query(QAHistory).count() == 0


def test_ask_mode_generates_answer_and_saves_compact_history(db_session):
    question = "clear detailed question"
    long_text = "A" * 500
    graph, _, _, _, llm = workflow(
        {
            question: [
                raw_chunk("doc-1_0", 0.9, text=long_text),
                raw_chunk("doc-2_0", 0.8, "doc-2"),
            ]
        }
    )

    state = graph.invoke(mode="ask", question=question, document_id=None, top_k=5, db=db_session)
    history = db_session.query(QAHistory).one()

    assert llm.answer_calls == 1
    assert state["answer"] == "answer [来源1]"
    assert len(history.source_chunks[0]["text"]) == 300


def test_ask_without_results_skips_answer_generation_and_saves_empty_sources(db_session):
    question = "missing detailed topic"
    graph, _, _, _, llm = workflow({question: [], "rewritten query": []})

    state = graph.invoke(mode="ask", question=question, document_id=None, top_k=5, db=db_session)
    assert llm.answer_calls == 0
    assert state["context_chunks"] == []
    assert state["answer_policy"] == "abstain"
    assert db_session.query(QAHistory).count() == 0


def test_invalid_citations_are_detected_and_removed(db_session):
    question = "clear detailed question"
    llm = FakeLlmClient(answer="valid [来源1] invalid [来源99]")
    graph, _, _, _, _ = workflow(
        {question: [raw_chunk("doc-1_0", 0.9), raw_chunk("doc-2_0", 0.8, "doc-2")]}, llm=llm
    )

    state = graph.invoke(mode="ask", question=question, document_id=None, top_k=5, db=db_session)

    assert "[来源99]" not in state["answer"]
    assert state["evaluation_result"]["invalid_citations"] == [99]
    assert [item["citation_index"] for item in state["citations"]] == [1]


def test_rag_state_contains_no_runtime_service_objects():
    forbidden = {"db", "session", "engine", "client", "model", "service"}

    assert forbidden.isdisjoint({name.lower() for name in RagState.__annotations__})
