"""Base class for all Deep Verify verification methods.

This module defines the abstract base class that all verification methods
(Pattern Match, Boundary Analysis, Assumption Surfacing, etc.) must implement.

The ABC pattern ensures consistent interfaces across all methods while allowing
for method-specific implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bmad_assist.deep_verify.core.types import Finding, MethodId


class BaseVerificationMethod(ABC):
    """Abstract base class for Deep Verify verification methods.

    All verification methods (Pattern Match #153, Boundary Analysis #154, etc.)
    must inherit from this class and implement the analyze() method.

    Attributes:
        method_id: Unique method identifier (e.g., "#153", "#154").

    Example:
        >>> class PatternMatchMethod(BaseVerificationMethod):
        ...     method_id = MethodId("#153")
        ...
        ...     async def analyze(
        ...         self,
        ...         artifact_text: str,
        ...         **kwargs: dict[str, object]
        ...     ) -> list[Finding]:
        ...         # Method-specific implementation
        ...         return findings

    """

    method_id: MethodId

    @abstractmethod
    async def analyze(
        self,
        artifact_text: str,
        **kwargs: dict[str, object],
    ) -> list[Finding]:
        """Analyze artifact text and return findings.

        Args:
            artifact_text: The text content to analyze.
            **kwargs: Additional context including:
                - domains: Optional list of ArtifactDomain to filter patterns
                - config: Optional DeepVerifyConfig for method configuration
                - context: Optional additional context for analysis

        Returns:
            List of Finding objects with method-prefixed temporary IDs.
            The DeepVerifyEngine will reassign final sequential IDs (F1, F2, ...).

        Raises:
            Exception: Method implementations should handle their own errors
                gracefully and return empty list on failure.

        """
        ...

    def get_method_prompt(self, **kwargs: object) -> str:
        """Return method's analysis instructions WITHOUT file content.

        Sent as Turn 1 of multi-turn batch session. Override in subclasses
        that support batch mode.

        Args:
            **kwargs: Additional context, may include 'domains'.

        Returns:
            Method instruction prompt string.

        Raises:
            NotImplementedError: If method doesn't support batch mode.

        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support batch mode")

    def get_file_prompt(self, file_path: str, content: str) -> str:
        """Return per-file prompt for Turn 2..N of batch session.

        Args:
            file_path: Path to the file being analyzed.
            content: File content to analyze.

        Returns:
            Formatted file analysis prompt.

        """
        return (
            f"Analyze this file:\n"
            f"=== FILE: {file_path} ===\n{content}\n=== END ===\n\n"
            f"Return your findings in the same JSON format as instructed."
        )

    def parse_file_response(self, raw_response: str, file_path: str) -> list[Finding]:
        """Parse LLM response for a single file in batch mode.

        Args:
            raw_response: Raw LLM response text for one file.
            file_path: Path to the file that was analyzed.

        Returns:
            List of Finding objects extracted from the response.

        Raises:
            NotImplementedError: If method doesn't support batch mode.

        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support batch mode")

    @property
    def supports_batch(self) -> bool:
        """Whether this method supports batch mode."""
        return False

    def __repr__(self) -> str:
        """Return a string representation of the method."""
        method_id = getattr(self, "method_id", "unknown")
        return f"{self.__class__.__name__}(method_id={method_id!r})"
