import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.tools.audit import ToolAuditService
from app.tools.definitions import CallerType
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry, get_tool_registry


@dataclass(slots=True)
class ToolExecutionContext:
    db: Session
    services: dict[str, Any] = field(default_factory=dict)


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, execution_id: str | None = None, decision: str = "denied") -> None:
        self.code = code
        self.execution_id = execution_id
        self.decision = decision
        super().__init__(message)


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
        audit_service: ToolAuditService | None = None,
    ) -> None:
        self.registry = registry or get_tool_registry()
        self.policy = policy or ToolPolicy()
        self.audit = audit_service or ToolAuditService()

    async def execute(
        self,
        db: Session,
        *,
        tool_name: str,
        raw_input: dict,
        caller: CallerType = CallerType.user,
        idempotency_key: str | None = None,
        run_id: str | None = None,
        services: dict[str, Any] | None = None,
    ) -> tuple[dict, Any]:
        definition = self.registry.get(tool_name)
        if definition is None:
            audit = self.audit.create(
                db,
                tool_name=tool_name,
                risk_level=None,
                caller_type=caller.value,
                decision="denied",
                status="denied",
                raw_input=raw_input,
                idempotency_key=idempotency_key,
                run_id=run_id,
                error_code="tool_not_registered",
            )
            raise ToolExecutionError(
                "tool_not_registered", "Tool is not registered", execution_id=audit.id
            )

        try:
            parsed_input = definition.input_schema.model_validate(raw_input)
        except ValidationError as exc:
            audit = self.audit.create(
                db,
                tool_name=tool_name,
                risk_level=definition.risk_level.value,
                caller_type=caller.value,
                decision="denied",
                status="denied",
                raw_input=raw_input,
                idempotency_key=idempotency_key,
                run_id=run_id,
                error_code="input_validation_failed",
            )
            raise ToolExecutionError(
                "input_validation_failed", "Tool input validation failed", execution_id=audit.id
            ) from exc

        decision = self.policy.evaluate(definition, caller)
        status = "pending" if decision.decision == "allowed" else "denied"
        audit = self.audit.create(
            db,
            tool_name=tool_name,
            risk_level=definition.risk_level.value,
            caller_type=caller.value,
            decision=decision.decision,
            status=status,
            raw_input=raw_input,
            idempotency_key=idempotency_key,
            run_id=run_id,
            error_code=decision.error_code,
        )
        if decision.decision != "allowed":
            raise ToolExecutionError(
                decision.error_code or "tool_denied",
                "Tool execution requires approval" if decision.decision == "approval_required" else "Tool execution denied",
                execution_id=audit.id,
                decision=decision.decision,
            )

        self.audit.mark_running(db, audit)
        context = ToolExecutionContext(db=db, services=services or {})
        try:
            output = await asyncio.wait_for(
                self._invoke(definition.callable, parsed_input, context),
                timeout=definition.timeout_seconds,
            )
        except TimeoutError as exc:
            self.audit.fail(db, audit, error_code="tool_timeout", error="Tool execution timed out")
            raise ToolExecutionError("tool_timeout", "Tool execution timed out", execution_id=audit.id) from exc
        except Exception as exc:
            self.audit.fail(db, audit, error_code="tool_execution_failed", error=type(exc).__name__)
            raise ToolExecutionError(
                "tool_execution_failed", "Tool execution failed", execution_id=audit.id
            ) from exc

        try:
            validated = definition.output_schema.model_validate(output)
        except ValidationError as exc:
            self.audit.fail(db, audit, error_code="output_validation_failed", error="Tool output validation failed")
            raise ToolExecutionError(
                "output_validation_failed", "Tool output validation failed", execution_id=audit.id
            ) from exc
        result = validated.model_dump(mode="json")
        record = self.audit.succeed(db, audit, result)
        return result, record

    @staticmethod
    async def _invoke(callable_obj, parsed_input, context: ToolExecutionContext):
        if inspect.iscoroutinefunction(callable_obj):
            return await callable_obj(parsed_input, context)
        return await asyncio.to_thread(callable_obj, parsed_input, context)
