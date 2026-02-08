"""Deep Verify integration hook for code_review phase.

Story 26.20: Code Review Integration Hook

This module provides the integration point for Deep Verify into the
code_review phase, running DV verification on actual source code files
in parallel with Multi-LLM reviewers via asyncio.gather().

Key differences from validate_story hook:
- Analyzes actual source code files (not story specs)
- Uses language detection to filter patterns
- Discovers files from story's "## File List" section
- Supports multiple files per story

Example:
    >>> from pathlib import Path
    >>> from bmad_assist.deep_verify.integration.code_review_hook import (
    ...     run_deep_verify_code_review,
    ... )
    >>> result = await run_deep_verify_code_review(
    ...     file_path=Path("src/main.py"),
    ...     code_content="def authenticate_user(token): ...",
    ...     config=config,
    ...     project_path=Path("."),
    ...     epic_num=26,
    ...     story_num=20,
    ... )
    >>> print(result.verdict)
    VerdictDecision.REJECT

"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bmad_assist.core.exceptions import BmadAssistError, ProviderError, ProviderTimeoutError
from bmad_assist.core.types import EpicId
from bmad_assist.deep_verify.core.engine import DeepVerifyEngine, VerificationContext
from bmad_assist.deep_verify.core.language_detector import LanguageDetector
from bmad_assist.deep_verify.core.types import (
    DeepVerifyValidationResult,
    VerdictDecision,
    serialize_validation_result,
)

if TYPE_CHECKING:
    from bmad_assist.core.config import Config

logger = logging.getLogger(__name__)

# Maximum combined code size (100KB) to prevent timeout
MAX_CODE_SIZE_BYTES = 100 * 1024

# Module dependency patterns to filter from File List extraction
# These are Go/etc. module paths, not local project files
_MODULE_DEP_PATTERN = re.compile(
    r"^(?:github\.com|gopkg\.in|golang\.org|bitbucket\.org|gitlab\.com)/",
)


async def run_deep_verify_code_review(
    file_path: Path,
    code_content: str,
    config: Config,
    project_path: Path,
    epic_num: EpicId,
    story_num: int | str,
    story_ref: str | None = None,
    timeout: int | None = None,
) -> DeepVerifyValidationResult:
    """Run Deep Verify code review on a source file.

    This function is designed to be added to the asyncio.gather() call
    in the code review orchestrator. It:
    1. Detects the programming language from file path/content
    2. Creates a VerificationContext with language info
    3. Runs DeepVerifyEngine with code-specific patterns
    4. Returns findings with line numbers for code issues

    If Deep Verify is disabled in config, or if the engine fails,
    returns an empty ACCEPT result (non-blocking behavior).

    Args:
        file_path: Path to the source file being reviewed.
        code_content: The actual code content to analyze.
        config: Application configuration with deep_verify settings.
        project_path: Path to project root.
        epic_num: Epic number being reviewed (int or str like "testarch").
        story_num: Story number being reviewed (int or str).
        story_ref: Optional story reference string (e.g., "26.20").
        timeout: Optional timeout in seconds. If None, uses config default.

    Returns:
        DeepVerifyValidationResult with findings, domains, verdict, and score.
        Returns empty ACCEPT result if DV is disabled or fails.

    Example:
        >>> result = await run_deep_verify_code_review(
        ...     file_path=Path("src/auth.py"),
        ...     code_content="def login(user, pwd): ...",
        ...     config=config,
        ...     project_path=Path("."),
        ...     epic_num=26,
        ...     story_num=20,
        ...     story_ref="26.20",
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
        logger.info(
            "Starting Deep Verify code review for %s (story %s.%s)",
            file_path,
            epic_num,
            story_num,
        )

        # Track duration
        start_time = time.perf_counter()

        # Detect language from file path and content
        detector = LanguageDetector()
        lang_info = detector.detect(file_path, code_content)

        if lang_info.is_unknown:
            logger.warning(
                "Could not detect language for %s, using spec patterns only",
                file_path,
            )
            detected_language = None
        else:
            detected_language = lang_info.language
            logger.debug(
                "Detected language: %s (confidence: %.2f, method: %s)",
                lang_info.language,
                lang_info.confidence,
                lang_info.detection_method,
            )

        # Create verification context with language info
        context = VerificationContext(
            file_path=file_path,
            language=detected_language,
            story_ref=story_ref or f"{epic_num}.{story_num}",
            epic_num=epic_num,
            story_num=story_num,
        )

        # Get helper provider config for fallback (when deep_verify.provider not set)
        helper_provider_config = getattr(config.providers, "helper", None)

        # Create engine and run verification
        engine = DeepVerifyEngine(
            project_root=project_path,
            config=dv_config,
            helper_provider_config=helper_provider_config,
        )

        verdict = await engine.verify(
            artifact_text=code_content,
            context=context,
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
            "Deep Verify code review complete for %s: verdict=%s, score=%.1f, findings=%d",
            file_path.name,
            result.verdict.value,
            result.score,
            len(result.findings),
        )

        return result

    except (ProviderError, ProviderTimeoutError, BmadAssistError) as e:
        # Expected provider/config errors - non-blocking
        logger.warning(
            "Deep Verify code review failed (non-blocking) for %s: %s",
            file_path,
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
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as e:
        # Unexpected errors - log with exc_info for debugging but still non-blocking
        # Specific exception types per project anti-patterns (avoid bare Exception)
        logger.warning(
            "Deep Verify unexpected error (non-blocking) for %s: %s",
            file_path,
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


def _resolve_code_files(
    project_path: Path,
    epic_num: EpicId,
    story_num: int | str,
) -> list[tuple[Path, str]]:
    """Resolve code files from story File List.

    Parses the story markdown file to extract file paths from the
    "## File List" section, validates them, and detects language for each.

    Args:
        project_path: Project root path.
        epic_num: Epic number.
        story_num: Story number.

    Returns:
        List of (file_path, language) tuples. Language is detected from
        file extension. Files that don't exist or fail validation are skipped.

    """
    # Find story file
    story_file = _find_story_file(project_path, epic_num, story_num)
    if story_file is None:
        logger.info(
            "No story file found for %s.%s, skipping file discovery",
            epic_num,
            story_num,
        )
        return []

    # Read story content
    try:
        content = story_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read story file %s: %s", story_file, e)
        return []

    # Extract File List section (stop at any ## or ### header)
    file_list_match = re.search(
        r"##\s*File\s*List.*?(?=\n#{2,}\s|$)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if not file_list_match:
        logger.info(
            "No '## File List' section found in %s, skipping DV code review",
            story_file.name,
        )
        return []

    # Extract file paths from bullet points
    # Primary: backtick-wrapped paths (e.g., "- `cmd/relay/main.go` (new) - description")
    # Fallback: first path-like token without backticks (e.g., "- src/main.py - description")
    file_list_content = file_list_match.group()
    file_paths = re.findall(r"[-*]\s+`([^`\n]+)`", file_list_content, re.MULTILINE)
    if not file_paths:
        # Fallback: extract first token that looks like a file path
        file_paths = re.findall(
            r"[-*]\s+(\S+\.\w+)", file_list_content, re.MULTILINE
        )

    # Filter out module dependencies (e.g., "github.com/go-chi/chi/v5 v5.2.5")
    # and markdown artifacts (e.g., "*Status: ready-for-dev*")
    file_paths = [
        p for p in file_paths
        if not _MODULE_DEP_PATTERN.match(p.strip())
        and not p.strip().startswith("*")
        and ("/" in p or "." in p)
    ]

    if not file_paths:
        logger.info(
            "No files found in '## File List' section of %s",
            story_file.name,
        )
        return []

    # Resolve and validate each file
    resolved: list[tuple[Path, str]] = []
    total_size = 0
    detector = LanguageDetector()

    # Project root for relative path resolution
    project_root = project_path.resolve()

    for file_path_str in file_paths:
        file_path_str = file_path_str.strip()
        if not file_path_str:
            continue

        # Resolve relative to project root (not story directory)
        # File paths in stories are typically relative to project root
        path = project_path / file_path_str

        try:
            # Normalize and resolve the path
            path = path.resolve()
        except OSError as e:
            logger.warning("Failed to resolve path %s: %s", file_path_str, e)
            continue

        # Path traversal prevention: ensure path is within project
        try:
            path.relative_to(project_root)
        except ValueError:
            logger.warning(
                "Path traversal attempt blocked: %s is outside project directory",
                file_path_str,
            )
            continue

        # Check file exists and is a file
        if not path.exists():
            logger.warning("File not found, skipping DV: %s", path)
            continue

        if not path.is_file():
            logger.debug("Skipping non-file path: %s", path)
            continue

        # Check size limit
        try:
            size = path.stat().st_size
        except OSError:
            continue

        if total_size + size > MAX_CODE_SIZE_BYTES:
            logger.warning(
                "Code size limit (%d KB) exceeded, skipping remaining files",
                MAX_CODE_SIZE_BYTES // 1024,
            )
            break

        total_size += size

        # Detect language
        lang_info = detector.detect(path)
        language = lang_info.language if not lang_info.is_unknown else "unknown"

        resolved.append((path, language))

    logger.debug(
        "Resolved %d code files from story %s.%s (total size: %.1f KB)",
        len(resolved),
        epic_num,
        story_num,
        total_size / 1024,
    )

    return resolved


def _find_story_file(
    _project_path: Path,
    epic_num: EpicId,
    story_num: int | str,
) -> Path | None:
    """Find story file by epic and story numbers.

    Args:
        _project_path: Project root path (unused, kept for backward compat).
        epic_num: Epic number.
        story_num: Story number.

    Returns:
        Path to story file if found, None otherwise.

    """
    from bmad_assist.core.paths import get_paths

    try:
        stories_dir = get_paths().stories_dir
    except RuntimeError:
        logger.warning("Paths not initialized, cannot find story file")
        return None

    if not stories_dir.exists():
        return None

    # Pattern: {epic}-{story}-*.md
    pattern = f"{epic_num}-{story_num}-*.md"
    matches = sorted(stories_dir.glob(pattern))  # Sorted for deterministic behavior

    if matches:
        return matches[0]

    return None


def save_dv_findings_for_synthesis(
    result: DeepVerifyValidationResult,
    project_path: Path,
    session_id: str,
    file_path: Path | None = None,
    language: str | None = None,
) -> Path:
    """Save DV findings to cache for synthesis phase retrieval.

    Saves DV results to .bmad-assist/cache/deep-verify-{session_id}.json
    so the code_review_synthesis handler can load and include them in
    the synthesis prompt.

    Args:
        result: DeepVerifyValidationResult to save.
        project_path: Project root directory.
        session_id: Session ID for correlation with code reviews.
        file_path: Optional path to the reviewed file.
        language: Optional detected language.

    Returns:
        Path to saved cache file.

    Raises:
        OSError: If write fails.

    """
    from bmad_assist.core.io import atomic_write

    cache_dir = project_path / ".bmad-assist" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Use unique filename per file to prevent overwrite in multi-file stories
    file_id = hashlib.md5(str(file_path).encode()).hexdigest()[:8] if file_path else "global"
    filename = f"deep-verify-{session_id}-{file_id}.json"
    file_path_cache = cache_dir / filename

    # Serialize result with additional metadata
    data = serialize_validation_result(result)
    data["session_id"] = session_id
    data["file_path"] = str(file_path) if file_path else None
    data["language"] = language

    content = json.dumps(data, indent=2)
    atomic_write(file_path_cache, content)
    logger.info("Saved DV findings for synthesis: %s", file_path_cache)
    return file_path_cache


def load_dv_findings_from_cache(
    session_id: str,
    project_path: Path,
    file_path: Path | None = None,
) -> DeepVerifyValidationResult | None:
    """Load DV findings from cache by session ID.

    For multi-file stories, provide file_path to load specific file's results.
    If file_path is None, aggregates all DV findings for the session.

    Args:
        session_id: Session ID from save_dv_findings_for_synthesis.
        project_path: Project root directory.
        file_path: Optional specific file path to load results for.

    Returns:
        DeepVerifyValidationResult if found and valid, None otherwise.

    """
    cache_dir = project_path / ".bmad-assist" / "cache"

    if file_path is not None:
        # Load specific file's results
        file_id = hashlib.md5(str(file_path).encode()).hexdigest()[:8]
        cache_file_path = cache_dir / f"deep-verify-{session_id}-{file_id}.json"
        return _load_single_dv_cache(cache_file_path)

    # No file_path - aggregate all DV findings for this session
    pattern = f"deep-verify-{session_id}-*.json"
    cache_files = list(cache_dir.glob(pattern)) if cache_dir.exists() else []

    if not cache_files:
        logger.debug("DV findings cache not found for session: %s", session_id)
        return None

    # Load and aggregate all findings
    all_findings: list[Any] = []
    all_domains: list[Any] = []
    all_methods: set[str] = set()
    total_duration = 0
    worst_verdict = None
    min_score = 100.0

    from bmad_assist.deep_verify.core.types import (
        DeepVerifyValidationResult,
        VerdictDecision,
    )

    for cache_file in cache_files:
        result = _load_single_dv_cache(cache_file)
        if result is None:
            continue

        all_findings.extend(result.findings)
        all_domains.extend(result.domains_detected)
        all_methods.update(result.methods_executed)
        total_duration += result.duration_ms
        min_score = min(min_score, result.score)

        # Track worst verdict (REJECT > UNCERTAIN > ACCEPT)
        if worst_verdict is None:
            worst_verdict = result.verdict
        elif result.verdict == VerdictDecision.REJECT:
            worst_verdict = VerdictDecision.REJECT
        elif result.verdict == VerdictDecision.UNCERTAIN and worst_verdict == VerdictDecision.ACCEPT:
            worst_verdict = VerdictDecision.UNCERTAIN

    if not all_findings and worst_verdict is None:
        logger.debug("No valid DV findings loaded for session: %s", session_id)
        return None

    logger.debug(
        "Aggregated DV findings from %d files: %d findings, verdict=%s",
        len(cache_files),
        len(all_findings),
        worst_verdict.value if worst_verdict else "none",
    )

    from bmad_assist.deep_verify.core.types import MethodId

    return DeepVerifyValidationResult(
        verdict=worst_verdict or VerdictDecision.ACCEPT,
        score=min_score,
        findings=all_findings,
        domains_detected=all_domains,
        methods_executed=[MethodId(m) for m in all_methods],
        duration_ms=total_duration,
        error=None,
    )


def _load_single_dv_cache(cache_file_path: Path) -> DeepVerifyValidationResult | None:
    """Load a single DV cache file.

    Args:
        cache_file_path: Path to the cache file.

    Returns:
        DeepVerifyValidationResult if valid, None otherwise.

    """
    if not cache_file_path.exists():
        logger.debug("DV findings cache not found: %s", cache_file_path)
        return None

    try:
        with open(cache_file_path, encoding="utf-8") as f:
            data = json.load(f)

        # Remove metadata fields before deserialization
        data.pop("session_id", None)
        data.pop("file_path", None)
        data.pop("language", None)

        from bmad_assist.deep_verify.core.types import deserialize_validation_result

        return deserialize_validation_result(data)
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning("Failed to load DV findings from cache %s: %s", cache_file_path, e)
        return None
