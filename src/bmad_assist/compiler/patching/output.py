"""Output generator for compiled workflow templates.

This module handles generating the final template output with
metadata headers for compiled workflows.

Classes:
    TemplateMetadata: Metadata to include in template header

Functions:
    generate_template: Generate template with metadata header
"""

from dataclasses import dataclass


@dataclass
class TemplateMetadata:
    """Metadata to include in template header.

    Attributes:
        workflow: Name of the source workflow.
        patch_name: Name of the patch applied.
        patch_version: Version of the patch.
        bmad_version: Version of bmad_assist.
        compiled_at: ISO 8601 timestamp of compilation.
        source_hash: SHA-256 hash of source files.
        defaults_hash: Combined SHA-256 of defaults files, or None.
        is_markdown: Whether the output is markdown (vs XML).

    """

    workflow: str
    patch_name: str
    patch_version: str
    bmad_version: str
    compiled_at: str
    source_hash: str
    defaults_hash: str | None = None
    is_markdown: bool = False


def generate_template(content: str, metadata: TemplateMetadata) -> str:
    """Generate template with metadata header.

    Creates a header containing compilation metadata (XML comment for XML,
    markdown comment for markdown), followed by the compiled workflow content.

    Args:
        content: The compiled workflow content.
        metadata: Metadata to include in header.

    Returns:
        Complete template with header and content.

    """
    # Build header content lines
    content_lines = [
        f"Compiled from: {metadata.workflow} workflow",
        f"Patch: {metadata.patch_name} v{metadata.patch_version}",
        f"BMAD: {metadata.bmad_version}",
        f"Compiled at: {metadata.compiled_at}",
        f"Source hash: {metadata.source_hash}",
    ]
    if metadata.defaults_hash is not None:
        content_lines.append(f"Defaults hash: {metadata.defaults_hash}")

    # Format as markdown or XML comment based on output type
    if metadata.is_markdown:
        # Markdown: use HTML comment that renders invisibly
        header_lines = ["<!--"] + [f"  {line}" for line in content_lines] + ["-->"]
    else:
        # XML: use XML comment
        header_lines = ["<!--"] + [f"  {line}" for line in content_lines] + ["-->"]

    header = "\n".join(header_lines)

    # Combine header and content
    return f"{header}\n{content}"
