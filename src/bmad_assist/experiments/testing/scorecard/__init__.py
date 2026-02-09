"""Automated Quality Scorecard Generator (modular package).

Public API:
    generate_scorecard(fixture_name, *, fixture_path=None) -> dict
    save_scorecard(scorecard) -> Path
"""

from .orchestrator import generate_scorecard, save_scorecard

__all__ = ["generate_scorecard", "save_scorecard"]
