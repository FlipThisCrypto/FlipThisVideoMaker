from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderFailureKind(StrEnum):
    """Stable provider failure categories understood by provider-neutral orchestration."""

    EXECUTION_FAILED = "execution_failed"
    OUT_OF_MEMORY = "out_of_memory"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    CONTENT_MODERATED = "content_moderated"
    INVALID_INPUT = "invalid_input"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    OUTPUT_INVALID = "output_invalid"


class ProviderExecutionError(RuntimeError):
    """A sanitized provider failure without raw backend payloads, stdout, or stderr."""

    def __init__(
        self,
        *,
        provider_id: str,
        operation: str,
        failure_kind: ProviderFailureKind = ProviderFailureKind.EXECUTION_FAILED,
        retryable: bool = False,
        backend_code: str | None = None,
        provider_job_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.provider_id = _required_value("provider_id", provider_id)
        self.operation = _required_value("operation", operation)
        self.failure_kind = failure_kind
        self.retryable = retryable
        self.backend_code = _optional_value("backend_code", backend_code)
        self.provider_job_id = _optional_value("provider_job_id", provider_job_id)
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Provider {self.provider_id!r} failed during {self.operation!r} "
            f"({self.failure_kind.value})"
        )

    def to_safe_dict(self) -> dict[str, str | bool | float | None]:
        """Return fields safe for core persistence and structured job logs."""

        result: dict[str, str | bool | float | None] = {
            "provider_id": self.provider_id,
            "operation": self.operation,
            "failure_kind": self.failure_kind.value,
            "retryable": self.retryable,
            "backend_code": self.backend_code,
        }
        # Preserve the original stable error shape unless the asynchronous
        # provider supplied these newer optional fields.
        if self.provider_job_id is not None:
            result["provider_job_id"] = self.provider_job_id
        if self.retry_after_seconds is not None:
            result["retry_after_seconds"] = self.retry_after_seconds
        return result


class ProviderOutOfMemoryError(ProviderExecutionError):
    """An OOM classification made explicitly by a provider adapter."""

    def __init__(
        self,
        *,
        provider_id: str,
        operation: str,
        backend_code: str | None = None,
    ) -> None:
        super().__init__(
            provider_id=provider_id,
            operation=operation,
            failure_kind=ProviderFailureKind.OUT_OF_MEMORY,
            retryable=True,
            backend_code=backend_code,
        )


class ProviderCleanupResult(BaseModel):
    """Sanitized provider-owned cleanup outcome used to decide whether retry is safe."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    provider_id: str = Field(min_length=1, max_length=200)
    completed: bool
    retry_safe: bool
    action_code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_.:-]*$")

    @model_validator(mode="after")
    def safe_retry_requires_completed_cleanup(self) -> Self:
        if self.retry_safe and not self.completed:
            raise ValueError("Cleanup must complete before a provider retry can be safe")
        return self


def _required_value(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    if len(normalized) > 200:
        raise ValueError(f"{field} cannot exceed 200 characters")
    return normalized


def _optional_value(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _required_value(field, value)


__all__ = [
    "ProviderCleanupResult",
    "ProviderExecutionError",
    "ProviderFailureKind",
    "ProviderOutOfMemoryError",
]
