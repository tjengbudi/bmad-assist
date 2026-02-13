"""Tech-stack-aware file filter for Deep Verify scanning.

Loads per-stack exclusion patterns from patterns/data/exclusions.yaml
and provides a single entry point to decide whether a file should be
skipped during DV analysis.

Usage:
    >>> from bmad_assist.deep_verify.file_filter import DVFileFilter
    >>> filt = DVFileFilter.for_project(Path("/my/project"))
    >>> filt.should_exclude("src/auth.py")       # False — scan it
    >>> filt.should_exclude("package.json")       # True  — skip
    >>> filt.should_exclude("vite.config.ts")     # True  — skip

"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).parent / "patterns" / "data" / "exclusions.yaml"


@lru_cache(maxsize=1)
def _load_exclusions() -> dict[str, Any]:
    """Load and cache exclusions YAML (once per process)."""
    try:
        with _DATA_FILE.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning("exclusions.yaml root is not a dict, using empty")
            return {}
        return data
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Cannot load exclusions.yaml: %s", e)
        return {}


class DVFileFilter:
    """Tech-stack-aware file exclusion filter.

    Merges 'common' exclusions with stack-specific ones and compiles
    them into fast lookup sets and regex patterns.

    Args:
        stacks: List of detected tech stacks (e.g. ["python", "javascript"]).
            Stack identifiers match security/tech_stack.py output.

    """

    def __init__(self, stacks: list[str] | None = None) -> None:  # noqa: D107
        data = _load_exclusions()
        stacks = stacks or []

        # Merge common + stack-specific sections
        sections = ["common"] + [s for s in stacks if s in data]

        self._extensions: set[str] = set()
        self._directories: set[str] = set()
        self._filenames: set[str] = set()
        self._patterns: list[re.Pattern[str]] = []

        for section_name in sections:
            section = data.get(section_name, {})
            if not isinstance(section, dict):
                continue

            for ext in section.get("extensions", []):
                self._extensions.add(ext.lower())

            for d in section.get("directories", []):
                # Normalize: strip trailing slash
                self._directories.add(d.rstrip("/"))

            for fn in section.get("filenames", []):
                self._filenames.add(fn)

            for pat in section.get("patterns", []):
                try:
                    self._patterns.append(re.compile(pat, re.IGNORECASE))
                except re.error as e:
                    logger.warning("Invalid exclusion pattern %r: %s", pat, e)

        logger.debug(
            "DVFileFilter initialized: stacks=%s, extensions=%d, "
            "directories=%d, filenames=%d, patterns=%d",
            sections,
            len(self._extensions),
            len(self._directories),
            len(self._filenames),
            len(self._patterns),
        )

    def should_exclude(self, rel_path: str) -> bool:
        """Check whether a file should be excluded from DV scanning.

        Args:
            rel_path: Relative file path (forward slashes, e.g. "src/auth.py").

        Returns:
            True if the file should be skipped.

        """
        # Normalize to forward slashes
        normalized = rel_path.replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1]

        # 1. Extension check (fast)
        dot_idx = basename.rfind(".")
        if dot_idx >= 0:
            ext = basename[dot_idx:].lower()
            if ext in self._extensions:
                return True
            # Handle compound extensions like .d.ts
            second_dot = basename.rfind(".", 0, dot_idx)
            if second_dot >= 0:
                compound_ext = basename[second_dot:].lower()
                if compound_ext in self._extensions:
                    return True

        # 2. Filename check (fast)
        if basename in self._filenames:
            return True

        # 3. Directory segment check
        parts = normalized.split("/")
        for part in parts[:-1]:  # Exclude basename
            if part in self._directories:
                return True

        # 4. Regex patterns against full relative path (slowest — last)
        return any(pat.search(normalized) for pat in self._patterns)

    @classmethod
    def for_project(
        cls,
        project_path: Path,
        config: Any = None,
    ) -> DVFileFilter:
        """Create a filter with auto-detected tech stacks.

        Uses LLM-based detection from project docs (architecture.md,
        project-context.md) with marker-based fallback.

        Args:
            project_path: Path to the project root.
            config: Optional BmadAssistConfig for LLM-based detection.
                If None, falls back to marker-based detection only.

        Returns:
            Configured DVFileFilter instance.

        """
        try:
            from bmad_assist.deep_verify.stack_detector import detect_project_stacks

            stacks = detect_project_stacks(project_path, config=config)
        except (ImportError, RuntimeError) as e:
            logger.debug("Stack detection unavailable: %s", e)
            stacks = []

        return cls(stacks=stacks)
