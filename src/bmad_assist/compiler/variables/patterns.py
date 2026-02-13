"""Input file pattern resolution for BMAD workflow variables.

This module handles input_file_patterns processing:
- Resolving sharded vs whole document patterns
- Finding epic files in sharded directories
- Token estimation for resolved files

Dependencies flow: patterns.py imports _estimate_tokens from project_context.py.
"""

import glob as glob_module
import logging
from pathlib import Path
from typing import Any

from bmad_assist.compiler.types import CompilerContext
from bmad_assist.compiler.variables.project_context import _estimate_tokens

logger = logging.getLogger(__name__)

# Fallback locations for strategic docs when not found in planning_artifacts
FALLBACK_DOC_LOCATIONS = ["docs", "docs/modules"]

__all__ = [
    "_resolve_input_file_patterns",
    "_find_sharded_index",
    "_find_epic_file_in_sharded_dir",
    "_find_whole_file",
]


def _resolve_input_file_patterns(
    resolved: dict[str, Any],
    context: CompilerContext,
) -> dict[str, Any]:
    """Resolve input_file_patterns to variables with attributes.

    For each pattern in input_file_patterns (e.g., 'architecture', 'epics'):
    - Creates variable named '{pattern}_file'
    - Detects if artifact is sharded (directory with index.md) or whole (single file)
    - Adds attributes: description, load_strategy, sharded, token_approx (if applicable)
    - If file not found, variable has only description attribute

    Variable with attributes is stored as dict with special keys:
    - _value: file path (or None if not found)
    - _description: description text
    - _load_strategy: load strategy (only if file found)
    - _sharded: "true" (only if sharded and found)
    - _token_approx: estimated token count (only if file found and readable)

    Args:
        resolved: Dict of resolved variables.
        context: Compiler context.

    Returns:
        Dict with input_file_patterns resolved to attributed variables.

    """
    input_patterns = resolved.get("input_file_patterns")
    if not isinstance(input_patterns, dict):
        return resolved

    for pattern_name, pattern_config in input_patterns.items():
        if not isinstance(pattern_config, dict):
            continue

        var_name = f"{pattern_name}_file"
        description = pattern_config.get("description", "")
        load_strategy = pattern_config.get("load_strategy", "FULL_LOAD")
        sharded_pattern = pattern_config.get("sharded", "")
        whole_pattern = pattern_config.get("whole", "")

        # Try to find the file
        file_path: str | None = None
        is_sharded = False

        # Check sharded first (directory with index.md)
        if sharded_pattern:
            # Special handling for epics: use specific epic file for story context
            # instead of index.md (which is generic overview)
            if pattern_name == "epics":
                epic_num = resolved.get("epic_num")
                epic_file = _find_epic_file_in_sharded_dir(sharded_pattern, epic_num)
                if epic_file:
                    file_path = str(epic_file)
                    is_sharded = True
                    logger.debug("Found sharded epic for story context: %s", file_path)
            # TODO: Tech debt - other sharded docs (architecture, prd, etc.)
            # currently fall back to index.md. Consider context-aware resolution
            # similar to epics_file when story/sprint context is available.
            if not file_path:
                sharded_index = _find_sharded_index(sharded_pattern)
                if sharded_index:
                    file_path = str(sharded_index)
                    is_sharded = True
                    logger.debug("Found sharded artifact for '%s': %s", pattern_name, file_path)

        # Fall back to whole file
        if not file_path and whole_pattern:
            whole_file = _find_whole_file(whole_pattern, context.project_root)
            if whole_file:
                file_path = str(whole_file)
                logger.debug("Found whole artifact for '%s': %s", pattern_name, file_path)

        # Always set _value - if file not found, use pattern as fallback path
        if not file_path:
            # Use pattern as fallback (shows where file would be)
            fallback = whole_pattern if whole_pattern else sharded_pattern
            file_path = fallback if fallback else f"{pattern_name} (not found)"
            logger.debug("No file found for '%s', using pattern as path: %s", pattern_name, file_path)

        # Create variable with attributes
        var_attrs: dict[str, Any] = {
            "_value": file_path,
            "_description": description,
            "_load_strategy": load_strategy,
        }
        if is_sharded:
            var_attrs["_sharded"] = "true"
        # Add token estimate for LLM context awareness (only if file exists)
        try:
            token_estimate = _estimate_tokens(Path(file_path))
            if token_estimate is not None:
                var_attrs["_token_approx"] = str(token_estimate)
        except (OSError, FileNotFoundError):
            pass  # File doesn't exist, skip token estimate

        # Replace existing variable (even if it was set earlier)
        resolved[var_name] = var_attrs

    # Remove input_file_patterns from resolved (it's been processed)
    del resolved["input_file_patterns"]

    return resolved


def _find_sharded_index(sharded_pattern: str) -> Path | None:
    """Find index.md in a sharded directory matching the pattern.

    Args:
        sharded_pattern: Glob pattern for sharded files (e.g., 'docs/*architecture*/*.md')

    Returns:
        Path to index.md if sharded directory exists, None otherwise.

    """
    # The sharded pattern points to files in a directory
    # We need to find the directory and check for index.md
    matches = glob_module.glob(sharded_pattern, recursive=True)

    if not matches:
        return None

    # Find unique directories containing matches
    dirs: set[Path] = set()
    for match in matches:
        path = Path(match)
        if path.is_file():
            dirs.add(path.parent)

    # Check each directory for index.md
    for dir_path in sorted(dirs, key=lambda p: len(p.parts)):
        index_path = dir_path / "index.md"
        if index_path.exists():
            return index_path

    return None


def _find_epic_file_in_sharded_dir(sharded_pattern: str, epic_num: Any) -> Path | None:
    """Find specific epic file in sharded epics directory.

    When epics are sharded (epics/*.md), finds the epic file matching
    the current story's epic number instead of returning index.md.

    Args:
        sharded_pattern: Glob pattern for sharded files (e.g., 'docs/*epic*/*.md')
        epic_num: Epic number to find (e.g., 6 for epic-6-*.md)

    Returns:
        Path to epic-{num}-*.md if found, None otherwise.

    """
    if epic_num is None:
        return None

    matches = glob_module.glob(sharded_pattern, recursive=True)
    if not matches:
        return None

    # Find the epic file matching epic_num
    epic_prefix = f"epic-{epic_num}-"
    for match in matches:
        path = Path(match)
        if path.is_file() and path.name.startswith(epic_prefix):
            logger.debug(
                "Found epic file for story context: %s (epic_num=%s)",
                path,
                epic_num,
            )
            return path

    logger.debug("No epic-%s-*.md found in sharded directory", epic_num)
    return None


def _find_whole_file(whole_pattern: str, project_root: Path | None = None) -> Path | None:
    """Find a single whole file matching the pattern.

    Args:
        whole_pattern: Glob pattern for whole file (e.g., 'docs/*architecture*.md')
        project_root: Optional project root for fallback search in docs/

    Returns:
        Path to the file if found (closest to root), None otherwise.

    """
    matches = glob_module.glob(whole_pattern, recursive=True)

    if not matches and project_root is not None:
        # Fallback: try to find file by name in docs/ directories
        # Extract filename pattern from whole_pattern
        # e.g., "*prd*.md" -> "prd", "*ux*.md" -> "ux"
        # Try to extract the base name from pattern
        base_name = None
        if "prd" in whole_pattern.lower():
            base_name = "prd.md"
        elif "architecture" in whole_pattern.lower():
            base_name = "architecture.md"
        elif "ux" in whole_pattern.lower():
            base_name = "ux-design.md"  # Common UX filename
        elif "epic" in whole_pattern.lower():
            base_name = None  # Don't fallback for epics (story-specific)

        if base_name:
            for fallback_dir in FALLBACK_DOC_LOCATIONS:
                fallback_path = project_root / fallback_dir / base_name
                if fallback_path.exists():
                    logger.debug("Found file in fallback location: %s", fallback_path)
                    return fallback_path

    if not matches:
        return None

    # Filter to actual files, exclude archive directories
    valid_files: list[Path] = []
    for match in matches:
        path = Path(match)
        if not path.is_file():
            continue
        # Exclude archive directories
        path_parts_lower = [p.lower() for p in path.parts]
        if "archive" in path_parts_lower:
            continue
        valid_files.append(path)

    if not valid_files:
        return None

    # Sort by path depth (closest to root first), then alphabetically
    valid_files.sort(key=lambda p: (len(p.parts), str(p)))
    return valid_files[0]
