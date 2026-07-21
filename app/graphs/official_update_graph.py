from langgraph.graph import END, START, StateGraph

from app.graphs.official_update_nodes import OfficialUpdateNodes
from app.graphs.official_update_state import OfficialUpdateContext, OfficialUpdateState


class OfficialUpdateWorkflow:
    def __init__(self, checkpointer) -> None:
        nodes = OfficialUpdateNodes()
        builder = StateGraph(OfficialUpdateState, context_schema=OfficialUpdateContext)
        builder.add_node("initialize_official_audit", nodes.initialize_official_audit)
        builder.add_node("persist_proposal", nodes.persist_proposal)
        builder.add_node("create_approval_request", nodes.create_approval_request)
        builder.add_node("interrupt_for_approval", nodes.interrupt_for_approval)
        builder.add_node("apply_note_batch", nodes.apply_note_batch)
        builder.add_node("finalize_rejected", nodes.finalize_rejected)
        builder.add_edge(START, "initialize_official_audit")
        builder.add_edge("initialize_official_audit", "persist_proposal")
        builder.add_conditional_edges(
            "persist_proposal", nodes.route_proposal, {"approval": "create_approval_request", "done": END}
        )
        builder.add_edge("create_approval_request", "interrupt_for_approval")
        builder.add_conditional_edges(
            "interrupt_for_approval", nodes.route_decision, {"apply": "apply_note_batch", "reject": "finalize_rejected"}
        )
        builder.add_edge("apply_note_batch", END)
        builder.add_edge("finalize_rejected", END)
        self.graph = builder.compile(checkpointer=checkpointer, name="official_update_workflow")
