"""Format ExtractedContext into string representations for consumers.

Provides two formatters:
- format_for_dv(): For Deep Verify LLM analysis — labeled sections
- format_for_source_context(): For source_context.py — plain code
"""

from __future__ import annotations

import logging

from bmad_assist.context.types import ExtractedContext

logger = logging.getLogger(__name__)


def format_for_dv(ctx: ExtractedContext) -> str:
    """Format for Deep Verify LLM analysis — labeled sections.

    Output format:
        # File: path (N symbols, M modified)
        ## Imports
        ...imports...
        ## Modified (diff lines X-Y)
        ...full body...
        ## Signatures
        ...one-liners for unmodified...

    Args:
        ctx: Extracted context to format.

    Returns:
        Formatted string for DV analysis.

    """
    parts: list[str] = []

    # Header
    mod_count = len(ctx.modified_symbols)
    parts.append(
        f"# File: {ctx.file_path} ({ctx.total_symbols} symbols, {mod_count} modified)"
    )

    # Fallback mode — include truncated raw content
    if ctx.extraction_mode == "fallback":
        if ctx.fallback_content:
            parts.append(f"\n```\n{ctx.fallback_content[:ctx.budget_total]}\n```")
        else:
            parts.append(f"\n[Fallback extraction — {ctx.budget_used} chars]")
        return "\n".join(parts)

    # Imports section
    if ctx.imports.content:
        parts.append("\n## Imports")
        parts.append(ctx.imports.content)

    # Modified symbols section
    if ctx.modified_symbols:
        parts.append("\n## Modified Functions")
        for sym in ctx.modified_symbols:
            label = f"# --- Modified: {sym.name} (lines {sym.start_line}-{sym.end_line}) ---"
            parts.append(label)
            # Truncate body if it would exceed remaining budget
            body = sym.body
            remaining = ctx.budget_total - _current_length(parts)
            if len(body) > remaining and remaining > 50:
                body = body[:remaining - 20] + "\n# ... truncated ..."
            parts.append(body)

    # Signatures section
    if ctx.unmodified_symbols:
        parts.append("\n## Signatures")
        for sym in ctx.unmodified_symbols:
            parts.append(f"{sym.signature}  # ...")

    result = "\n".join(parts)

    # Final budget enforcement
    if len(result) > ctx.budget_total:
        result = result[: ctx.budget_total]

    return result


def format_for_source_context(ctx: ExtractedContext, max_chars: int) -> str:
    """Format for source_context.py — plain code without labels.

    Compatible with existing truncation behavior. No section headers.

    Args:
        ctx: Extracted context to format.
        max_chars: Maximum characters from source_context budget.

    Returns:
        Formatted string compatible with existing source_context format.

    """
    parts: list[str] = []

    # Fallback — let caller handle its own truncation
    if ctx.extraction_mode == "fallback":
        return ""

    # Imports first
    if ctx.imports.content:
        parts.append(ctx.imports.content)

    # Modified symbol bodies (or all symbols in no-diff mode)
    symbols_to_include = ctx.modified_symbols or ctx.unmodified_symbols
    for sym in symbols_to_include:
        if _current_length(parts) + len(sym.body) + 2 <= max_chars:
            parts.append("")
            parts.append(sym.body)
        elif _current_length(parts) + len(sym.signature) + 10 <= max_chars:
            parts.append("")
            parts.append(f"{sym.signature}  # ...")

    # Fill with remaining signatures
    if ctx.modified_symbols and ctx.unmodified_symbols:
        for sym in ctx.unmodified_symbols:
            line = f"{sym.signature}  # ..."
            if _current_length(parts) + len(line) + 1 <= max_chars:
                parts.append(line)

    result = "\n".join(parts)

    if len(result) > max_chars:
        result = result[:max_chars]

    return result


def _current_length(parts: list[str]) -> int:
    """Calculate current length including newline separators."""
    if not parts:
        return 0
    return sum(len(p) for p in parts) + len(parts) - 1
