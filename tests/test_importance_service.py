from app.db.models import LearningGoal, LearningStatus
from app.services.importance_service import ImportanceService
from tests.test_goals import create_goal
from tests.test_learning_status import create_status


def make_status(**overrides) -> LearningStatus:
    values = {
        "topic": "RAG",
        "status": "learning",
        "mastery_level": 2,
        "manual_importance": 3,
        "current_problem": None,
        "related_goal_ids": [],
        "related_note_refs": [],
        "project_relevance": False,
    }
    values.update(overrides)
    return LearningStatus(**values)


def make_goal(goal_id: str, *, priority: int = 3, status: str = "active") -> LearningGoal:
    return LearningGoal(
        id=goal_id,
        name=goal_id,
        goal_type="learning",
        priority=priority,
        status=status,
        related_topics=[],
    )


def test_importance_without_related_goal_has_zero_goal_relevance():
    result = ImportanceService().calculate(make_status(), {})
    assert result.importance_breakdown["goal_relevance"] == 0.0


def test_paused_goal_does_not_contribute():
    status = make_status(related_goal_ids=["goal"])
    result = ImportanceService().calculate(status, {"goal": make_goal("goal", priority=5, status="paused")})
    assert result.importance_breakdown["goal_relevance"] == 0.0


def test_completed_goal_does_not_contribute():
    status = make_status(related_goal_ids=["goal"])
    result = ImportanceService().calculate(status, {"goal": make_goal("goal", priority=5, status="completed")})
    assert result.importance_breakdown["goal_relevance"] == 0.0


def test_multiple_active_goals_use_highest_priority():
    status = make_status(related_goal_ids=["low", "high", "paused"])
    goals = {
        "low": make_goal("low", priority=2),
        "high": make_goal("high", priority=5),
        "paused": make_goal("paused", priority=5, status="paused"),
    }
    result = ImportanceService().calculate(status, goals)
    assert result.importance_breakdown["goal_relevance"] == 1.0


def test_mastery_zero_has_maximum_gap():
    result = ImportanceService().calculate(make_status(mastery_level=0), {})
    assert result.importance_breakdown["mastery_gap"] == 1.0


def test_mastery_five_has_zero_gap():
    result = ImportanceService().calculate(make_status(mastery_level=5), {})
    assert result.importance_breakdown["mastery_gap"] == 0.0


def test_importance_star_boundaries_are_deterministic():
    service = ImportanceService()
    assert [service.score_to_stars(score) for score in (0.0, 0.2, 0.2001, 0.4)] == [1, 1, 2, 2]
    assert [service.score_to_stars(score) for score in (0.4001, 0.6, 0.6001, 0.8)] == [3, 3, 4, 4]
    assert [service.score_to_stars(score) for score in (0.8001, 1.0)] == [5, 5]


def test_importance_returns_complete_breakdown():
    status = make_status(
        mastery_level=2,
        manual_importance=5,
        project_relevance=True,
        related_goal_ids=["goal"],
    )
    result = ImportanceService().calculate(status, {"goal": make_goal("goal", priority=4)})
    assert result.importance_score == 0.7675
    assert result.importance_stars == 4
    assert result.importance_breakdown == {
        "goal_relevance": 0.75,
        "mastery_gap": 0.6,
        "manual_importance": 1.0,
        "project_relevance": 1.0,
    }


def test_goal_priority_update_changes_score_immediately(client):
    goal = create_goal(client, priority=1)
    record = create_status(client, related_goal_ids=[goal["id"]], mastery_level=5, manual_importance=1)
    before = record["importance_score"]
    client.patch(f"/goals/{goal['id']}", json={"priority": 5})
    after = client.get(f"/learning-status/{record['id']}").json()["importance_score"]
    assert before == 0.1
    assert after == 0.55


def test_learning_status_update_changes_score_immediately(client):
    record = create_status(client, mastery_level=5, manual_importance=1, project_relevance=False)
    before = record["importance_score"]
    response = client.patch(
        f"/learning-status/{record['id']}",
        json={"mastery_level": 0, "manual_importance": 5, "project_relevance": True},
    )
    assert before == 0.0
    assert response.json()["importance_score"] == 0.55
