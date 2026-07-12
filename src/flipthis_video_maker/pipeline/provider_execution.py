from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from flipthis_video_maker.config.render_profiles import (
    RenderProfileExecution,
    RenderProfileFallbackRecord,
)
from flipthis_video_maker.providers.base.errors import (
    ProviderCleanupResult,
    ProviderOutOfMemoryError,
)
from flipthis_video_maker.providers.base.protocols import OOMRecoverableProvider

type ProfileOperation[ResultT] = Callable[[RenderProfileExecution], Awaitable[ResultT]]
FallbackCallback = Callable[
    [RenderProfileExecution, ProviderOutOfMemoryError, ProviderCleanupResult], None
]
CleanupCallback = Callable[[ProviderOutOfMemoryError, ProviderCleanupResult], None]


async def execute_with_oom_fallback[ResultT](
    provider: object,
    operation: ProfileOperation[ResultT],
    execution: RenderProfileExecution,
    *,
    job_attempt: int,
    gpu_assignment: str,
    on_fallback: FallbackCallback | None = None,
    on_cleanup: CleanupCallback | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> tuple[ResultT, RenderProfileExecution]:
    """Execute along a captured profile chain after provider-owned, retry-safe cleanup."""

    current = execution
    while True:
        if check_cancelled is not None:
            check_cancelled()
        try:
            return await operation(current), current
        except ProviderOutOfMemoryError as error:
            if check_cancelled is not None:
                check_cancelled()
            if not isinstance(provider, OOMRecoverableProvider):
                raise

            cleanup = await provider.cleanup_after_oom(error)
            if cleanup.provider_id != error.provider_id:
                raise RuntimeError(
                    "Provider cleanup result does not match the failed provider"
                ) from error
            if on_cleanup is not None:
                on_cleanup(error, cleanup)
            if check_cancelled is not None:
                check_cancelled()
            if not cleanup.completed or not cleanup.retry_safe:
                raise

            current_index = next(
                index
                for index, item in enumerate(current.fallback_chain)
                if item.name == current.effective_profile
            )
            if current_index + 1 >= len(current.fallback_chain):
                raise

            target = current.fallback_chain[current_index + 1]
            record = RenderProfileFallbackRecord(
                occurred_at=datetime.now(UTC),
                provider_id=error.provider_id,
                operation=error.operation,
                from_profile=current.effective_profile,
                to_profile=target.name,
                job_attempt=job_attempt,
                gpu_assignment=gpu_assignment,
                backend_code=error.backend_code,
                cleanup_action=cleanup.action_code,
                cleanup_completed=cleanup.completed,
                cleanup_retry_safe=cleanup.retry_safe,
            )
            advanced = current.advance(record)
            if on_fallback is not None:
                on_fallback(advanced, error, cleanup)
            current = advanced


__all__ = ["CleanupCallback", "FallbackCallback", "execute_with_oom_fallback"]
