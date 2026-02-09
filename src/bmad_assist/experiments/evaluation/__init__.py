"""Evaluation framework for benchmark fixture projects.

This framework provides a unified way to evaluate LLM-generated code
across different stacks (Python API, Python library, TypeScript UI, Go, etc.)

Usage:
    python -m bmad_assist.experiments.evaluation run auth-service
    python -m bmad_assist.experiments.evaluation calc auth-service
"""

from .adapters.base import BaseEvaluator
from .core.scoring import grade, score
from .core.session import SessionManager

__all__ = ["score", "grade", "SessionManager", "BaseEvaluator"]
