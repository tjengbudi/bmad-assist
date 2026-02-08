"""Method selector for Deep Verify.

This module provides the MethodSelector class that selects verification methods
based on detected artifact domains and configuration settings.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from bmad_assist.deep_verify.core.types import ArtifactDomain
from bmad_assist.deep_verify.methods import (
    AdversarialReviewMethod,
    AssumptionSurfacingMethod,
    BoundaryAnalysisMethod,
    DomainExpertMethod,
    IntegrationAnalysisMethod,
    PatternMatchMethod,
    TemporalConsistencyMethod,
    WorstCaseMethod,
)

if TYPE_CHECKING:
    from bmad_assist.deep_verify.config import DeepVerifyConfig, MethodConfig
    from bmad_assist.deep_verify.infrastructure.llm_client import LLMClient
    from bmad_assist.deep_verify.methods.base import BaseVerificationMethod


class MethodSelector:
    """Selects verification methods based on detected domains and config.

    The method selection follows the matrix defined in Story 26.15:
    - Always Run: #153 (Pattern Match), #154 (Boundary Analysis), #203 (Domain Expert)
    - CONCURRENCY: + #155 (Assumption Surfacing), #205 (Worst-Case)
    - API: + #155 (Assumption Surfacing), #201 (Adversarial), #204 (Integration)
    - MESSAGING: + #157 (Temporal), #204 (Integration), #205 (Worst-Case)
    - STORAGE: + #157 (Temporal), #204 (Integration), #205 (Worst-Case)
    - SECURITY: + #201 (Adversarial)
    - TRANSFORM: (patterns only)

    Attributes:
        _config: DeepVerifyConfig with method enable/disable flags.
        _llm_client: LLMClient for LLM-based methods.
        _model: Model identifier for LLM calls.

    Example:
        >>> config = DeepVerifyConfig()
        >>> selector = MethodSelector(config, llm_client=client, model="haiku")
        >>> methods = selector.select([ArtifactDomain.SECURITY, ArtifactDomain.API])
        >>> len(methods) >= 3  # At least always-run methods
        True

    """

    def __init__(
        self,
        config: DeepVerifyConfig,
        llm_client: LLMClient | None = None,
        model: str = "haiku",
    ) -> None:
        """Initialize the method selector.

        Args:
            config: DeepVerifyConfig with method enable/disable flags.
            llm_client: LLMClient for LLM-based methods. If None, methods
                will create their own providers (legacy behavior).
            model: Model identifier for LLM calls (default: "haiku").

        """
        self._config = config
        self._llm_client = llm_client
        self._model = model

    def _get_method_timeout(self, method_config: MethodConfig) -> int:
        """Resolve timeout for a method.

        Uses per-method timeout_seconds if set, otherwise falls back to
        llm_config.default_timeout_seconds.

        Args:
            method_config: Per-method configuration.

        Returns:
            Timeout in seconds.

        """
        if method_config.timeout_seconds is not None:
            return method_config.timeout_seconds
        return self._config.llm_config.default_timeout_seconds

    def select(self, domains: list[ArtifactDomain]) -> list[BaseVerificationMethod]:
        """Select methods based on domains and enabled flags.

        If config.enabled is False, returns empty list.
        Uses default pattern library for PatternMatchMethod (no-arg constructor).
        Respects per-method enabled flags and timeout_seconds from config.

        Args:
            domains: List of detected artifact domains.

        Returns:
            List of selected verification methods.

        """
        if not self._config.enabled:
            return []

        # Map method IDs to their per-method config for timeout resolution
        method_configs: dict[str, MethodConfig] = {
            "#153": self._config.method_153_pattern_match,
            "#154": self._config.method_154_boundary_analysis,
            "#155": self._config.method_155_assumption_surfacing,
            "#157": self._config.method_157_temporal_consistency,
            "#201": self._config.method_201_adversarial_review,
            "#203": self._config.method_203_domain_expert,
            "#204": self._config.method_204_integration_analysis,
            "#205": self._config.method_205_worst_case,
        }

        methods: list[BaseVerificationMethod] = []
        added_method_ids: set[str] = set()  # Track added methods to prevent duplicates
        domain_set = set(domains)

        def add_method(
            method_id: str,
            factory: Callable[..., BaseVerificationMethod],
            needs_llm: bool = False,
        ) -> None:
            """Add method if not already added.

            Args:
                method_id: Method ID for deduplication.
                factory: Method class/factory.
                needs_llm: If True, passes llm_client, model, and timeout to factory.

            """
            if method_id not in added_method_ids:
                timeout = self._get_method_timeout(method_configs[method_id])
                if needs_llm and self._llm_client is not None:
                    methods.append(
                        factory(
                            llm_client=self._llm_client,
                            model=self._model,
                            timeout=timeout,
                        )
                    )
                else:
                    methods.append(factory())
                added_method_ids.add(method_id)

        # Always-run methods
        # #153 Pattern Match - no LLM needed (regex-based)
        if self._config.method_153_pattern_match.enabled:
            add_method("#153", PatternMatchMethod, needs_llm=False)
        # #154 Boundary Analysis - LLM-based
        if self._config.method_154_boundary_analysis.enabled:
            add_method("#154", BoundaryAnalysisMethod, needs_llm=True)
        # #203 Domain Expert - LLM-based
        if self._config.method_203_domain_expert.enabled:
            add_method("#203", DomainExpertMethod, needs_llm=True)

        # Domain-specific methods (all LLM-based)
        if (
            ArtifactDomain.CONCURRENCY in domain_set or ArtifactDomain.API in domain_set
        ) and self._config.method_155_assumption_surfacing.enabled:
            add_method("#155", AssumptionSurfacingMethod, needs_llm=True)

        if ArtifactDomain.API in domain_set:
            if self._config.method_201_adversarial_review.enabled:
                add_method("#201", AdversarialReviewMethod, needs_llm=True)
            if self._config.method_204_integration_analysis.enabled:
                add_method("#204", IntegrationAnalysisMethod, needs_llm=True)

        if ArtifactDomain.MESSAGING in domain_set or ArtifactDomain.STORAGE in domain_set:
            if self._config.method_157_temporal_consistency.enabled:
                add_method("#157", TemporalConsistencyMethod, needs_llm=True)
            if self._config.method_204_integration_analysis.enabled:
                add_method("#204", IntegrationAnalysisMethod, needs_llm=True)
            if self._config.method_205_worst_case.enabled:
                add_method("#205", WorstCaseMethod, needs_llm=True)

        if (
            ArtifactDomain.CONCURRENCY in domain_set
            and self._config.method_205_worst_case.enabled
        ):
            add_method("#205", WorstCaseMethod, needs_llm=True)

        if (
            ArtifactDomain.SECURITY in domain_set
            and self._config.method_201_adversarial_review.enabled
        ):
            add_method("#201", AdversarialReviewMethod, needs_llm=True)

        return methods

    def __repr__(self) -> str:
        """Return string representation."""
        enabled_count = len(self._config.get_enabled_methods())
        return f"MethodSelector(enabled_methods={enabled_count})"
