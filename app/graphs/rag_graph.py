from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session
from threading import Lock
import time

from app.core.config import Settings
from app.graphs.rag_nodes import RagNodes
from app.graphs.rag_state import RagRuntimeContext, RagState
from app.observability import get_observability
from app.observability.decorators import observe_graph_node
from app.services.context_builder import ContextBuilder
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import DeepSeekClient
from app.services.qa_history_service import QAHistoryService
from app.services.rerank_service import RerankService
from app.services.vector_service import VectorService


class RagWorkflow:
    def __init__(
        self,
        *,
        settings: Settings,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
        rerank_service: RerankService,
        llm_client: DeepSeekClient,
    ) -> None:
        nodes = RagNodes(
            settings=settings,
            embedding_service=embedding_service,
            vector_service=vector_service,
            rerank_service=rerank_service,
            llm_client=llm_client,
            context_builder=ContextBuilder(
                max_chars=settings.rag_context_max_chars,
                max_chunks_per_source=settings.rag_max_chunks_per_source,
            ),
            history_service=QAHistoryService(),
        )
        builder = StateGraph(RagState, context_schema=RagRuntimeContext)
        builder.add_node("initialize_state", observe_graph_node("ola.rag.load_context", nodes.initialize_state))
        builder.add_node(
            "load_user_preferences",
            observe_graph_node("ola.rag.load_context", nodes.load_user_preferences),
        )
        builder.add_node(
            "load_explicit_learning_context",
            observe_graph_node("ola.rag.load_context", nodes.load_explicit_learning_context),
        )
        builder.add_node(
            "load_session_memory",
            observe_graph_node("ola.memory.session.load", nodes.load_session_memory),
        )
        builder.add_node(
            "retrieve_original_query",
            observe_graph_node("ola.rag.retrieve.original", nodes.retrieve_original_query),
        )
        builder.add_node(
            "assess_retrieval_quality",
            observe_graph_node("ola.rag.rewrite.decide", nodes.assess_retrieval_quality),
        )
        builder.add_node(
            "rewrite_query",
            observe_graph_node("ola.rag.rewrite.generate", nodes.rewrite_query),
        )
        builder.add_node(
            "retrieve_rewritten_query",
            observe_graph_node("ola.rag.retrieve.rewritten", nodes.retrieve_rewritten_query),
        )
        builder.add_node(
            "merge_results",
            observe_graph_node("ola.rag.retrieve.merge", nodes.merge_results),
        )
        builder.add_node(
            "expand_links",
            observe_graph_node("ola.rag.links.expand", nodes.expand_links),
        )
        builder.add_node(
            "rerank_results",
            observe_graph_node("ola.rag.rerank", nodes.rerank_results),
        )
        builder.add_node(
            "expand_neighbors",
            observe_graph_node("ola.rag.neighbors.expand", nodes.expand_neighbors),
        )
        builder.add_node(
            "build_context",
            observe_graph_node("ola.rag.context.build", nodes.build_context),
        )
        builder.add_node("finalize_retrieval", nodes.finalize_retrieval)
        builder.add_node(
            "evaluate_evidence_sufficiency",
            observe_graph_node(
                "ola.rag.evidence.evaluate",
                nodes.evaluate_evidence_sufficiency,
            ),
        )
        builder.add_node("finalize_no_answer", nodes.finalize_no_answer)
        builder.add_node(
            "generate_answer",
            observe_graph_node("ola.rag.answer_policy.route", nodes.generate_answer),
        )
        builder.add_node(
            "validate_citations",
            observe_graph_node("ola.rag.citations.validate", nodes.validate_citations),
        )
        builder.add_node(
            "enforce_answer_safety",
            observe_graph_node(
                "ola.rag.safety.enforce",
                nodes.enforce_answer_safety,
            ),
        )
        builder.add_node(
            "basic_evaluate",
            observe_graph_node("ola.rag.basic_evaluate", nodes.basic_evaluate),
        )
        builder.add_node(
            "save_qa_history",
            observe_graph_node("ola.rag.persist", nodes.save_qa_history),
        )

        builder.add_edge(START, "initialize_state")
        builder.add_edge("initialize_state", "load_user_preferences")
        builder.add_edge("load_user_preferences", "load_explicit_learning_context")
        builder.add_edge("load_explicit_learning_context", "load_session_memory")
        builder.add_edge("load_session_memory", "retrieve_original_query")
        builder.add_edge("retrieve_original_query", "assess_retrieval_quality")
        builder.add_conditional_edges(
            "assess_retrieval_quality",
            nodes.route_rewrite,
            {"rewrite": "rewrite_query", "merge": "merge_results"},
        )
        builder.add_edge("rewrite_query", "retrieve_rewritten_query")
        builder.add_edge("retrieve_rewritten_query", "merge_results")
        builder.add_edge("merge_results", "expand_links")
        builder.add_edge("expand_links", "rerank_results")
        builder.add_edge("rerank_results", "expand_neighbors")
        builder.add_edge("expand_neighbors", "build_context")
        builder.add_conditional_edges(
            "build_context",
            nodes.route_mode,
            {
                "retrieve": "finalize_retrieval",
                "ask": "evaluate_evidence_sufficiency",
            },
        )
        builder.add_edge("finalize_retrieval", END)
        builder.add_conditional_edges(
            "evaluate_evidence_sufficiency",
            nodes.route_answer_policy,
            {
                "answer": "generate_answer",
                "answer_with_uncertainty": "generate_answer",
                "abstain": "finalize_no_answer",
            },
        )
        builder.add_conditional_edges(
            "finalize_no_answer",
            nodes.route_persistence,
            {"persist": "save_qa_history", "end": END},
        )
        builder.add_edge("generate_answer", "validate_citations")
        builder.add_edge("validate_citations", "enforce_answer_safety")
        builder.add_edge("enforce_answer_safety", "basic_evaluate")
        builder.add_conditional_edges(
            "basic_evaluate",
            nodes.route_persistence,
            {"persist": "save_qa_history", "end": END},
        )
        builder.add_edge("save_qa_history", END)
        self.graph = builder.compile(name="rag_workflow")
        self._invocation_lock = Lock()
        self._invocation_count = 0

    def invoke(
        self,
        *,
        mode: str,
        question: str,
        document_id: str | None,
        top_k: int,
        db: Session | None = None,
        rewrite_enabled: bool | None = None,
        source_types: list[str] | None = None,
        source_path_prefixes: list[str] | None = None,
        session_id: str | None = None,
        goal_id: str | None = None,
        learning_status_id: str | None = None,
        run_id: str | None = None,
        evaluation_mode: bool = False,
        evaluation_conversation_context: list[dict] | None = None,
        retrieval_profile: dict | None = None,
    ) -> RagState:
        started = time.perf_counter()
        with self._invocation_lock:
            cold_start = self._invocation_count == 0
            self._invocation_count += 1
        state: RagState = {
            "mode": mode,
            "question": question,
            "document_id": document_id,
            "top_k": top_k,
            "rewrite_enabled": rewrite_enabled,
            "source_types": source_types,
            "source_path_prefixes": source_path_prefixes,
            "session_id": session_id,
            "goal_id": goal_id,
            "learning_status_id": learning_status_id,
            "run_id": run_id,
            "evaluation_mode": evaluation_mode,
            "evaluation_conversation_context": evaluation_conversation_context or [],
            "retrieval_profile": retrieval_profile or {},
            "cold_start": cold_start,
        }
        with get_observability().suppress_automatic_tracing():
            result = self.graph.invoke(state, context={"db": db})
        performance = dict(result.get("performance_metrics") or {})
        performance["total_ms"] = (time.perf_counter() - started) * 1000
        result["performance_metrics"] = performance
        return result
