"""Main loop pause/resume functionality using file-based IPC.

Story 22.10: Pause functionality.

This module provides pause/resume capabilities for the main loop through
file-based inter-process communication. The dashboard server writes
pause.flag files, and the main loop subprocess detects them at safe
interrupt points.

IPC Protocol:
- Dashboard: Write pause.flag to request pause
- Main Loop: Check pause.flag at safe points, enter wait loop if exists
- Dashboard: Remove pause.flag to resume (writes stop.flag to terminate)
- Main Loop: Check both pause.flag and stop.flag in wait loop

State Consistency:
- State is validated before pause (required fields, YAML parseable)
- State is persisted before entering wait loop
- Stale flags are cleaned on startup (from crashed sessions)

Safe Interrupt Points:
- AFTER state.yaml persist (atomic write complete)
- BEFORE next phase dispatch
- AFTER LLM response processed and saved

NOT Safe:
- During LLM streaming (allow request to complete)
- During file write operations (wait for atomic rename)
- During state.yaml persist (wait for temp file + rename)
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from bmad_assist.core.exceptions import StateError
from bmad_assist.core.loop.signals import shutdown_requested
from bmad_assist.core.state import load_state

logger = logging.getLogger(__name__)

__all__ = [
    "check_pause_flag",
    "cleanup_stale_pause_flags",
    "wait_for_resume",
    "validate_state_for_pause",
]


# =============================================================================
# File-based IPC: Pause flag detection
# =============================================================================


def check_pause_flag(project_path: Path) -> bool:
    """Check if pause has been requested via flag file.

    Args:
        project_path: Project root directory.

    Returns:
        True if pause.flag exists, False otherwise.

    """
    pause_flag = project_path / ".bmad-assist" / "pause.flag"
    return pause_flag.exists()


# =============================================================================
# Startup: Stale flag cleanup
# =============================================================================


def cleanup_stale_pause_flags(project_path: Path) -> None:
    """Remove stale pause.flag and stop.flag from previous crashed session (AC #6, #7).

    This should be called on main loop startup BEFORE the loop begins.
    If stale flags are detected, they are removed with a WARNING log
    and the run proceeds normally (not paused).

    This handles the crash scenario:
    1. User clicks Pause during workflow
    2. Dashboard writes pause.flag
    3. Main loop crashes (kill -9, system crash, etc.)
    4. On restart, stale pause.flag would cause immediate pause
    5. Cleanup removes flag with WARNING, allowing normal startup

    Also cleans stop.flag for the case where:
    1. User clicks Stop while paused
    2. stop.flag is created
    3. Subprocess crashes before detecting stop.flag
    4. On restart, stale stop.flag could cause unexpected termination

    Args:
        project_path: Project root directory.

    """
    pause_flag = project_path / ".bmad-assist" / "pause.flag"
    stop_flag = project_path / ".bmad-assist" / "stop.flag"

    if pause_flag.exists():
        logger.warning(
            "Removed stale pause flag from previous crashed session. "
            "Run will start normally (not paused)."
        )
        try:
            pause_flag.unlink()
        except OSError as e:
            logger.warning("Failed to remove stale pause flag: %s", e)

    # AC #6: Also clean stale stop.flag
    if stop_flag.exists():
        logger.warning("Removed stale stop flag from previous crashed session.")
        try:
            stop_flag.unlink()
        except OSError as e:
            logger.warning("Failed to remove stale stop flag: %s", e)


# =============================================================================
# Wait loop: Pause state management
# =============================================================================


def wait_for_resume(
    project_path: Path,
    stop_event: threading.Event | None = None,
    pause_timeout_minutes: int = 60,
) -> bool:
    """Wait for pause flag to be cleared (resume) or stop event.

    Enters a wait loop that checks for:
    1. pause.flag removal (resume)
    2. stop.flag creation (stop while paused, AC #6)
    3. Timeout (auto-resume to prevent indefinite hangs)
    4. shutdown_requested() signal handler check (Story 22.10)

    Args:
        project_path: Project root directory.
        stop_event: (Optional) Threading event for external stop requests.
            If None, only shutdown_requested() is checked.
        pause_timeout_minutes: Auto-resume timeout in minutes (0 = disabled).
            Default 60 minutes to prevent indefinite hangs if dashboard crashes.

    Returns:
        True if resumed (pause.flag cleared).
        False if stopped (stop.flag detected or shutdown_requested) or timed out.

    """
    pause_flag = project_path / ".bmad-assist" / "pause.flag"
    stop_flag = project_path / ".bmad-assist" / "stop.flag"
    pause_start = time.time()

    logger.info("Entering pause wait loop (paused at current phase)")

    while pause_flag.exists():
        # Check for stop request via stop.flag (AC #6)
        if stop_flag.exists():
            logger.info("Stop requested while paused (stop.flag) - terminating wait loop")
            # Clean up both flags
            stop_flag.unlink(missing_ok=True)
            pause_flag.unlink(missing_ok=True)
            return False

        # Check for timeout (if configured)
        if pause_timeout_minutes > 0:
            elapsed = time.time() - pause_start
            if elapsed > (pause_timeout_minutes * 60):
                logger.warning(
                    f"Pause timeout exceeded ({pause_timeout_minutes}min), auto-resuming. "
                    f"This prevents indefinite hangs if dashboard crashes."
                )
                pause_flag.unlink(missing_ok=True)
                return True

        # Check signal handler shutdown (Story 22.10 - fixes dead stop_event issue)
        if shutdown_requested():
            logger.info("Shutdown signal detected while paused")
            pause_flag.unlink(missing_ok=True)
            return False

        # Check external stop event if provided (legacy support)
        if stop_event is not None and stop_event.is_set():
            logger.info("External stop event detected while paused")
            pause_flag.unlink(missing_ok=True)
            return False

        # Sleep before next check (2s interval — pause is human-initiated)
        time.sleep(2.0)

    logger.info("Pause flag cleared - resuming from pause")
    return True


# =============================================================================
# State consistency validation
# =============================================================================


def validate_state_for_pause(state_path: Path) -> bool:
    """Validate state.yaml consistency before pause (AC #3, #6).

    Checks:
    1. File exists and is readable
    2. YAML parsing succeeds
    3. Required fields present (current_epic, current_story, current_phase)

    Args:
        state_path: Path to state.yaml file.

    Returns:
        True if state is valid and consistent.
        False if state is corrupted or missing required fields.

    """
    try:
        # Check file exists
        if not state_path.exists():
            logger.error("State file does not exist: %s", state_path)
            return False

        # Load and parse YAML
        state = load_state(state_path)

        # Validate required fields
        required_fields = ["current_epic", "current_story", "current_phase"]
        for field in required_fields:
            value = getattr(state, field, None)
            if value is None:
                logger.error(
                    "State validation failed: required field '%s' is None. "
                    "Cannot pause safely with inconsistent state.",
                    field,
                )
                return False

        # State is valid
        logger.debug("State validation passed: all required fields present")
        return True

    except StateError as e:
        logger.error("State validation failed (StateError): %s", e)
        return False
    except Exception as e:
        logger.error("State validation failed (unexpected error): %s", e)
        return False
