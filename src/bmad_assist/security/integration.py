"""Security findings cache integration for synthesis.

Provides save/load functions for security findings cache,
mirroring deep_verify/integration/code_review_hook.py pattern.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from bmad_assist.security.report import SecurityReport

logger = logging.getLogger(__name__)


def save_security_findings_for_synthesis(
    report: SecurityReport,
    project_path: Path,
    session_id: str,
) -> Path:
    """Save security findings to cache for synthesis phase retrieval.

    Saves to .bmad-assist/cache/security-{session_id}.json.

    Args:
        report: SecurityReport to save.
        project_path: Project root directory.
        session_id: Session ID for correlation with code reviews.

    Returns:
        Path to saved cache file.

    Raises:
        OSError: If write fails.

    """
    from bmad_assist.core.io import atomic_write

    cache_dir = project_path / ".bmad-assist" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    filename = f"security-{session_id}.json"
    cache_path = cache_dir / filename

    data = report.to_cache_dict()
    data["session_id"] = session_id

    content = json.dumps(data, indent=2)
    atomic_write(cache_path, content)
    logger.info("Saved security findings for synthesis: %s", cache_path)
    return cache_path


def load_security_findings_from_cache(
    session_id: str,
    project_path: Path,
) -> SecurityReport | None:
    """Load security findings from cache by session ID.

    Args:
        session_id: Session ID from save_security_findings_for_synthesis.
        project_path: Project root directory.

    Returns:
        SecurityReport if found and valid, None otherwise.

    """
    cache_dir = project_path / ".bmad-assist" / "cache"
    cache_path = cache_dir / f"security-{session_id}.json"

    if not cache_path.exists():
        logger.debug("Security findings cache not found for session: %s", session_id)
        return None

    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)

        # Remove metadata field before deserialization
        data.pop("session_id", None)

        return SecurityReport.from_cache_dict(data)
    except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
        logger.warning(
            "Failed to load security findings from cache %s: %s",
            cache_path,
            e,
        )
        return None
