"""QA remediation — collect epic issues and produce escalation/remediation reports.

Provides:
- EpicIssue / CollectionResult / EscalationItem dataclasses
- collect_epic_issues() — aggregates issues from 6 sources
- save_escalation_report() / save_remediation_report() — persist reports
- extract_escalations() — parse LLM output for escalation markers
- extract_modified_files() — parse file paths from LLM tool output
- compare_failure_sets() — regression detection helper
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from bmad_assist.core.io import atomic_write
from bmad_assist.core.types import EpicId

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


@dataclass
class EpicIssue:
    """Single issue collected from an epic's artifact sources.

    Attributes:
        source: Origin identifier (qa_results, code_review, retro, scorecard,
                validation, review_individual).
        severity: high / medium / low.
        description: Human-readable issue description.
        file_path: Affected file if known.
        context: Raw content for LLM (stack traces, finding text, etc.).

    """

    source: str
    severity: str
    description: str
    file_path: str | None = None
    context: str = ""


@dataclass
class CollectionResult:
    """Aggregated result from collect_epic_issues().

    Attributes:
        issues: All collected issues across sources.
        sources_checked: Number of sources attempted.
        sources_found: Number of sources that returned at least one issue.
        stale_sources: Source labels whose files exceed max_age_days.
        warnings: Non-fatal diagnostic messages.

    """

    issues: list[EpicIssue] = field(default_factory=list)
    sources_checked: int = 0
    sources_found: int = 0
    stale_sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class EscalationItem:
    """Parsed from LLM output between REMEDIATE_ESCALATIONS markers.

    Attributes:
        title: Issue title from ### heading.
        source: Source tag from **Source:** line.
        severity: From **Severity:** line.
        problem: From **Problem:** section.
        proposals: From **Proposals:** numbered list.
        llm_context: From ``llm-context`` code block.

    """

    title: str
    source: str = ""
    severity: str = "medium"
    problem: str = ""
    proposals: list[str] = field(default_factory=list)
    llm_context: str = ""


# ---------------------------------------------------------------------------
# LLM output markers
# ---------------------------------------------------------------------------

REMEDIATE_ESCALATIONS_START = "<!-- REMEDIATE_ESCALATIONS_START -->"
REMEDIATE_ESCALATIONS_END = "<!-- REMEDIATE_ESCALATIONS_END -->"


# ---------------------------------------------------------------------------
# collect_epic_issues — aggregator
# ---------------------------------------------------------------------------


def collect_epic_issues(
    epic_id: EpicId,
    project_path: Path,
    *,
    max_age_days: int = 7,
) -> CollectionResult:
    """Collect issues from all epic artifact sources.

    Each source is independently try/excepted so a single corrupt file
    never crashes the entire collection.

    Args:
        epic_id: Epic identifier (int or str).
        project_path: Project root path.
        max_age_days: Warn on sources older than this.

    Returns:
        CollectionResult with aggregated issues.

    """
    result = CollectionResult()
    collectors = [
        ("qa_results", _collect_from_qa_results),
        ("deep_verify", _collect_from_deep_verify),
        ("security", _collect_from_security),
        ("code_review", _collect_from_code_review_synthesis),
        ("retro", _collect_from_retro),
        ("scorecard", _collect_from_scorecard),
        ("validation", _collect_from_validation),
        ("review_individual", _collect_from_individual_reviews),
    ]

    for label, fn in collectors:
        result.sources_checked += 1
        try:
            issues = fn(epic_id, project_path, max_age_days, result)
            if issues:
                result.issues.extend(issues)
                result.sources_found += 1
        except Exception as exc:
            msg = f"Source '{label}' collection failed: {exc}"
            logger.warning(msg)
            result.warnings.append(msg)

    return result


# ---------------------------------------------------------------------------
# Source #1: QA test results
# ---------------------------------------------------------------------------


def _collect_from_qa_results(
    epic_id: EpicId,
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Load failed tests from QA result YAML files."""
    qa_dir = project_path / "_bmad-output" / "qa-artifacts" / "test-results"
    if not qa_dir.exists():
        return []

    pattern = f"epic-{epic_id}-run-*.yaml"
    files = sorted(qa_dir.glob(pattern))
    if not files:
        return []

    # Use latest run
    latest = files[-1]
    _check_freshness(latest, "qa_results", max_age_days, result)

    data = yaml.safe_load(latest.read_text(encoding="utf-8"))
    if not data:
        return []

    issues: list[EpicIssue] = []
    tests = data if isinstance(data, list) else data.get("tests", data.get("results", []))
    if not isinstance(tests, list):
        return []

    for t in tests:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status", "")).upper()
        if status in ("PASS", "PASSED", "OK", "SKIP", "SKIPPED"):
            continue
        desc = t.get("name", t.get("test", "unknown test"))
        ctx = t.get("error", t.get("stack_trace", t.get("output", "")))
        file_p = t.get("file", t.get("file_path", None))
        issues.append(
            EpicIssue(
                source="qa_results",
                severity="high" if status in ("ERROR", "CRASH") else "medium",
                description=str(desc),
                file_path=str(file_p) if file_p else None,
                context=str(ctx)[:4000],
            )
        )

    return issues


# ---------------------------------------------------------------------------
# Source #2: Deep Verify reports
# ---------------------------------------------------------------------------

# DV findings severity: CRITICAL → high, ERROR → high, WARNING → medium
_DV_SEVERITY_MAP = {"critical": "high", "error": "high", "warning": "medium"}

# DV findings table row: | F1 | CRITICAL | Title text... | domain | #method |
_DV_FINDING_ROW = re.compile(
    r"^\|\s*F\d+\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|",
    re.MULTILINE,
)

# DV detailed finding header: #### F1: Title
_DV_DETAIL_HEADER = re.compile(
    r"^####\s+F\d+:\s+(.+)", re.MULTILINE
)


def _collect_from_deep_verify(
    epic_id: EpicId,
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Extract findings from Deep Verify reports.

    Parses the findings table (ID, Severity, Title, Domain, Method)
    and optionally enriches with detailed evidence sections.
    """
    dv_dir = project_path / "_bmad-output" / "implementation-artifacts" / "deep-verify"
    if not dv_dir.exists():
        return []

    pattern = f"deep-verify-{epic_id}-*-*.md"
    files = sorted(dv_dir.glob(pattern))
    if not files:
        return []

    issues: list[EpicIssue] = []
    seen_titles: set[str] = set()

    for f in files:
        _check_freshness(f, "deep_verify", max_age_days, result)
        content = f.read_text(encoding="utf-8")

        # Build detail lookup: title → (analysis + evidence block)
        detail_map: dict[str, str] = {}
        detail_blocks = re.split(r"\n(?=####\s+F\d+:)", content)
        for block in detail_blocks:
            hdr = _DV_DETAIL_HEADER.match(block)
            if hdr:
                detail_map[hdr.group(1).strip().lower()] = block.strip()[:4000]

        # Parse findings table rows
        for m in _DV_FINDING_ROW.finditer(content):
            raw_sev = m.group(1).strip().lower()
            title = m.group(2).strip()
            domain = m.group(3).strip()

            # Deduplicate across DV reports (same title from different phases)
            dedup_key = title.lower()
            if dedup_key in seen_titles:
                continue
            seen_titles.add(dedup_key)

            severity = _DV_SEVERITY_MAP.get(raw_sev, "medium")
            context = detail_map.get(dedup_key, "")

            desc = title[:200]
            if domain and domain != "-":
                desc = f"[{domain}] {desc}"

            issues.append(
                EpicIssue(
                    source="deep_verify",
                    severity=severity,
                    description=desc,
                    file_path=str(f),
                    context=context,
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Source #3: Security reports
# ---------------------------------------------------------------------------


def _collect_from_security(
    epic_id: EpicId,
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Extract findings from security review reports.

    Parses YAML frontmatter for total_findings. If > 0, extracts
    finding sections from markdown body.
    """
    sec_dir = project_path / "_bmad-output" / "implementation-artifacts" / "security-reports"
    if not sec_dir.exists():
        return []

    pattern = f"security-{epic_id}-*.md"
    files = sorted(sec_dir.glob(pattern))
    if not files:
        return []

    issues: list[EpicIssue] = []
    finding_header_re = re.compile(
        r"^##\s+(?:CWE-\d+|Finding\s*\d*|Issue\s*\d*):\s*(.+)", re.MULTILINE
    )

    for f in files:
        _check_freshness(f, "security", max_age_days, result)
        content = f.read_text(encoding="utf-8")

        # Quick check: skip if total_findings: 0 in frontmatter
        fm_match = re.search(r"total_findings:\s*(\d+)", content)
        if fm_match and int(fm_match.group(1)) == 0:
            continue

        # Extract finding sections
        for block in re.split(r"\n(?=##\s)", content):
            m = finding_header_re.match(block)
            if m:
                title = m.group(1).strip()
                issues.append(
                    EpicIssue(
                        source="security",
                        severity="high",
                        description=f"Security: {title[:200]}",
                        file_path=str(f),
                        context=block.strip()[:4000],
                    )
                )

    return issues


# ---------------------------------------------------------------------------
# Source #4: Code review synthesis
# ---------------------------------------------------------------------------


def _collect_from_code_review_synthesis(
    epic_id: EpicId,
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Extract unresolved findings from code review synthesis reports."""
    cr_dir = project_path / "_bmad-output" / "implementation-artifacts" / "code-reviews"
    if not cr_dir.exists():
        return []

    pattern = f"synthesis-{epic_id}-*-*.md"
    files = sorted(cr_dir.glob(pattern))
    if not files:
        return []

    latest = files[-1]
    _check_freshness(latest, "code_review", max_age_days, result)
    content = latest.read_text(encoding="utf-8")

    issues: list[EpicIssue] = []
    severity_re = re.compile(
        r"(?:MUST\s+FIX|CRITICAL|HIGH|SEVERE)", re.IGNORECASE
    )
    # Split by headings and look for findings sections
    for block in re.split(r"\n(?=##?\s)", content):
        if severity_re.search(block):
            lines = block.strip().split("\n")
            title = lines[0].lstrip("#").strip() if lines else "Finding"
            sev = "high" if re.search(r"CRITICAL|SEVERE", block, re.I) else "medium"
            issues.append(
                EpicIssue(
                    source="code_review",
                    severity=sev,
                    description=title[:200],
                    context=block[:4000],
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Source #5: Retrospective
# ---------------------------------------------------------------------------


def _collect_from_retro(
    epic_id: EpicId,
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Extract action items from retrospective reports."""
    retro_dir = project_path / "_bmad-output" / "implementation-artifacts" / "retrospectives"
    if not retro_dir.exists():
        return []

    pattern = f"epic-{epic_id}-retro*.md"
    files = sorted(retro_dir.glob(pattern))
    if not files:
        return []

    latest = files[-1]
    _check_freshness(latest, "retro", max_age_days, result)
    content = latest.read_text(encoding="utf-8")

    issues: list[EpicIssue] = []
    action_re = re.compile(
        r"^(?:\s*-\s*\[\s*\]\s*|"  # - [ ] unchecked checkbox
        r"\s*-\s*TODO\b|"  # - TODO items
        r"\s*Action:\s*)"  # Action: prefix
        r"(.+)",
        re.MULTILINE | re.IGNORECASE,
    )
    for m in action_re.finditer(content):
        desc = m.group(1).strip()
        if desc:
            issues.append(
                EpicIssue(
                    source="retro",
                    severity="medium",
                    description=desc[:200],
                    context=desc,
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Source #6: Scorecard (optional)
# ---------------------------------------------------------------------------


def _collect_from_scorecard(
    epic_id: EpicId,  # noqa: ARG001 — scorecards are not per-epic
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Read scorecard YAML for TODOs, security findings, correctness proxies.

    NOTE: Scorecards are project-wide, not epic-specific. The latest
    scorecard is used regardless of which epic is being remediated.
    """
    sc_dir = project_path / "experiments" / "analysis" / "scorecards"
    if not sc_dir.exists():
        return []

    files = sorted(sc_dir.glob("*.yaml"))
    if not files:
        return []

    latest = files[-1]
    _check_freshness(latest, "scorecard", max_age_days, result)
    data = yaml.safe_load(latest.read_text(encoding="utf-8"))
    if not data or not isinstance(data, dict):
        return []

    issues: list[EpicIssue] = []

    # TODOs
    todos = data.get("todos", data.get("todo_count", 0))
    if isinstance(todos, int) and todos > 0:
        issues.append(
            EpicIssue(
                source="scorecard",
                severity="low",
                description=f"Scorecard: {todos} TODO(s) remaining",
                context=f"TODO count: {todos}",
            )
        )

    # Security
    security = data.get("security", {})
    if isinstance(security, dict):
        findings = security.get("findings", [])
        if isinstance(findings, list):
            for f in findings:
                desc = f if isinstance(f, str) else str(f.get("description", f))
                issues.append(
                    EpicIssue(
                        source="scorecard",
                        severity="high",
                        description=f"Security: {desc[:200]}",
                        context=str(f)[:2000],
                    )
                )

    return issues


# ---------------------------------------------------------------------------
# Source #7: Story validations (prefer synthesis over individuals)
# ---------------------------------------------------------------------------

# Validation finding patterns — only match structured findings, not random text
_VALIDATION_BULLET_RE = re.compile(
    r"^\s*-\s+\*\*(.+?)\*\*[:\s](.+)",
    re.MULTILINE,
)


def _collect_from_validation(
    epic_id: EpicId,
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Scan validation reports for unmet AC / requirements.

    Prefers synthesis reports (curated, deduplicated) over individual
    validator outputs. Falls back to individual files only when no
    synthesis exists.
    """
    val_dir = project_path / "_bmad-output" / "implementation-artifacts" / "story-validations"
    if not val_dir.exists():
        return []

    epic_id_str = str(epic_id)
    epic_filter = re.compile(rf"(?:^|[-_]){re.escape(epic_id_str)}(?:[-_])")

    # Prefer synthesis reports
    synthesis_pattern = f"synthesis-{epic_id}-*.md"
    synthesis_files = [
        f for f in sorted(val_dir.glob(synthesis_pattern))
        if epic_filter.search(f.name)
    ]

    if synthesis_files:
        return _collect_validation_from_synthesis(
            synthesis_files, max_age_days, result,
        )

    # Fallback: individual validation reports
    pattern = f"*{epic_id}-*.md"
    files = [
        f for f in sorted(val_dir.glob(pattern))
        if epic_filter.search(f.name) and not f.name.startswith("synthesis-")
    ]
    if not files:
        return []

    return _collect_validation_from_individuals(files, max_age_days, result)


def _collect_validation_from_synthesis(
    files: list[Path],
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Extract structured findings from validation synthesis reports.

    Synthesis reports have nested structure:
      ## Issues Verified (by severity)
        ### Critical
          - **Title**: Description
        ### High
          - **Title**: Description
    """
    issues: list[EpicIssue] = []

    for f in files:
        _check_freshness(f, "validation", max_age_days, result)
        content = f.read_text(encoding="utf-8")

        # Find the "Issues Verified" section
        verified_match = re.search(
            r"^##\s+Issues?\s+Verified.*$",
            content, re.MULTILINE | re.IGNORECASE,
        )
        if not verified_match:
            continue

        # Get content from "Issues Verified" to next ## section
        rest = content[verified_match.end():]
        next_h2 = re.search(r"^##\s+(?!#)", rest, re.MULTILINE)
        verified_section = rest[:next_h2.start()] if next_h2 else rest

        # Split by ### sub-headings (severity levels)
        sub_sections = re.split(r"\n(?=###\s)", verified_section)
        for sub in sub_sections:
            sub_header = sub.split("\n", 1)[0].strip()

            # Determine severity from sub-header
            if re.search(r"Critical|High", sub_header, re.IGNORECASE):
                sev = "high"
            elif re.search(r"Medium", sub_header, re.IGNORECASE):
                sev = "medium"
            else:
                sev = "low"

            # Extract structured bullet findings: - **Title**: description
            for m in _VALIDATION_BULLET_RE.finditer(sub):
                title = m.group(1).strip()
                desc = m.group(2).strip()
                issues.append(
                    EpicIssue(
                        source="validation",
                        severity=sev,
                        description=f"{title}: {desc}"[:200],
                        file_path=str(f),
                        context=m.group(0).strip()[:4000],
                    )
                )

    return issues


def _collect_validation_from_individuals(
    files: list[Path],
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Fallback: extract findings from individual validation reports."""
    issues: list[EpicIssue] = []
    fail_re = re.compile(r"\b(FAIL|NOT\s+MET|MISSING|REJECTED)\b", re.IGNORECASE)

    for f in files:
        _check_freshness(f, "validation", max_age_days, result)
        content = f.read_text(encoding="utf-8")
        for line in content.split("\n"):
            if fail_re.search(line):
                stripped = line.strip()
                # Skip table header/separator lines and short noise
                if stripped.startswith("|--") or len(stripped) < 20:
                    continue
                issues.append(
                    EpicIssue(
                        source="validation",
                        severity="high",
                        description=stripped[:200],
                        file_path=str(f),
                        context=stripped,
                    )
                )

    return issues


# ---------------------------------------------------------------------------
# Source #8: Individual code reviews (only if no synthesis)
# ---------------------------------------------------------------------------


def _collect_from_individual_reviews(
    epic_id: EpicId,
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Load severe findings from individual review files (skip if synthesis exists)."""
    cr_dir = project_path / "_bmad-output" / "implementation-artifacts" / "code-reviews"
    if not cr_dir.exists():
        return []

    # Skip individual reviews if synthesis collector already found issues
    if any(i.source == "code_review" for i in result.issues):
        return []

    pattern = f"code-review-{epic_id}-*.md"
    files = sorted(cr_dir.glob(pattern))
    if not files:
        return []

    issues: list[EpicIssue] = []
    severe_re = re.compile(r"\b(CRITICAL|HIGH|SEVERE)\b", re.IGNORECASE)

    for f in files:
        _check_freshness(f, "review_individual", max_age_days, result)
        content = f.read_text(encoding="utf-8")
        for block in re.split(r"\n(?=##?\s)", content):
            if severe_re.search(block):
                lines = block.strip().split("\n")
                title = lines[0].lstrip("#").strip() if lines else "Finding"
                issues.append(
                    EpicIssue(
                        source="review_individual",
                        severity="high",
                        description=title[:200],
                        context=block[:4000],
                    )
                )

    return issues


# ---------------------------------------------------------------------------
# Freshness helper
# ---------------------------------------------------------------------------


def _check_freshness(
    path: Path,
    label: str,
    max_age_days: int,
    result: CollectionResult,
) -> None:
    """Add stale_sources warning if file is older than max_age_days."""
    try:
        mtime = path.stat().st_mtime
        age_days = (time.time() - mtime) / 86400
        if age_days > max_age_days:
            entry = f"{label}:{path.name}"
            if entry not in result.stale_sources:
                result.stale_sources.append(entry)
    except OSError as exc:
        logger.debug("Cannot check freshness for %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Report persistence
# ---------------------------------------------------------------------------


def save_escalation_report(
    escalations: list[EscalationItem],
    epic_id: EpicId,
    project_path: Path,
    iteration: int,
    total_issues: int,
    auto_fixed: int,
) -> Path:
    """Write escalation report as markdown with YAML frontmatter.

    Args:
        escalations: Parsed escalation items.
        epic_id: Epic identifier.
        project_path: Project root.
        iteration: Current iteration (1-based).
        total_issues: Total issues found.
        auto_fixed: Number auto-fixed.

    Returns:
        Path to written report.

    """
    esc_dir = project_path / "_bmad-output" / "qa-artifacts" / "escalations"
    esc_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%d-%H%M%S")
    filename = f"epic-{epic_id}-escalation-iter{iteration}-{ts}.md"
    report_path = esc_dir / filename

    # Use repr for string epic_ids to ensure valid YAML
    epic_val = epic_id if isinstance(epic_id, int) else f"'{epic_id}'"
    lines = [
        "---",
        f"epic: {epic_val}",
        f"generated_at: '{now.isoformat()}'",
        "handler: qa_remediate",
        f"iteration: {iteration}",
        f"total_issues: {total_issues}",
        f"auto_fixed: {auto_fixed}",
        f"escalated: {len(escalations)}",
        "---",
        "",
        f"# Escalation Report - Epic {epic_id}",
        "",
        "## Summary",
        f"- Issues found: {total_issues} | Auto-fixed: {auto_fixed} | Escalated: {len(escalations)}",
        "",
        "## Escalated Issues",
        "",
    ]

    for i, esc in enumerate(escalations, 1):
        lines.append(f"### {i}. {esc.title}")
        if esc.source:
            lines.append(f"**Source:** {esc.source}")
        if esc.severity:
            lines.append(f"**Severity:** {esc.severity}")
        if esc.problem:
            lines.append(f"**Problem:** {esc.problem}")
        if esc.proposals:
            lines.append("**Proposals:**")
            for j, prop in enumerate(esc.proposals, 1):
                lines.append(f"{j}. {prop}")
        if esc.llm_context:
            lines.append("")
            lines.append("```llm-context")
            lines.append(esc.llm_context)
            lines.append("```")
        lines.append("")

    atomic_write(report_path, "\n".join(lines))
    logger.info("Escalation report saved: %s", report_path)
    return report_path


def save_remediation_report(
    epic_id: EpicId,
    project_path: Path,
    status: str,
    iterations: int,
    issues_found: int,
    issues_fixed: int,
    issues_escalated: int,
    pass_rate: float,
) -> Path:
    """Write final remediation summary report.

    Args:
        epic_id: Epic identifier.
        project_path: Project root.
        status: "clean" | "partial" | "escalated".
        iterations: Number of fix cycles run.
        issues_found: Total issues.
        issues_fixed: Issues auto-fixed.
        issues_escalated: Issues escalated.
        pass_rate: Final test pass rate (0-100).

    Returns:
        Path to written report.

    """
    rem_dir = project_path / "_bmad-output" / "qa-artifacts" / "remediation"
    rem_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%d-%H%M%S")
    filename = f"epic-{epic_id}-remediation-{ts}.md"
    report_path = rem_dir / filename

    # Use repr for string epic_ids to ensure valid YAML
    epic_val = epic_id if isinstance(epic_id, int) else f"'{epic_id}'"
    lines = [
        "---",
        f"epic: {epic_val}",
        f"generated_at: '{now.isoformat()}'",
        "handler: qa_remediate",
        f"status: '{status}'",
        f"iterations: {iterations}",
        f"issues_found: {issues_found}",
        f"issues_fixed: {issues_fixed}",
        f"issues_escalated: {issues_escalated}",
        f"pass_rate: {pass_rate:.1f}",
        "---",
        "",
        f"# Remediation Report - Epic {epic_id}",
        "",
        "## Summary",
        f"- **Status:** {status}",
        f"- **Iterations:** {iterations}",
        f"- **Issues found:** {issues_found}",
        f"- **Auto-fixed:** {issues_fixed}",
        f"- **Escalated:** {issues_escalated}",
        f"- **Final pass rate:** {pass_rate:.1f}%",
        "",
    ]

    atomic_write(report_path, "\n".join(lines))
    logger.info("Remediation report saved: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# LLM output extraction
# ---------------------------------------------------------------------------


def extract_escalations(llm_output: str) -> list[EscalationItem]:
    """Parse escalation items from LLM output between markers.

    Args:
        llm_output: Full LLM stdout.

    Returns:
        List of parsed EscalationItem. Empty if no markers found.

    """
    start_idx = llm_output.find(REMEDIATE_ESCALATIONS_START)
    end_idx = llm_output.find(REMEDIATE_ESCALATIONS_END)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return []

    section = llm_output[start_idx + len(REMEDIATE_ESCALATIONS_START):end_idx].strip()
    if not section:
        return []

    items: list[EscalationItem] = []

    # Split by ### headings
    issue_blocks = re.split(r"\n(?=###\s)", section)
    for block in issue_blocks:
        block = block.strip()
        if not block.startswith("###"):
            continue

        # Title from ### line
        first_line, _, rest = block.partition("\n")
        title = re.sub(r"^###\s*(?:Issue\s*\d+:\s*)?", "", first_line).strip()

        # Parse fields
        source_m = re.search(r"\*\*Source:\*\*\s*(.+)", rest)
        severity_m = re.search(r"\*\*Severity:\*\*\s*(.+)", rest)
        problem_m = re.search(r"\*\*Problem:\*\*\s*(.+)", rest)

        # Proposals
        proposals: list[str] = []
        prop_match = re.search(r"\*\*Proposals:\*\*\s*\n((?:\d+\..+\n?)+)", rest)
        if prop_match:
            for line in prop_match.group(1).strip().split("\n"):
                cleaned = re.sub(r"^\d+\.\s*", "", line.strip())
                if cleaned:
                    proposals.append(cleaned)

        # llm-context code block
        ctx_match = re.search(r"```llm-context\s*\n(.*?)```", rest, re.DOTALL)
        llm_ctx = ctx_match.group(1).strip() if ctx_match else ""

        items.append(
            EscalationItem(
                title=title,
                source=source_m.group(1).strip() if source_m else "",
                severity=severity_m.group(1).strip() if severity_m else "medium",
                problem=problem_m.group(1).strip() if problem_m else "",
                proposals=proposals,
                llm_context=llm_ctx,
            )
        )

    return items


def extract_modified_files(llm_output: str) -> set[str]:
    """Parse file paths modified by LLM from tool output patterns.

    Looks for common patterns in LLM tool use output:
    - Write tool: wrote to /path/to/file
    - Edit tool: edited /path/to/file
    - Bash tool: common write patterns

    Args:
        llm_output: Full LLM stdout.

    Returns:
        Set of file paths that were modified.

    """
    paths: set[str] = set()

    # Write/Edit tool patterns (allow extensionless files like Makefile, Dockerfile)
    patterns = [
        r"(?:Wrote|Writing|Created|Updated|Edited)\s+(?:to\s+)?['\"]?([^\s'\"]+(?:\.\w+)?)",
        r"(?:file_path|path)['\"]?\s*[:=]\s*['\"]([^\s'\"]+(?:\.\w+)?)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, llm_output, re.IGNORECASE):
            p = m.group(1)
            if "/" in p or "\\" in p:
                paths.add(p)

    return paths


def compare_failure_sets(
    pre: set[str],
    post: set[str],
) -> tuple[set[str], set[str], set[str]]:
    """Compare pre-fix and post-fix failure sets for regression detection.

    Args:
        pre: Failure descriptions before fix.
        post: Failure descriptions after fix.

    Returns:
        Tuple of (fixed, new, remaining) sets.

    """
    fixed = pre - post
    new = post - pre
    remaining = pre & post
    return fixed, new, remaining


def _apply_issue_limit(
    issues: list[EpicIssue],
    max_issues: int,
) -> list[EpicIssue]:
    """Truncate issues to max_issues, preserving high-severity first.

    Args:
        issues: List of issues to truncate.
        max_issues: Maximum number of issues to return.

    Returns:
        Truncated list, sorted by severity (high → medium → low).

    """
    if len(issues) <= max_issues:
        return issues

    severity_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        issues,
        key=lambda i: severity_order.get(i.severity, 99)
    )[:max_issues]
