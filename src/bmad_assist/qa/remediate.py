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
# Source #2: Code review synthesis
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
# Source #3: Retrospective
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
# Source #4: Scorecard (optional)
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
# Source #5: Story validations
# ---------------------------------------------------------------------------


def _collect_from_validation(
    epic_id: EpicId,
    project_path: Path,
    max_age_days: int,
    result: CollectionResult,
) -> list[EpicIssue]:
    """Scan validation reports for unmet AC / requirements."""
    val_dir = project_path / "_bmad-output" / "implementation-artifacts" / "story-validations"
    if not val_dir.exists():
        return []

    # Use broad glob then post-filter to ensure epic_id is a complete segment
    # (avoids matching epic 11 when looking for epic 1)
    pattern = f"*{epic_id}-*.md"
    epic_id_str = str(epic_id)
    files = [
        f for f in sorted(val_dir.glob(pattern))
        if re.search(rf"(?:^|[-_]){re.escape(epic_id_str)}(?:[-_])", f.name)
    ]
    if not files:
        return []

    issues: list[EpicIssue] = []
    fail_re = re.compile(r"\b(FAIL|NOT\s+MET|MISSING|REJECTED)\b", re.IGNORECASE)

    for f in files:
        _check_freshness(f, "validation", max_age_days, result)
        content = f.read_text(encoding="utf-8")
        for line in content.split("\n"):
            if fail_re.search(line):
                issues.append(
                    EpicIssue(
                        source="validation",
                        severity="high",
                        description=line.strip()[:200],
                        file_path=str(f),
                        context=line.strip(),
                    )
                )

    return issues


# ---------------------------------------------------------------------------
# Source #6: Individual code reviews (only if no synthesis)
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
