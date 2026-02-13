"""Data models for BMAD workflow patches.

This module defines the core data structures for patch definitions:
- PatchConfig: Patch metadata (name, version, author, description)
- Compatibility: Version requirements (bmad_version, workflow)
- TransformResult: Result of applying transforms
- Validation: Output validation rules (must_contain, must_not_contain)
- GitCommand: Single git command to run at compile time
- GitIntelligence: Git intelligence configuration (commands, embed_marker)
- WorkflowPatch: Complete patch definition

Transforms are simple instruction strings - natural language instructions
for the LLM to apply to the workflow content.
"""

from pydantic import BaseModel, field_validator


class PatchConfig(BaseModel):
    """Patch metadata configuration.

    Attributes:
        name: Unique identifier for the patch (required).
        version: Patch version string (required).
        author: Patch author (optional).
        description: Human-readable description (optional).

    """

    name: str
    version: str
    author: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        """Validate that name is not empty."""
        if not v or not v.strip():
            raise ValueError("name cannot be empty")
        return v

    @field_validator("version")
    @classmethod
    def version_not_empty(cls, v: str) -> str:
        """Validate that version is not empty."""
        if not v or not v.strip():
            raise ValueError("version cannot be empty")
        return v


class Compatibility(BaseModel):
    """Version compatibility requirements.

    Attributes:
        bmad_version: Required bmad-assist version (exact match).
        workflow: Name of the workflow this patch applies to.

    """

    bmad_version: str
    workflow: str

    @field_validator("bmad_version")
    @classmethod
    def bmad_version_not_empty(cls, v: str) -> str:
        """Validate that bmad_version is not empty."""
        if not v or not v.strip():
            raise ValueError("bmad_version cannot be empty")
        return v

    @field_validator("workflow")
    @classmethod
    def workflow_not_empty(cls, v: str) -> str:
        """Validate that workflow is not empty."""
        if not v or not v.strip():
            raise ValueError("workflow cannot be empty")
        return v


class TransformResult(BaseModel):
    """Result of applying a single transform.

    Attributes:
        success: Whether the transform was applied successfully.
        transform_index: Index of the transform in the transforms list.
        reason: Failure reason if success is False.

    """

    success: bool
    transform_index: int
    reason: str | None = None


class Validation(BaseModel):
    """Output validation rules.

    Validation rules can be substrings (case-sensitive partial match) or
    regex patterns (detected by /pattern/ format).

    Attributes:
        must_contain: List of patterns that must be present in output.
        must_not_contain: List of patterns that must NOT be present in output.

    """

    must_contain: list[str] = []
    must_not_contain: list[str] = []


class GitCommand(BaseModel):
    """Single git command to run at compile time.

    Attributes:
        name: Identifier for this command (e.g., "recent_commits").
        description: Human-readable description of what this command shows.
        command: Git command to execute (e.g., "git log --oneline -5").
            Supports {{variable}} placeholders from resolved_variables.

    """

    name: str
    description: str | None = None
    command: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        """Validate that name is not empty."""
        if not v or not v.strip():
            raise ValueError("name cannot be empty")
        return v

    @field_validator("command")
    @classmethod
    def command_not_empty(cls, v: str) -> str:
        """Validate that command is not empty."""
        if not v or not v.strip():
            raise ValueError("command cannot be empty")
        return v


class PostProcessRule(BaseModel):
    """Single post-processing rule for deterministic text replacement.

    Attributes:
        pattern: Regex pattern to match.
        replacement: Replacement string (can use regex groups).
        flags: Optional regex flags as string (e.g., "MULTILINE", "IGNORECASE").

    """

    pattern: str
    replacement: str
    flags: str = ""

    @field_validator("pattern")
    @classmethod
    def pattern_not_empty(cls, v: str) -> str:
        """Validate that pattern is not empty."""
        if not v or not v.strip():
            raise ValueError("pattern cannot be empty")
        return v


class GitIntelligence(BaseModel):
    """Git intelligence configuration for compile-time extraction.

    When enabled, runs git commands at compile time and embeds results
    in the workflow. This prevents LLM from running expensive git
    archaeology at runtime.

    Attributes:
        enabled: Whether git intelligence is enabled (default True).
        inherit_from: Optional name of git-intelligence config to inherit from
            (e.g., "dev-story", "code-review"). Loads from git-intelligence/{name}.yaml.
        exclude_patterns: Glob patterns to exclude from git commands programmatically.
            These are applied in addition to any exclusions in the git command itself.
        commands: List of git commands to run.
        embed_marker: XML tag name for embedding results (default "git-intelligence").
        no_git_message: Message to embed when git is not initialized.

    """

    enabled: bool = True
    inherit_from: str | None = None
    exclude_patterns: list[str] = []
    commands: list[GitCommand] = []
    embed_marker: str = "git-intelligence"
    no_git_message: str = (
        "This project is not under git version control. "
        "Do NOT attempt to run git commands - they will fail. "
        "Focus on embedded context and file reading instead."
    )


class WorkflowPatch(BaseModel):
    """Complete workflow patch definition.

    Attributes:
        config: Patch metadata (name, version, author, description).
        compatibility: Version requirements (bmad_version, workflow).
        transforms: Ordered list of natural language transform instructions.
        validation: Optional output validation rules.
        git_intelligence: Optional git intelligence configuration.
        post_process: Optional list of deterministic post-processing rules.

    """

    config: PatchConfig
    compatibility: Compatibility
    transforms: list[str]
    validation: Validation | None = None
    git_intelligence: GitIntelligence | None = None
    post_process: list[PostProcessRule] | None = None

    @field_validator("transforms")
    @classmethod
    def transforms_not_empty(cls, v: list[str]) -> list[str]:
        """Validate that transforms list is not empty and has valid instructions."""
        if not v:
            raise ValueError("transforms list cannot be empty")
        for i, instruction in enumerate(v):
            if not instruction or not instruction.strip():
                raise ValueError(f"transform at index {i} cannot be empty")
        return v
