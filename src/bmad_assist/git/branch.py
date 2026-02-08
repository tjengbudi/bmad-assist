"""Git branch management for epic-based workflow.

Ensures each epic is developed on its own branch (epic-XX) to maintain
clean git history and prevent accidental commits to master.

Story 22.X: Epic branch management for dogfooding workflow.
"""

import logging
import subprocess
from pathlib import Path

from bmad_assist.core.types import EpicId

logger = logging.getLogger(__name__)

# Default timeout for git commands
_GIT_TIMEOUT = 10


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command and return exit code, stdout, stderr.

    Args:
        args: Git command arguments (without 'git' prefix).
        cwd: Working directory for git command.

    Returns:
        Tuple of (exit_code, stdout, stderr).

    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Git command timed out"
    except FileNotFoundError:
        return 1, "", "Git not found in PATH"


def get_current_branch(project_path: Path) -> str | None:
    """Get the name of the current git branch.

    Args:
        project_path: Path to git repository.

    Returns:
        Branch name or None if not in a git repo or error.

    """
    exit_code, stdout, _ = _run_git(
        ["branch", "--show-current"],
        project_path,
    )
    if exit_code != 0 or not stdout:
        return None
    return stdout


def get_epic_branch_name(epic_id: EpicId) -> str:
    """Get the expected branch name for an epic.

    Args:
        epic_id: Epic identifier (int or str).

    Returns:
        Branch name in format 'epic-{epic_id}'.

    """
    return f"epic-{epic_id}"


def branch_exists(branch_name: str, project_path: Path) -> bool:
    """Check if a local branch exists.

    Args:
        branch_name: Name of the branch to check.
        project_path: Path to git repository.

    Returns:
        True if branch exists locally.

    """
    exit_code, _, _ = _run_git(
        ["rev-parse", "--verify", f"refs/heads/{branch_name}"],
        project_path,
    )
    return exit_code == 0


def create_branch(branch_name: str, project_path: Path) -> bool:
    """Create a new branch at current HEAD.

    Args:
        branch_name: Name for the new branch.
        project_path: Path to git repository.

    Returns:
        True if branch was created successfully.

    """
    exit_code, _, stderr = _run_git(
        ["branch", branch_name],
        project_path,
    )
    if exit_code != 0:
        logger.error("Failed to create branch %s: %s", branch_name, stderr)
        return False
    logger.info("Created branch: %s", branch_name)
    return True


def checkout_branch(branch_name: str, project_path: Path) -> bool:
    """Switch to an existing branch.

    Args:
        branch_name: Name of the branch to checkout.
        project_path: Path to git repository.

    Returns:
        True if checkout was successful.

    """
    exit_code, _, stderr = _run_git(
        ["checkout", branch_name],
        project_path,
    )
    if exit_code != 0:
        logger.error("Failed to checkout branch %s: %s", branch_name, stderr)
        return False
    logger.info("Switched to branch: %s", branch_name)
    return True


def ensure_epic_branch(epic_id: EpicId, project_path: Path) -> bool:
    """Ensure we're on the correct branch for the given epic.

    This function:
    1. Checks if we're already on the epic branch
    2. If not, checks if the branch exists and switches to it
    3. If branch doesn't exist, creates it and switches to it

    IMPORTANT: This should be called when starting work on an epic,
    NOT when finishing an epic. The branch switch happens at the
    START of a new epic, not at the END of the previous one.

    Args:
        epic_id: Epic identifier (int or str).
        project_path: Path to git repository.

    Returns:
        True if we're now on the correct branch.
        False if git operations failed (logged as warning, non-fatal).

    """
    expected_branch = get_epic_branch_name(epic_id)
    current_branch = get_current_branch(project_path)

    if current_branch is None:
        logger.warning(
            "Could not determine current branch - git may not be available. "
            "Continuing without branch management."
        )
        return False

    # Already on correct branch
    if current_branch == expected_branch:
        logger.debug("Already on branch %s", expected_branch)
        return True

    # Check if we're on master - warn but proceed
    if current_branch == "master":
        logger.warning(
            "Currently on 'master' branch. Switching to '%s' for epic %s. "
            "Committing directly to master is not recommended for epic work.",
            expected_branch,
            epic_id,
        )

    # Branch exists - just checkout
    if branch_exists(expected_branch, project_path):
        logger.info(
            "Branch %s exists, switching from %s",
            expected_branch,
            current_branch,
        )
        return checkout_branch(expected_branch, project_path)

    # Branch doesn't exist - create and checkout atomically.
    # Use 'checkout -b' instead of separate 'branch' + 'checkout' because
    # 'git branch' fails in empty repos (no HEAD), while 'checkout -b' works.
    logger.info(
        "Creating new branch %s for epic %s (from %s)",
        expected_branch,
        epic_id,
        current_branch,
    )
    exit_code, _, stderr = _run_git(
        ["checkout", "-b", expected_branch],
        project_path,
    )
    if exit_code != 0:
        logger.error("Failed to create branch %s: %s", expected_branch, stderr)
        return False
    logger.info("Created and switched to branch: %s", expected_branch)
    return True


def is_git_enabled() -> bool:
    """Check if git branch management is enabled via environment variable.

    Returns:
        True if BMAD_GIT_BRANCH is set to "1".
        Defaults to True if BMAD_GIT_COMMIT is enabled (same as commit).

    """
    import os

    # Explicit branch management flag
    branch_flag = os.environ.get("BMAD_GIT_BRANCH")
    if branch_flag is not None:
        return branch_flag == "1"

    # Fall back to commit flag (if commits are enabled, branches should be too)
    return os.environ.get("BMAD_GIT_COMMIT") == "1"
