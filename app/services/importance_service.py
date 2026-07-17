from dataclasses import dataclass

from app.db.models import LearningGoal, LearningStatus


@dataclass(frozen=True, slots=True)
class ImportanceResult:
    importance_score: float
    importance_stars: int
    importance_breakdown: dict[str, float]


class ImportanceService:
    """Calculate a deterministic score at read time; no score is persisted."""

    GOAL_WEIGHT = 0.45
    MASTERY_WEIGHT = 0.30
    MANUAL_WEIGHT = 0.15
    PROJECT_WEIGHT = 0.10

    def calculate(
        self,
        learning_status: LearningStatus,
        goals_by_id: dict[str, LearningGoal],
    ) -> ImportanceResult:
        active_priorities = [
            goal.priority
            for goal_id in learning_status.related_goal_ids
            if (goal := goals_by_id.get(goal_id)) is not None and goal.status == "active"
        ]
        goal_relevance = (max(active_priorities) - 1) / 4 if active_priorities else 0.0
        mastery_gap = 1 - learning_status.mastery_level / 5
        manual_importance = (learning_status.manual_importance - 1) / 4
        project_relevance = 1.0 if learning_status.project_relevance else 0.0

        score = (
            self.GOAL_WEIGHT * goal_relevance
            + self.MASTERY_WEIGHT * mastery_gap
            + self.MANUAL_WEIGHT * manual_importance
            + self.PROJECT_WEIGHT * project_relevance
        )
        rounded_score = round(score, 4)
        return ImportanceResult(
            importance_score=rounded_score,
            importance_stars=self.score_to_stars(rounded_score),
            importance_breakdown={
                "goal_relevance": round(goal_relevance, 4),
                "mastery_gap": round(mastery_gap, 4),
                "manual_importance": round(manual_importance, 4),
                "project_relevance": project_relevance,
            },
        )

    @staticmethod
    def score_to_stars(score: float) -> int:
        bounded = min(max(score, 0.0), 1.0)
        if bounded <= 0.20:
            return 1
        if bounded <= 0.40:
            return 2
        if bounded <= 0.60:
            return 3
        if bounded <= 0.80:
            return 4
        return 5
