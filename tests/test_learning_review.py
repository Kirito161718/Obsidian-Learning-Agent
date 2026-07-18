import json

import pytest

from app.db.models import LearningSession, LearningSessionReview, QAHistory
from app.services.learning_review_service import EmptyLearningSessionError, InvalidReviewOutputError, LearningReviewService


class FakeReviewClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.payloads = []

    def generate_learning_review(self, payload):
        self.payloads.append(payload)
        return self.outputs.pop(0)


def make_session(db_session):
    session = LearningSession(title="Review")
    db_session.add(session)
    db_session.commit()
    return session


def add_qa(db_session, session_id, index, source_path=None):
    source = {"source_name": "Note", "heading_path": ["RAG"], "text": "X" * 300}
    if source_path:
        source["source_path"] = source_path
    qa = QAHistory(
        question=f"q{index}", rewritten_query=f"q{index}", answer=f"a{index}",
        source_chunks=[source], session_id=session_id,
    )
    db_session.add(qa)
    db_session.commit()
    return qa


def test_empty_session_review_does_not_call_model(db_session):
    session = make_session(db_session)
    client = FakeReviewClient([])
    with pytest.raises(EmptyLearningSessionError):
        LearningReviewService(client).generate(db_session, session.id)
    assert client.payloads == []


def test_review_parses_and_updates_single_record(db_session):
    session = make_session(db_session)
    qa = add_qa(db_session, session.id, 1, "Knowledge/RAG.md")
    outputs = [
        json.dumps({"knowledge_summary": "Summary", "covered_topics": ["RAG"], "unresolved_questions": [], "next_steps": ["Test"]}),
        json.dumps({"knowledge_summary": "Updated", "covered_topics": [], "unresolved_questions": ["Why"], "next_steps": []}),
    ]
    service = LearningReviewService(FakeReviewClient(outputs))
    first = service.generate(db_session, session.id)
    second = service.generate(db_session, session.id)
    assert first.id == second.id
    assert second.knowledge_summary == "Updated"
    assert second.source_qa_ids == [qa.id]
    assert db_session.query(LearningSessionReview).count() == 1


def test_review_payload_excludes_chunk_text_and_absolute_paths(db_session):
    session = make_session(db_session)
    add_qa(db_session, session.id, 1, "C:/Secret/Vault.md")
    client = FakeReviewClient([json.dumps({"knowledge_summary": "S", "covered_topics": [], "unresolved_questions": [], "next_steps": []})])
    LearningReviewService(client).generate(db_session, session.id)
    rendered = json.dumps(client.payloads[0], ensure_ascii=False)
    assert "X" * 100 not in rendered
    assert "C:/Secret" not in rendered


def test_review_rejects_invalid_model_json(db_session):
    session = make_session(db_session)
    add_qa(db_session, session.id, 1)
    with pytest.raises(InvalidReviewOutputError):
        LearningReviewService(FakeReviewClient(["not-json"])).generate(db_session, session.id)
