"""Tests for MethodSelector.

This module provides tests for the MethodSelector class from Story 26.15.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bmad_assist.deep_verify.config import DeepVerifyConfig, LLMConfig, MethodConfig
from bmad_assist.deep_verify.core import ArtifactDomain, MethodSelector
from bmad_assist.deep_verify.core.types import MethodId


class TestMethodSelector:
    """Tests for MethodSelector class."""

    def test_selector_initializes_with_config(self) -> None:
        """Test that MethodSelector initializes with config."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)
        assert selector._config == config

    def test_always_run_methods_selected(self) -> None:
        """Test that always-run methods are always selected."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.TRANSFORM])  # Domain with no extras
        method_ids = [m.method_id for m in methods]

        assert MethodId("#153") in method_ids  # Pattern Match
        assert MethodId("#154") in method_ids  # Boundary Analysis
        assert MethodId("#203") in method_ids  # Domain Expert

    def test_security_domain_selects_adversarial(self) -> None:
        """Test SECURITY domain selects adversarial review method."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.SECURITY])
        method_ids = [m.method_id for m in methods]

        assert MethodId("#201") in method_ids  # Adversarial Review

    def test_api_domain_selects_multiple_methods(self) -> None:
        """Test API domain selects appropriate methods."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.API])
        method_ids = [m.method_id for m in methods]

        assert MethodId("#155") in method_ids  # Assumption Surfacing
        assert MethodId("#201") in method_ids  # Adversarial Review
        assert MethodId("#204") in method_ids  # Integration Analysis

    def test_concurrency_domain_selects_assumption_and_worst_case(self) -> None:
        """Test CONCURRENCY domain selects assumption and worst-case methods."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.CONCURRENCY])
        method_ids = [m.method_id for m in methods]

        assert MethodId("#155") in method_ids  # Assumption Surfacing
        assert MethodId("#205") in method_ids  # Worst-Case

    def test_messaging_domain_selects_temporal_integration_worst_case(
        self,
    ) -> None:
        """Test MESSAGING domain selects temporal, integration, and worst-case methods."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.MESSAGING])
        method_ids = [m.method_id for m in methods]

        assert MethodId("#157") in method_ids  # Temporal Consistency
        assert MethodId("#204") in method_ids  # Integration Analysis
        assert MethodId("#205") in method_ids  # Worst-Case

    def test_storage_domain_selects_temporal_integration_worst_case(
        self,
    ) -> None:
        """Test STORAGE domain selects temporal, integration, and worst-case methods."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.STORAGE])
        method_ids = [m.method_id for m in methods]

        assert MethodId("#157") in method_ids  # Temporal Consistency
        assert MethodId("#204") in method_ids  # Integration Analysis
        assert MethodId("#205") in method_ids  # Worst-Case

    def test_multiple_domains_select_union_of_methods(self) -> None:
        """Test multiple domains select union of appropriate methods."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.SECURITY, ArtifactDomain.API])
        method_ids = [m.method_id for m in methods]

        # Always-run methods
        assert MethodId("#153") in method_ids
        assert MethodId("#154") in method_ids
        assert MethodId("#203") in method_ids

        # SECURITY + API specific
        assert MethodId("#155") in method_ids  # From API
        assert MethodId("#201") in method_ids  # From both
        assert MethodId("#204") in method_ids  # From API

    def test_disabled_config_returns_empty_list(self) -> None:
        """Test that disabled config returns empty list."""
        config = DeepVerifyConfig(enabled=False)
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.SECURITY])
        assert methods == []

    def test_disabled_method_not_selected(self) -> None:
        """Test that disabled method is not selected."""
        config = DeepVerifyConfig(
            method_201_adversarial_review=MethodConfig(enabled=False)
        )
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.SECURITY])
        method_ids = [m.method_id for m in methods]

        assert MethodId("#201") not in method_ids
        # But always-run methods should still be there
        assert MethodId("#153") in method_ids

    def test_pattern_match_uses_default_library(self) -> None:
        """Test that PatternMatchMethod is created with default library."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.API])
        pattern_method = next(
            (m for m in methods if m.method_id == MethodId("#153")), None
        )

        assert pattern_method is not None
        # Should be created with no-arg constructor (uses default library)
        from bmad_assist.deep_verify.methods import PatternMatchMethod
        assert isinstance(pattern_method, PatternMatchMethod)

    def test_selector_repr(self) -> None:
        """Test MethodSelector string representation."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        repr_str = repr(selector)
        assert "MethodSelector" in repr_str
        assert "enabled_methods=" in repr_str

    def test_transform_domain_only_always_run(self) -> None:
        """Test TRANSFORM domain only selects always-run methods."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.TRANSFORM])
        method_ids = [m.method_id for m in methods]

        # Only always-run methods
        assert MethodId("#153") in method_ids
        assert MethodId("#154") in method_ids
        assert MethodId("#203") in method_ids

        # No domain-specific methods for TRANSFORM
        assert MethodId("#155") not in method_ids
        assert MethodId("#157") not in method_ids
        assert MethodId("#201") not in method_ids
        assert MethodId("#204") not in method_ids
        assert MethodId("#205") not in method_ids

    def test_empty_domains_only_always_run(self) -> None:
        """Test empty domains list only selects always-run methods."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        methods = selector.select([])
        method_ids = [m.method_id for m in methods]

        # Only always-run methods
        assert MethodId("#153") in method_ids
        assert MethodId("#154") in method_ids
        assert MethodId("#203") in method_ids

    def test_all_methods_disabled_returns_empty(self) -> None:
        """Test that all methods disabled returns empty list."""
        config = DeepVerifyConfig(
            method_153_pattern_match=MethodConfig(enabled=False),
            method_154_boundary_analysis=MethodConfig(enabled=False),
            method_155_assumption_surfacing=MethodConfig(enabled=False),
            method_157_temporal_consistency=MethodConfig(enabled=False),
            method_201_adversarial_review=MethodConfig(enabled=False),
            method_203_domain_expert=MethodConfig(enabled=False),
            method_204_integration_analysis=MethodConfig(enabled=False),
            method_205_worst_case=MethodConfig(enabled=False),
        )
        selector = MethodSelector(config)

        methods = selector.select([ArtifactDomain.API, ArtifactDomain.SECURITY])
        assert methods == []

    def test_api_and_concurrency_both_select_assumption_surfacing(self) -> None:
        """Test that API and CONCURRENCY both select assumption surfacing."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        # API domain
        api_methods = selector.select([ArtifactDomain.API])
        api_ids = [m.method_id for m in api_methods]

        # CONCURRENCY domain
        concurrency_methods = selector.select([ArtifactDomain.CONCURRENCY])
        concurrency_ids = [m.method_id for m in concurrency_methods]

        # Both should have assumption surfacing
        assert MethodId("#155") in api_ids
        assert MethodId("#155") in concurrency_ids

    def test_concurrency_and_messaging_both_select_worst_case(self) -> None:
        """Test that CONCURRENCY and MESSAGING both select worst-case."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        # CONCURRENCY domain
        concurrency_methods = selector.select([ArtifactDomain.CONCURRENCY])
        concurrency_ids = [m.method_id for m in concurrency_methods]

        # MESSAGING domain
        messaging_methods = selector.select([ArtifactDomain.MESSAGING])
        messaging_ids = [m.method_id for m in messaging_methods]

        # Both should have worst-case
        assert MethodId("#205") in concurrency_ids
        assert MethodId("#205") in messaging_ids

    def test_storage_and_messaging_same_method_selection(self) -> None:
        """Test that STORAGE and MESSAGING have same method selection."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        storage_methods = selector.select([ArtifactDomain.STORAGE])
        storage_ids = {m.method_id for m in storage_methods}

        messaging_methods = selector.select([ArtifactDomain.MESSAGING])
        messaging_ids = {m.method_id for m in messaging_methods}

        # Both should have same methods
        assert storage_ids == messaging_ids

    def test_method_count_reasonable(self) -> None:
        """Test that method count is reasonable (not too many or too few)."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        # Single domain should have at least always-run (3)
        api_methods = selector.select([ArtifactDomain.API])
        assert len(api_methods) >= 3

        # TRANSFORM domain should have exactly always-run methods (3)
        transform_methods = selector.select([ArtifactDomain.TRANSFORM])
        assert len(transform_methods) == 3

    def test_no_duplicate_methods(self) -> None:
        """Test that no duplicate methods are returned."""
        config = DeepVerifyConfig()
        selector = MethodSelector(config)

        # API + CONCURRENCY (both have overlapping methods)
        methods = selector.select([ArtifactDomain.API, ArtifactDomain.CONCURRENCY])
        method_ids = [m.method_id for m in methods]

        # Should have no duplicates
        assert len(method_ids) == len(set(method_ids))

    def test_per_method_timeout_propagated(self) -> None:
        """Test that per-method timeout_seconds is passed to method constructor."""
        config = DeepVerifyConfig(
            method_154_boundary_analysis=MethodConfig(enabled=True, timeout_seconds=90),
        )
        mock_client = MagicMock()
        selector = MethodSelector(config, llm_client=mock_client, model="haiku")

        methods = selector.select([ArtifactDomain.TRANSFORM])
        boundary = next(m for m in methods if m.method_id == MethodId("#154"))

        assert boundary._timeout == 90

    def test_global_default_timeout_propagated(self) -> None:
        """Test that llm_config.default_timeout_seconds is used when per-method is None."""
        config = DeepVerifyConfig(
            llm_config=LLMConfig(default_timeout_seconds=120),
        )
        mock_client = MagicMock()
        selector = MethodSelector(config, llm_client=mock_client, model="haiku")

        methods = selector.select([ArtifactDomain.TRANSFORM])
        boundary = next(m for m in methods if m.method_id == MethodId("#154"))

        assert boundary._timeout == 120

    def test_default_timeout_is_30_without_config(self) -> None:
        """Test that default timeout is 30s when no config overrides."""
        config = DeepVerifyConfig()
        mock_client = MagicMock()
        selector = MethodSelector(config, llm_client=mock_client, model="haiku")

        methods = selector.select([ArtifactDomain.TRANSFORM])
        boundary = next(m for m in methods if m.method_id == MethodId("#154"))

        assert boundary._timeout == 30
