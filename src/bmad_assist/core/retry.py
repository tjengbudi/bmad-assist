"""Provider timeout retry logic.

Shared retry wrapper for all provider invocations across single-LLM and
multi-LLM phases. Handles ProviderTimeoutError with configurable retry count.

Story: Per-phase timeout retry configuration.
"""

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from bmad_assist.core.exceptions import ProviderTimeoutError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def invoke_with_timeout_retry(
    invoke_fn: Callable[..., T],
    *,
    timeout_retries: int | None,
    phase_name: str,
    **kwargs: Any,
) -> T:
    """Invoke provider function with timeout retry logic.

    Retries provider invocation on ProviderTimeoutError, preserving all
    parameters (including prompt) across retry attempts. Timer is reset
    for each retry.

    Args:
        invoke_fn: Callable that invokes the provider (e.g., provider.invoke).
        timeout_retries: Retry count from get_phase_retries().
            None = no retry (fail immediately on timeout).
            0 = infinite retry (until success).
            N = retry N times, then fail.
        phase_name: Phase name for logging (e.g., "dev_story", "validate_story").
        **kwargs: Arguments to pass to invoke_fn.

    Returns:
        Result from invoke_fn on success.

    Raises:
        ProviderTimeoutError: If all retry attempts exhausted.
            - When timeout_retries is None: raised immediately.
            - When timeout_retries is N: raised after N+1 attempts.
            - When timeout_retries is 0: never raised (infinite retry).

    Examples:
        >>> # Single-LLM phase (BaseHandler)
        >>> result = invoke_with_timeout_retry(
        ...     provider.invoke,
        ...     timeout_retries=get_phase_retries(config, "dev_story"),
        ...     phase_name="dev_story",
        ...     prompt=prompt,
        ...     model=model,
        ...     timeout=timeout,
        ... )

        >>> # Multi-LLM phase (async orchestrator)
        >>> async def invoke_with_retry():
        ...     return await asyncio.to_thread(
        ...         invoke_with_timeout_retry,
        ...         provider.invoke,
        ...         timeout_retries=get_phase_retries(config, "validate_story"),
        ...         phase_name="validate_story",
        ...         prompt=prompt,
        ...         model=model,
        ...         timeout=timeout,
        ...     )
        >>> result = await invoke_with_retry()

    """
    # Check if retry is configured
    if timeout_retries is None:
        # No retry - invoke once and let timeout propagate
        return invoke_fn(**kwargs)

    # Retry is configured
    timeout_attempt = 0

    while True:
        timeout_attempt += 1

        try:
            return invoke_fn(**kwargs)
        except ProviderTimeoutError as e:
            # Check retry limit
            if timeout_retries != 0 and timeout_attempt > timeout_retries:
                # Retries exhausted
                logger.error(
                    "Provider timeout in %s phase after %d attempts (max %d configured): %s",
                    phase_name,
                    timeout_attempt,
                    timeout_retries,
                    str(e)[:200],
                )
                raise

            # Retry - preserve kwargs (including prompt), reset timer
            remaining_retries = (
                "infinite" if timeout_retries == 0 else timeout_retries - timeout_attempt
            )
            logger.warning(
                "Provider timeout in %s phase (attempt %d/%s): %s. Retrying with same prompt...",
                phase_name,
                timeout_attempt,
                remaining_retries,
                str(e)[:100],
            )
            # No delay for timeout retry - restart immediately
            continue
