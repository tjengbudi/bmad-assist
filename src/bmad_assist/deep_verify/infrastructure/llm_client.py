"""LLM Client with retry, rate limiting, cost tracking, and timeout handling.

This module provides the LLMClient class that wraps the provider with:
- Retry logic with exponential backoff
- Token bucket rate limiting
- Cost tracking per model and method
- Timeout handling with graceful degradation
- Comprehensive logging of all calls

Example:
    >>> from bmad_assist.deep_verify.infrastructure.llm_client import LLMClient
    >>> from bmad_assist.providers import ClaudeSDKProvider
    >>> from bmad_assist.deep_verify.config import DeepVerifyConfig
    >>>
    >>> config = DeepVerifyConfig()
    >>> client = LLMClient(config, ClaudeSDKProvider())
    >>>
    >>> # Async invocation with all features
    >>> result = await client.invoke(
    ...     prompt="Analyze this code",
    ...     model="haiku",
    ...     timeout=30,
    ...     method_id="#153",
    ... )
    >>>
    >>> # Get cost summary
    >>> summary = client.get_cost_summary()
    >>> print(f"Total cost: ${summary.estimated_cost_usd:.4f}")

"""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from bmad_assist.deep_verify.config import DeepVerifyConfig

from bmad_assist.core.exceptions import (
    ProviderError,
    ProviderTimeoutError,
)
from bmad_assist.deep_verify.infrastructure.cost_tracker import (
    create_cost_tracker,
    estimate_tokens,
)
from bmad_assist.deep_verify.infrastructure.rate_limiter import (
    create_rate_limiter,
)
from bmad_assist.deep_verify.infrastructure.retry_handler import (
    RetryConfig,
    RetryHandler,
)
from bmad_assist.deep_verify.infrastructure.types import (
    CostSummary,
    LLMCallRecord,
)
from bmad_assist.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger(__name__)


# =============================================================================
# LLM Client
# =============================================================================


class LLMClient:
    """Wraps provider with retry, rate limiting, cost tracking, and timeout handling.

    This client provides a robust async interface for LLM calls with:
    - Exponential backoff retry for transient failures
    - Token bucket rate limiting to prevent quota exhaustion
    - Cost tracking per model and verification method
    - Timeout handling with graceful degradation
    - Comprehensive call logging

    The client is thread-safe for concurrent use by multiple verification methods.
    It bridges the sync ClaudeSDKProvider to an async interface using
    asyncio.to_thread() where needed.

    Attributes:
        config: DeepVerifyConfig with LLM settings.
        provider: The underlying provider to wrap.

    Example:
        >>> client = LLMClient(config, provider)
        >>> result = await client.invoke(prompt, model="haiku", method_id="#153")
        >>> print(f"Cost: ${client.get_cost_summary().estimated_cost_usd:.4f}")

    """

    def __init__(
        self,
        config: DeepVerifyConfig,
        provider: BaseProvider,
        settings_file: Path | None = None,
        thinking: bool | None = None,
    ) -> None:
        """Initialize the LLM client.

        Args:
            config: DeepVerifyConfig with llm_config settings.
            provider: The underlying provider to wrap.
            settings_file: Optional path to provider settings JSON file.
            thinking: Optional thinking mode flag for supported providers.

        """
        self.config = config
        self.provider = provider
        self._settings_file = settings_file
        self._thinking = thinking

        # Get LLM config (handle both new and old config structures)
        llm_config = getattr(config, "llm_config", None)
        if llm_config is None:
            # Create default config if not present
            from bmad_assist.deep_verify.config import LLMConfig

            llm_config = LLMConfig()

        # Initialize retry handler
        retry_config = RetryConfig(
            max_retries=llm_config.max_retries,
            base_delay_seconds=llm_config.base_delay_seconds,
            max_delay_seconds=llm_config.max_delay_seconds,
            jitter_factor=0.2,
        )
        self._retry_handler = RetryHandler(retry_config)

        # Initialize rate limiter
        self._rate_limiter = create_rate_limiter(
            llm_config.tokens_per_minute_limit if llm_config.tokens_per_minute_limit > 0 else None
        )

        # Initialize cost tracker
        self._cost_tracker = create_cost_tracker(enabled=llm_config.cost_tracking_enabled)

        # Initialize call log
        self._call_log: list[LLMCallRecord] = []
        self._log_all_calls = llm_config.log_all_calls

        # Default timeouts
        self._default_timeout = llm_config.default_timeout_seconds
        self._total_timeout = llm_config.total_timeout_seconds

        logger.debug(
            "LLMClient initialized: retries=%d, rate_limit=%s, cost_tracking=%s",
            retry_config.max_retries,
            llm_config.tokens_per_minute_limit,
            llm_config.cost_tracking_enabled,
        )

    def __repr__(self) -> str:
        """Return string representation for logging."""
        summary = self._cost_tracker.get_summary()
        return f"LLMClient(calls={summary.total_calls}, cost=${summary.estimated_cost_usd:.4f})"

    async def invoke(
        self,
        prompt: str,
        model: str,
        timeout: int | None = None,
        method_id: str | None = None,
    ) -> ProviderResult:
        """Invoke LLM with retry, rate limit, cost tracking, and timeout.

        This is the main method for making LLM calls. It handles:
        1. Rate limiting (waits if tokens exhausted)
        2. Retry logic for transient failures
        3. Cost tracking (input/output tokens)
        4. Call logging

        Args:
            prompt: The prompt to send to the LLM.
            model: Model identifier to use.
            timeout: Timeout in seconds (None = use default from config).
            method_id: Optional method ID for cost tracking attribution.

        Returns:
            ProviderResult with the LLM response.

        Raises:
            ProviderTimeoutError: After max retries exceeded or total timeout.
            ProviderError: For non-retriable provider errors.

        Example:
            >>> result = await client.invoke(
            ...     prompt="Analyze this code",
            ...     model="haiku",
            ...     timeout=30,
            ...     method_id="#153",
            ... )
            >>> print(result.stdout)

        """
        timeout = timeout or self._default_timeout

        # Estimate tokens for rate limiting
        estimated_tokens = estimate_tokens(prompt)

        # Apply rate limiting
        wait_time = await self._rate_limiter.acquire(estimated_tokens)
        if wait_time > 0:
            logger.info(
                "Rate limited: waited %.2fs for %d tokens",
                wait_time,
                estimated_tokens,
            )

        # Execute with retry
        last_error: Exception | None = None
        retry_count = 0
        start_time = datetime.now()

        for attempt in range(self._retry_handler.config.max_retries + 1):
            try:
                result = await self._execute_with_timeout(
                    prompt=prompt,
                    model=model,
                    timeout=timeout,
                )

                # Success - record and return
                await self._record_call(
                    start_time=start_time,
                    model=model,
                    prompt=prompt,
                    result=result,
                    method_id=method_id,
                    retry_count=retry_count,
                )

                if retry_count > 0:
                    logger.info(
                        "LLM call succeeded after %d retries",
                        retry_count,
                    )

                return result

            except (ProviderTimeoutError, ProviderError, ConnectionError, TimeoutError) as e:
                last_error = e

                # Check if we should retry
                if (
                    attempt < self._retry_handler.config.max_retries
                    and self._retry_handler.should_retry(e)
                ):
                    retry_count += 1
                    delay = self._retry_handler.calculate_backoff(attempt)

                    logger.warning(
                        "LLM call failed (attempt %d/%d): %s. Retrying in %.2fs...",
                        attempt + 1,
                        self._retry_handler.config.max_retries + 1,
                        type(e).__name__,
                        delay,
                    )

                    await asyncio.sleep(delay)
                else:
                    # No more retries or not retriable
                    break

        # All retries exhausted
        logger.error(
            "LLM call failed after %d attempts: %s",
            retry_count + 1,
            last_error,
        )

        # Record the failed call
        await self._record_failed_call(
            start_time=start_time,
            model=model,
            error=last_error,
            method_id=method_id,
            retry_count=retry_count,
        )

        # Re-raise the last error
        if last_error:
            raise last_error

        # Should not reach here, but just in case
        raise ProviderError("LLM call failed with unknown error")

    async def _execute_with_timeout(
        self,
        prompt: str,
        model: str,
        timeout: int,
    ) -> ProviderResult:
        """Execute provider call with timeout.

        Args:
            prompt: The prompt to send.
            model: Model identifier.
            timeout: Timeout in seconds.

        Returns:
            ProviderResult from the provider.

        Raises:
            ProviderTimeoutError: If the call times out.
            ProviderError: For other provider errors.

        """
        # Run sync provider in thread pool
        loop = asyncio.get_running_loop()

        try:
            # Use asyncio.timeout() for Python 3.11+
            async with asyncio.timeout(timeout):
                kwargs: dict[str, Any] = {
                    "prompt": prompt,
                    "model": model,
                    "timeout": timeout,
                }
                if self._settings_file is not None:
                    kwargs["settings_file"] = self._settings_file
                if self._thinking is not None:
                    kwargs["thinking"] = self._thinking
                result = await loop.run_in_executor(
                    None,  # Default ThreadPoolExecutor
                    functools.partial(self.provider.invoke, **kwargs),
                )
                return result
        except TimeoutError as e:
            raise ProviderTimeoutError(f"LLM call exceeded {timeout}s timeout") from e

    async def _record_call(
        self,
        start_time: datetime,
        model: str,
        prompt: str,
        result: ProviderResult,
        method_id: str | None,
        retry_count: int,
    ) -> None:
        """Record a successful call.

        Args:
            start_time: When the call started.
            model: Model used.
            prompt: The prompt sent to the LLM.
            result: Provider result.
            method_id: Method identifier.
            retry_count: Number of retries performed.

        """
        now = datetime.now()
        latency_ms = int((now - start_time).total_seconds() * 1000)

        # Estimate tokens (fallback since ProviderResult may not have usage)
        input_tokens = estimate_tokens(prompt)
        output_tokens = estimate_tokens(result.stdout)

        # Record cost
        self._cost_tracker.record_call(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            method_id=method_id or "unknown",
        )

        # Create call record
        record = LLMCallRecord(
            timestamp=start_time,
            method_id=method_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            success=True,
            error=None,
            retry_count=retry_count,
        )

        self._call_log.append(record)

        if self._log_all_calls:
            logger.info(
                "LLM call: model=%s, method=%s, tokens=%d+%d, latency=%dms, retries=%d",
                model,
                method_id or "unknown",
                input_tokens,
                output_tokens,
                latency_ms,
                retry_count,
            )

    async def _record_failed_call(
        self,
        start_time: datetime,
        model: str,
        error: Exception | None,
        method_id: str | None,
        retry_count: int,
    ) -> None:
        """Record a failed call.

        Args:
            start_time: When the call started.
            model: Model used.
            error: The error that occurred.
            method_id: Method identifier.
            retry_count: Number of retries performed.

        """
        now = datetime.now()
        latency_ms = int((now - start_time).total_seconds() * 1000)

        error_msg = str(error) if error else "Unknown error"

        # Create call record
        record = LLMCallRecord(
            timestamp=start_time,
            method_id=method_id,
            model=model,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            success=False,
            error=error_msg[:500],  # Truncate long errors
            retry_count=retry_count,
        )

        self._call_log.append(record)

        logger.warning(
            "LLM call failed: model=%s, method=%s, latency=%dms, retries=%d, error=%s",
            model,
            method_id or "unknown",
            latency_ms,
            retry_count,
            error_msg[:200],
        )

    def get_cost_summary(self) -> CostSummary:
        """Get cost summary for all recorded calls.

        Returns:
            CostSummary with aggregated cost information.

        """
        return self._cost_tracker.get_summary()

    def get_call_log(self) -> list[LLMCallRecord]:
        """Get log of all LLM calls.

        Returns:
            List of LLMCallRecord in chronological order.

        """
        return list(self._call_log)

    def reset_tracking(self) -> None:
        """Reset cost tracking and call log.

        This clears all accumulated cost and call history.
        Use this between verification runs.
        """
        self._cost_tracker.reset()
        self._call_log.clear()
        logger.debug("LLMClient tracking reset")

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics.

        Returns:
            Dictionary with client statistics.

        """
        summary = self._cost_tracker.get_summary()

        return {
            "total_calls": summary.total_calls,
            "total_tokens": summary.total_tokens,
            "total_cost_usd": summary.estimated_cost_usd,
            "calls_by_model": {model: cost.calls for model, cost in summary.by_model.items()},
            "calls_by_method": {method: cost.calls for method, cost in summary.by_method.items()},
            "failed_calls": sum(1 for record in self._call_log if not record.success),
        }


# =============================================================================
# Convenience Functions
# =============================================================================


def create_llm_client(
    config: DeepVerifyConfig,
    provider: BaseProvider | None = None,
) -> LLMClient:
    """Create an LLM client with the given configuration.

    Args:
        config: DeepVerifyConfig with llm_config settings.
        provider: Optional provider (defaults to ClaudeSDKProvider).

    Returns:
        Configured LLMClient.

    Example:
        >>> from bmad_assist.deep_verify.config import DeepVerifyConfig
        >>> config = DeepVerifyConfig()
        >>> client = create_llm_client(config)

    """
    if provider is None:
        from bmad_assist.providers import ClaudeSDKProvider

        provider = ClaudeSDKProvider()

    return LLMClient(config, provider)
