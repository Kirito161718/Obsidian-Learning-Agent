from langgraph.graph import END, START, StateGraph

from app.graphs.official_update_nodes import OfficialUpdateNodes
from app.graphs.official_update_state import OfficialUpdateContext, OfficialUpdateState
from app.observability.decorators import observe_official_node


class OfficialUpdateWorkflow:
    def __init__(self, checkpointer) -> None:
        nodes = OfficialUpdateNodes()
        builder = StateGraph(OfficialUpdateState, context_schema=OfficialUpdateContext)
        builder.add_node(
            "initialize_official_audit",
            observe_official_node("ola.official.audit", nodes.initialize_official_audit),
        )
        builder.add_node(
            "persist_proposal",
            observe_official_node("ola.official.proposal.build", nodes.persist_proposal),
        )
        builder.add_node(
            "create_approval_request",
            observe_official_node("ola.official.approval.interrupt", nodes.create_approval_request),
        )
        builder.add_node(
            "interrupt_for_approval",
            observe_official_node("ola.official.approval.interrupt", nodes.interrupt_for_approval),
        )
        builder.add_node(
            "apply_note_batch",
            observe_official_node("ola.official.apply", nodes.apply_note_batch),
        )
        builder.add_node(
            "finalize_rejected",
            observe_official_node("ola.official.approval.resume", nodes.finalize_rejected),
        )
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
