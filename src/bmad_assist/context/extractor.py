"""Main extraction pipeline for context extraction.

Pipeline: detect language → parse symbols → mark modified → budget allocation
→ return ExtractedContext.

Provides two modes:
- Diff-aware: caller provides hunk_ranges, extractor finds enclosing functions
- No-diff fallback: imports + all signatures + truncated bodies
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Literal as Lit

from bmad_assist.context.types import ExtractedContext, ImportBlock, Symbol

logger = logging.getLogger(__name__)

# Minimum budget enforced
MIN_BUDGET = 2000

# Language detection by file extension
_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
}


@functools.lru_cache(maxsize=1)
def _get_parsers() -> dict[str, Callable[..., tuple[ImportBlock, list[Symbol]]]]:
    """Lazy import parsers to avoid circular imports."""
    from bmad_assist.context.parsers.go import parse_go_symbols
    from bmad_assist.context.parsers.javascript import parse_js_symbols
    from bmad_assist.context.parsers.python import parse_python_symbols

    return {
        "python": parse_python_symbols,
        "javascript": parse_js_symbols,
        "typescript": parse_js_symbols,
        "go": parse_go_symbols,
    }


def extract_context(
    content: str,
    file_path: str,
    budget: int = 4000,
    hunk_ranges: list[tuple[int, int]] | None = None,
    language: str | None = None,
) -> ExtractedContext:
    """Extract structured context from source code within a budget.

    Args:
        content: Full source file content.
        file_path: Path to the file (used for language detection).
        budget: Maximum characters for output (default 4000, min 2000).
        hunk_ranges: Optional list of (start_line, end_line) 1-indexed inclusive
            ranges from git diff. If provided, enables diff-aware mode.
        language: Optional language override (skips extension detection).

    Returns:
        ExtractedContext with structured extraction results.

    """
    # Enforce minimum budget
    if budget < MIN_BUDGET:
        logger.warning(
            "Budget %d below minimum %d, enforcing minimum", budget, MIN_BUDGET
        )
        budget = MIN_BUDGET

    # Handle empty content
    if not content:
        return ExtractedContext(
            file_path=file_path,
            language=language or "unknown",
            imports=ImportBlock(content="", line_count=0),
            extraction_mode="fallback",
            budget_used=0,
            budget_total=budget,
        )

    # Step 1: Detect language
    if language is None:
        suffix = PurePosixPath(file_path).suffix.lower()
        language = _LANGUAGE_MAP.get(suffix, "unknown")

    # Step 2: Parse symbols
    parsers = _get_parsers()
    parser = parsers.get(language)

    if parser is None:
        # Unknown language → fallback mode
        return _fallback_context(content, file_path, language, budget)

    try:
        imports, symbols = parser(content)
    except ValueError as e:
        logger.debug("Parser failed for %s: %s — using fallback", file_path, e)
        return _fallback_context(content, file_path, language, budget)

    # Step 3: Mark modified symbols
    mode: Lit["diff_aware", "no_diff", "fallback"] = "no_diff"
    if hunk_ranges:
        # Filter out invalid ranges
        valid_ranges = [
            (s, e) for s, e in hunk_ranges if s >= 1 and e >= s
        ]

        if valid_ranges:
            marked_symbols = _mark_modified(symbols, valid_ranges)
            modified = [s for s in marked_symbols if s.is_modified]
            unmodified = [s for s in marked_symbols if not s.is_modified]

            if modified:
                mode = "diff_aware"
            else:
                # Hunk ranges don't overlap any symbol → no-diff mode
                logger.info(
                    "Hunk ranges %s don't overlap any symbol in %s — using no-diff mode",
                    valid_ranges,
                    file_path,
                )
                modified = []
                unmodified = symbols
        else:
            modified = []
            unmodified = symbols
    else:
        modified = []
        unmodified = symbols

    # Step 4: Budget allocation
    budget_used, imports = _allocate_budget(imports, modified, unmodified, budget)

    return ExtractedContext(
        file_path=file_path,
        language=language,
        imports=imports,
        modified_symbols=modified,
        unmodified_symbols=unmodified,
        total_symbols=len(symbols),
        extraction_mode=mode,
        budget_used=budget_used,
        budget_total=budget,
    )


def _mark_modified(
    symbols: list[Symbol],
    hunk_ranges: list[tuple[int, int]],
) -> list[Symbol]:
    """Mark symbols that overlap with hunk ranges.

    Overlap check: symbol.start_line <= hunk.end_line AND
                   symbol.end_line >= hunk.start_line

    Args:
        symbols: List of parsed symbols.
        hunk_ranges: Valid (start, end) tuples, 1-indexed inclusive.

    Returns:
        New list with is_modified set where applicable.

    """
    result: list[Symbol] = []
    for sym in symbols:
        is_modified = any(
            sym.start_line <= hunk_end and sym.end_line >= hunk_start
            for hunk_start, hunk_end in hunk_ranges
        )
        if is_modified:
            result.append(replace(sym, is_modified=True))
        else:
            result.append(sym)
    return result


def _allocate_budget(
    imports: ImportBlock,
    modified: list[Symbol],
    unmodified: list[Symbol],
    budget: int,
) -> tuple[int, ImportBlock]:
    """Allocate budget across imports, modified bodies, and signatures.

    Priority order:
    1. Imports (capped at 50% of budget)
    2. All modified symbol full bodies (never skip entirely)
    3. Unmodified symbol signatures
    4. Unmodified symbol bodies (fill remaining, shortest first)

    Args:
        imports: Import block.
        modified: Modified symbols.
        unmodified: Unmodified symbols.
        budget: Total character budget.

    Returns:
        Tuple of (total characters used, possibly-truncated ImportBlock).

    """
    used = 0

    # 1. Imports (capped at 50%)
    import_cap = budget // 2
    if imports.content:
        if len(imports.content) <= import_cap:
            used += len(imports.content)
        else:
            # Truncate imports
            truncated_lines = []
            char_count = 0
            for line in imports.content.split("\n"):
                if char_count + len(line) + 1 > import_cap:
                    break
                truncated_lines.append(line)
                char_count += len(line) + 1

            remaining = imports.line_count - len(truncated_lines)
            if remaining > 0:
                truncated_lines.append(f"# ... {remaining} more imports ...")

            imports_text = "\n".join(truncated_lines)
            used += len(imports_text)
            imports = ImportBlock(content=imports_text, line_count=len(truncated_lines))

    # 2. Modified symbol full bodies
    for sym in modified:
        body_len = len(sym.body)
        remaining = budget - used
        if body_len <= remaining:
            used += body_len
        else:
            # Include as much as fits + truncation marker
            if remaining > len(sym.signature) + 20:
                used += remaining  # Formatter will handle truncation
            else:
                # At minimum include signature
                used += len(sym.signature)

    # 3. Unmodified signatures (cheap, ~50 chars each)
    for sym in unmodified:
        sig_len = len(sym.signature) + 5  # "...\n"
        if used + sig_len <= budget:
            used += sig_len

    # 4. Unmodified bodies (fill remaining, shortest first)
    sorted_unmod = sorted(unmodified, key=lambda s: len(s.body))
    for sym in sorted_unmod:
        body_len = len(sym.body)
        if used + body_len <= budget:
            used += body_len

    return used, imports


def _fallback_context(
    content: str,
    file_path: str,
    language: str,
    budget: int,
) -> ExtractedContext:
    """Create fallback context with raw truncation.

    Args:
        content: Source code.
        file_path: File path.
        language: Detected language.
        budget: Character budget.

    Returns:
        ExtractedContext in fallback mode.

    """
    truncated = content[:budget]
    return ExtractedContext(
        file_path=file_path,
        language=language,
        imports=ImportBlock(content="", line_count=0),
        extraction_mode="fallback",
        budget_used=len(truncated),
        budget_total=budget,
        fallback_content=truncated,
    )
