"""Claude Agent SDK-based provider implementation.

This module implements the ClaudeSDKProvider class - the PRIMARY Claude integration
for bmad-assist. It uses the official claude-agent-sdk package which provides:
- Native Python async/await API
- Typed message classes (AssistantMessage, ResultMessage, etc.)
- Proper error types (CLINotFoundError, ProcessError, etc.)
- Session management for multi-turn conversations

This is the default Claude provider. The subprocess-based ClaudeSubprocessProvider
(claude.py) is retained only for benchmarking and fair comparison with Codex and
Gemini providers which only have subprocess interfaces.

Key Design Decision: NO FALLBACK
- If SDK fails, the operation fails immediately
- No silent fallback to subprocess - errors must be visible
- Subprocess provider only used when explicitly requested

Example:
    >>> from bmad_assist.providers import ClaudeSDKProvider
    >>> provider = ClaudeSDKProvider()
    >>> result = provider.invoke("What is 2+2?", model="sonnet")
    >>> response = provider.parse_output(result)

"""

import asyncio
import logging
import shutil
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    ProcessError,
    TextBlock,
    query,
)

from bmad_assist.core.exceptions import (
    ProviderError,
    ProviderTimeoutError,
)
from bmad_assist.providers.base import (
    BaseProvider,
    ProviderResult,
    validate_settings_file,
)

logger = logging.getLogger(__name__)

# Supported short model names accepted by Claude Code
SUPPORTED_MODELS: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})

# Default timeout in seconds (5 minutes)
DEFAULT_TIMEOUT: int = 300


class ClaudeSDKProvider(BaseProvider):
    """Claude Code SDK-based provider implementation.

    The PRIMARY Claude integration for bmad-assist. Uses the official
    claude-agent-sdk package for native async support, typed messages,
    and proper SDK error handling.

    This provider should be used for all Claude invocations. The subprocess-based
    ClaudeSubprocessProvider is retained only for benchmarking purposes.

    Claude Code supports these models:
        - opus: Most capable model
        - sonnet: Balanced model (default)
        - haiku: Fastest model
        - Any full identifier starting with "claude-"

    Key Design: NO FALLBACK
        If the SDK fails for any reason, ProviderError is raised immediately.
        There is no silent fallback to subprocess - errors must be visible.
        This ensures predictable behavior and accurate error reporting.

    Settings File Handling:
        When settings_file is provided to invoke(), it is validated for
        existence before SDK invocation. If the file is missing or is not
        a regular file, a warning is logged and settings parameter is omitted
        (graceful degradation).

    Example:
        >>> provider = ClaudeSDKProvider()
        >>> result = provider.invoke("Hello", model="opus", timeout=60)
        >>> print(provider.parse_output(result))

    """

    @property
    def provider_name(self) -> str:
        """Return unique identifier for this provider.

        Returns:
            The string "claude" as the provider identifier.
            Note: Both ClaudeSDKProvider and ClaudeSubprocessProvider
            represent the "claude" logical provider, but SDK is primary.

        """
        return "claude"

    @property
    def default_model(self) -> str | None:
        """Return default model when none specified.

        Returns:
            The string "sonnet" as the balanced default choice.

        """
        return "sonnet"

    def supports_model(self, model: str) -> bool:
        """Check if this provider supports the given model.

        Validates both short model names (opus, sonnet, haiku) and
        full Claude model identifiers (strings starting with "claude-").

        Args:
            model: Model identifier to check.

        Returns:
            True if provider supports the model, False otherwise.

        Example:
            >>> provider = ClaudeSDKProvider()
            >>> provider.supports_model("sonnet")
            True
            >>> provider.supports_model("claude-sonnet-4-20250514")
            True
            >>> provider.supports_model("gpt-4")
            False

        """
        return model in SUPPORTED_MODELS or model.startswith("claude-")

    def _resolve_settings(
        self,
        settings_file: Path | None,
        model: str,
    ) -> Path | None:
        """Resolve and validate settings file for invocation.

        Internal helper that validates settings file existence and logs
        a warning if missing. Called after model validation, before
        SDK invocation.

        Args:
            settings_file: Settings file path from caller, or None.
            model: Model identifier for logging context.

        Returns:
            Validated settings file Path if exists and is a file,
            None otherwise (triggers graceful degradation to defaults).

        """
        if settings_file is None:
            return None

        return validate_settings_file(
            settings_file=settings_file,
            provider_name=self.provider_name,
            model=model,
        )

    @staticmethod
    async def _prompt_stream(prompt: str) -> AsyncIterator[dict[str, Any]]:
        """Wrap prompt string as async stream-json iterator.

        Uses SDK streaming input mode (--input-format stream-json) to send
        prompt via stdin instead of CLI args. This avoids E2BIG (Argument
        list too long) errors with large compiled workflow prompts.

        Args:
            prompt: The prompt text to send.

        Yields:
            Single user message dict in SDK stream-json format.

        """
        yield {
            "type": "user",
            "message": {"role": "user", "content": prompt},
        }

    async def _invoke_async(
        self,
        prompt: str,
        model: str,
        settings: Path | None,
        cwd: Path | None,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """Execute SDK query asynchronously.

        Internal async helper that performs the actual SDK call. Uses streaming
        input mode to pass prompt via stdin (avoids CLI argument length limits).
        Iterates through SDK messages and extracts text content from
        AssistantMessage blocks.

        Args:
            prompt: The prompt text to send to Claude.
            model: Model identifier to use.
            settings: Optional validated settings file path.
            cwd: Working directory for the CLI process.
            allowed_tools: Optional list of tools to allow. If provided, uses
                the SDK's 'tools' parameter to restrict available tools.

        Returns:
            Response text extracted from AssistantMessage TextBlocks.

        Raises:
            CLINotFoundError: If Claude Code CLI is not found.
            ProcessError: If Claude Code process fails.
            ProviderError: If no response is received (empty iteration).

        """
        # Prefer system-installed CLI; fall back to SDK-bundled binary
        system_cli = shutil.which("claude")
        if system_cli:
            logger.debug("Using system Claude CLI: %s", system_cli)
        else:
            logger.warning("System Claude CLI not found, falling back to SDK-bundled binary")

        # Build SDK options with explicit values
        options = ClaudeAgentOptions(
            model=model,
            permission_mode="acceptEdits",  # Explicit automation mode
            settings=str(settings) if settings is not None else None,
            cwd=cwd,
            cli_path=system_cli,  # Skip bundled CLI, use system install
            # Tool restrictions: use 'tools' parameter to set explicit list
            # IMPORTANT: empty list [] means "no tools", None means "all tools"
            # Use explicit check for None to distinguish [] from None
            tools=allowed_tools if allowed_tools is not None else None,
        )

        response_parts: list[str] = []

        try:
            # Use streaming input to avoid E2BIG with large prompts
            prompt_iter = self._prompt_stream(prompt)
            async for message in query(prompt=prompt_iter, options=options):
                # Extract text content from AssistantMessage only
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            response_parts.append(block.text)
                # ResultMessage is metadata only (cost/usage) - skip
                # Other message types (SystemMessage, UserMessage) - skip

        except (CLINotFoundError, ProcessError):
            # Re-raise SDK errors for handling in invoke()
            raise

        # Validate we got a response (AC12)
        if not response_parts:
            raise ProviderError("No response received from SDK")

        return "".join(response_parts)

    async def _invoke_with_cancel(
        self,
        prompt: str,
        model: str,
        settings: Path | None,
        cwd: Path | None,
        allowed_tools: list[str] | None,
        cancel_token: threading.Event,
        timeout: int,
    ) -> str:
        """Execute SDK query with cancel_token support.

        Runs SDK query and cancel monitor as concurrent tasks. When
        cancel_token is set, the SDK task is cancelled — this propagates
        CancelledError into the async generator, killing the subprocess.

        Args:
            prompt: The prompt text to send to Claude.
            model: Model identifier to use.
            settings: Optional validated settings file path.
            cwd: Working directory for the CLI process.
            allowed_tools: Optional list of allowed tools.
            cancel_token: Threading event checked for cancellation.
            timeout: Timeout in seconds.

        Returns:
            Response text from SDK.

        Raises:
            asyncio.CancelledError: If cancel_token was set.
            TimeoutError: If timeout exceeded.
            CLINotFoundError: If Claude Code CLI is not found.
            ProcessError: If Claude Code process fails.

        """
        sdk_task = asyncio.create_task(
            self._invoke_async(prompt, model, settings, cwd, allowed_tools)
        )

        async def _wait_for_cancel() -> None:
            while not cancel_token.is_set():
                await asyncio.sleep(0.5)

        cancel_task = asyncio.create_task(_wait_for_cancel())

        try:
            done, pending = await asyncio.wait(
                {sdk_task, cancel_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            sdk_task.cancel()
            cancel_task.cancel()
            raise

        # Clean up pending tasks
        for task in pending:
            task.cancel()

        # Timeout — neither finished
        if not done:
            raise TimeoutError

        # Cancel was triggered
        if cancel_task in done and sdk_task not in done:
            logger.info("Cancel token set, cancelling SDK task")
            raise asyncio.CancelledError

        # SDK completed (cancel_task still pending — already cancelled above)
        return sdk_task.result()

    def invoke(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: int | None = None,
        settings_file: Path | None = None,
        cwd: Path | None = None,
        disable_tools: bool = False,
        allowed_tools: list[str] | None = None,
        no_cache: bool = False,
        color_index: int | None = None,
        display_model: str | None = None,
        thinking: bool | None = None,
        cancel_token: threading.Event | None = None,
        reasoning_effort: str | None = None,
    ) -> ProviderResult:
        """Execute Claude Code SDK with the given prompt.

        Invokes Claude Code via the official SDK with the specified prompt
        and optional configuration. Uses asyncio.run() with wait_for() for
        timeout enforcement since SDK has no native timeout option.

        Args:
            prompt: The prompt text to send to Claude.
            model: Model to use (opus, sonnet, haiku, or claude-* identifier).
                If None, uses default_model ("sonnet").
            timeout: Timeout in seconds. Must be positive (>= 1) if specified.
                If None, uses DEFAULT_TIMEOUT (300s).
            settings_file: Path to Claude settings JSON file.
            cwd: Working directory for the CLI process.
            disable_tools: Disable tools (ignored - SDK doesn't support).
            allowed_tools: List of allowed tools (e.g., ["TodoWrite"]).
                When set, only specified tools are available to the agent.
                Uses SDK's 'tools' parameter to set explicit tool list.
            no_cache: Disable caching (ignored - SDK doesn't support).
            color_index: Color index for terminal output differentiation.

        Returns:
            ProviderResult containing:
                - stdout: response text extracted from AssistantMessage
                - stderr: empty string (SDK doesn't separate stderr)
                - exit_code: 0
                - duration_ms: execution time in milliseconds
                - model: the model used
                - command: list describing SDK invocation (e.g., ["sdk", "query", model])

        Raises:
            ValueError: If timeout is not positive (<=0).
            ProviderError: If SDK execution fails due to:
                - Unsupported model specified
                - CLI not found (CLINotFoundError)
                - Process failure (ProcessError)
                - No response received
                - Any unexpected exception
            ProviderTimeoutError: If SDK invocation exceeds timeout.

        Note:
            NO FALLBACK - If SDK fails, error is propagated immediately.
            No attempt to use subprocess is made.

        Example:
            >>> provider = ClaudeSDKProvider()
            >>> result = provider.invoke("Hello", model="sonnet", timeout=60)
            >>> result.exit_code
            0

        """
        # Ignored parameters (SDK doesn't support these)
        _ = no_cache, color_index
        # Note: allowed_tools IS supported - passed to _invoke_async
        if disable_tools and allowed_tools is None:
            allowed_tools = []

        # Validate timeout parameter
        if timeout is not None and timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        # Resolve model with fallback chain: explicit -> default -> literal
        effective_model = model or self.default_model or "sonnet"
        effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

        # Validate model before SDK invocation (fail-fast)
        if not self.supports_model(effective_model):
            raise ProviderError(
                f"Unsupported model '{effective_model}' for Claude provider. "
                f"Supported: {', '.join(sorted(SUPPORTED_MODELS))} or claude-* identifiers"
            )

        # Validate and resolve settings file
        validated_settings = self._resolve_settings(settings_file, effective_model)

        # Resolve display model (model_name from config, e.g. "glm-4.7")
        shown_model = display_model or effective_model

        tools_info = allowed_tools if allowed_tools else "all"
        logger.debug(
            "Invoking Claude SDK: model=%s, display_model=%s, timeout=%ds, "
            "prompt_len=%d, settings=%s, tools=%s",
            effective_model,
            shown_model,
            effective_timeout,
            len(prompt),
            validated_settings,
            tools_info,
        )

        start_time = time.perf_counter()

        # Build command representation for ProviderResult
        command: tuple[str, ...] = ("sdk", "query", effective_model)

        try:
            # CRITICAL: Use run_async_in_thread() instead of asyncio.run() to avoid
            # shutting down the default executor when called via asyncio.to_thread()
            from bmad_assist.core.async_utils import run_async_in_thread

            if cancel_token is not None:
                response_text = run_async_in_thread(
                    self._invoke_with_cancel(
                        prompt, effective_model, validated_settings, cwd,
                        allowed_tools, cancel_token, effective_timeout,
                    )
                )
            else:
                response_text = run_async_in_thread(
                    asyncio.wait_for(
                        self._invoke_async(
                            prompt, effective_model, validated_settings, cwd, allowed_tools
                        ),
                        timeout=effective_timeout,
                    )
                )
        except asyncio.CancelledError:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            logger.info("SDK cancelled after %dms", duration_ms)
            return ProviderResult(
                stdout="",
                stderr="",
                exit_code=-15,
                duration_ms=duration_ms,
                model=shown_model,
                command=command,
            )
        except TimeoutError as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            logger.warning(
                "SDK timeout: model=%s, timeout=%ds, duration_ms=%d",
                effective_model,
                effective_timeout,
                duration_ms,
            )
            raise ProviderTimeoutError(f"SDK timeout after {effective_timeout}s") from e
        except CLINotFoundError as e:
            logger.error("Claude Code not found")
            raise ProviderError("Claude Code not found. Is 'claude' installed and in PATH?") from e
        except ProcessError as e:
            # Extract exit code and stderr from ProcessError
            exit_code = e.exit_code if e.exit_code is not None else 1
            stderr = e.stderr or ""
            logger.error(
                "Claude SDK process error: exit_code=%s, stderr=%s",
                exit_code,
                stderr[:200] if stderr else "(empty)",
            )
            raise ProviderError(
                f"Claude SDK failed with exit code {exit_code}: {stderr[:200]}"
            ) from e
        except ProviderError:
            # Re-raise ProviderError (e.g., "No response received")
            raise
        except Exception as e:
            # Catch any unexpected exception and wrap in ProviderError
            # NO FALLBACK - error propagates immediately
            logger.error("Unexpected SDK error: %s", e)
            raise ProviderError(f"Unexpected SDK error: {e}") from e

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        logger.info(
            "Claude SDK completed: duration=%dms, response_len=%d",
            duration_ms,
            len(response_text),
        )

        return ProviderResult(
            stdout=response_text,
            stderr="",  # SDK doesn't separate stderr
            exit_code=0,
            duration_ms=duration_ms,
            model=shown_model,
            command=command,
        )

    def parse_output(self, result: ProviderResult) -> str:
        r"""Extract response text from SDK output.

        The SDK already returns clean text extracted from AssistantMessage.
        This method strips leading/trailing whitespace for consistency.

        Args:
            result: ProviderResult from invoke() containing response text.

        Returns:
            Extracted response text with whitespace stripped.
            Empty string if stdout is empty.

        Example:
            >>> result = ProviderResult(stdout="  Hello world  \n", ...)
            >>> provider.parse_output(result)
            'Hello world'

        """
        return result.stdout.strip()
