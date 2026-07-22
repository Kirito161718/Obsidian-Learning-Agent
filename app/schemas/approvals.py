from pydantic import BaseModel, ConfigDict, Field


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(min_length=64, max_length=64)
    confirm_all: bool
    confirmation_text: str = Field(min_length=1, max_length=100)


class RejectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=1000)
