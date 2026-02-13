"""LLM-based tech stack detection from project documentation.

Reads architecture.md and project-context.md, sends them to the helper
LLM, and parses the response into a list of stack identifiers.

Falls back to marker-based detection (security/tech_stack.py) if the
helper provider is unavailable or the LLM call fails.

Results are cached per project path for the lifetime of the process.

Usage:
    >>> from bmad_assist.deep_verify.stack_detector import detect_project_stacks
    >>> stacks = detect_project_stacks(Path("/my/project"), config)
    >>> print(stacks)  # ["javascript", "python"]

"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bmad_assist.core.config.models.main import Config

logger = logging.getLogger(__name__)

# Valid stack identifiers (must match exclusions.yaml sections)
VALID_STACKS = frozenset({
    "python", "javascript", "go", "java", "rust",
    "ruby", "csharp", "cpp", "swift",
})

# Docs to read (in priority order)
_DOC_CANDIDATES = (
    "docs/architecture.md",
    "docs/project-context.md",
    "docs/prd.md",
)

# Max chars to send to LLM (keep prompt small for fast model)
_MAX_DOC_CHARS = 12000


def detect_project_stacks(
    project_path: Path,
    config: Config | None = None,
) -> list[str]:
    """Detect tech stacks for a project, with LLM + marker fallback.

    Strategy:
      1. Try LLM-based detection from project docs (if helper configured)
      2. Fall back to marker-based detection (file presence heuristic)

    Args:
        project_path: Project root directory.
        config: Application config (needed for helper provider). If None,
            skips LLM detection and goes straight to markers.

    Returns:
        Sorted list of stack identifiers (e.g. ["javascript", "python"]).

    """
    # Check cache first
    cache_key = str(project_path.resolve())
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    stacks: list[str] = []

    # 1. Try LLM-based detection
    if config is not None:
        stacks = _detect_via_llm(project_path, config)

    # 2. Fall back to marker-based detection
    if not stacks:
        stacks = _detect_via_markers(project_path)

    # Cache and return
    _set_cached(cache_key, stacks)

    if stacks:
        logger.info("Detected project stacks: %s", stacks)
    else:
        logger.warning("No tech stacks detected for %s", project_path)

    return stacks


# ---------------------------------------------------------------------------
# LLM detection
# ---------------------------------------------------------------------------


def _detect_via_llm(
    project_path: Path,
    config: Config,
) -> list[str]:
    """Detect stacks by sending project docs to helper LLM."""
    # Check helper provider is configured
    helper_config = getattr(config.providers, "helper", None)
    if helper_config is None:
        logger.debug("No helper provider configured, skipping LLM detection")
        return []

    # Collect project documentation
    docs_text = _collect_project_docs(project_path)
    if not docs_text:
        logger.debug("No project docs found for LLM stack detection")
        return []

    # Load prompt template
    try:
        from bmad_assist.deep_verify.prompts import get_detect_tech_stack_prompt

        template = get_detect_tech_stack_prompt()
    except (ImportError, FileNotFoundError) as e:
        logger.debug("Cannot load stack detection prompt: %s", e)
        return []

    prompt = template.format(project_docs=docs_text)

    # Invoke helper
    try:
        from bmad_assist.providers import get_provider

        provider = get_provider(helper_config.provider)
        result = provider.invoke(
            prompt,
            model=helper_config.model,
            settings_file=helper_config.settings_path,
            timeout=30,
            disable_tools=True,
        )
    except Exception as e:
        logger.warning("LLM stack detection failed: %s", e)
        return []

    if not result.stdout:
        return []

    return _parse_llm_response(result.stdout.strip())


def _collect_project_docs(project_path: Path) -> str:
    """Read project documentation files, truncated to budget."""
    parts: list[str] = []
    total = 0

    for rel_path in _DOC_CANDIDATES:
        doc_path = project_path / rel_path
        if not doc_path.is_file():
            continue

        try:
            content = doc_path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Budget control
        remaining = _MAX_DOC_CHARS - total
        if remaining <= 0:
            break

        if len(content) > remaining:
            content = content[:remaining] + "\n[... truncated ...]"

        parts.append(f"### {rel_path}\n{content}")
        total += len(content)

    return "\n\n".join(parts)


def _parse_llm_response(text: str) -> list[str]:
    """Parse comma-separated stack identifiers from LLM output.

    Handles various formats:
      - "python, javascript"
      - "python,javascript"
      - Lines with extra text (extracts known identifiers)

    """
    # Strip markdown code fences if present
    text = re.sub(r"```\w*\n?", "", text).strip()

    # Extract all known stack identifiers from the text
    found: list[str] = []
    seen: set[str] = set()

    for token in re.split(r"[,\s\n]+", text.lower()):
        token = token.strip().strip("\"'`")
        if token in VALID_STACKS and token not in seen:
            found.append(token)
            seen.add(token)

    return sorted(found)


# ---------------------------------------------------------------------------
# Marker-based fallback
# ---------------------------------------------------------------------------


def _detect_via_markers(project_path: Path) -> list[str]:
    """Fall back to security/tech_stack.py marker detection."""
    try:
        from bmad_assist.security.tech_stack import detect_tech_stack

        return detect_tech_stack(project_path)
    except (ImportError, RuntimeError) as e:
        logger.debug("Marker-based detection unavailable: %s", e)
        return []


# ---------------------------------------------------------------------------
# Simple process-level cache
# ---------------------------------------------------------------------------

_cache: dict[str, list[str]] = {}


def _get_cached(key: str) -> list[str] | None:
    """Get cached result (None if not cached)."""
    return _cache.get(key)


def _set_cached(key: str, value: list[str]) -> None:
    """Store result in cache."""
    _cache[key] = value


def clear_cache() -> None:
    """Clear the stack detection cache (for testing)."""
    _cache.clear()
