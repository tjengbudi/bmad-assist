"""Shared Pydantic validators for Deep Verify LLM response models.

LLMs sometimes return non-integer values for line_number fields
(e.g., "Task 1", "Subtask 1.1", "Multiple", "N/A"). These validators
coerce such values gracefully instead of failing entire response parsing.
"""

from __future__ import annotations

from typing import Any


def coerce_line_number(v: Any) -> int | None:
    """Coerce LLM line_number output to int or None.

    Handles: int passthrough, str→int parsing, garbage strings→None,
    None→None, float→int, negative/zero→None.
    """
    if v is None:
        return None
    if isinstance(v, int):
        return v if v >= 1 else None
    if isinstance(v, float):
        return int(v) if v >= 1 else None
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        try:
            result = int(v)
            return result if result >= 1 else None
        except ValueError:
            return None
    return None
