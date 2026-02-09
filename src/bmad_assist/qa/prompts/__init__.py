"""Package resources for QA prompts.

This package contains prompt templates for the QA module.
These are bundled as package resources and loaded via importlib.resources.

Available prompts:
- remediate.xml: QA remediation prompt template (fix/escalate workflow)

Available functions:
- get_remediate_prompt(): Load remediate prompt template
"""

from importlib import resources

__all__ = ["get_remediate_prompt"]


def get_remediate_prompt() -> str:
    """Load QA remediation prompt template from package resources.

    The returned template contains placeholders that must be substituted
    by the caller:
    - {epic_id}: Epic identifier
    - {issues_count}: Number of issues
    - {safety_cap_pct}: Safety cap percentage (integer)
    - {escalation_start}: Escalation marker start
    - {escalation_end}: Escalation marker end
    - {fixed_files_section}: XML block for already-fixed files (or empty)
    - {issues_xml}: XML block with individual issues

    Returns:
        Raw XML prompt template string with placeholders.

    Raises:
        FileNotFoundError: If prompt file or package is missing.

    """
    try:
        prompt_file = resources.files("bmad_assist.qa.prompts").joinpath(
            "remediate.xml"
        )
        return prompt_file.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as e:
        raise FileNotFoundError(
            "QA remediate prompt not found. "
            "This may indicate a broken installation. "
            "Please reinstall bmad-assist."
        ) from e
