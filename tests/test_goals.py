from app.db.models import LearningGoal


def goal_payload(**overrides):
    payload = {
        "name": "Learn LangGraph",
        "goal_type": "learning",
        "description": "Build a reliable workflow",
        "priority": 4,
        "related_topics": ["RAG", "LangGraph"],
    }
    payload.update(overrides)
    return payload


def create_goal(client, **overrides) -> dict:
    response = client.post("/goals", json=goal_payload(**overrides))
    assert response.status_code == 201
    return response.json()


def test_create_goal(client):
    body = create_goal(client, name="  Learn LangGraph  ")
    assert body["name"] == "Learn LangGraph"
    assert body["status"] == "active"
    assert body["priority"] == 4
    assert body["related_topics"] == ["RAG", "LangGraph"]


def test_create_goal_rejects_blank_name(client):
    response = client.post("/goals", json=goal_payload(name="   "))
    assert response.status_code == 422


def test_create_goal_rejects_invalid_type(client):
    response = client.post("/goals", json=goal_payload(goal_type="unknown"))
    assert response.status_code == 422


def test_create_goal_rejects_priority_out_of_range(client):
    assert client.post("/goals", json=goal_payload(priority=0)).status_code == 422
    assert client.post("/goals", json=goal_payload(priority=6)).status_code == 422


def test_list_goals(client):
    create_goal(client, name="First")
    create_goal(client, name="Second", priority=5)
    body = client.get("/goals").json()
    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["Second", "First"]


def test_list_goals_filters_by_type(client):
    create_goal(client, name="Learning", goal_type="learning")
    create_goal(client, name="Project", goal_type="project")
    body = client.get("/goals", params={"goal_type": "project"}).json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Project"


def test_list_goals_filters_by_status(client):
    active = create_goal(client, name="Active")
    paused = create_goal(client, name="Paused")
    client.patch(f"/goals/{paused['id']}", json={"status": "paused"})
    body = client.get("/goals", params={"status": "active"}).json()
    assert [item["id"] for item in body["items"]] == [active["id"]]


def test_update_goal(client):
    goal = create_goal(client)
    response = client.patch(
        f"/goals/{goal['id']}",
        json={"name": "  Updated goal  ", "priority": 5, "related_topics": ["RAG", "RAG", "API"]},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated goal"
    assert response.json()["priority"] == 5
    assert response.json()["related_topics"] == ["RAG", "API"]
    assert client.patch(f"/goals/{goal['id']}", json={"name": "   "}).status_code == 422


def test_pause_goal(client):
    goal = create_goal(client)
    response = client.patch(f"/goals/{goal['id']}", json={"status": "paused"})
    assert response.json()["status"] == "paused"


def test_resume_goal(client):
    goal = create_goal(client, status="paused")
    response = client.patch(f"/goals/{goal['id']}", json={"status": "active"})
    assert response.json()["status"] == "active"


def test_complete_goal(client):
    goal = create_goal(client)
    response = client.patch(f"/goals/{goal['id']}", json={"status": "completed"})
    assert response.json()["status"] == "completed"


def test_delete_goal(client, db_session):
    goal = create_goal(client)
    response = client.delete(f"/goals/{goal['id']}")
    assert response.status_code == 204
    assert db_session.get(LearningGoal, goal["id"]) is None


def test_missing_goal_returns_404(client):
    assert client.get("/goals/missing-id").status_code == 404
    assert client.patch("/goals/missing-id", json={"priority": 4}).status_code == 404
    assert client.delete("/goals/missing-id").status_code == 404
