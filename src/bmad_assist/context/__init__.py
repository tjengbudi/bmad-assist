"""Shared context extraction module for Deep Verify and source context.

Provides language-aware, budget-constrained extraction of code symbols
from source files. Supports diff-aware mode (prioritizing modified functions)
and no-diff fallback mode.

Pipeline: parse_symbols() → select_relevant() → format_context()
"""

from bmad_assist.context.extractor import extract_context
from bmad_assist.context.formatter import format_for_dv, format_for_source_context
from bmad_assist.context.types import ExtractedContext, ImportBlock, Symbol

__all__ = [
    "extract_context",
    "format_for_dv",
    "format_for_source_context",
    "ExtractedContext",
    "ImportBlock",
    "Symbol",
]
