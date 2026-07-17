from app.db.models import LearningStatus
from tests.test_goals import create_goal


def status_payload(**overrides):
    payload = {
        "topic": "LangGraph State",
        "status": "learning",
        "mastery_level": 2,
        "manual_importance": 4,
        "current_problem": "How should state stay serializable?",
        "related_goal_ids": [],
        "related_note_refs": ["Knowledge/LangGraph/State.md"],
        "project_relevance": True,
    }
    payload.update(overrides)
    return payload


def create_status(client, **overrides) -> dict:
    response = client.post("/learning-status", json=status_payload(**overrides))
    assert response.status_code == 201
    return response.json()


def test_create_learning_status(client):
    body = create_status(client, topic="  LangGraph State  ")
    assert body["topic"] == "LangGraph State"
    assert body["importance_score"] == 0.3925
    assert body["importance_stars"] == 2


def test_learning_status_rejects_mastery_out_of_range(client):
    assert client.post("/learning-status", json=status_payload(mastery_level=-1)).status_code == 422
    assert client.post("/learning-status", json=status_payload(mastery_level=6)).status_code == 422


def test_learning_status_rejects_manual_importance_out_of_range(client):
    assert client.post("/learning-status", json=status_payload(manual_importance=0)).status_code == 422
    assert client.post("/learning-status", json=status_payload(manual_importance=6)).status_code == 422


def test_learning_status_rejects_invalid_status(client):
    response = client.post("/learning-status", json=status_payload(status="unknown"))
    assert response.status_code == 422


def test_learning_status_rejects_unknown_goal(client):
    response = client.post("/learning-status", json=status_payload(related_goal_ids=["missing-goal"]))
    assert response.status_code == 422
    assert response.json()["detail"]["missing_goal_ids"] == ["missing-goal"]


def test_list_learning_statuses(client):
    create_status(client, topic="First")
    create_status(client, topic="Second")
    body = client.get("/learning-status").json()
    assert body["total"] == 2
    assert {item["topic"] for item in body["items"]} == {"First", "Second"}


def test_list_learning_statuses_filters_by_status(client):
    create_status(client, topic="Learning", status="learning")
    create_status(client, topic="Paused", status="paused")
    body = client.get("/learning-status", params={"status": "paused"}).json()
    assert body["total"] == 1
    assert body["items"][0]["topic"] == "Paused"


def test_list_learning_statuses_filters_by_mastery(client):
    create_status(client, topic="Low", mastery_level=1)
    create_status(client, topic="Middle", mastery_level=3)
    create_status(client, topic="High", mastery_level=5)
    body = client.get("/learning-status", params={"min_mastery": 2, "max_mastery": 4}).json()
    assert [item["topic"] for item in body["items"]] == ["Middle"]


def test_list_learning_statuses_filters_by_related_goal(client):
    goal = create_goal(client)
    create_status(client, topic="Related", related_goal_ids=[goal["id"]])
    create_status(client, topic="Unrelated")
    body = client.get("/learning-status", params={"related_goal_id": goal["id"]}).json()
    assert body["total"] == 1
    assert body["items"][0]["topic"] == "Related"


def test_update_learning_status(client):
    record = create_status(client)
    response = client.patch(
        f"/learning-status/{record['id']}",
        json={"topic": "  Updated topic  ", "status": "practicing", "mastery_level": 3},
    )
    assert response.status_code == 200
    assert response.json()["topic"] == "Updated topic"
    assert response.json()["status"] == "practicing"
    assert response.json()["mastery_level"] == 3
    invalid_reference = client.patch(
        f"/learning-status/{record['id']}",
        json={"related_goal_ids": ["missing-goal"]},
    )
    assert invalid_reference.status_code == 422


def test_delete_learning_status(client, db_session):
    record = create_status(client)
    response = client.delete(f"/learning-status/{record['id']}")
    assert response.status_code == 204
    assert db_session.get(LearningStatus, record["id"]) is None


def test_delete_goal_cleans_learning_status_references(client):
    goal = create_goal(client)
    record = create_status(client, related_goal_ids=[goal["id"]])
    assert client.delete(f"/goals/{goal['id']}").status_code == 204
    body = client.get(f"/learning-status/{record['id']}").json()
    assert body["related_goal_ids"] == []
    assert body["importance_breakdown"]["goal_relevance"] == 0.0
