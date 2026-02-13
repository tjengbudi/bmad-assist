"""Project context resolution for BMAD workflow variables.

This module handles project_context.md file detection and resolution:
- Dual naming convention support (project_context.md vs project-context.md)
- Symlink detection
- Token count estimation

Dependencies flow: project_context.py has no dependencies on other variables/ modules.
"""

import logging
from pathlib import Path
from typing import Any

from bmad_assist.compiler.types import CompilerContext
from bmad_assist.core.exceptions import VariableError

logger = logging.getLogger(__name__)

# Default project context path when file not found
DEFAULT_PROJECT_CONTEXT = "{project-root}/docs/project-context.md"

__all__ = [
    "_resolve_project_context",
    "_estimate_tokens",
]


def _resolve_project_context(
    resolved: dict[str, Any],
    context: CompilerContext,
) -> dict[str, Any]:
    """Resolve project_context variable to file path with token estimate.

    Searches for project context file in output_folder (docs/):
    - Checks both project_context.md and project-context.md
    - If one is symlink to other: use project-context.md
    - If both exist with same size (copy): use project-context.md
    - If both exist with different sizes: raise VariableError (ambiguous)
    - If only one exists: use that one
    - If neither exists: set to "none"

    Creates attributed variable with:
    - _value: file path (or "none" if not found)
    - _token_approx: estimated token count (only if file found)

    Args:
        resolved: Dict of resolved variables.
        context: Compiler context.

    Returns:
        Dict with project_context resolved.

    Raises:
        VariableError: If both files exist with different sizes.

    """
    # Get output_folder (docs directory)
    output_folder_str = resolved.get("output_folder")
    if not output_folder_str or not isinstance(output_folder_str, str):
        # No output_folder - use default path
        resolved["project_context"] = {"_value": DEFAULT_PROJECT_CONTEXT}
        logger.debug("No output_folder, using default project_context path")
        return resolved

    output_folder = Path(output_folder_str)
    if not output_folder.exists():
        # output_folder doesn't exist - use default path
        resolved["project_context"] = {"_value": DEFAULT_PROJECT_CONTEXT}
        logger.debug("output_folder does not exist, using default project_context path")
        return resolved

    # Check both naming conventions
    underscore_path = output_folder / "project_context.md"
    hyphen_path = output_folder / "project-context.md"

    underscore_exists = underscore_path.exists()
    hyphen_exists = hyphen_path.exists()

    selected_path: Path | None = None

    if underscore_exists and hyphen_exists:
        # Both exist - check if one is symlink to other
        underscore_is_link = underscore_path.is_symlink()
        hyphen_is_link = hyphen_path.is_symlink()

        if underscore_is_link or hyphen_is_link:
            # One is symlink - use project-context.md (preferred)
            selected_path = hyphen_path
            logger.debug("project_context: symlink detected, using project-context.md")
        else:
            # Both are real files - check sizes
            underscore_size = underscore_path.stat().st_size
            hyphen_size = hyphen_path.stat().st_size

            if underscore_size == hyphen_size:
                # Same size (likely copy) - use project-context.md
                selected_path = hyphen_path
                logger.debug("project_context: same size files, using project-context.md")
            else:
                # Different sizes - ambiguous, raise error
                raise VariableError(
                    "Ambiguous project context files\n"
                    f"  Found: {underscore_path} ({underscore_size} bytes)\n"
                    f"  Also: {hyphen_path} ({hyphen_size} bytes)\n"
                    "  Why it's a problem: Files have different sizes, "
                    "cannot determine which to use\n"
                    "  How to fix: Keep only one file, or make one a symlink to the other",
                    variable_name="project_context",
                    sources_checked=[str(underscore_path), str(hyphen_path)],
                    suggestion="Keep only one file, or make one a symlink to the other",
                )
    elif hyphen_exists:
        selected_path = hyphen_path
        logger.debug("project_context: found project-context.md")
    elif underscore_exists:
        selected_path = underscore_path
        logger.debug("project_context: found project_context.md")
    else:
        # Neither exists in output_folder - try fallback to docs/
        docs_context = context.project_root / "docs" / "project-context.md"
        if docs_context.exists():
            selected_path = docs_context
            logger.debug("project_context: found in docs/ fallback")
        else:
            # Use default path (will be resolved later)
            resolved["project_context"] = {"_value": DEFAULT_PROJECT_CONTEXT}
            logger.debug("No project context file found, using default path: %s", DEFAULT_PROJECT_CONTEXT)
            return resolved

    # Create attributed variable with token estimate
    token_estimate = _estimate_tokens(selected_path)
    var_attrs: dict[str, Any] = {
        "_value": str(selected_path),
    }
    if token_estimate is not None:
        var_attrs["_token_approx"] = str(token_estimate)

    resolved["project_context"] = var_attrs
    logger.debug("Resolved project_context -> %s", selected_path)

    return resolved


def _estimate_tokens(file_path: Path) -> int | None:
    """Estimate token count for a file.

    Uses chars / 4 approximation for English text.

    Args:
        file_path: Path to the file.

    Returns:
        Estimated token count, or None if file cannot be read.

    """
    try:
        content = file_path.read_text(encoding="utf-8")
        return len(content) // 4
    except OSError as e:
        logger.debug("Cannot read file for token estimate: %s - %s", file_path, e)
        return None
