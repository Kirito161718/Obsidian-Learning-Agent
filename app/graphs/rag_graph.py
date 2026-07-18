from sqlalchemy.orm import Session
from langgraph.graph import END, START, StateGraph

from app.core.config import Settings
from app.graphs.rag_nodes import RagNodes
from app.graphs.rag_state import RagRuntimeContext, RagState
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
        builder.add_node("initialize_state", nodes.initialize_state)
        builder.add_node("retrieve_original_query", nodes.retrieve_original_query)
        builder.add_node("assess_retrieval_quality", nodes.assess_retrieval_quality)
        builder.add_node("rewrite_query", nodes.rewrite_query)
        builder.add_node("retrieve_rewritten_query", nodes.retrieve_rewritten_query)
        builder.add_node("merge_results", nodes.merge_results)
        builder.add_node("expand_links_placeholder", nodes.expand_links_placeholder)
        builder.add_node("rerank_results", nodes.rerank_results)
        builder.add_node("build_context", nodes.build_context)
        builder.add_node("finalize_retrieval", nodes.finalize_retrieval)
        builder.add_node("check_answerability", nodes.check_answerability)
        builder.add_node("finalize_no_answer", nodes.finalize_no_answer)
        builder.add_node("generate_answer", nodes.generate_answer)
        builder.add_node("validate_citations", nodes.validate_citations)
        builder.add_node("basic_evaluate", nodes.basic_evaluate)
        builder.add_node("save_qa_history", nodes.save_qa_history)

        builder.add_edge(START, "initialize_state")
        builder.add_edge("initialize_state", "retrieve_original_query")
        builder.add_edge("retrieve_original_query", "assess_retrieval_quality")
        builder.add_conditional_edges(
            "assess_retrieval_quality",
            nodes.route_rewrite,
            {"rewrite": "rewrite_query", "merge": "merge_results"},
        )
        builder.add_edge("rewrite_query", "retrieve_rewritten_query")
        builder.add_edge("retrieve_rewritten_query", "merge_results")
        builder.add_edge("merge_results", "expand_links_placeholder")
        builder.add_edge("expand_links_placeholder", "rerank_results")
        builder.add_edge("rerank_results", "build_context")
        builder.add_conditional_edges(
            "build_context",
            nodes.route_mode,
            {"retrieve": "finalize_retrieval", "ask": "check_answerability"},
        )
        builder.add_edge("finalize_retrieval", END)
        builder.add_conditional_edges(
            "check_answerability",
            nodes.route_answerability,
            {"answer": "generate_answer", "no_answer": "finalize_no_answer"},
        )
        builder.add_edge("finalize_no_answer", "save_qa_history")
        builder.add_edge("generate_answer", "validate_citations")
        builder.add_edge("validate_citations", "basic_evaluate")
        builder.add_edge("basic_evaluate", "save_qa_history")
        builder.add_edge("save_qa_history", END)
        self.graph = builder.compile(name="rag_workflow")

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
    ) -> RagState:
        state: RagState = {
            "mode": mode,
            "question": question,
            "document_id": document_id,
            "top_k": top_k,
            "rewrite_enabled": rewrite_enabled,
            "source_types": source_types,
            "source_path_prefixes": source_path_prefixes,
        }
        return self.graph.invoke(state, context={"db": db})
