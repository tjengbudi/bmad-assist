"""Multi-turn session for batch Deep Verify analysis.

Wraps ClaudeSDKClient for one DV method's multi-turn analysis session.
Turn 1 sends method instructions, turns 2..N send files one at a time.

Max 10 files per session to prevent context degradation; fresh session
for each batch of 10.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from bmad_assist.deep_verify.core.types import FileAnalysisResult

if TYPE_CHECKING:
    from pathlib import Path

    from bmad_assist.deep_verify.methods.base import BaseVerificationMethod

logger = logging.getLogger(__name__)

# Suffix appended to method prompt to prime the session
_ACK_SUFFIX = (
    "\n\nI will send files one at a time for analysis. "
    "For each file, return your findings in the JSON format above. "
    "Acknowledge with 'Ready' and wait for the first file."
)

MAX_FILES_PER_SESSION = 10


class MultiTurnSession:
    """Wraps ClaudeSDKClient for one DV method's multi-turn analysis session.

    Usage::

        async with MultiTurnSession(method, model="haiku") as session:
            result = await session.analyze_file(path, content)

    """

    def __init__(
        self,
        method: BaseVerificationMethod,
        model: str = "haiku",
        base_timeout: int = 30,
        settings: Path | None = None,
    ) -> None:
        """Initialize session with method, model, timeout, and settings."""
        self._method = method
        self._model = model
        self._base_timeout = base_timeout
        self._settings = settings
        self._client: ClaudeSDKClient | None = None

    async def __aenter__(self) -> MultiTurnSession:
        """Connect, send method instructions (Turn 1), consume ack."""
        cli_path = shutil.which("claude")
        options = ClaudeAgentOptions(
            model=self._model,
            permission_mode="acceptEdits",
            settings=str(self._settings) if self._settings else None,
            cli_path=cli_path,
            tools=[],  # No tools — analysis only
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()

        # Turn 1: method prompt + ack request (with timeout)
        method_prompt = self._method.get_method_prompt()
        await self._client.query(method_prompt + _ACK_SUFFIX)

        # Consume the ack response (timeout same as base)
        async with asyncio.timeout(self._base_timeout):
            async for _msg in self._client.receive_response():
                pass  # Discard ack content

        logger.debug(
            "MultiTurnSession established for method %s (model=%s, timeout=%ds)",
            self._method.method_id,
            self._model,
            self._base_timeout,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Disconnect the SDK client."""
        if self._client is not None:
            try:
                await self._client.disconnect()
            except (OSError, RuntimeError) as e:
                logger.debug("Session disconnect error (non-fatal): %s", e)
            self._client = None

    async def analyze_file(
        self,
        file_path: Path,
        content: str,
        timeout: int | None = None,
        extracted_content: str | None = None,
    ) -> FileAnalysisResult:
        """Send a file for analysis (Turn 2..N) and collect findings.

        Args:
            file_path: Path to the file being analyzed.
            content: File content (fallback truncation to 4000 chars).
            timeout: Per-file timeout in seconds.
            extracted_content: Pre-extracted context from intelligent extractor.
                If provided, used instead of raw content[:4000].

        Returns:
            FileAnalysisResult with findings or error.

        """
        if self._client is None:
            return FileAnalysisResult(
                file_path=file_path,
                findings=[],
                raw_response="",
                success=False,
                error="Session not connected",
            )

        start = time.perf_counter()
        effective_timeout = timeout or self._base_timeout

        try:
            # Use intelligent extraction if available, else truncate
            truncated = extracted_content if extracted_content else content[:4000]
            file_prompt = self._method.get_file_prompt(str(file_path), truncated)

            await self._client.query(file_prompt)

            # Collect response
            response_parts: list[str] = []
            async with asyncio.timeout(effective_timeout):
                async for msg in self._client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                response_parts.append(block.text)
                    elif isinstance(msg, ResultMessage):
                        break  # End of response

            raw = "".join(response_parts)
            duration_ms = int((time.perf_counter() - start) * 1000)

            # Parse findings using method's parser
            findings = self._method.parse_file_response(raw, str(file_path))

            return FileAnalysisResult(
                file_path=file_path,
                findings=findings,
                raw_response=raw,
                success=True,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "File analysis timed out for %s (method %s, %ds)",
                file_path.name,
                self._method.method_id,
                effective_timeout,
            )
            return FileAnalysisResult(
                file_path=file_path,
                findings=[],
                raw_response="",
                success=False,
                error=f"Timeout after {effective_timeout}s",
                duration_ms=duration_ms,
            )

        except (OSError, RuntimeError, ValueError) as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "File analysis error for %s (method %s): %s",
                file_path.name,
                self._method.method_id,
                e,
            )
            return FileAnalysisResult(
                file_path=file_path,
                findings=[],
                raw_response="",
                success=False,
                error=f"{type(e).__name__}: {e}",
                duration_ms=duration_ms,
            )
