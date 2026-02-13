"""Git intelligence extraction for compile-time embedding.

This module provides functionality to extract git information at compile time
and embed it in the workflow prompt. This prevents LLM from running expensive
git archaeology at runtime.

Key functions:
    load_git_intelligence_config: Load git intelligence from YAML file with inheritance
    is_git_repo: Check if a directory is a git repository
    run_git_command: Execute a git command with variable substitution
    apply_exclusions_to_command: Apply glob exclusions to git command pathspecs
    extract_git_intelligence: Run all configured commands and format output
"""

import fnmatch
import logging
import re
import subprocess
from pathlib import Path

import yaml

from bmad_assist.compiler.patching.types import GitCommand, GitIntelligence

logger = logging.getLogger(__name__)

# Git intelligence config locations (searched in order)
_GIT_INTEL_DIRS = [
    "src/bmad_assist/default_patches/git-intelligence",  # Development
    "bmad_assist/default_patches/git-intelligence",  # Installed package
    "_bmad/bmm/git-intelligence",  # Project override
    ".bmad-assist/patches/git-intelligence",  # User override
]


def _find_git_intel_file(name: str, project_root: Path) -> Path | None:
    """Find git intelligence YAML file by name.

    Searches standard locations for git-intelligence/{name}.yaml.

    Args:
        name: Name of git-intelligence config (e.g., "dev-story").
        project_root: Project root directory.

    Returns:
        Path to the YAML file, or None if not found.

    """
    filename = f"{name}.yaml"

    # First check project override locations
    for rel_dir in [_d for _d in _GIT_INTEL_DIRS if "default_patches" not in _d]:
        path = project_root / rel_dir / filename
        if path.exists():
            return path

    # Then check installed/default locations
    # Try from current directory (for development)
    for rel_dir in _GIT_INTEL_DIRS:
        path = Path.cwd() / rel_dir / filename
        if path.exists():
            return path

    # Try from package location
    try:
        import bmad_assist
        pkg_root = Path(bmad_assist.__file__).parent
        for rel_dir in _GIT_INTEL_DIRS:
            path = pkg_root / rel_dir / filename
            if path.exists():
                return path
    except (ImportError, AttributeError):
        pass

    return None


def load_git_intelligence_config(
    config: GitIntelligence,
    project_root: Path,
) -> GitIntelligence:
    """Load and merge git intelligence configuration from YAML files.

    Supports inheritance via `inherit_from` field. Loads the base config,
    merges exclude_patterns, and extends commands.

    Args:
        config: Git intelligence configuration from patch (may have inherit_from).
        project_root: Project root directory.

    Returns:
        Complete GitIntelligence with all inherited values merged.

    Raises:
        CompilerError: If inherited config cannot be found or loaded.

    """
    from bmad_assist.core.exceptions import CompilerError

    # If no inheritance, return as-is
    if not config.inherit_from:
        return config

    # Load base config
    base_file = _find_git_intel_file(config.inherit_from, project_root)
    if base_file is None:
        raise CompilerError(
            f"Git intelligence config not found: {config.inherit_from}.yaml\n"
            f"  Searched in: {_GIT_INTEL_DIRS}"
        )

    try:
        with open(base_file) as f:
            base_data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise CompilerError(
            f"Failed to load git intelligence config: {base_file}\n"
            f"  Error: {e}"
        ) from e

    # Merge exclude_patterns (base + specific)
    exclude_patterns = list(base_data.get("exclude_patterns", []))
    if hasattr(config, "exclude_patterns"):
        exclude_patterns.extend(config.exclude_patterns)

    # Merge commands (base phase_commands or base_commands + specific)
    commands_data = base_data.get("phase_commands") or base_data.get("base_commands") or []
    commands: list[GitCommand] = []

    for cmd in commands_data:
        if isinstance(cmd, dict):
            commands.append(GitCommand(
                name=cmd["name"],
                description=cmd.get("description"),
                command=cmd["command"],
            ))
        elif isinstance(cmd, GitCommand):
            commands.append(cmd)

    # Add commands from config
    commands.extend(config.commands)

    # Return merged config
    return GitIntelligence(
        enabled=config.enabled,
        inherit_from=None,  # Resolved
        exclude_patterns=exclude_patterns,
        commands=commands,
        embed_marker=config.embed_marker,
        no_git_message=config.no_git_message,
    )


def apply_exclusions_to_command(
    command: str,
    exclude_patterns: list[str],
) -> str:
    """Apply exclusion patterns to a git command.

    Modifies git commands that support pathspec negation by adding
    exclusion patterns. Currently handles:
    - git diff --stat
    - git diff --name-only
    - git log

    NOTE: git status does NOT support pathspec exclusion well.
    Use filter_git_output() to filter status output after execution.

    Args:
        command: Git command string.
        exclude_patterns: List of glob patterns to exclude.

    Returns:
        Modified command with exclusions applied (for commands that support it).
        For git status, returns command unchanged (filter output separately).

    """
    if not exclude_patterns:
        return command

    # Don't modify commands that already have exclusions
    if "':" in command or "':!" in command:
        return command

    # Determine which commands support pathspec exclusions
    cmd_parts = command.strip().split()
    if not cmd_parts:
        return command

    # git status - return unchanged, will filter output instead
    if "status" in command:
        return command

    # Handle git diff variants
    if "diff" in command:
        # Find where to insert exclusions (after --stat or --name-only, before files)
        insert_idx = len(cmd_parts)
        for i, part in enumerate(cmd_parts):
            if part.startswith("--"):
                continue
            if not part.startswith("-"):
                # First non-option argument - insert exclusions before it
                insert_idx = i
                break

        # Build exclusion pathspecs using :(exclude) syntax
        exclusions = [f':(exclude){p}' for p in exclude_patterns]
        return " ".join(cmd_parts[:insert_idx] + exclusions + cmd_parts[insert_idx:])

    # Handle git log - add -- . with exclusions
    if "log" in command and "--" not in command:
        # Add pathspec delimiter with exclusions
        exclusions = [f':(exclude){p}' for p in exclude_patterns]
        return command + " -- " + " ".join(exclusions)

    return command


def filter_git_output(
    output: str,
    exclude_patterns: list[str],
) -> str:
    """Filter git command output by removing lines matching exclude patterns.

    For git status --short, filters out files matching the patterns.
    Each line format: "XY filename" or "XY filename -> newname"

    Args:
        output: Git command output to filter.
        exclude_patterns: List of glob patterns to exclude.

    Returns:
        Filtered output with matching lines removed.

    """
    if not exclude_patterns or not output:
        return output

    lines = output.split("\n")
    filtered_lines = []

    for line in lines:
        if not line.strip():
            filtered_lines.append(line)
            continue

        # Extract filename from git status --short format
        # Format: "XY filename" or "XY  filename -> newname"
        parts = line.split()
        if len(parts) >= 2:
            filename = parts[1]
            # Handle rename notation: "oldname -> newname"
            if "->" in filename:
                filename = filename.split("->")[-1].strip()

            # Check if filename matches any exclude pattern
            excluded = False
            for pattern in exclude_patterns:
                # Convert glob pattern to regex
                # Remove **/ prefix for matching
                test_pattern = pattern
                if test_pattern.startswith("**/"):
                    test_pattern = test_pattern[3:]
                elif test_pattern.startswith("*/"):
                    test_pattern = test_pattern[2:]

                # Check if filename path components match
                # Pattern like ".bmad-assist/**" should match ".bmad-assist/cache/foo.json"
                path_parts = Path(filename).parts
                pattern_parts = Path(test_pattern).parts

                # Match if path starts with pattern parts
                if len(path_parts) >= len(pattern_parts):
                    match = True
                    for i, pp in enumerate(pattern_parts):
                        if pp != "**" and pp != "*" and pp != path_parts[i]:
                            match = False
                            break
                    if match:
                        excluded = True
                        break

                # Also try fnmatch for simpler patterns
                if fnmatch.fnmatch(filename, test_pattern) or fnmatch.fnmatch(filename, pattern):
                    excluded = True
                    break
                # Try matching from start of path
                if fnmatch.fnmatch(filename, "*" + test_pattern):
                    excluded = True
                    break

            if not excluded:
                filtered_lines.append(line)
        else:
            # Keep lines that don't match status format
            filtered_lines.append(line)

    return "\n".join(filtered_lines)

# Timeout for git commands (seconds)
GIT_COMMAND_TIMEOUT = 10

# Max output length per command (characters)
MAX_OUTPUT_LENGTH = 2000


def is_git_repo(path: Path) -> bool:
    """Check if a directory is a git repository ROOT.

    This function checks if `path` is itself the root of a git repository,
    NOT just a subdirectory within one. This is important when the project
    directory is a subdirectory of another git repository (e.g., test fixtures
    inside the main bmad-assist repo).

    Args:
        path: Directory to check.

    Returns:
        True if path is a git repository root, False otherwise.

    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            return False

        # Compare the git root with the provided path
        # Both must resolve to the same directory
        git_root = Path(result.stdout.strip()).resolve()
        target_path = path.resolve()

        return git_root == target_path
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _substitute_variables(command: str, variables: dict[str, str | int | None]) -> str:
    """Substitute {{variable}} placeholders in command string.

    Args:
        command: Command string with optional {{variable}} placeholders.
        variables: Dictionary of variable names to values.

    Returns:
        Command string with variables substituted.

    """
    result = command
    for name, value in variables.items():
        # Handle both {{name}} and {{ name }} formats
        pattern = r"\{\{\s*" + re.escape(str(name)) + r"\s*\}\}"
        result = re.sub(pattern, str(value), result)
    return result


def run_git_command(
    command: str,
    cwd: Path,
    variables: dict[str, str | int | None] | None = None,
) -> str:
    """Execute a git command and return output.

    Args:
        command: Git command to execute (e.g., "git log --oneline -5").
        cwd: Working directory for the command.
        variables: Optional variables for substitution in command.

    Returns:
        Command output (stdout), truncated if too long.
        On error, returns error message.

    """
    # Substitute variables
    if variables:
        command = _substitute_variables(command, variables)

    logger.debug("Running git command: %s (cwd=%s)", command, cwd)

    try:
        result = subprocess.run(
            command,
            shell=True,  # Need shell for pipes, grep etc.
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT,
        )

        output = result.stdout.strip()

        # Truncate if too long
        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"

        if result.returncode != 0 and result.stderr:
            # Include stderr if command failed
            return f"(command failed: {result.stderr.strip()})"

        return output or "(no output)"

    except subprocess.TimeoutExpired:
        logger.warning("Git command timed out: %s", command)
        return "(command timed out)"
    except OSError as e:
        logger.warning("Git command failed: %s - %s", command, e)
        return f"(command error: {e})"


def extract_git_intelligence(
    config: GitIntelligence,
    project_root: Path,
    variables: dict[str, str | int | None] | None = None,
) -> str:
    """Extract git intelligence and format as embedded content.

    Checks if git is initialized, runs configured commands, and formats
    the output for embedding in the workflow prompt.

    Args:
        config: Git intelligence configuration from patch.
        project_root: Project root directory (for git commands).
        variables: Optional variables for command substitution.

    Returns:
        Formatted string to embed in workflow, wrapped in embed_marker tags.

    """
    # Resolve inheritance first
    config = load_git_intelligence_config(config, project_root)

    if not config.enabled:
        logger.debug("Git intelligence disabled")
        return ""

    marker = config.embed_marker
    parts = [f"<{marker}>"]

    # Check if git is initialized
    if not is_git_repo(project_root):
        logger.info("Project is not a git repository: %s", project_root)
        parts.append(config.no_git_message)
        parts.append(f"</{marker}>")
        return "\n".join(parts)

    # Run each configured command
    parts.append(
        "Git intelligence extracted at compile time. "
        "Do NOT run additional git commands - use this embedded data instead."
    )
    parts.append("")

    for git_cmd in config.commands:
        # Apply exclusions to command before running
        command_to_run = apply_exclusions_to_command(
            git_cmd.command,
            config.exclude_patterns,
        )
        output = run_git_command(command_to_run, project_root, variables)

        # For git status, filter output after execution
        # (git status doesn't support pathspec exclusion well)
        if "status" in git_cmd.command and config.exclude_patterns:
            output = filter_git_output(output, config.exclude_patterns)

        # Use description if available, otherwise name
        header = git_cmd.description or git_cmd.name
        parts.append(f"### {header}")
        parts.append("```")
        parts.append(output)
        parts.append("```")
        parts.append("")

    parts.append(f"</{marker}>")

    return "\n".join(parts)
