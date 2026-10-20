"""Request and response validation models."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class SupportRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000, description="The customer's support question.")
    temperature: float = Field(0.2, ge=0.0, le=1.0, description="Sampling temperature (0 to 1).")
    max_output_tokens: int = Field(800, ge=16, le=2000, description="Upper limit on reply length.")

    @field_validator("question")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("question must contain at least 3 non-space characters")
        return v


class SupportResponse(BaseModel):
    answer: str
    model_used: str
    used_fallback: bool
    truncated: bool = False
    retry_count: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    request_id: str


class ErrorResponse(BaseModel):
    error: Literal[
        "invalid_input", "service_misconfigured", "rate_limited", "timeout",
        "service_unavailable", "bad_model_response", "internal_error",
    ]
    message: str
    request_id: Optional[str] = None
