"""Core data types for context extraction pipeline.

Defines Symbol, ImportBlock, and ExtractedContext as the intermediate
representations used by language parsers, extractor, and formatters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class Symbol:
    """A code symbol (function, class, method) extracted from source.

    Attributes:
        name: Symbol name (e.g., "authenticate" or "AuthService.authenticate").
        kind: Symbol type.
        start_line: 1-indexed inclusive start line.
        end_line: 1-indexed inclusive end line.
        body: Full source text of the symbol.
        signature: First line only (for compact display).
        is_modified: True if overlaps with hunk_ranges.

    """

    name: str
    kind: Literal["function", "async_function", "class", "method"]
    start_line: int
    end_line: int
    body: str
    signature: str
    is_modified: bool = False


@dataclass(frozen=True, slots=True)
class ImportBlock:
    """Import statements from a source file.

    Attributes:
        content: Full text of all import lines.
        line_count: Number of import lines.

    """

    content: str
    line_count: int


@dataclass
class ExtractedContext:
    """Result of context extraction — structured for adapters to format.

    Attributes:
        file_path: Path to the source file.
        language: Detected language identifier.
        imports: Extracted import block.
        modified_symbols: Symbols overlapping hunk_ranges.
        unmodified_symbols: Remaining symbols (for signatures).
        total_symbols: Total number of symbols found.
        extraction_mode: How extraction was performed.
        budget_used: Characters used.
        budget_total: Budget limit.

    """

    file_path: str
    language: str
    imports: ImportBlock
    modified_symbols: list[Symbol] = field(default_factory=list)
    unmodified_symbols: list[Symbol] = field(default_factory=list)
    total_symbols: int = 0
    extraction_mode: Literal["diff_aware", "no_diff", "fallback"] = "fallback"
    budget_used: int = 0
    budget_total: int = 4000
    fallback_content: str = ""
