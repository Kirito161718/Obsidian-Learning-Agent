import json
from copy import deepcopy
from dataclasses import asdict, dataclass

from app.core.config import Settings, get_settings


@dataclass(slots=True)
class ContextBundle:
    user_preferences: dict
    explicit_learning_context: dict
    session_memory: list[dict]
    knowledge_context: str
    manifest: dict

    def to_dict(self) -> dict:
        return asdict(self)


class ContextBundleService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build(
        self,
        *,
        user_preferences: dict | None,
        explicit_learning_context: dict | None,
        session_memory: list[dict] | None,
        knowledge_context: str,
        knowledge_source_count: int,
        session_memory_truncated: bool = False,
    ) -> ContextBundle:
        preferences = deepcopy(user_preferences or {})
        explicit = deepcopy(explicit_learning_context or {})
        memory = deepcopy(session_memory or [])
        knowledge = knowledge_context or ""
        budget = self.settings.rag_total_prompt_max_chars
        truncated = {"user_preferences": False, "explicit_learning_context": False, "session_memory": session_memory_truncated, "knowledge_context": False}

        while memory and self._total_chars(preferences, explicit, memory, knowledge) > budget:
            memory.pop(0)
            truncated["session_memory"] = True

        if self._total_chars(preferences, explicit, memory, knowledge) > budget and preferences.get("custom_instructions"):
            preferences["custom_instructions"] = None
            truncated["user_preferences"] = True

        if self._total_chars(preferences, explicit, memory, knowledge) > budget:
            if explicit.get("goal"):
                explicit["goal"]["description"] = None
                explicit["goal"]["related_topics"] = explicit["goal"].get("related_topics", [])[:3]
            if explicit.get("learning_status"):
                explicit["learning_status"]["current_problem"] = None
            truncated["explicit_learning_context"] = True

        total = self._total_chars(preferences, explicit, memory, knowledge)
        if total > budget:
            knowledge = knowledge[: max(0, len(knowledge) - (total - budget))]
            truncated["knowledge_context"] = True

        manifest = {
            "user_preferences": self._section_manifest(preferences, truncated["user_preferences"]),
            "explicit_learning_context": self._section_manifest(explicit, truncated["explicit_learning_context"]),
            "session_memory": {
                "included": bool(memory),
                "turns": len(memory),
                "chars": self._chars(memory),
                "truncated": truncated["session_memory"],
            },
            "knowledge_context": {
                "included": bool(knowledge),
                "sources": knowledge_source_count,
                "chars": len(knowledge),
                "truncated": truncated["knowledge_context"],
            },
            "total_chars": self._total_chars(preferences, explicit, memory, knowledge),
            "budget_chars": budget,
        }
        return ContextBundle(preferences, explicit, memory, knowledge, manifest)

    @classmethod
    def _total_chars(cls, preferences: dict, explicit: dict, memory: list[dict], knowledge: str) -> int:
        return cls._chars(preferences) + cls._chars(explicit) + cls._chars(memory) + len(knowledge)

    @staticmethod
    def _chars(value: object) -> int:
        if not value:
            return 0
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    @classmethod
    def _section_manifest(cls, value: dict, truncated: bool) -> dict:
        return {"included": bool(value), "chars": cls._chars(value), "truncated": truncated}
