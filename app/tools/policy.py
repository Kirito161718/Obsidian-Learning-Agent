from dataclasses import dataclass

from app.tools.definitions import CallerType, RiskLevel, ToolDefinition


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: str
    error_code: str | None = None


class ToolPolicy:
    def evaluate(self, definition: ToolDefinition, caller: CallerType) -> PolicyDecision:
        if not definition.enabled:
            return PolicyDecision("denied", "tool_disabled")
        if definition.tool_source == "mcp":
            if definition.risk_level is not RiskLevel.READ_ONLY:
                return PolicyDecision("denied", "mcp_risk_not_allowed")
            if caller is not CallerType.user:
                return PolicyDecision("denied", "caller_not_allowed")
        if caller not in definition.allowed_callers:
            return PolicyDecision("denied", "caller_not_allowed")
        if definition.risk_level is RiskLevel.HIGH_RISK:
            return PolicyDecision("approval_required", "approval_required")
        if definition.risk_level is RiskLevel.LOW_RISK:
            return PolicyDecision("denied", "low_risk_tools_not_enabled")
        return PolicyDecision("allowed")
