import inspect
import logging
import re
import time
from typing import Literal

from langgraph.runtime import Runtime

from app.core.config import Settings
from app.domain.retrieved_chunk import RetrievedChunk
from app.graphs.rag_state import RagRuntimeContext, RagState
from app.services.context_builder import ContextBuilder
from app.services.context_bundle_service import ContextBundleService
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import DeepSeekClient
from app.services.qa_history_service import QAHistoryService
from app.services.learning_context_service import LearningContextService
from app.services.learning_session_service import LearningSessionService
from app.services.rerank_service import RerankService
from app.services.vector_service import VectorService
from app.services.session_memory_service import SessionMemoryService
from app.services.user_preference_service import UserPreferenceService

logger = logging.getLogger(__name__)

NO_CONTEXT_ANSWER = "根据当前知识库资料，无法确定。"
CITATION_PATTERN = re.compile(r"\[来源(\d+)]")
REFERENTIAL_TERMS = ("这个", "那个", "它", "上面", "前面", "刚才", "该内容")


class RagNodes:
    def __init__(
        self,
        *,
        settings: Settings,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
        rerank_service: RerankService,
        llm_client: DeepSeekClient,
        context_builder: ContextBuilder,
        history_service: QAHistoryService,
    ) -> None:
        self.settings = settings
        self.embedding_service = embedding_service
        self.vector_service = vector_service
        self.rerank_service = rerank_service
        self.llm_client = llm_client
        self.context_builder = context_builder
        self.history_service = history_service
        self.preference_service = UserPreferenceService(settings)
        self.session_memory_service = SessionMemoryService(settings)
        self.learning_context_service = LearningContextService(settings)
        self.context_bundle_service = ContextBundleService(settings)

    def initialize_state(self, state: RagState) -> RagState:
        question = str(state.get("question") or "")
        normalized = " ".join(question.split()).strip()
        return {
            "run_id": state.get("run_id"),
            "mode": state.get("mode") or "retrieve",
            "question": question,
            "original_query": normalized,
            "rewritten_query": None,
            "rewrite_applied": False,
            "rewrite_reason": None,
            "rewrite_enabled": state.get("rewrite_enabled"),
            "evaluation_mode": bool(state.get("evaluation_mode")),
            "evaluation_conversation_context": list(
                state.get("evaluation_conversation_context") or []
            ),
            "retrieval_profile": dict(state.get("retrieval_profile") or {}),
            "document_id": state.get("document_id"),
            "source_types": state.get("source_types"),
            "source_path_prefixes": state.get("source_path_prefixes"),
            "session_id": state.get("session_id"),
            "goal_id": state.get("goal_id"),
            "learning_status_id": state.get("learning_status_id"),
            "learning_context": {},
            "user_preferences": {},
            "session_memory": [],
            "session_memory_truncated": False,
            "context_manifest": {},
            "qa_history_id": None,
            "top_k": int(state.get("top_k") or 5),
            "retrieval_scope": {
                "document_id": state.get("document_id"),
                "source_types": state.get("source_types"),
                "source_path_prefixes": state.get("source_path_prefixes"),
            },
            "original_results": [],
            "rewritten_results": [],
            "merged_results": [],
            "expanded_results": [],
            "reranked_results": [],
            "context_chunks": [],
            "context_text": "",
            "context_sources": [],
            "answer": "",
            "citations": [],
            "retrieval_quality": {},
            "evaluation_result": {},
            "model_call_count": 0,
            "tool_call_count": 0,
            "retry_count": 0,
            "retrieval_latency_ms": 0.0,
            "rewrite_latency_ms": 0.0,
            "rerank_latency_ms": 0.0,
            "llm_latency_ms": 0.0,
            "answerable": False,
            "error_code": None,
            "error_message": None,
        }

    def load_user_preferences(self, state: RagState, runtime: Runtime[RagRuntimeContext]) -> RagState:
        db = (runtime.context or {}).get("db")
        value = self.preference_service.get(db)
        return {
            "user_preferences": {
                key: value.get(key)
                for key in (
                    "preferred_language",
                    "answer_depth",
                    "answer_style",
                    "citation_mode",
                    "custom_instructions",
                )
            }
        }

    def load_explicit_learning_context(
        self,
        state: RagState,
        runtime: Runtime[RagRuntimeContext],
    ) -> RagState:
        db = (runtime.context or {}).get("db")
        resolved = self.learning_context_service.resolve(
            db,
            session_id=state.get("session_id"),
            goal_id=state.get("goal_id"),
            learning_status_id=state.get("learning_status_id"),
            source_types=state.get("source_types"),
            source_path_prefixes=state.get("source_path_prefixes"),
        )
        return {
            "goal_id": resolved["goal_id"],
            "learning_status_id": resolved["learning_status_id"],
            "source_types": resolved["source_types"],
            "source_path_prefixes": resolved["source_path_prefixes"],
            "retrieval_scope": {
                "document_id": state.get("document_id"),
                "source_types": resolved["source_types"],
                "source_path_prefixes": resolved["source_path_prefixes"],
            },
            "learning_context": resolved["context"] if state.get("mode") == "ask" else {},
        }

    def load_session_memory(self, state: RagState, runtime: Runtime[RagRuntimeContext]) -> RagState:
        profile = state.get("retrieval_profile") or {}
        if profile.get("session_memory_for_rewrite") is False:
            return {"session_memory": [], "session_memory_truncated": False}
        if state.get("evaluation_mode") and state.get(
            "evaluation_conversation_context"
        ):
            turns = state["evaluation_conversation_context"]
            memory: list[dict] = []
            pending_question: str | None = None
            for turn in turns:
                if turn.get("role") == "user":
                    pending_question = str(turn.get("content") or "")
                elif turn.get("role") == "assistant" and pending_question:
                    memory.append(
                        {
                            "question": pending_question,
                            "answer_preview": str(turn.get("content") or ""),
                        }
                    )
                    pending_question = None
            if pending_question:
                memory.append({"question": pending_question, "answer_preview": ""})
            return {"session_memory": memory, "session_memory_truncated": False}
        db = (runtime.context or {}).get("db")
        memory, truncated = self.session_memory_service.load_with_metadata(db, state.get("session_id"))
        return {"session_memory": memory, "session_memory_truncated": truncated}

    def retrieve_original_query(self, state: RagState) -> RagState:
        started = time.perf_counter()
        results = self._retrieve(state["original_query"], state, rewritten=False)
        return {
            "original_results": results,
            "tool_call_count": state["tool_call_count"] + 2,
            "retrieval_latency_ms": state.get("retrieval_latency_ms", 0.0)
            + (time.perf_counter() - started) * 1000,
        }

    def assess_retrieval_quality(self, state: RagState) -> RagState:
        results = state["original_results"]
        max_score = max((chunk.score or 0.0 for chunk in results), default=None)
        query = state["original_query"]
        reason: str | None = None

        rewrite_enabled = state.get("rewrite_enabled")
        if rewrite_enabled is None:
            rewrite_enabled = (state.get("retrieval_profile") or {}).get(
                "rewrite_enabled", self.settings.rag_rewrite_enabled
            )

        if not rewrite_enabled:
            reason = "rewrite_disabled"
        elif not results:
            reason = "empty_results"
        elif len(results) < self.settings.rag_rewrite_min_results:
            reason = "insufficient_results"
        elif max_score is None or max_score < self.settings.rag_rewrite_score_threshold:
            reason = "low_retrieval_score"
        elif any(term in query for term in REFERENTIAL_TERMS):
            reason = "referential_query"
        elif len(query.replace(" ", "")) <= self.settings.rag_rewrite_short_query_length:
            reason = "short_query"

        rewrite_needed = reason not in {None, "rewrite_disabled"}
        return {
            "rewrite_reason": reason or "sufficient_retrieval_quality",
            "retrieval_quality": {
                "result_count": len(results),
                "max_score": max_score,
                "rewrite_needed": rewrite_needed,
                "reason": reason or "sufficient_retrieval_quality",
            },
        }

    def route_rewrite(self, state: RagState) -> Literal["rewrite", "merge"]:
        if (state.get("retrieval_profile") or {}).get("dual_retrieval_enabled") is False:
            return "merge"
        return "rewrite" if state["retrieval_quality"].get("rewrite_needed") else "merge"

    def rewrite_query(self, state: RagState) -> RagState:
        started = time.perf_counter()
        try:
            rewrite = self.llm_client.rewrite_query
            memory = state.get("session_memory") or []
            parameters = inspect.signature(rewrite).parameters
            if memory and ("session_memory" in parameters or any(p.kind == p.VAR_KEYWORD for p in parameters.values())):
                rewritten = rewrite(state["original_query"], session_memory=memory)
            else:
                rewritten = rewrite(state["original_query"])
            rewritten = " ".join(rewritten.split()).strip()
        except Exception as exc:
            logger.warning("Conditional query rewrite failed: error_type=%s", type(exc).__name__)
            return {
                "rewritten_query": state["original_query"],
                "rewrite_applied": False,
                "rewrite_reason": "rewrite_failed",
                "error_code": "rewrite_failed",
                "error_message": type(exc).__name__,
                "model_call_count": state["model_call_count"] + 1,
                "rewrite_latency_ms": state.get("rewrite_latency_ms", 0.0)
                + (time.perf_counter() - started) * 1000,
            }
        return {
            "rewritten_query": rewritten or state["original_query"],
            "rewrite_applied": bool(rewritten and rewritten != state["original_query"]),
            "model_call_count": state["model_call_count"] + 1,
            "rewrite_latency_ms": state.get("rewrite_latency_ms", 0.0)
            + (time.perf_counter() - started) * 1000,
        }

    def retrieve_rewritten_query(self, state: RagState) -> RagState:
        if not state["rewrite_applied"]:
            return {"rewritten_results": []}
        started = time.perf_counter()
        results = self._retrieve(
            state["rewritten_query"] or state["original_query"],
            state,
            rewritten=True,
        )
        return {
            "rewritten_results": results,
            "tool_call_count": state["tool_call_count"] + 2,
            "retrieval_latency_ms": state.get("retrieval_latency_ms", 0.0)
            + (time.perf_counter() - started) * 1000,
        }

    def merge_results(self, state: RagState) -> RagState:
        merged: dict[str, RetrievedChunk] = {}
        for hit_source, chunks in (
            ("original", state["original_results"]),
            ("rewritten", state["rewritten_results"]),
        ):
            for chunk in chunks:
                existing = merged.get(chunk.chunk_id)
                if existing is None:
                    metadata = dict(chunk.metadata)
                    metadata["retrieval_source"] = hit_source
                    merged[chunk.chunk_id] = chunk.model_copy(update={"metadata": metadata})
                    continue
                metadata = dict(existing.metadata)
                metadata["retrieval_source"] = "both"
                best_score = max(existing.score or 0.0, chunk.score or 0.0)
                merged[chunk.chunk_id] = existing.model_copy(update={"score": best_score, "metadata": metadata})
        return {"merged_results": list(merged.values())}

    def expand_links_placeholder(self, state: RagState) -> RagState:
        evaluation = dict(state.get("evaluation_result") or {})
        evaluation["link_expansion_applied"] = False
        return {"expanded_results": list(state["merged_results"]), "evaluation_result": evaluation}

    def rerank_results(self, state: RagState) -> RagState:
        chunks = state["expanded_results"]
        if not chunks:
            return {"reranked_results": []}
        profile = state.get("retrieval_profile") or {}
        if profile.get("rerank_enabled") is False:
            limit = int(profile.get("final_top_k") or state["top_k"])
            return {"reranked_results": chunks[:limit]}
        rerank_limit = int(profile.get("rerank_top_k") or state["top_k"])
        query = state.get("rewritten_query") if state.get("rewrite_applied") else state["original_query"]
        started = time.perf_counter()
        try:
            reranked = self.rerank_service.rerank(
                query or state["original_query"],
                [chunk.to_rerank_item() for chunk in chunks],
                rerank_limit,
            )
            converted = [RetrievedChunk.from_search_result(item) for item in reranked]
        except Exception as exc:
            logger.warning("Graph rerank fallback: error_type=%s", type(exc).__name__)
            converted = chunks[:rerank_limit]
            return {
                "reranked_results": converted,
                "tool_call_count": state["tool_call_count"] + 1,
                "error_code": "rerank_failed",
                "error_message": type(exc).__name__,
                "rerank_latency_ms": (time.perf_counter() - started) * 1000,
            }
        return {
            "reranked_results": converted,
            "tool_call_count": state["tool_call_count"] + 1,
            "rerank_latency_ms": (time.perf_counter() - started) * 1000,
        }

    def build_context(self, state: RagState) -> RagState:
        result = self.context_builder.build(state["reranked_results"], max_chunks=state["top_k"])
        bundle = self.context_bundle_service.build(
            user_preferences=state.get("user_preferences"),
            explicit_learning_context=state.get("learning_context"),
            session_memory=state.get("session_memory"),
            knowledge_context=result.context_text,
            knowledge_source_count=len(result.context_sources),
            session_memory_truncated=bool(state.get("session_memory_truncated")),
        )
        return {
            "context_text": bundle.knowledge_context,
            "context_sources": result.context_sources,
            "context_chunks": result.chunks,
            "user_preferences": bundle.user_preferences,
            "learning_context": bundle.explicit_learning_context,
            "session_memory": bundle.session_memory,
            "context_manifest": bundle.manifest,
        }

    def route_mode(self, state: RagState) -> Literal["retrieve", "ask"]:
        return "ask" if state["mode"] == "ask" else "retrieve"

    def finalize_retrieval(self, state: RagState) -> RagState:
        return {}

    def check_answerability(self, state: RagState) -> RagState:
        chunks = state["context_chunks"]
        max_score = max((chunk.score or 0.0 for chunk in chunks), default=0.0)
        threshold = float(
            (state.get("retrieval_profile") or {}).get(
                "no_answer_score_threshold", self.settings.rag_rewrite_score_threshold
            )
        )
        answerable = bool(
            chunks
            and state["context_text"].strip()
            and max_score >= threshold
        )
        return {
            "answerable": answerable,
            "evaluation_result": {
                **state.get("evaluation_result", {}),
                "answerability_max_score": max_score,
            },
        }

    def route_answerability(self, state: RagState) -> Literal["answer", "no_answer"]:
        return "answer" if state["answerable"] else "no_answer"

    def finalize_no_answer(self, state: RagState) -> RagState:
        return {"answer": NO_CONTEXT_ANSWER, "context_chunks": [], "context_sources": [], "citations": []}

    def generate_answer(self, state: RagState) -> RagState:
        started = time.perf_counter()
        generate = self.llm_client.generate_answer
        parameters = inspect.signature(generate).parameters
        kwargs = {}
        optional = {
            "learning_context": state.get("learning_context") or {},
            "user_preferences": state.get("user_preferences") or {},
            "session_memory": state.get("session_memory") or [],
        }
        accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in parameters.values())
        for name, value in optional.items():
            if value and (name in parameters or accepts_kwargs):
                kwargs[name] = value
        answer = generate(state["question"], state["context_text"], **kwargs)
        return {
            "answer": answer,
            "model_call_count": state["model_call_count"] + 1,
            "llm_latency_ms": state.get("llm_latency_ms", 0.0)
            + (time.perf_counter() - started) * 1000,
        }

    def validate_citations(self, state: RagState) -> RagState:
        sources_by_index = {source["citation_index"]: source for source in state["context_sources"]}
        cited_indices = [int(value) for value in CITATION_PATTERN.findall(state["answer"])]
        invalid = sorted({index for index in cited_indices if index not in sources_by_index})
        valid_indices = list(dict.fromkeys(index for index in cited_indices if index in sources_by_index))
        answer = CITATION_PATTERN.sub(
            lambda match: "" if int(match.group(1)) in invalid else match.group(0),
            state["answer"],
        )
        citations = [sources_by_index[index] for index in valid_indices]
        return {
            "answer": " ".join(answer.split()),
            "citations": citations,
            "evaluation_result": {
                **state.get("evaluation_result", {}),
                "invalid_citations": invalid,
                "answer_had_citations": bool(cited_indices),
            },
        }

    def basic_evaluate(self, state: RagState) -> RagState:
        invalid = state.get("evaluation_result", {}).get("invalid_citations", [])
        result = {
            **state.get("evaluation_result", {}),
            "has_context": bool(state["context_text"]),
            "has_valid_citations": bool(state["citations"]),
            "invalid_citations": invalid,
            "source_count": len(state["context_sources"]),
            "rewrite_applied": state["rewrite_applied"],
        }
        result["passed"] = bool(result["has_context"] and result["has_valid_citations"] and not invalid)
        return {"evaluation_result": result}

    def route_persistence(self, state: RagState) -> Literal["persist", "end"]:
        return "end" if state.get("evaluation_mode") else "persist"

    def save_qa_history(self, state: RagState, runtime: Runtime[RagRuntimeContext]) -> RagState:
        db = (runtime.context or {}).get("db")
        if db is None:
            raise RuntimeError("Database session is required for ask mode")
        qa = self.history_service.save(
            db,
            question=state["question"],
            rewritten_query=state.get("rewritten_query") or state["original_query"],
            answer=state["answer"],
            chunks=state["context_chunks"],
            session_id=state.get("session_id"),
            run_id=state.get("run_id"),
        )
        if state.get("session_id"):
            LearningSessionService().record_activity(db, state["session_id"], state["question"])
        return {"qa_history_id": qa.id}

    def _retrieve(
        self,
        query: str,
        state: RagState,
        *,
        rewritten: bool,
    ) -> list[RetrievedChunk]:
        vector = self.embedding_service.embed_query(query)
        profile = state.get("retrieval_profile") or {}
        profile_key = "rewritten_top_k" if rewritten else "original_top_k"
        retrieve_top_k = int(
            profile.get(profile_key) or max(state["top_k"] * 5, 20)
        )
        scope = {
            "source_types": state.get("source_types"),
            "source_path_prefixes": state.get("source_path_prefixes"),
        }
        if any(scope.values()):
            raw = self.vector_service.search(
                query_vector=vector,
                top_k=retrieve_top_k,
                document_id=state.get("document_id"),
                **scope,
            )
        else:
            raw = self.vector_service.search(
                query_vector=vector,
                top_k=retrieve_top_k,
                document_id=state.get("document_id"),
            )
        return [RetrievedChunk.from_search_result(item) for item in raw]
