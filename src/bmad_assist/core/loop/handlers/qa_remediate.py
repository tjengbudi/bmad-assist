"""QA_REMEDIATE phase handler.

Collects epic issues from multiple sources, invokes master LLM to auto-fix
or escalate, then optionally re-tests. Runs as part of epic_teardown.

Uses direct invocation pattern (like qa_plan_generate/execute) — calls
Python functions directly instead of compiling workflow prompts.

NOTE: Like QaPlanGenerateHandler and QaPlanExecuteHandler, this handler
overrides BaseHandler.execute() entirely (direct invocation pattern).
Prompt saving and timing tracking are not applicable for this pattern.
"""

from __future__ import annotations

import logging
from typing import Any

from bmad_assist.core.loop.handlers.base import BaseHandler
from bmad_assist.core.loop.types import PhaseResult
from bmad_assist.core.state import State
from bmad_assist.qa.prompts import get_remediate_prompt
from bmad_assist.qa.remediate import (
    REMEDIATE_ESCALATIONS_END,
    REMEDIATE_ESCALATIONS_START,
    EscalationItem,
    collect_epic_issues,
    extract_escalations,
    extract_modified_files,
    save_escalation_report,
    save_remediation_report,
)

logger = logging.getLogger(__name__)


class QaRemediateHandler(BaseHandler):
    """Handler for QA_REMEDIATE phase.

    Collects issues from QA results, code reviews, retro, scorecard,
    validations. Invokes master LLM with inline triage (AUTO-FIX or
    ESCALATE) instructions. Supports internal fix→re-test loop.

    This phase runs after QA_PLAN_EXECUTE in epic_teardown.
    """

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "qa_remediate"

    def build_context(self, state: State) -> dict[str, Any]:
        """Not used — direct invocation pattern."""
        return {}

    def execute(self, state: State) -> PhaseResult:
        """Execute QA remediation.

        1. Collect issues from all sources (deduplicated across iterations).
        2. If no issues → clean exit.
        3. Build prompt, invoke master LLM (bash/write tools enabled).
        4. Extract escalations, save reports.
        5. Re-test on iteration > 0 with regression detection.

        Args:
            state: Current loop state with epic information.

        Returns:
            PhaseResult with remediation summary.

        """
        if state.current_epic is None:
            return PhaseResult.fail("Cannot remediate: no current epic set")

        epic_id = state.current_epic
        logger.info("Starting QA remediation for epic %s...", epic_id)

        try:
            qa_config = self.config.qa
            max_iterations = qa_config.remediate_max_iterations if qa_config else 2
            max_age = qa_config.remediate_max_age_days if qa_config else 7
            safety_cap = qa_config.remediate_safety_cap if qa_config else 0.8

            fixed_files: set[str] = set()
            all_escalations: list[EscalationItem] = []
            final_pass_rate: float | None = None
            prev_pass_rate: float | None = None
            iterations_run = 0
            total_issues_accumulated = 0
            seen_descriptions: set[str] = set()
            last_esc_path: str | None = None

            for iteration in range(max_iterations):
                iterations_run = iteration + 1
                logger.info("Remediation iteration %d/%d for epic %s", iterations_run, max_iterations, epic_id)

                # 1. Collect issues
                collection = collect_epic_issues(
                    epic_id=epic_id,
                    project_path=self.project_path,
                    max_age_days=max_age,
                )

                if collection.stale_sources:
                    logger.warning("Stale sources detected: %s", collection.stale_sources)

                # Deduplicate: skip issues already seen in previous iterations
                new_issues = [
                    i for i in collection.issues
                    if i.description not in seen_descriptions
                ]
                for i in new_issues:
                    seen_descriptions.add(i.description)

                if not new_issues:
                    logger.info("No new issues found — epic %s is clean.", epic_id)
                    if iterations_run == 1:
                        return PhaseResult.ok({
                            "status": "clean",
                            "report_path": None,
                            "escalation_path": None,
                            "iterations": iterations_run,
                            "issues_found": 0,
                            "issues_fixed": 0,
                            "issues_escalated": 0,
                            "retest_pass_rate": 100.0,
                        })
                    break  # Clean after a fix iteration

                total_issues_accumulated += len(new_issues)

                # 2. Build prompt + invoke LLM
                prompt = self._build_remediate_prompt(
                    new_issues, fixed_files, epic_id, safety_cap,
                )
                result = self.invoke_provider(prompt)

                # 3. Track modified files
                new_fixed = extract_modified_files(result.stdout)
                refixed = new_fixed & fixed_files
                if refixed:
                    logger.warning("Files re-fixed (will escalate): %s", refixed)
                fixed_files |= new_fixed

                # 4. Extract escalations
                escalations = extract_escalations(result.stdout)
                all_escalations.extend(escalations)

                if escalations:
                    esc_path = save_escalation_report(
                        escalations=escalations,
                        epic_id=epic_id,
                        project_path=self.project_path,
                        iteration=iterations_run,
                        total_issues=total_issues_accumulated,
                        auto_fixed=total_issues_accumulated - len(all_escalations),
                    )
                    last_esc_path = str(esc_path)
                    logger.info("Escalation report: %s", esc_path)

                # 5. Re-test (if qa executor available, skip on first iteration)
                pass_rate = self._run_retest(epic_id, iteration)

                if pass_rate is not None:
                    # Regression detection: pass rate should not drop between iterations
                    if prev_pass_rate is not None and pass_rate < prev_pass_rate:
                        logger.warning(
                            "Regression detected: pass rate dropped %.1f%% → %.1f%%",
                            prev_pass_rate,
                            pass_rate,
                        )
                    prev_pass_rate = pass_rate
                    final_pass_rate = pass_rate

                    if pass_rate >= 99.99:
                        logger.info("All tests pass after iteration %d — done.", iterations_run)
                        break

            # Compute fix estimate: total issues minus escalated
            issues_fixed_estimate = max(0, total_issues_accumulated - len(all_escalations))

            # Determine status
            if not all_escalations and (final_pass_rate is None or final_pass_rate >= 99.99):
                status = "clean"
            elif all_escalations:
                status = "escalated"
            else:
                status = "partial"

            report_path = save_remediation_report(
                epic_id=epic_id,
                project_path=self.project_path,
                status=status,
                iterations=iterations_run,
                issues_found=total_issues_accumulated,
                issues_fixed=issues_fixed_estimate,
                issues_escalated=len(all_escalations),
                pass_rate=final_pass_rate if final_pass_rate is not None else 0.0,
            )

            return PhaseResult.ok({
                "status": status,
                "report_path": str(report_path),
                "escalation_path": last_esc_path,
                "iterations": iterations_run,
                "issues_found": total_issues_accumulated,
                "issues_fixed": issues_fixed_estimate,
                "issues_escalated": len(all_escalations),
                "retest_pass_rate": final_pass_rate if final_pass_rate is not None else 0.0,
            })

        except Exception as e:
            logger.error("QA remediation failed for epic %s: %s", epic_id, e, exc_info=True)
            return PhaseResult.fail(f"QA remediation error: {e}")

    def _run_retest(
        self,
        epic_id: Any,
        iteration: int,
    ) -> float | None:
        """Attempt re-test using existing QA executor.

        Returns pass rate as float, or None if retest was skipped/unavailable.
        """
        if iteration == 0:
            # First iteration: don't re-test, let next iteration collect fresh issues
            return None

        try:
            from bmad_assist.qa.executor import execute_qa_plan

            retest_result = execute_qa_plan(
                config=self.config,
                project_path=self.project_path,
                epic_id=epic_id,
                retry=True,
                batch_mode="batch",
            )
            return retest_result.pass_rate
        except Exception as e:
            logger.warning("Re-test failed (continuing): %s", e)
            return None

    def _build_remediate_prompt(
        self,
        issues: list[Any],
        fixed_files: set[str],
        epic_id: Any,
        safety_cap: float,
    ) -> str:
        """Build the LLM prompt for remediation.

        Loads the XML template from qa/prompts/remediate.xml and populates
        dynamic sections (issues, fixed files, escalation markers).

        Args:
            issues: List of EpicIssue to fix.
            fixed_files: Files already modified (exclude from fixes).
            epic_id: Epic identifier.
            safety_cap: Max fraction of issues that can be AUTO-FIX.

        Returns:
            Prompt string.

        """
        # Build issues XML block
        issue_lines: list[str] = []
        for i, issue in enumerate(issues, 1):
            issue_lines.append(f'    <issue n="{i}" source="{issue.source}" severity="{issue.severity}">')
            issue_lines.append(f"      <description>{issue.description}</description>")
            if issue.file_path:
                issue_lines.append(f"      <file>{issue.file_path}</file>")
            if issue.context:
                truncated = issue.context[:2000]
                issue_lines.append(f"      <context>{truncated}</context>")
            issue_lines.append("    </issue>")
        issues_xml = "\n".join(issue_lines)

        # Build fixed files section (or empty string)
        if fixed_files:
            ff_lines = ["  <already_fixed_files>"]
            for fp in sorted(fixed_files):
                ff_lines.append(f"    <file>{fp}</file>")
            ff_lines.append("  </already_fixed_files>")
            fixed_files_section = "\n".join(ff_lines)
        else:
            fixed_files_section = ""

        template = get_remediate_prompt()
        return template.format(
            epic_id=epic_id,
            issues_count=len(issues),
            safety_cap_pct=round(safety_cap * 100),
            escalation_start=REMEDIATE_ESCALATIONS_START,
            escalation_end=REMEDIATE_ESCALATIONS_END,
            fixed_files_section=fixed_files_section,
            issues_xml=issues_xml,
        )
