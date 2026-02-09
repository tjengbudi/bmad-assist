"""Tests for qa/remediate.py — data types, collect_epic_issues, reports, extraction."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from bmad_assist.qa.remediate import (
    CollectionResult,
    EpicIssue,
    EscalationItem,
    REMEDIATE_ESCALATIONS_END,
    REMEDIATE_ESCALATIONS_START,
    collect_epic_issues,
    compare_failure_sets,
    extract_escalations,
    extract_modified_files,
    save_escalation_report,
    save_remediation_report,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class TestEpicIssue:
    def test_basic_fields(self) -> None:
        issue = EpicIssue(source="qa_results", severity="high", description="test fails")
        assert issue.source == "qa_results"
        assert issue.severity == "high"
        assert issue.description == "test fails"
        assert issue.file_path is None
        assert issue.context == ""

    def test_all_fields(self) -> None:
        issue = EpicIssue(
            source="code_review",
            severity="medium",
            description="missing error handling",
            file_path="src/foo.py",
            context="line 42: bare except",
        )
        assert issue.file_path == "src/foo.py"
        assert "line 42" in issue.context


class TestCollectionResult:
    def test_empty_result(self) -> None:
        r = CollectionResult()
        assert r.issues == []
        assert r.sources_checked == 0
        assert r.sources_found == 0
        assert r.stale_sources == []
        assert r.warnings == []


class TestEscalationItem:
    def test_defaults(self) -> None:
        item = EscalationItem(title="Fix auth")
        assert item.title == "Fix auth"
        assert item.source == ""
        assert item.severity == "medium"
        assert item.proposals == []
        assert item.llm_context == ""


# ---------------------------------------------------------------------------
# collect_epic_issues
# ---------------------------------------------------------------------------


class TestCollectEpicIssues:
    def test_no_sources_returns_empty(self, tmp_path: Path) -> None:
        result = collect_epic_issues(epic_id=99, project_path=tmp_path)
        assert result.issues == []
        assert result.sources_checked == 6
        assert result.sources_found == 0

    def test_qa_results_source(self, tmp_path: Path) -> None:
        """Source #1: QA test results YAML."""
        qa_dir = tmp_path / "_bmad-output" / "qa-artifacts" / "test-results"
        qa_dir.mkdir(parents=True)
        data = {
            "tests": [
                {"name": "test_login", "status": "FAIL", "error": "timeout"},
                {"name": "test_home", "status": "PASS"},
                {"name": "test_crash", "status": "ERROR", "error": "segfault"},
            ]
        }
        (qa_dir / "epic-1-run-001.yaml").write_text(yaml.dump(data))

        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        assert result.sources_found >= 1
        qa_issues = [i for i in result.issues if i.source == "qa_results"]
        assert len(qa_issues) == 2
        assert any("test_login" in i.description for i in qa_issues)
        assert any(i.severity == "high" for i in qa_issues)  # ERROR → high

    def test_code_review_synthesis_source(self, tmp_path: Path) -> None:
        """Source #2: Code review synthesis."""
        cr_dir = tmp_path / "_bmad-output" / "implementation-artifacts" / "code-reviews"
        cr_dir.mkdir(parents=True)
        content = (
            "# Synthesis\n\n"
            "## Finding 1 - MUST FIX\n"
            "Missing input validation in auth module.\n\n"
            "## Finding 2 - Nice to Have\n"
            "Consider adding docstrings.\n"
        )
        (cr_dir / "synthesis-1-story-1.md").write_text(content)

        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        cr_issues = [i for i in result.issues if i.source == "code_review"]
        assert len(cr_issues) == 1  # Only MUST FIX

    def test_retro_source(self, tmp_path: Path) -> None:
        """Source #3: Retrospective action items."""
        retro_dir = tmp_path / "_bmad-output" / "implementation-artifacts" / "retrospectives"
        retro_dir.mkdir(parents=True)
        content = (
            "# Epic 1 Retrospective\n\n"
            "## Action Items\n"
            "- [ ] Fix flaky test suite\n"
            "- [x] Already done item\n"
            "- TODO improve error messages\n"
        )
        (retro_dir / "epic-1-retro-2026-02.md").write_text(content)

        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        retro_issues = [i for i in result.issues if i.source == "retro"]
        assert len(retro_issues) == 2  # unchecked checkbox + TODO

    def test_scorecard_source_optional(self, tmp_path: Path) -> None:
        """Source #4: Scorecard — optional, skip if missing."""
        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        sc_issues = [i for i in result.issues if i.source == "scorecard"]
        assert len(sc_issues) == 0

    def test_scorecard_with_todos(self, tmp_path: Path) -> None:
        """Source #4: Scorecard with TODOs."""
        sc_dir = tmp_path / "experiments" / "analysis" / "scorecards"
        sc_dir.mkdir(parents=True)
        data = {"todos": 3, "security": {"findings": ["XSS in /api/search"]}}
        (sc_dir / "fixture-001.yaml").write_text(yaml.dump(data))

        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        sc_issues = [i for i in result.issues if i.source == "scorecard"]
        assert len(sc_issues) == 2  # 1 TODO + 1 security

    def test_validation_source(self, tmp_path: Path) -> None:
        """Source #5: Story validations."""
        val_dir = tmp_path / "_bmad-output" / "implementation-artifacts" / "story-validations"
        val_dir.mkdir(parents=True)
        content = (
            "# Validation\n"
            "- AC1: PASS\n"
            "- AC2: FAIL - missing error handling\n"
            "- AC3: NOT MET - no tests\n"
        )
        (val_dir / "story-1-1-validation.md").write_text(content)

        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        val_issues = [i for i in result.issues if i.source == "validation"]
        assert len(val_issues) == 2  # FAIL + NOT MET

    def test_individual_reviews_only_when_no_synthesis(self, tmp_path: Path) -> None:
        """Source #6: Individual reviews skipped when synthesis exists."""
        cr_dir = tmp_path / "_bmad-output" / "implementation-artifacts" / "code-reviews"
        cr_dir.mkdir(parents=True)
        # Both synthesis and individual exist
        (cr_dir / "synthesis-1-story-1.md").write_text("# Synthesis\n## CRITICAL finding\n")
        (cr_dir / "code-review-1-story-1.md").write_text("# Review\n## CRITICAL bug\n")

        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        ind_issues = [i for i in result.issues if i.source == "review_individual"]
        assert len(ind_issues) == 0  # Synthesis exists, skip individual

    def test_stale_detection(self, tmp_path: Path) -> None:
        """Stale file detection when file is older than max_age_days."""
        retro_dir = tmp_path / "_bmad-output" / "implementation-artifacts" / "retrospectives"
        retro_dir.mkdir(parents=True)
        f = retro_dir / "epic-1-retro.md"
        f.write_text("- [ ] Old action item\n")
        # Make file appear old (30 days ago)
        import os
        old_time = time.time() - (30 * 86400)
        os.utime(f, (old_time, old_time))

        result = collect_epic_issues(epic_id=1, project_path=tmp_path, max_age_days=7)
        assert len(result.stale_sources) > 0
        assert any("retro" in s for s in result.stale_sources)

    def test_individual_reviews_collected_when_no_synthesis(self, tmp_path: Path) -> None:
        """Source #6: Individual reviews ARE collected when no synthesis exists."""
        cr_dir = tmp_path / "_bmad-output" / "implementation-artifacts" / "code-reviews"
        cr_dir.mkdir(parents=True)
        (cr_dir / "code-review-1-story-1.md").write_text(
            "# Review\n## CRITICAL security bug\nSQL injection in login.\n"
        )

        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        ind_issues = [i for i in result.issues if i.source == "review_individual"]
        assert len(ind_issues) >= 1
        assert any("security bug" in i.description for i in ind_issues)

    def test_graceful_failure_on_corrupt_file(self, tmp_path: Path) -> None:
        """Corrupt YAML file doesn't crash the aggregator."""
        qa_dir = tmp_path / "_bmad-output" / "qa-artifacts" / "test-results"
        qa_dir.mkdir(parents=True)
        (qa_dir / "epic-1-run-001.yaml").write_text("{{invalid yaml: [")

        result = collect_epic_issues(epic_id=1, project_path=tmp_path)
        # Should not raise — warnings capture the error
        assert result.sources_checked == 6
        assert len(result.warnings) > 0  # Warning was actually recorded


# ---------------------------------------------------------------------------
# Report persistence
# ---------------------------------------------------------------------------


class TestSaveEscalationReport:
    def test_creates_report(self, tmp_path: Path) -> None:
        items = [
            EscalationItem(
                title="Auth bypass",
                source="code_review",
                severity="high",
                problem="Missing auth check on /api/admin",
                proposals=["Add middleware", "Check JWT"],
                llm_context="src/routes/admin.py:42",
            )
        ]
        path = save_escalation_report(
            escalations=items,
            epic_id=1,
            project_path=tmp_path,
            iteration=1,
            total_issues=5,
            auto_fixed=3,
        )
        assert path.exists()
        content = path.read_text()
        assert "Auth bypass" in content
        assert "code_review" in content
        assert "escalated: 1" in content
        assert "iter1" in path.name  # iteration in filename

    def test_creates_dirs(self, tmp_path: Path) -> None:
        """Report creates parent dirs if missing."""
        path = save_escalation_report(
            escalations=[EscalationItem(title="test")],
            epic_id=99,
            project_path=tmp_path,
            iteration=1,
            total_issues=1,
            auto_fixed=0,
        )
        assert path.exists()
        assert "escalations" in str(path)


class TestSaveRemediationReport:
    def test_creates_report(self, tmp_path: Path) -> None:
        path = save_remediation_report(
            epic_id=1,
            project_path=tmp_path,
            status="partial",
            iterations=2,
            issues_found=10,
            issues_fixed=7,
            issues_escalated=3,
            pass_rate=85.5,
        )
        assert path.exists()
        content = path.read_text()
        assert "partial" in content
        assert "85.5" in content


# ---------------------------------------------------------------------------
# LLM output extraction
# ---------------------------------------------------------------------------


class TestExtractEscalations:
    def test_with_markers(self) -> None:
        output = f"""
Some LLM thinking...

{REMEDIATE_ESCALATIONS_START}
## Escalated Issues
### Issue 1: Auth bypass
**Source:** code_review
**Severity:** high
**Problem:** Missing auth check on /api/admin
**Proposals:**
1. Add middleware
2. Check JWT

```llm-context
src/routes/admin.py:42
```
{REMEDIATE_ESCALATIONS_END}

Done.
"""
        items = extract_escalations(output)
        assert len(items) == 1
        assert items[0].title == "Auth bypass"
        assert items[0].source == "code_review"
        assert items[0].severity == "high"
        assert len(items[0].proposals) == 2
        assert "admin.py" in items[0].llm_context

    def test_no_markers(self) -> None:
        items = extract_escalations("Just some regular output")
        assert items == []

    def test_multiple_issues(self) -> None:
        output = f"""
{REMEDIATE_ESCALATIONS_START}
## Escalated Issues
### Issue 1: Bug A
**Source:** qa_results
**Problem:** Test flaky

### Issue 2: Bug B
**Source:** retro
**Problem:** Missing docs
{REMEDIATE_ESCALATIONS_END}
"""
        items = extract_escalations(output)
        assert len(items) == 2
        assert items[0].title == "Bug A"
        assert items[0].source == "qa_results"
        assert items[1].title == "Bug B"
        assert items[1].source == "retro"

    def test_markers_present_but_empty(self) -> None:
        output = f"""
{REMEDIATE_ESCALATIONS_START}
{REMEDIATE_ESCALATIONS_END}
"""
        items = extract_escalations(output)
        assert items == []

    def test_missing_fields_graceful(self) -> None:
        """Issue with only title, no Source/Severity/Problem — should not crash."""
        output = f"""
{REMEDIATE_ESCALATIONS_START}
### Issue 1: Bare minimum
{REMEDIATE_ESCALATIONS_END}
"""
        items = extract_escalations(output)
        assert len(items) == 1
        assert items[0].title == "Bare minimum"
        assert items[0].source == ""
        assert items[0].severity == "medium"  # default

    def test_start_marker_only(self) -> None:
        output = f"text {REMEDIATE_ESCALATIONS_START} more text"
        items = extract_escalations(output)
        assert items == []


class TestExtractModifiedFiles:
    def test_write_patterns(self) -> None:
        output = """
Wrote to '/home/user/project/src/auth.py'
Updated src/models/user.py
Created 'tests/test_auth.py'
"""
        files = extract_modified_files(output)
        assert any("auth.py" in f for f in files)
        assert any("user.py" in f for f in files)

    def test_path_key_pattern(self) -> None:
        """Second regex pattern: file_path/path key=value."""
        output = """file_path: "src/config/settings.py"\npath='/home/user/Makefile'"""
        files = extract_modified_files(output)
        assert any("settings.py" in f for f in files)

    def test_empty_output(self) -> None:
        assert extract_modified_files("") == set()


class TestCompareFailureSets:
    def test_basic(self) -> None:
        pre = {"test_a", "test_b", "test_c"}
        post = {"test_b", "test_d"}
        fixed, new, remaining = compare_failure_sets(pre, post)
        assert fixed == {"test_a", "test_c"}
        assert new == {"test_d"}
        assert remaining == {"test_b"}

    def test_all_fixed(self) -> None:
        pre = {"test_a", "test_b"}
        post: set[str] = set()
        fixed, new, remaining = compare_failure_sets(pre, post)
        assert fixed == pre
        assert new == set()
        assert remaining == set()
