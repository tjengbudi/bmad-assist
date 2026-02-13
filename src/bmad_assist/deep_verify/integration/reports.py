"""Deep Verify report generation for integration hooks.

Story 26.16: Validate Story Integration Hook (AC-5)

This module provides functions to generate and save Deep Verify
findings reports in markdown format.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bmad_assist.deep_verify.core.types import (
        DeepVerifyValidationResult,
        Finding,
    )

logger = logging.getLogger(__name__)


def save_deep_verify_report(
    result: DeepVerifyValidationResult,
    epic: int | str,
    story: int | str,
    validations_dir: Path | None = None,
    *,
    output_dir: Path | None = None,
    phase_type: str | None = None,
) -> Path:
    """Save Deep Verify findings as markdown report.

    Creates a markdown report with:
    - Verdict and score header
    - Domains detected with confidence
    - Methods executed
    - Findings table (ID, Severity, Title, Domain, Method)
    - Detailed findings with evidence quotes

    Args:
        result: DeepVerifyValidationResult from verification.
        epic: Epic number or name.
        story: Story number or name.
        validations_dir: DEPRECATED - use output_dir instead.
        output_dir: Directory to save the report (deep-verify/).
        phase_type: Phase identifier (e.g. "story-validation", "code-review").
            When set, included in filename and report metadata.

    Returns:
        Path to the saved report file.

    Example:
        >>> report_path = save_deep_verify_report(
        ...     result=dv_result,
        ...     epic=26,
        ...     story=16,
        ...     output_dir=Path("deep-verify"),
        ...     phase_type="story-validation",
        ... )
        >>> print(f"Report saved to: {report_path}")

    """
    # Support both old and new parameter names during migration
    target_dir = output_dir or validations_dir
    if target_dir is None:
        raise ValueError("output_dir is required")
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    if phase_type:
        filename = f"deep-verify-{epic}-{story}-{phase_type}-{timestamp}.md"
    else:
        filename = f"deep-verify-{epic}-{story}-{timestamp}.md"
    report_path = target_dir / filename

    content = _format_report_content(result, epic, story, phase_type=phase_type)

    # Atomic write
    temp_path = report_path.with_suffix(".tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.rename(report_path)
        logger.info("Saved Deep Verify report: %s", report_path)
    except OSError as e:
        logger.warning("Failed to save Deep Verify report: %s", e)
        raise

    return report_path


def save_deep_verify_batch_report(
    batch_results: dict[Path, DeepVerifyValidationResult],
    epic: int | str,
    story: int | str,
    output_dir: Path,
    *,
    phase_type: str | None = None,
) -> Path:
    """Save consolidated Deep Verify batch report (one file for all results).

    Produces a single markdown report with:
    - Overall verdict summary (worst across all files)
    - Per-file verdict table
    - Findings grouped by file with details

    Args:
        batch_results: Dict mapping file_path to DeepVerifyValidationResult.
        epic: Epic number or name.
        story: Story number or name.
        output_dir: Directory to save the report.
        phase_type: Phase identifier (e.g. "remediation", "code-review").

    Returns:
        Path to the saved report file.

    """
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    if phase_type:
        filename = f"deep-verify-{epic}-{story}-{phase_type}-{timestamp}.md"
    else:
        filename = f"deep-verify-{epic}-{story}-{timestamp}.md"
    report_path = output_dir / filename

    content = _format_batch_report_content(
        batch_results, epic, story, phase_type=phase_type,
    )

    temp_path = report_path.with_suffix(".tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.rename(report_path)
        logger.info("Saved Deep Verify report: %s", report_path)
    except OSError as e:
        logger.warning("Failed to save Deep Verify report: %s", e)
        raise

    return report_path


def _format_batch_report_content(
    batch_results: dict[Path, DeepVerifyValidationResult],
    epic: int | str,
    story: int | str,
    *,
    phase_type: str | None = None,
) -> str:
    """Format consolidated batch report content."""
    from bmad_assist.deep_verify.core.types import VerdictDecision

    # Compute overall stats
    total_findings = sum(len(r.findings) for r in batch_results.values())
    total_duration = sum(r.duration_ms for r in batch_results.values())

    worst_verdict: VerdictDecision | None = None
    min_score = 100.0
    for result in batch_results.values():
        min_score = min(min_score, result.score)
        if worst_verdict is None:
            worst_verdict = result.verdict
        elif result.verdict == VerdictDecision.REJECT:
            worst_verdict = VerdictDecision.REJECT
        elif (
            result.verdict == VerdictDecision.UNCERTAIN
            and worst_verdict == VerdictDecision.ACCEPT
        ):
            worst_verdict = VerdictDecision.UNCERTAIN

    if worst_verdict is None:
        worst_verdict = VerdictDecision.ACCEPT

    lines = [
        "# Deep Verify Batch Report",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"**Overall Verdict:** {worst_verdict.value}",
        f"**Score:** {min_score:.1f}",
        f"**Duration:** {total_duration / 1000:.1f}s",
        f"**Total Findings:** {total_findings}",
        f"**Files Analyzed:** {len(batch_results)}",
        "",
        "---",
        "",
        "## Metadata",
        "",
        f"- **Epic:** {epic}",
        f"- **Story:** {story}",
        *([ f"- **Phase:** {phase_type}"] if phase_type else []),
        f"- **Generated:** {datetime.now(UTC).isoformat()}",
        "",
        "---",
        "",
        "## Per-File Verdicts",
        "",
        "| File | Verdict | Score | Findings |",
        "|---|---|---|---|",
    ]

    for file_path, result in batch_results.items():
        lines.append(
            f"| `{file_path.name}` | {result.verdict.value} "
            f"| {result.score:.1f} | {len(result.findings)} |"
        )

    # Per-file findings sections
    for file_path, result in batch_results.items():
        if not result.findings:
            continue

        lines.extend([
            "",
            "---",
            "",
            f"## {file_path.name}",
            "",
            f"**Verdict:** {result.verdict.value} | "
            f"**Score:** {result.score:.1f} | "
            f"**Findings:** {len(result.findings)}",
            "",
        ])

        lines.append(_format_findings_table(result.findings))

        lines.extend(["", "### Details", ""])
        for finding in result.findings:
            lines.append(_format_finding_detail(finding))
            lines.append("")

    # Files with no findings summary
    clean_files = [
        fp.name for fp, r in batch_results.items() if not r.findings
    ]
    if clean_files:
        lines.extend(["", "---", "", "## Clean Files", ""])
        for name in clean_files:
            lines.append(f"- `{name}`")
        lines.append("")

    return "\n".join(lines)


def _format_report_content(
    result: DeepVerifyValidationResult,
    epic: int | str,
    story: int | str,
    *,
    phase_type: str | None = None,
) -> str:
    """Format the markdown report content.

    Args:
        result: DeepVerifyValidationResult.
        epic: Epic identifier.
        story: Story identifier.
        phase_type: Phase identifier for metadata (e.g. "story-validation").

    Returns:
        Formatted markdown string.

    """
    lines = [
        "# Deep Verify Report",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"**Verdict:** {result.verdict.value}",
        f"**Score:** {result.score:.1f}",
        f"**Duration:** {result.duration_ms / 1000:.1f}s",
        f"**Findings:** {len(result.findings)}",
        "",
        "---",
        "",
        "## Metadata",
        "",
        f"- **Epic:** {epic}",
        f"- **Story:** {story}",
        *([ f"- **Phase:** {phase_type}"] if phase_type else []),
        f"- **Generated:** {datetime.now(UTC).isoformat()}",
        "",
        "---",
        "",
        "## Domains Detected",
        "",
    ]

    if result.domains_detected:
        lines.append("| Domain | Confidence | Signals |")
        lines.append("|---|---|---|")
        for dc in result.domains_detected:
            signals_str = ", ".join(dc.signals[:5]) if dc.signals else "-"
            lines.append(f"| {dc.domain.value} | {dc.confidence:.2f} | {signals_str} |")
    else:
        lines.append("No domains detected.")

    lines.extend(
        [
            "",
            "---",
            "",
            "## Methods Executed",
            "",
        ]
    )

    if result.methods_executed:
        for method_id in result.methods_executed:
            lines.append(f"- {method_id}")
    else:
        lines.append("No methods executed.")

    lines.extend(
        [
            "",
            "---",
            "",
            "## Findings",
            "",
        ]
    )

    # Findings table
    lines.append(_format_findings_table(result.findings))

    # Detailed findings
    if result.findings:
        lines.extend(
            [
                "",
                "### Details",
                "",
            ]
        )
        for finding in result.findings:
            lines.append(_format_finding_detail(finding))
            lines.append("")

    # Error section (if any)
    if result.error:
        lines.extend(
            [
                "---",
                "",
                "## Error",
                "",
                f"**Error:** {result.error}",
                "",
            ]
        )

    return "\n".join(lines)


def _format_findings_table(findings: list[Finding]) -> str:
    """Format findings as markdown table.

    Args:
        findings: List of findings.

    Returns:
        Markdown table string.

    """
    if not findings:
        return "No findings reported.\n"

    lines = [
        "| ID | Severity | Title | Domain | Method |",
        "|---|---|---|---|---|",
    ]

    for finding in findings:
        domain = finding.domain.value if finding.domain else "-"
        lines.append(
            f"| {finding.id} | {finding.severity.value.upper()} | "
            f"{finding.title} | {domain} | {finding.method_id} |"
        )

    return "\n".join(lines)


def _format_finding_detail(finding: Finding) -> str:
    """Format single finding detail section.

    Args:
        finding: Finding to format.

    Returns:
        Markdown formatted finding detail.

    """
    lines = [
        f"#### {finding.id}: {finding.title}",
        "",
        f"**Severity:** {finding.severity.value.upper()}",
    ]

    if finding.domain:
        lines.append(f"**Domain:** {finding.domain.value}")

    lines.extend(
        [
            f"**Method:** {finding.method_id}",
            "",
            f"{finding.description}",
            "",
        ]
    )

    if finding.pattern_id:
        lines.append(f"**Pattern:** {finding.pattern_id}")
        lines.append("")

    # Evidence
    if finding.evidence:
        lines.append("**Evidence:**")
        lines.append("")
        for evidence in finding.evidence:
            if evidence.quote:
                lines.append(f"> {evidence.quote}")
                if evidence.line_number:
                    lines.append(f"> *Line {evidence.line_number}*")
                lines.append("")

    return "\n".join(lines)
