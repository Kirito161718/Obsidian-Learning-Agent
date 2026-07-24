import json

import pytest

from app.db.models import AgentRun, LearningSession, LearningStatus, QAHistory
from app.schemas.rag import AskRequest, RetrieveRequest
from app.services.learning_review_service import EmptyLearningSessionError, LearningReviewService
from app.services.rag_service import RagService
from app.services.run_history_service import RunHistoryService


def hit(text="Query rewrite improves retrieval", *, chunk_id="chunk-1"):
    return {
        "score": 0.91,
        "payload": {
            "chunk_id": chunk_id,
            "source_id": "note-1",
            "source_type": "obsidian_note",
            "source_name": "RAG",
            "source_path": "Knowledge/RAG.md",
            "section_title": "Rewrite",
            "chunk_index": 0,
            "text": text,
        },
    }


class FakeEmbedding:
    vector_size = 3

    def __init__(self, fail=False):
        self.queries = []
        self.fail = fail

    def embed_query(self, query):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("database_url=mysql://root:secret@localhost/private")
        return [0.1, 0.2, 0.3]


class FakeVector:
    def __init__(self, results=None):
        self.results = results if results is not None else [hit()]
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.results)


class FakeRerank:
    def rerank(self, query, chunks, top_n):
        return chunks[:top_n]


class FakeLlm:
    def __init__(self):
        self.rewrite_memories = []
        self.answer_memories = []

    def rewrite_query(self, question, session_memory=None):
        self.rewrite_memories.append(session_memory or [])
        return "Query Rewrite 和 Rerank 的区别"

    def generate_answer(self, question, context, learning_context=None, user_preferences=None, session_memory=None):
        self.answer_memories.append(session_memory or [])
        return "基于知识库回答 [来源1]"


def make_service(*, fail=False, results=None):
    embedding = FakeEmbedding(fail=fail)
    vector = FakeVector(results=results)
    llm = FakeLlm()
    service = RagService(embedding, vector, FakeRerank(), llm)
    return service, embedding, vector, llm


def make_session(db):
    session = LearningSession(
        title="Session",
        source_types=["obsidian_note"],
        source_path_prefixes=["Knowledge"],
        memory_enabled=True,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_ask_creates_succeeded_run_and_links_qa(db_session):
    session = make_session(db_session)
    service, embedding, _, _ = make_service()
    response = service.ask(AskRequest(question="Explain query rewrite", session_id=session.id), db_session)
    run = db_session.get(AgentRun, response.run_id)
    qa = db_session.get(QAHistory, response.qa_history_id)
    assert run.status == "succeeded"
    assert run.run_type == "rag_ask"
    assert run.duration_ms is not None
    assert run.retrieved_count == 1 and run.source_count == 1
    assert run.model_call_count == 2
    assert qa.run_id == run.id
    assert embedding.queries[0] == "Explain query rewrite"
    assert response.context_manifest["knowledge_context"]["sources"] == 1
    assert response.context_manifest["session_memory"]["turns"] == 0


def test_second_referential_question_uses_only_current_session_memory(db_session):
    session = make_session(db_session)
    other = make_session(db_session)
    db_session.add(
        QAHistory(question="other", rewritten_query="other", answer="other answer", source_chunks=[], session_id=other.id)
    )
    db_session.commit()
    service, embedding, _, llm = make_service()
    service.ask(AskRequest(question="Query Rewrite 是什么？", session_id=session.id), db_session)
    embedding.queries.clear()
    response = service.ask(AskRequest(question="它和 Rerank 有什么区别？", session_id=session.id), db_session)
    assert embedding.queries[0] == "它和 Rerank 有什么区别？"
    assert len(llm.rewrite_memories[-1]) == 1
    assert llm.rewrite_memories[-1][0]["question"] == "Query Rewrite 是什么？"
    assert "other" not in json.dumps(llm.rewrite_memories[-1])
    assert response.context_manifest["session_memory"]["turns"] == 1


def test_retrieve_creates_run_without_qa_or_answer_generation(db_session):
    service, _, _, llm = make_service()
    before = db_session.query(QAHistory).count()
    response = service.retrieve(RetrieveRequest(question="retrieve only"), db_session)
    run = db_session.get(AgentRun, response.run_id)
    assert run.run_type == "rag_retrieve" and run.status == "succeeded"
    assert db_session.query(QAHistory).count() == before
    assert llm.answer_memories == []


def test_rag_failure_records_failed_run_with_redacted_truncated_error(db_session):
    service, _, _, _ = make_service(fail=True)
    with pytest.raises(RuntimeError):
        service.ask(AskRequest(question="top secret question"), db_session)
    run = db_session.query(AgentRun).one()
    assert run.status == "failed"
    assert run.duration_ms is not None
    assert "secret" not in (run.error_message_preview or "")
    assert "top secret question" not in (run.question_hash or "")
    assert len(run.question_hash) == 64


class ReviewClient:
    def generate_learning_review(self, payload):
        return json.dumps(
            {
                "knowledge_summary": "Summary",
                "covered_topics": [],
                "unresolved_questions": [],
                "next_steps": [],
            }
        )


def test_session_review_records_success_and_qa_count(db_session):
    session = make_session(db_session)
    db_session.add(QAHistory(question="q", rewritten_query="q", answer="a", source_chunks=[], session_id=session.id))
    db_session.commit()
    LearningReviewService(ReviewClient()).generate(db_session, session.id)
    run = db_session.query(AgentRun).filter(AgentRun.run_type == "session_review").one()
    assert run.status == "succeeded"
    assert run.session_id == session.id
    assert run.retrieved_count == 1
    assert run.model_call_count == 1


def test_empty_review_records_failed_run(db_session):
    session = make_session(db_session)
    with pytest.raises(EmptyLearningSessionError):
        LearningReviewService(ReviewClient()).generate(db_session, session.id)
    run = db_session.query(AgentRun).one()
    assert run.status == "failed"
    assert run.error_code == "EmptyLearningSessionError"


def test_run_history_api_filters_and_missing_returns_404(client, db_session):
    service = RunHistoryService()
    first = service.create(db_session, run_type="rag_ask", question="one")
    service.succeed(db_session, first.id)
    second = service.create(db_session, run_type="rag_retrieve", question="two")
    service.fail(db_session, second.id, RuntimeError("failed"))
    response = client.get("/agent/runs", params={"run_type": "rag_retrieve", "status": "failed"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == second.id
    assert client.get("/agent/runs/missing").status_code == 404
    assert client.get("/agent/runs", params={"limit": 101}).status_code == 422


def test_run_history_api_serializes_profile_activation(client, db_session):
    service = RunHistoryService()
    run = service.create(
        db_session,
        run_type="rag_profile_activation",
        mode="activation",
    )
    service.succeed(db_session, run.id)

    response = client.get("/agent/runs")

    assert response.status_code == 200
    assert response.json()["items"][0]["run_type"] == "rag_profile_activation"


def test_run_history_never_stores_prompt_answer_or_full_question(db_session):
    question = "q" * 400
    run = RunHistoryService().create(db_session, run_type="rag_ask", question=question)
    assert len(run.question_preview) == 200
    assert run.question_preview != question
    columns = {column.name for column in AgentRun.__table__.columns}
    assert "prompt" not in columns and "answer" not in columns and "state" not in columns


def test_ask_does_not_modify_learning_status(db_session):
    status = LearningStatus(topic="RAG", status="learning", mastery_level=2, manual_importance=3)
    db_session.add(status)
    db_session.commit()
    service, _, _, _ = make_service()
    service.ask(AskRequest(question="Explain", learning_status_id=status.id), db_session)
    db_session.refresh(status)
    assert status.status == "learning" and status.mastery_level == 2
