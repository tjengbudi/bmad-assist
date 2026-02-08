"""Deep Verify integration hook for validate_story phase.

Story 26.16: Validate Story Integration Hook

This module provides the integration point for Deep Verify into the
validate_story phase, running DV verification in parallel with Multi-LLM
validators via asyncio.gather().

The hook now loads the actual story file (not compiled prompt) for DV analysis,
with optional additional context documents (PRD, architecture, project-context).

Example:
    >>> from bmad_assist.deep_verify.integration.validate_story_hook import (
    ...     run_deep_verify_validation,
    ... )
    >>> result = await run_deep_verify_validation(
    ...     artifact_text=None,  # Hook loads story file automatically
    ...     config=config,
    ...     project_path=Path("."),
    ...     epic_num=26,
    ...     story_num=16,
    ... )
    >>> print(result.verdict)
    VerdictDecision.REJECT

"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from bmad_assist.core.exceptions import BmadAssistError, ProviderError, ProviderTimeoutError
from bmad_assist.core.types import EpicId
from bmad_assist.deep_verify.config import DeepVerifyContextConfig
from bmad_assist.deep_verify.core.engine import DeepVerifyEngine
from bmad_assist.deep_verify.core.types import (
    DeepVerifyValidationResult,
    VerdictDecision,
)

if TYPE_CHECKING:
    from bmad_assist.core.config import Config

logger = logging.getLogger(__name__)


def _load_story_artifact(
    epic_num: EpicId,
    story_num: int | str,
) -> str | None:
    """Load story file content for DV analysis.

    Args:
        epic_num: Epic number (int or str like "testarch").
        story_num: Story number (int or str).

    Returns:
        Story file content as string, or None if not found/error.

    """
    from bmad_assist.core.paths import get_paths

    try:
        stories_dir = get_paths().stories_dir
    except RuntimeError:
        logger.warning("Paths not initialized, cannot load story artifact")
        return None

    pattern = f"{epic_num}-{story_num}-*.md"
    logger.debug("Loading story artifact: pattern=%s in %s", pattern, stories_dir)

    matches = sorted(stories_dir.glob(pattern))  # Sorted for deterministic behavior
    if not matches:
        logger.warning("No story file found for %s-%s", epic_num, story_num)
        return None

    story_file = matches[0]
    logger.debug("Found %d matches, using: %s", len(matches), story_file)

    try:
        content = story_file.read_text(encoding="utf-8")
        logger.debug("Loaded story file: %d bytes", len(content))
        return content
    except UnicodeDecodeError as e:
        logger.warning("Encoding error reading %s: %s, trying with replacement", story_file, e)
        return story_file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("Error reading story file %s: %s", story_file, e)
        return None


def _load_context_documents(
    config: DeepVerifyContextConfig,
) -> str:
    """Load additional context documents based on config.

    Args:
        config: DeepVerifyContextConfig with include flags.

    Returns:
        Concatenated context documents with delimiters, or empty string.

    """
    from bmad_assist.core.paths import get_paths

    context_parts: list[str] = []
    total_size = 0

    try:
        paths = get_paths()
    except RuntimeError:
        logger.warning("Paths not initialized, skipping context documents")
        return ""

    # Map config flags to paths and names
    # Note: project_context_file uses underscore (project_context.md), but we
    # also check the hyphenated version (project-context.md) for compatibility
    doc_mappings: list[tuple[bool, Path, str]] = [
        (config.include_prd, paths.prd_file, "PRD"),
        (config.include_architecture, paths.architecture_file, "Architecture"),
    ]

    # Handle project-context with fallback to both naming conventions
    if config.include_project_context:
        # Primary: project_context.md (as defined in paths.py)
        pc_path = paths.project_context_file
        # Fallback: project-context.md (common convention)
        pc_path_alt = paths.project_knowledge / "project-context.md"
        if pc_path.exists():
            doc_mappings.append((True, pc_path, "Project Context"))
        elif pc_path_alt.exists():
            doc_mappings.append((True, pc_path_alt, "Project Context"))
        else:
            logger.warning(
                "Project context not found at %s or %s", pc_path, pc_path_alt
            )

    for include, doc_path, doc_name in doc_mappings:
        if not include:
            continue
        if not doc_path.exists():
            logger.warning("Context document not found: %s at %s", doc_name, doc_path)
            continue
        try:
            content = doc_path.read_text(encoding="utf-8")
            doc_size = len(content.encode("utf-8"))
            if total_size + doc_size > config.max_context_size:
                logger.warning(
                    "Context size limit exceeded, skipping %s (%d bytes would exceed %d limit)",
                    doc_name,
                    doc_size,
                    config.max_context_size,
                )
                continue
            total_size += doc_size
            context_parts.append(f"<!-- DV Context: {doc_name} -->\n{content}\n")
            logger.debug("Added context document: %s (%d bytes)", doc_name, doc_size)
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Error loading context document %s: %s", doc_name, e)
            continue

    if total_size > config.max_context_size * 0.8:
        logger.warning(
            "Context documents approaching size limit: %d / %d bytes",
            total_size,
            config.max_context_size,
        )

    return "\n".join(context_parts)


async def run_deep_verify_validation(
    artifact_text: str | None,
    config: Config,
    project_path: Path,
    epic_num: EpicId,
    story_num: int | str,
    timeout: int | None = None,
) -> DeepVerifyValidationResult:
    """Run Deep Verify validation parallel to Multi-LLM validators.

    This function is designed to be added to the asyncio.gather() call
    in the validation orchestrator. It loads the story file and runs DV
    verification on it.

    Artifact text handling:
    - If artifact_text is provided and non-empty: use it (backward compat)
    - Otherwise: load story file internally using epic_num/story_num
    - If both are None/empty: return ACCEPT with warning (non-blocking)

    Optional context documents (PRD, architecture, project-context) can be
    included via deep_verify.context config section.

    If Deep Verify is disabled in config, or if the engine fails,
    returns an empty ACCEPT result (non-blocking behavior).

    Args:
        artifact_text: Optional artifact text for backward compatibility.
            If None or empty, hook loads story file automatically.
        config: Application configuration with deep_verify settings.
        project_path: Path to project root (used for fallback only).
        epic_num: Epic number being validated (int or str like "testarch").
        story_num: Story number being validated (int or str).
        timeout: Optional timeout in seconds. If None, uses config default.

    Returns:
        DeepVerifyValidationResult with findings, domains, verdict, and score.
        Returns empty ACCEPT result if DV is disabled or fails.

    Example:
        >>> result = await run_deep_verify_validation(
        ...     artifact_text=None,  # Hook loads story file
        ...     config=config,
        ...     project_path=Path("."),
        ...     epic_num=26,
        ...     story_num=16,
        ...     timeout=60,
        ... )
        >>> print(f"DV Verdict: {result.verdict.value}")
        >>> print(f"Findings: {len(result.findings)}")

    """
    # Check if DV is enabled in config
    dv_config = getattr(config, "deep_verify", None)
    if dv_config is None:
        logger.debug("Deep Verify config not present, skipping")
        return DeepVerifyValidationResult(
            findings=[],
            domains_detected=[],
            methods_executed=[],
            verdict=VerdictDecision.ACCEPT,
            score=0.0,
            duration_ms=0,
            error=None,
        )

    if not dv_config.enabled:
        logger.debug("Deep Verify disabled in config")
        return DeepVerifyValidationResult(
            findings=[],
            domains_detected=[],
            methods_executed=[],
            verdict=VerdictDecision.ACCEPT,
            score=0.0,
            duration_ms=0,
            error=None,
        )

    try:
        logger.info("Starting Deep Verify validation for story %s.%s", epic_num, story_num)

        # Track duration
        start_time = time.perf_counter()

        # Determine artifact to analyze
        # Precedence: explicit artifact_text > auto-loaded story file
        if artifact_text:
            logger.debug("Using provided artifact_text (%d chars)", len(artifact_text))
            final_artifact = artifact_text
        else:
            # Load story file
            story_content = _load_story_artifact(epic_num, story_num)
            if story_content is None:
                logger.warning(
                    "No artifact to analyze for story %s.%s (no artifact_text, story file not found)",
                    epic_num,
                    story_num,
                )
                return DeepVerifyValidationResult(
                    findings=[],
                    domains_detected=[],
                    methods_executed=[],
                    verdict=VerdictDecision.ACCEPT,
                    score=0.0,
                    duration_ms=0,
                    error="No artifact to analyze - story file not found",
                )

            # Load optional context documents
            context_config = getattr(dv_config, "context", None)
            if context_config is not None:
                context_docs = _load_context_documents(context_config)
                if context_docs:
                    final_artifact = context_docs + "\n\n" + story_content
                    logger.debug(
                        "Combined artifact: %d context + %d story = %d chars",
                        len(context_docs),
                        len(story_content),
                        len(final_artifact),
                    )
                else:
                    final_artifact = story_content
            else:
                final_artifact = story_content

            logger.debug("Using story file artifact (%d chars)", len(final_artifact))

        # Get helper provider config for fallback (when deep_verify.provider not set)
        helper_provider_config = getattr(config.providers, "helper", None)

        # Create engine and run verification
        engine = DeepVerifyEngine(
            project_root=project_path,
            config=dv_config,
            helper_provider_config=helper_provider_config,
        )

        verdict = await engine.verify(
            artifact_text=final_artifact,
            timeout=timeout,
        )

        # Calculate duration
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        # Convert Verdict to DeepVerifyValidationResult
        result = DeepVerifyValidationResult(
            findings=verdict.findings,
            domains_detected=verdict.domains_detected,
            methods_executed=verdict.methods_executed,
            verdict=verdict.decision,
            score=verdict.score,
            duration_ms=duration_ms,
            error=None,
        )

        logger.info(
            "Deep Verify validation complete: verdict=%s, score=%.1f, findings=%d",
            result.verdict.value,
            result.score,
            len(result.findings),
        )

        return result

    except (ProviderError, ProviderTimeoutError, BmadAssistError) as e:
        # Expected provider/config errors - non-blocking
        logger.warning(
            "Deep Verify validation failed (non-blocking) for story %s.%s: %s",
            epic_num,
            story_num,
            type(e).__name__,
        )
        return DeepVerifyValidationResult(
            findings=[],
            domains_detected=[],
            methods_executed=[],
            verdict=VerdictDecision.ACCEPT,
            score=0.0,
            duration_ms=0,
            error=f"{type(e).__name__}: {e}",
        )
    except Exception as e:
        # Unexpected errors - log with exc_info for debugging but still non-blocking
        logger.warning(
            "Deep Verify unexpected error (non-blocking) for story %s.%s: %s",
            epic_num,
            story_num,
            type(e).__name__,
            exc_info=True,
        )
        return DeepVerifyValidationResult(
            findings=[],
            domains_detected=[],
            methods_executed=[],
            verdict=VerdictDecision.ACCEPT,
            score=0.0,
            duration_ms=0,
            error=f"{type(e).__name__}: {e}",
        )
