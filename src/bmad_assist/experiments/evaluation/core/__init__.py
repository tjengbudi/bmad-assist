"""Core evaluation framework components."""

from .scoring import GRADE_THRESHOLDS, grade, score
from .session import SessionManager

__all__ = ["score", "grade", "GRADE_THRESHOLDS", "SessionManager"]
