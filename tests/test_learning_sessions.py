from app.db.models import LearningGoal, LearningSession, LearningStatus, QAHistory


def create_session(client, **overrides):
    payload = {"title": "RAG 学习会话"}
    payload.update(overrides)
    response = client.post("/learning/sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_session_defaults_source_scope(client):
    body = create_session(client)
    assert body["status"] == "active"
    assert body["source_types"] == ["obsidian_note", "uploaded_document"]
    assert body["source_path_prefixes"] == []


def test_create_session_rejects_blank_title_and_invalid_source(client):
    assert client.post("/learning/sessions", json={"title": "   "}).status_code == 422
    assert client.post("/learning/sessions", json={"title": "x", "source_types": ["web"]}).status_code == 422


def test_create_session_rejects_unsafe_prefixes(client):
    for prefix in ("C:/Vault", "../Secret", "/absolute", "\\\\server\\share"):
        response = client.post(
            "/learning/sessions",
            json={"title": "x", "source_types": ["obsidian_note"], "source_path_prefixes": [prefix]},
        )
        assert response.status_code == 422


def test_create_session_validates_goal_and_status(client):
    assert create_session
    assert client.post("/learning/sessions", json={"title": "x", "goal_id": "missing"}).status_code == 422
    assert client.post("/learning/sessions", json={"title": "x", "learning_status_id": "missing"}).status_code == 422


def test_session_get_patch_archive_restore(client):
    session = create_session(client)
    patched = client.patch(
        f"/learning/sessions/{session['id']}",
        json={"title": "New title", "source_types": ["obsidian_note"], "source_path_prefixes": ["RAG/Notes"]},
    )
    assert patched.status_code == 200
    assert patched.json()["source_path_prefixes"] == ["RAG/Notes"]
    assert client.post(f"/learning/sessions/{session['id']}/archive").json()["status"] == "archived"
    assert client.get("/learning/sessions").json()["total"] == 0
    assert client.post(f"/learning/sessions/{session['id']}/restore").json()["status"] == "active"


def test_session_list_defaults_to_recent_ten_and_paginates(client):
    for index in range(12):
        create_session(client, title=f"Session {index}")
    first = client.get("/learning/sessions").json()
    second = client.get("/learning/sessions", params={"limit": 5, "offset": 10}).json()
    assert first["total"] == 12
    assert len(first["items"]) == 10
    assert len(second["items"]) == 2
    assert client.get("/learning/sessions", params={"limit": 51}).status_code == 422


def test_session_messages_return_only_linked_history(client, db_session):
    session = create_session(client)
    db_session.add_all([
        QAHistory(question="linked", rewritten_query="linked", answer="answer", source_chunks=[], session_id=session["id"]),
        QAHistory(question="legacy", rewritten_query="legacy", answer="answer", source_chunks=[]),
    ])
    db_session.commit()
    body = client.get(f"/learning/sessions/{session['id']}/messages").json()
    assert body["total"] == 1
    assert body["items"][0]["question"] == "linked"


def test_archiving_session_keeps_qa_history(client, db_session):
    session = create_session(client)
    qa = QAHistory(question="q", rewritten_query="q", answer="a", source_chunks=[], session_id=session["id"])
    db_session.add(qa)
    db_session.commit()
    client.post(f"/learning/sessions/{session['id']}/archive")
    assert db_session.get(QAHistory, qa.id) is not None


def test_goal_and_learning_status_delete_null_session_references(client, db_session):
    goal_response = client.post("/goals", json={"name": "Goal", "goal_type": "learning"})
    goal = goal_response.json()
    status_response = client.post("/learning-status", json={"topic": "Topic"})
    status = status_response.json()
    session = create_session(client, goal_id=goal["id"], learning_status_id=status["id"])
    client.delete(f"/goals/{goal['id']}")
    client.delete(f"/learning-status/{status['id']}")
    db_session.expire_all()
    record = db_session.get(LearningSession, session["id"])
    assert record.goal_id is None
    assert record.learning_status_id is None
