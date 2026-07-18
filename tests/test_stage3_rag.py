import json

from app.core.config import Settings
from app.db.models import LearningGoal, LearningSession, LearningStatus, QAHistory
from app.graphs.rag_graph import RagWorkflow


class Embedding:
    def __init__(self): self.queries = []
    def embed_query(self, query): self.queries.append(query); return query


class Vector:
    def __init__(self): self.calls = []
    def search(self, query_vector, top_k, document_id=None, **scope):
        self.calls.append({"query": query_vector, **scope})
        return [{"score": .9, "payload": {
            "chunk_id": "note-1", "source_type": "obsidian_note", "source_id": "note-id",
            "source_name": "RAG Note", "source_path": "Knowledge/RAG.md", "heading_path": ["RAG"],
            "chunk_index": 0, "text": "Query rewrite improves retrieval.",
        }}]


class Rerank:
    def rerank(self, query, chunks, top_n): return chunks[:top_n]


class LLM:
    def __init__(self): self.contexts = []; self.rewrite_calls = 0
    def rewrite_query(self, question): self.rewrite_calls += 1; return question
    def generate_answer(self, question, context_text, learning_context=None):
        self.contexts.append(learning_context)
        return "Answer [来源1]"


def make_workflow():
    embedding, vector, llm = Embedding(), Vector(), LLM()
    workflow = RagWorkflow(
        settings=Settings(_env_file=None, rag_rewrite_min_results=1),
        embedding_service=embedding, vector_service=vector, rerank_service=Rerank(), llm_client=llm,
    )
    return workflow, embedding, vector, llm


def make_context(db_session):
    goal = LearningGoal(name="Build RAG", goal_type="project", priority=5, related_topics=["RAG"], description="D" * 500)
    status = LearningStatus(topic="Query Rewrite", status="learning", mastery_level=2, manual_importance=5, current_problem="How to evaluate?", project_relevance=True)
    db_session.add_all([goal, status]); db_session.commit()
    session = LearningSession(title="新学习会话", goal_id=goal.id, learning_status_id=status.id, source_types=["obsidian_note"], source_path_prefixes=["Knowledge"])
    db_session.add(session); db_session.commit()
    return goal, status, session


def test_session_ask_loads_context_scope_and_saves_history(db_session):
    goal, status, session = make_context(db_session)
    workflow, embedding, vector, llm = make_workflow()
    before = (status.mastery_level, status.status, goal.status)
    state = workflow.invoke(mode="ask", question="Explain query rewrite", document_id=None, top_k=5, db=db_session, session_id=session.id)
    db_session.refresh(session); db_session.refresh(status); db_session.refresh(goal)
    history = db_session.query(QAHistory).one()
    assert embedding.queries == ["Explain query rewrite"]
    assert vector.calls[0]["source_types"] == ["obsidian_note"]
    assert vector.calls[0]["source_path_prefixes"] == ["Knowledge"]
    assert llm.contexts[0]["goal"]["name"] == "Build RAG"
    assert llm.contexts[0]["learning_status"]["mastery_level"] == 2
    assert len(json.dumps(state["learning_context"], ensure_ascii=False)) <= 1200
    assert history.session_id == session.id
    assert state["qa_history_id"] == history.id
    assert session.title == "Explain query rewrite"
    assert session.last_activity_at >= session.created_at
    assert (status.mastery_level, status.status, goal.status) == before


def test_retrieve_uses_session_scope_without_learning_context_or_history(db_session):
    _, _, session = make_context(db_session)
    workflow, _, vector, llm = make_workflow()
    state = workflow.invoke(mode="retrieve", question="Explain query rewrite", document_id=None, top_k=5, db=db_session, session_id=session.id)
    assert vector.calls[0]["source_types"] == ["obsidian_note"]
    assert state["learning_context"] == {}
    assert llm.contexts == []
    assert db_session.query(QAHistory).count() == 0


def test_explicit_request_context_overrides_session_without_mutating_it(db_session):
    goal, _, session = make_context(db_session)
    other = LearningStatus(topic="Other", mastery_level=4)
    db_session.add(other); db_session.commit()
    workflow, _, _, llm = make_workflow()
    workflow.invoke(mode="ask", question="Explain query rewrite", document_id=None, top_k=5, db=db_session, session_id=session.id, learning_status_id=other.id)
    db_session.refresh(session)
    assert llm.contexts[0]["learning_status"]["topic"] == "Other"
    assert session.learning_status_id != other.id
    assert session.goal_id == goal.id


def test_no_explicit_context_is_noop(db_session):
    workflow, _, _, llm = make_workflow()
    state = workflow.invoke(mode="ask", question="Explain query rewrite", document_id=None, top_k=5, db=db_session)
    assert state["learning_context"] == {}
    assert llm.contexts == [None]
