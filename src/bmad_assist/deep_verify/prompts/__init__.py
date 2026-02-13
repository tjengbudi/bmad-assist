"""Deep Verify prompt templates.

Loaded via importlib.resources as package data files.
"""

from importlib import resources

__all__ = ["get_detect_tech_stack_prompt"]


def get_detect_tech_stack_prompt() -> str:
    """Load tech stack detection prompt template.

    Returns:
        Raw XML prompt template with {project_docs} placeholder.

    """
    prompt_file = resources.files("bmad_assist.deep_verify.prompts").joinpath(
        "detect_tech_stack.xml"
    )
    return prompt_file.read_text(encoding="utf-8")
