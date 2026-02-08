"""Dual tech-stack detection for security pattern selection.

Primary: detect languages from diff file extensions.
Secondary: detect from project marker files.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Extension → language mapping
EXTENSION_MAP: dict[str, str] = {
    ".go": "go",
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".java": "java",
    ".kt": "java",
    ".kts": "java",
    ".rb": "ruby",
    ".erb": "ruby",
    ".cs": "csharp",
    ".rs": "rust",
    ".swift": "swift",
    ".c": "cpp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
}

# Project marker files → language mapping
# NOTE: Makefile intentionally excluded (too many false positives)
MARKER_MAP: dict[str, str] = {
    "go.mod": "go",
    "go.sum": "go",
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "requirements.txt": "python",
    "Pipfile": "python",
    "package.json": "javascript",
    "tsconfig.json": "javascript",
    "yarn.lock": "javascript",
    "pnpm-lock.yaml": "javascript",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "Gemfile": "ruby",
    "*.csproj": "csharp",
    "*.sln": "csharp",
    "Cargo.toml": "rust",
    "Package.swift": "swift",
    "CMakeLists.txt": "cpp",
}

# Pattern for extracting file paths from unified diff headers
_DIFF_FILE_PATTERN = re.compile(r"^(?:\+\+\+|---)\s+[ab]/(.+)$", re.MULTILINE)


def detect_tech_stack(
    project_path: Path,
    diff_content: str | None = None,
) -> list[str]:
    """Detect project tech stack using dual strategy.

    Primary: extract unique extensions from diff content.
    Secondary: scan project root for marker files.

    Args:
        project_path: Project root directory.
        diff_content: Optional git diff content for primary detection.

    Returns:
        Sorted list of detected language identifiers (e.g., ["go", "python"]).
        Empty list if nothing detected (warning logged).

    """
    languages: set[str] = set()

    # Primary: detect from diff file extensions
    if diff_content:
        diff_languages = _detect_from_diff(diff_content)
        languages.update(diff_languages)
        if diff_languages:
            logger.debug("Diff-based detection: %s", sorted(diff_languages))

    # Secondary: detect from project marker files
    marker_languages = _detect_from_markers(project_path)
    languages.update(marker_languages)
    if marker_languages:
        logger.debug("Marker-based detection: %s", sorted(marker_languages))

    if not languages:
        logger.warning(
            "No tech stack detected for %s — only core security patterns will be loaded",
            project_path,
        )

    result = sorted(languages)
    logger.info("Detected tech stack: %s", result if result else "(none)")
    return result


def _detect_from_diff(diff_content: str) -> set[str]:
    """Extract languages from diff file extensions.

    Args:
        diff_content: Unified diff content.

    Returns:
        Set of detected language identifiers.

    """
    languages: set[str] = set()

    for match in _DIFF_FILE_PATTERN.finditer(diff_content):
        file_path = match.group(1)
        ext = _get_extension(file_path)
        if ext in EXTENSION_MAP:
            languages.add(EXTENSION_MAP[ext])

    return languages


def _detect_from_markers(project_path: Path) -> set[str]:
    """Detect languages from project marker files.

    Args:
        project_path: Project root directory.

    Returns:
        Set of detected language identifiers.

    """
    languages: set[str] = set()

    if not project_path.is_dir():
        return languages

    for marker, language in MARKER_MAP.items():
        if "*" in marker:
            # Glob pattern (e.g., "*.csproj")
            if list(project_path.glob(marker)):
                languages.add(language)
        else:
            if (project_path / marker).exists():
                languages.add(language)

    return languages


def _get_extension(file_path: str) -> str:
    """Get file extension (lowercase, with dot).

    Args:
        file_path: File path string.

    Returns:
        Extension including dot (e.g., ".go"), or empty string.

    """
    from pathlib import PurePosixPath

    suffix = PurePosixPath(file_path).suffix
    return suffix.lower() if suffix else ""
