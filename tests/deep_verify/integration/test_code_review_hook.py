"""Tests for Deep Verify code_review integration hook.

Story 26.20: Code Review Integration Hook
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_assist.deep_verify.config import DeepVerifyConfig
from bmad_assist.deep_verify.core.language_detector import LanguageInfo
from bmad_assist.deep_verify.core.types import (
    ArtifactDomain,
    DeepVerifyValidationResult,
    DomainConfidence,
    Evidence,
    Finding,
    MethodId,
    Severity,
    Verdict,
    VerdictDecision,
)
from bmad_assist.deep_verify.integration.code_review_hook import (
    _find_story_file,
    _resolve_code_files,
    load_dv_findings_from_cache,
    run_deep_verify_code_review,
    save_dv_findings_for_synthesis,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_config():
    """Create a mock config with Deep Verify enabled."""
    config = MagicMock()
    config.deep_verify = DeepVerifyConfig(enabled=True)
    return config


@pytest.fixture
def mock_config_disabled():
    """Create a mock config with Deep Verify disabled."""
    config = MagicMock()
    config.deep_verify = DeepVerifyConfig(enabled=False)
    return config


@pytest.fixture
def mock_config_no_dv():
    """Create a mock config without Deep Verify field."""
    config = MagicMock()
    config.deep_verify = None
    return config


@pytest.fixture
def sample_finding():
    """Create a sample finding for testing."""
    return Finding(
        id="F1",
        severity=Severity.CRITICAL,
        title="SQL Injection Vulnerability",
        description="Unsanitized user input in SQL query",
        method_id=MethodId("#201"),
        pattern_id=None,
        domain=ArtifactDomain.SECURITY,
        evidence=[
            Evidence(
                quote="query = f'SELECT * FROM users WHERE id = {user_id}'",
                line_number=42,
                source="main.py",
                confidence=0.95,
            )
        ],
    )


@pytest.fixture
def sample_verdict(sample_finding):
    """Create a sample verdict for testing."""
    return Verdict(
        decision=VerdictDecision.REJECT,
        score=8.5,
        findings=[sample_finding],
        domains_detected=[
            DomainConfidence(
                domain=ArtifactDomain.SECURITY,
                confidence=0.95,
                signals=["auth", "token"],
            )
        ],
        methods_executed=[MethodId("#153"), MethodId("#201")],
        summary="REJECT verdict (score: 8.5). 1 findings: F1. Domains: security. Methods: #153, #201.",
    )


@pytest.fixture
def temp_project_path(tmp_path):
    """Create a temporary project path."""
    return tmp_path


@pytest.fixture
def mock_paths(temp_project_path):
    """Create mock ProjectPaths with stories_dir pointing to temp_project_path."""
    from unittest.mock import MagicMock
    mock_project_paths = MagicMock()
    # stories_dir is the implementation_artifacts dir (no "stories" subfolder)
    mock_project_paths.stories_dir = temp_project_path / "_bmad-output" / "implementation-artifacts"
    return mock_project_paths


@pytest.fixture
def sample_story_file(temp_project_path, mock_paths):
    """Create a sample story file with File List section."""
    stories_dir = mock_paths.stories_dir
    stories_dir.mkdir(parents=True, exist_ok=True)

    story_content = """# Story 26.20: Code Review Integration Hook

Status: ready-for-dev

## File List
- src/main.py - Main implementation
- src/utils.py - Utility functions
- tests/test_main.py - Tests

## Tasks
- [ ] Task 1
"""
    story_file = stories_dir / "26-20-code-review-integration-hook.md"
    story_file.write_text(story_content)
    return story_file


# =============================================================================
# run_deep_verify_code_review Tests
# =============================================================================


class TestRunDeepVerifyCodeReview:
    """Tests for run_deep_verify_code_review function."""

    @pytest.mark.asyncio
    async def test_success_case(self, mock_config, temp_project_path, sample_verdict):
        """Test successful DV code review execution."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(return_value=sample_verdict)
            mock_engine_class.return_value = mock_engine

            test_file = Path("src/main.py")
            result = await run_deep_verify_code_review(
                file_path=test_file,
                code_content="def test(): pass",
                config=mock_config,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
                story_ref="26.20",
                timeout=60,
            )

            assert isinstance(result, DeepVerifyValidationResult)
            assert result.verdict == VerdictDecision.REJECT
            assert result.score == 8.5
            assert len(result.findings) == 1
            assert result.findings[0].id == "F1"
            assert result.error is None

            mock_engine_class.assert_called_once()
            call_kwargs = mock_engine_class.call_args.kwargs
            assert call_kwargs["project_root"] == temp_project_path
            assert call_kwargs["config"] == mock_config.deep_verify
            assert "helper_provider_config" in call_kwargs
            mock_engine.verify.assert_called_once()

    @pytest.mark.asyncio
    async def test_disabled_in_config(self, mock_config_disabled, temp_project_path):
        """Test that DV is skipped when disabled in config."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            result = await run_deep_verify_code_review(
                file_path=Path("src/main.py"),
                code_content="def test(): pass",
                config=mock_config_disabled,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
            )

            assert isinstance(result, DeepVerifyValidationResult)
            assert result.verdict == VerdictDecision.ACCEPT
            assert result.score == 0.0
            assert len(result.findings) == 0
            assert result.error is None
            mock_engine_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_dv_config(self, mock_config_no_dv, temp_project_path):
        """Test behavior when deep_verify config is missing."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            result = await run_deep_verify_code_review(
                file_path=Path("src/main.py"),
                code_content="def test(): pass",
                config=mock_config_no_dv,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
            )

            assert isinstance(result, DeepVerifyValidationResult)
            assert result.verdict == VerdictDecision.ACCEPT
            assert result.score == 0.0
            assert len(result.findings) == 0
            assert result.error is None
            mock_engine_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_handling(self, mock_config, temp_project_path):
        """Test graceful error handling when engine fails."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            # Use RuntimeError (specific exception per project anti-patterns)
            mock_engine.verify = AsyncMock(side_effect=RuntimeError("Engine failure"))
            mock_engine_class.return_value = mock_engine

            result = await run_deep_verify_code_review(
                file_path=Path("src/main.py"),
                code_content="def test(): pass",
                config=mock_config,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
            )

            assert isinstance(result, DeepVerifyValidationResult)
            assert result.verdict == VerdictDecision.ACCEPT
            assert result.score == 0.0
            assert len(result.findings) == 0
            assert "Engine failure" in result.error
            assert "RuntimeError:" in result.error

    @pytest.mark.asyncio
    async def test_language_detection_integration(
        self, mock_config, temp_project_path, sample_verdict
    ):
        """Test that language detection is used."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(return_value=sample_verdict)
            mock_engine_class.return_value = mock_engine

            with patch(
                "bmad_assist.deep_verify.integration.code_review_hook.LanguageDetector"
            ) as mock_detector_class:
                mock_detector = MagicMock()
                mock_detector.detect.return_value = LanguageInfo(
                    language="python",
                    confidence=0.95,
                    file_type="source",
                    detection_method="extension",
                )
                mock_detector_class.return_value = mock_detector

                await run_deep_verify_code_review(
                    file_path=Path("src/main.py"),
                    code_content="def test(): pass",
                    config=mock_config,
                    project_path=temp_project_path,
                    epic_num=26,
                    story_num=20,
                )

                # Verify language detector was called
                mock_detector.detect.assert_called_once()
                # Verify engine.verify was called with context containing language
                call_kwargs = mock_engine.verify.call_args[1]
                assert call_kwargs.get("context") is not None
                assert call_kwargs["context"].language == "python"

    @pytest.mark.asyncio
    async def test_unknown_language_handling(
        self, mock_config, temp_project_path, sample_verdict
    ):
        """Test handling of unknown language."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(return_value=sample_verdict)
            mock_engine_class.return_value = mock_engine

            with patch(
                "bmad_assist.deep_verify.integration.code_review_hook.LanguageDetector"
            ) as mock_detector_class:
                mock_detector = MagicMock()
                mock_detector.detect.return_value = LanguageInfo.unknown()
                mock_detector_class.return_value = mock_detector

                result = await run_deep_verify_code_review(
                    file_path=Path("src/main.xyz"),
                    code_content="unknown content",
                    config=mock_config,
                    project_path=temp_project_path,
                    epic_num=26,
                    story_num=20,
                )

                assert result.verdict == VerdictDecision.REJECT
                # Verify context was created with None language
                call_kwargs = mock_engine.verify.call_args[1]
                assert call_kwargs["context"].language is None

    @pytest.mark.asyncio
    async def test_timeout_passed_to_engine(self, mock_config, temp_project_path, sample_verdict):
        """Test that timeout is passed to engine.verify()."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(return_value=sample_verdict)
            mock_engine_class.return_value = mock_engine

            await run_deep_verify_code_review(
                file_path=Path("src/main.py"),
                code_content="def test(): pass",
                config=mock_config,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
                timeout=120,
            )

            mock_engine.verify.assert_called_once()
            call_kwargs = mock_engine.verify.call_args[1]
            assert call_kwargs.get("timeout") == 120

    @pytest.mark.asyncio
    async def test_provider_error_handling(self, mock_config, temp_project_path):
        """Test that ProviderError is caught and handled gracefully."""
        from bmad_assist.core.exceptions import ProviderError

        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(side_effect=ProviderError("Provider failed"))
            mock_engine_class.return_value = mock_engine

            result = await run_deep_verify_code_review(
                file_path=Path("src/main.py"),
                code_content="def test(): pass",
                config=mock_config,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
            )

            assert isinstance(result, DeepVerifyValidationResult)
            assert result.verdict == VerdictDecision.ACCEPT
            assert result.error is not None
            assert "ProviderError" in result.error


# =============================================================================
# File Discovery Tests
# =============================================================================


class TestResolveCodeFiles:
    """Tests for _resolve_code_files function."""

    def test_resolve_from_story_file_list(self, temp_project_path, sample_story_file, mock_paths):
        """Test resolving code files from story File List."""
        # Create the referenced files
        src_dir = temp_project_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "main.py").write_text("def main(): pass")
        (src_dir / "utils.py").write_text("def util(): pass")
        tests_dir = temp_project_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_main.py").write_text("def test_main(): pass")

        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            files = _resolve_code_files(temp_project_path, 26, 20)

        assert len(files) == 3
        file_paths = [str(f[0]) for f in files]
        assert any("main.py" in p for p in file_paths)
        assert any("utils.py" in p for p in file_paths)
        assert any("test_main.py" in p for p in file_paths)

    def test_no_story_file(self, temp_project_path, mock_paths):
        """Test when no story file exists."""
        # Ensure stories_dir exists but is empty
        mock_paths.stories_dir.mkdir(parents=True, exist_ok=True)
        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            files = _resolve_code_files(temp_project_path, 99, 99)
        assert files == []

    def test_no_file_list_section(self, temp_project_path, mock_paths):
        """Test when story file has no File List section."""
        stories_dir = mock_paths.stories_dir
        stories_dir.mkdir(parents=True, exist_ok=True)
        story_file = stories_dir / "26-21-test.md"
        story_file.write_text("# Story without File List\n\nSome content\n")

        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            files = _resolve_code_files(temp_project_path, 26, 21)
        assert files == []

    def test_missing_files_logged(self, temp_project_path, sample_story_file, mock_paths, caplog):
        """Test that missing files are logged and skipped."""
        import logging

        # Don't create the files - they should be logged as missing
        with caplog.at_level(logging.WARNING):
            with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
                mock_get_paths.return_value = mock_paths
                files = _resolve_code_files(temp_project_path, 26, 20)

        # All files should be skipped since they don't exist
        assert len(files) == 0
        # Check that missing files were logged
        assert any("File not found" in msg for msg in caplog.messages)

    def test_path_traversal_prevention(self, temp_project_path, mock_paths):
        """Test that path traversal attempts are blocked."""
        # Create a story with malicious file path
        stories_dir = mock_paths.stories_dir
        stories_dir.mkdir(parents=True, exist_ok=True)
        story_file = stories_dir / "26-99-test.md"
        story_file.write_text("""# Story

## File List
- ../../../etc/passwd - Malicious path
- src/main.py - Normal path
""")

        # Create only the valid file
        src_dir = temp_project_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "main.py").write_text("def main(): pass")

        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            files = _resolve_code_files(temp_project_path, 26, 99)

        # Only the valid file should be included
        assert len(files) == 1
        assert "main.py" in str(files[0][0])

    def test_size_limit(self, temp_project_path, mock_paths):
        """Test that code size limit is enforced."""
        stories_dir = mock_paths.stories_dir
        stories_dir.mkdir(parents=True, exist_ok=True)
        story_file = stories_dir / "26-30-test.md"

        # Create a story with multiple large files
        story_content = "# Story\n\n## File List\n"
        for i in range(10):
            story_content += f"- src/large_{i}.py - Large file\n"
        story_file.write_text(story_content)

        # Create large files (15KB each)
        src_dir = temp_project_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        for i in range(10):
            (src_dir / f"large_{i}.py").write_text("x" * (15 * 1024))

        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            files = _resolve_code_files(temp_project_path, 26, 30)

        # Should be limited by 100KB max
        total_size = sum(f[0].stat().st_size for f in files)
        assert total_size <= 100 * 1024


# =============================================================================
# Cache Tests
# =============================================================================


class TestCacheOperations:
    """Tests for save/load DV findings cache."""

    def test_save_dv_findings_for_synthesis(self, temp_project_path, sample_finding):
        """Test saving DV findings to cache."""
        result = DeepVerifyValidationResult(
            findings=[sample_finding],
            domains_detected=[
                DomainConfidence(
                    domain=ArtifactDomain.SECURITY,
                    confidence=0.95,
                    signals=["auth"],
                )
            ],
            methods_executed=[MethodId("#201")],
            verdict=VerdictDecision.REJECT,
            score=8.5,
            duration_ms=4500,
            error=None,
        )

        cache_path = save_dv_findings_for_synthesis(
            result=result,
            project_path=temp_project_path,
            session_id="test-session-123",
            file_path=Path("src/main.py"),
            language="python",
        )

        assert cache_path.exists()
        data = json.loads(cache_path.read_text())

        assert data["verdict"] == "REJECT"
        assert data["score"] == 8.5
        assert data["session_id"] == "test-session-123"
        assert data["file_path"] == "src/main.py"
        assert data["language"] == "python"
        assert len(data["findings"]) == 1
        assert data["findings"][0]["id"] == "F1"

    def test_load_dv_findings_from_cache(self, temp_project_path, sample_finding):
        """Test loading DV findings from cache."""
        # First save
        result = DeepVerifyValidationResult(
            findings=[sample_finding],
            domains_detected=[
                DomainConfidence(
                    domain=ArtifactDomain.SECURITY,
                    confidence=0.95,
                    signals=["auth"],
                )
            ],
            methods_executed=[MethodId("#201")],
            verdict=VerdictDecision.REJECT,
            score=8.5,
            duration_ms=4500,
            error=None,
        )

        save_dv_findings_for_synthesis(
            result=result,
            project_path=temp_project_path,
            session_id="test-session-456",
        )

        # Then load (no file_path for global/single result)
        loaded = load_dv_findings_from_cache("test-session-456", temp_project_path)

        assert loaded is not None
        assert loaded.verdict == VerdictDecision.REJECT
        assert loaded.score == 8.5
        assert len(loaded.findings) == 1
        assert loaded.findings[0].id == "F1"

    def test_load_nonexistent_cache(self, temp_project_path):
        """Test loading from non-existent cache."""
        result = load_dv_findings_from_cache("nonexistent-session", temp_project_path)
        assert result is None

    def test_round_trip_serialization(self, temp_project_path, sample_finding):
        """Test that save/load round-trip preserves data."""
        original = DeepVerifyValidationResult(
            findings=[sample_finding],
            domains_detected=[
                DomainConfidence(
                    domain=ArtifactDomain.SECURITY,
                    confidence=0.95,
                    signals=["auth"],
                )
            ],
            methods_executed=[MethodId("#153"), MethodId("#201")],
            verdict=VerdictDecision.REJECT,
            score=8.5,
            duration_ms=4500,
            error=None,
        )

        save_dv_findings_for_synthesis(
            result=original,
            project_path=temp_project_path,
            session_id="round-trip-test",
        )

        # Load global result (no file_path)
        loaded = load_dv_findings_from_cache("round-trip-test", temp_project_path)

        assert loaded.verdict == original.verdict
        assert loaded.score == original.score
        assert loaded.duration_ms == original.duration_ms
        assert len(loaded.findings) == len(original.findings)
        assert len(loaded.domains_detected) == len(original.domains_detected)


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestFindStoryFile:
    """Tests for _find_story_file function."""

    def test_find_existing_story(self, temp_project_path, mock_paths):
        """Test finding an existing story file."""
        stories_dir = mock_paths.stories_dir
        stories_dir.mkdir(parents=True, exist_ok=True)
        story_file = stories_dir / "26-10-test-story.md"
        story_file.write_text("# Test Story")

        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            found = _find_story_file(temp_project_path, 26, 10)

        assert found is not None
        assert found.name == "26-10-test-story.md"

    def test_story_not_found(self, temp_project_path, mock_paths):
        """Test when story file doesn't exist."""
        # Ensure stories_dir exists but is empty
        mock_paths.stories_dir.mkdir(parents=True, exist_ok=True)
        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            found = _find_story_file(temp_project_path, 99, 99)
        assert found is None

    def test_find_with_string_epic(self, temp_project_path, mock_paths):
        """Test finding story with string epic ID."""
        stories_dir = mock_paths.stories_dir
        stories_dir.mkdir(parents=True, exist_ok=True)
        story_file = stories_dir / "testarch-1-config.md"
        story_file.write_text("# Test Story")

        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            found = _find_story_file(temp_project_path, "testarch", 1)

        assert found is not None
        assert "testarch" in found.name

    def test_paths_not_initialized(self, temp_project_path):
        """Test behavior when paths are not initialized."""
        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.side_effect = RuntimeError("Paths not initialized")
            found = _find_story_file(temp_project_path, 26, 10)
        assert found is None


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for DV code_review hook."""

    @pytest.mark.asyncio
    async def test_end_to_end_with_mock_engine(
        self, mock_config, temp_project_path
    ):
        """Test complete flow with mocked engine."""
        finding = Finding(
            id="F1",
            severity=Severity.ERROR,
            title="Race Condition",
            description="Potential race condition detected",
            method_id=MethodId("#153"),
            domain=ArtifactDomain.CONCURRENCY,
            evidence=[],
        )
        verdict = Verdict(
            decision=VerdictDecision.UNCERTAIN,
            score=3.0,
            findings=[finding],
            domains_detected=[
                DomainConfidence(domain=ArtifactDomain.CONCURRENCY, confidence=0.8)
            ],
            methods_executed=[MethodId("#153")],
            summary="UNCERTAIN verdict",
        )

        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(return_value=verdict)
            mock_engine_class.return_value = mock_engine

            result = await run_deep_verify_code_review(
                file_path=Path("src/worker.go"),
                code_content="go func() { ... }()",
                config=mock_config,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
            )

            # Verify result can be serialized
            from bmad_assist.deep_verify.core.types import serialize_validation_result

            data = serialize_validation_result(result)
            assert data["verdict"] == "UNCERTAIN"

    def test_critical_finding_detection(self, sample_finding):
        """Test logic for detecting CRITICAL findings."""
        # Create a result with CRITICAL finding
        result = DeepVerifyValidationResult(
            findings=[sample_finding],
            domains_detected=[],
            methods_executed=[],
            verdict=VerdictDecision.REJECT,
            score=8.5,
            duration_ms=1000,
            error=None,
        )

        has_critical = any(f.severity == Severity.CRITICAL for f in result.findings)
        assert has_critical is True

        # Test with non-critical result
        non_critical_finding = Finding(
            id="F2",
            severity=Severity.WARNING,
            title="Minor Issue",
            description="Minor issue",
            method_id=MethodId("#153"),
            evidence=[],
        )
        non_critical_result = DeepVerifyValidationResult(
            findings=[non_critical_finding],
            domains_detected=[],
            methods_executed=[],
            verdict=VerdictDecision.ACCEPT,
            score=-2.0,
            duration_ms=1000,
            error=None,
        )
        assert not any(f.severity == Severity.CRITICAL for f in non_critical_result.findings)


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Edge case tests for DV code review hook."""

    @pytest.mark.asyncio
    async def test_empty_code_content(self, mock_config, temp_project_path):
        """Test handling of empty code content."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(
                return_value=Verdict(
                    decision=VerdictDecision.ACCEPT,
                    score=0.0,
                    findings=[],
                    domains_detected=[],
                    methods_executed=[],
                    summary="Empty code",
                )
            )
            mock_engine_class.return_value = mock_engine

            result = await run_deep_verify_code_review(
                file_path=Path("src/empty.py"),
                code_content="",
                config=mock_config,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
            )

            assert result.verdict == VerdictDecision.ACCEPT
            assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_string_epic_and_story(self, mock_config, temp_project_path, sample_verdict):
        """Test with string epic_num and story_num."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(return_value=sample_verdict)
            mock_engine_class.return_value = mock_engine

            result = await run_deep_verify_code_review(
                file_path=Path("src/main.go"),
                code_content="package main",
                config=mock_config,
                project_path=temp_project_path,
                epic_num="testarch",
                story_num="A",
            )

            assert result is not None
            # Verify context was created with string values
            call_kwargs = mock_engine.verify.call_args[1]
            assert call_kwargs["context"].epic_num == "testarch"
            assert call_kwargs["context"].story_num == "A"

    def test_file_list_with_backticks(self, temp_project_path, mock_paths):
        """Test parsing File List with backtick formatting."""
        stories_dir = mock_paths.stories_dir
        stories_dir.mkdir(parents=True, exist_ok=True)
        story_file = stories_dir / "26-25-test.md"
        story_file.write_text("""# Story

## File List
- `src/file1.py` - File with backticks
- `src/file2.py` - Another file
""")

        # Create the files
        src_dir = temp_project_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "file1.py").write_text("# file1")
        (src_dir / "file2.py").write_text("# file2")

        with patch("bmad_assist.core.paths.get_paths") as mock_get_paths:
            mock_get_paths.return_value = mock_paths
            files = _resolve_code_files(temp_project_path, 26, 25)

        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_story_ref_in_context(self, mock_config, temp_project_path, sample_verdict):
        """Test that story_ref is passed to context."""
        with patch(
            "bmad_assist.deep_verify.integration.code_review_hook.DeepVerifyEngine"
        ) as mock_engine_class:
            mock_engine = MagicMock()
            mock_engine.verify = AsyncMock(return_value=sample_verdict)
            mock_engine_class.return_value = mock_engine

            await run_deep_verify_code_review(
                file_path=Path("src/main.py"),
                code_content="def test(): pass",
                config=mock_config,
                project_path=temp_project_path,
                epic_num=26,
                story_num=20,
                story_ref="26.20-custom",
            )

            call_kwargs = mock_engine.verify.call_args[1]
            assert call_kwargs["context"].story_ref == "26.20-custom"
