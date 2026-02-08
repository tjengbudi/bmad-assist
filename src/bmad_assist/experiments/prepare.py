"""Benchmark data preparer for LLM analysis.

This module provides the BenchmarkPreparer class for condensing benchmark data
from both traditional single-project runs and experiment runs into compact
JSON summaries suitable for LLM analysis.

Supports two modes:
- project: Traditional single-project consolidation (backward compatible)
- experiments: Multi-fixture experiment consolidation

Usage:
    from bmad_assist.experiments import BenchmarkPreparer, PrepareResult

    # Project mode (backward compat)
    preparer = BenchmarkPreparer(Path("./my-project"), mode="project")
    result = preparer.prepare_project()

    # Experiments mode
    preparer = BenchmarkPreparer(Path("./my-project"), mode="experiments")
    results = preparer.prepare_experiments()

"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median, stdev
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
)

from bmad_assist.core.exceptions import ConfigError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "PrepareResult",
    "RunData",
    "BenchmarkPreparer",
]


def _atomic_write_text(path: Path, content: str) -> None:
    """Write content to file atomically using temp file + rename.

    Args:
        path: Target file path.
        content: Content to write.

    """
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


# =============================================================================
# Data Models
# =============================================================================


class PrepareResult(BaseModel):
    """Result of benchmark preparation.

    Attributes:
        fixture_or_project: Fixture name or project directory name.
        output_path: Path to generated summary file.
        runs_processed: Number of runs/stories processed.
        evals_count: Total evaluation records.
        total_time_minutes: Aggregated time in minutes.
        models: Models encountered.
        generated_at: Generation timestamp.

    """

    model_config = ConfigDict(frozen=True)

    fixture_or_project: str = Field(..., description="Fixture name or project name")
    output_path: Path = Field(..., description="Path to generated summary file")
    runs_processed: int = Field(..., description="Number of runs/stories processed")
    evals_count: int = Field(..., description="Total evaluation records")
    total_time_minutes: float = Field(..., description="Aggregated time in minutes")
    models: list[str] = Field(..., description="Models encountered")
    generated_at: datetime = Field(..., description="Generation timestamp")

    @field_serializer("output_path")
    def serialize_path(self, path: Path) -> str:
        """Serialize path to string."""
        return str(path)

    @field_serializer("generated_at")
    def serialize_datetime(self, dt: datetime) -> str:
        """Serialize datetime to ISO 8601 with UTC timezone."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()


class RunData(BaseModel):
    """Extracted data from an experiment run.

    Attributes:
        run_id: Run identifier.
        fixture: Fixture name from manifest.
        manifest_path: Path to manifest.yaml.
        metrics_path: Path to metrics.yaml (optional).
        benchmark_files: Benchmark YAML paths.
        mapping_files: Validation-mapping JSON paths.
        code_review_syntheses: Code review synthesis paths.
        validation_syntheses: Validation synthesis paths.

    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(..., description="Run identifier")
    fixture: str = Field(..., description="Fixture name from manifest")
    manifest_path: Path = Field(..., description="Path to manifest.yaml")
    metrics_path: Path | None = Field(None, description="Path to metrics.yaml")
    benchmark_files: list[Path] = Field(default_factory=list, description="Benchmark YAML paths")
    mapping_files: list[Path] = Field(
        default_factory=list, description="Validation-mapping JSON paths"
    )
    code_review_syntheses: list[Path] = Field(
        default_factory=list, description="Code review synthesis paths"
    )
    validation_syntheses: list[Path] = Field(
        default_factory=list, description="Validation synthesis paths"
    )

    @field_serializer("manifest_path", "metrics_path")
    def serialize_path(self, path: Path | None) -> str | None:
        """Serialize path to string."""
        return str(path) if path else None

    @field_serializer(
        "benchmark_files", "mapping_files", "code_review_syntheses", "validation_syntheses"
    )
    def serialize_path_list(self, paths: list[Path]) -> list[str]:
        """Serialize path list to string list."""
        return [str(p) for p in paths]


# =============================================================================
# Helper Functions (ported from benchmark-prepare.py)
# =============================================================================


def _find_project_files(
    project_path: Path,
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """Find benchmark YAML files, validation-mapping JSON files, and synthesis MD files.

    Args:
        project_path: Root path of the project.

    Returns:
        Tuple of (benchmark_yamls, mapping_jsons, code_review_syntheses, validation_syntheses).

    """
    impl_artifacts = project_path / "_bmad-output" / "implementation-artifacts"
    benchmarks_dir = impl_artifacts / "benchmarks"
    cache_dir = project_path / ".bmad-assist" / "cache"
    code_reviews_dir = impl_artifacts / "code-reviews"
    story_validations_dir = impl_artifacts / "story-validations"

    benchmark_yamls = list(benchmarks_dir.glob("**/*.yaml")) if benchmarks_dir.exists() else []
    mapping_jsons = list(cache_dir.glob("validation-mapping-*.json")) if cache_dir.exists() else []
    code_review_syntheses = (
        list(code_reviews_dir.glob("synthesis-*.md")) if code_reviews_dir.exists() else []
    )
    validation_syntheses = (
        list(story_validations_dir.glob("synthesis-*.md")) if story_validations_dir.exists() else []
    )

    return benchmark_yamls, mapping_jsons, code_review_syntheses, validation_syntheses


def _load_validation_mappings(mapping_files: list[Path]) -> dict[str, dict[str, Any]]:
    """Load all validation mapping files and index by session_id.

    Args:
        mapping_files: List of validation-mapping JSON file paths.

    Returns:
        Dict mapping session_id to full mapping data.

    """
    mappings: dict[str, dict[str, Any]] = {}

    for path in mapping_files:
        try:
            with open(path) as f:
                data = json.load(f)
            session_id = data.get("session_id")
            if session_id:
                mappings[session_id] = data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load %s: %s", path, e)

    return mappings


def _resolve_model_name(mapping_entry: dict[str, Any]) -> str:
    """Extract model display name from mapping entry.

    Priority: model_name (if available) > provider string parsing > model field.

    Args:
        mapping_entry: Single validator mapping entry.

    Returns:
        Human-readable model name.

    """
    provider = mapping_entry.get("provider", "")
    model = mapping_entry.get("model", "unknown")

    if "-" in provider:
        parts = provider.split("-")
        if parts[0] in ("claude", "gemini", "codex", "master"):
            if parts[0] == "claude" and len(parts) > 1 and parts[1] == "subprocess":
                return "-".join(parts[2:]) if len(parts) > 2 else model
            else:
                return "-".join(parts[1:])

    return str(model)


def _build_model_lookup(mappings: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Build lookup from (session_id, role_id) to model name.

    Args:
        mappings: Dict of session_id -> mapping data.

    Returns:
        Dict mapping "session_id:role_id" to model info.

    """
    lookup: dict[str, dict[str, str]] = {}

    for _session_id, data in mappings.items():
        mapping = data.get("mapping", {})
        for validator_name, entry in mapping.items():
            if " " in validator_name:
                role_id = validator_name.split()[-1].lower()
            else:
                role_id = validator_name.lower()
            provider_session = entry.get("provider_session_id", "")

            key = f"{provider_session}"
            lookup[key] = {
                "model": _resolve_model_name(entry),
                "provider": entry.get("provider", ""),
                "role_id": role_id,
            }

    return lookup


def _load_benchmark_record(path: Path) -> dict[str, Any] | None:
    """Load a single benchmark YAML file.

    Args:
        path: Path to YAML file.

    Returns:
        Parsed YAML data or None on error or if not a valid eval record.

    """
    if path.name == "index.yaml" or not path.name.startswith("eval-"):
        return None

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if not data or not data.get("evaluator") or not data.get("story"):
            return None
        return dict(data)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("Failed to load %s: %s", path, e)
        return None


def _extract_essential_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Extract only essential fields from a benchmark record.

    Args:
        record: Full benchmark record.

    Returns:
        Condensed metrics dict.

    """
    evaluator = record.get("evaluator", {})
    execution = record.get("execution", {})
    findings = record.get("findings") or {}
    result: dict[str, Any] = {
        "role": evaluator.get("role", "unknown"),
        "role_id": evaluator.get("role_id"),
        "session_id": evaluator.get("session_id"),
        "provider": evaluator.get("provider"),
        "model": evaluator.get("model"),
        "dur_ms": execution.get("duration_ms", 0),
        "tokens": execution.get("output_tokens", 0),
    }

    if findings:
        result["findings"] = {
            "total": findings.get("total_count", 0),
            "by_sev": findings.get("by_severity", {}),
        }

    return result


def _process_benchmarks(
    benchmark_files: list[Path],
    model_lookup: dict[str, dict[str, str]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, list[int]]]:
    """Process all benchmark files and group by story.

    Args:
        benchmark_files: List of YAML file paths.
        model_lookup: Lookup from session_id to model info.

    Returns:
        Tuple of (stories_data, models_data, phases_data).

    """
    stories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    models_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    phases_raw: dict[str, list[int]] = defaultdict(list)

    for path in benchmark_files:
        record = _load_benchmark_record(path)
        if not record:
            continue

        story_info = record.get("story", {})
        epic = story_info.get("epic_num", 0)
        story_num = story_info.get("story_num", 0)
        story_key = f"{epic}-{story_num}"

        workflow = record.get("workflow", {})
        phase_id = workflow.get("id", "unknown")

        metrics = _extract_essential_metrics(record)

        if metrics.get("dur_ms"):
            phases_raw[phase_id].append(metrics["dur_ms"])

        session_id = metrics.get("session_id", "")
        if session_id in model_lookup:
            metrics["model_resolved"] = model_lookup[session_id]["model"]
        else:
            metrics["model_resolved"] = metrics.get("model", "unknown")

        stories[story_key].append(metrics)

        model_name = metrics["model_resolved"]
        models_raw[model_name].append(metrics)

    return dict(stories), dict(models_raw), dict(phases_raw)


def _calculate_model_aggregates(
    models_raw: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Calculate aggregate statistics per model.

    Args:
        models_raw: Dict of model_name -> list of metrics.

    Returns:
        Dict of model_name -> aggregate stats.

    """
    aggregates: dict[str, dict[str, Any]] = {}

    for model_name, records in models_raw.items():
        durations = [r["dur_ms"] for r in records if r.get("dur_ms")]
        tokens = [r["tokens"] for r in records if r.get("tokens")]

        quality_records = [r for r in records if r.get("quality")]
        actionable = [
            r["quality"]["actionable"] for r in quality_records if r["quality"].get("actionable")
        ]
        specificity = [
            r["quality"]["specificity"] for r in quality_records if r["quality"].get("specificity")
        ]

        findings_records = [r for r in records if r.get("findings")]
        total_findings = sum(r["findings"]["total"] for r in findings_records)
        severity_totals: dict[str, int] = defaultdict(int)
        for r in findings_records:
            for sev, count in r["findings"].get("by_sev", {}).items():
                severity_totals[sev] += count

        agg: dict[str, Any] = {
            "evals": len(records),
            "dur_avg": int(mean(durations)) if durations else 0,
            "dur_median": int(median(durations)) if durations else 0,
            "dur_std": int(stdev(durations)) if len(durations) > 1 else 0,
            "tokens_total": sum(tokens),
            "tokens_avg": int(mean(tokens)) if tokens else 0,
        }

        if actionable:
            agg["quality_avg"] = {
                "actionable": round(mean(actionable), 2),
                "specificity": round(mean(specificity), 2) if specificity else None,
            }

        if findings_records:
            agg["findings_total"] = total_findings
            agg["findings_by_sev"] = dict(severity_totals)

            if sum(tokens) > 0:
                agg["efficiency"] = round(total_findings / (sum(tokens) / 1000), 2)

        aggregates[model_name] = agg

    return aggregates


def _calculate_rankings(model_aggs: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Calculate model rankings by different criteria.

    Args:
        model_aggs: Model aggregate statistics.

    Returns:
        Dict of ranking_name -> ordered list of model names.

    """
    rankings: dict[str, list[str]] = {}

    speed_data = [(m, a["dur_avg"]) for m, a in model_aggs.items() if a.get("dur_avg")]
    if speed_data:
        rankings["speed"] = [m for m, _ in sorted(speed_data, key=lambda x: x[1])]

    quality_data = [
        (m, a["quality_avg"]["actionable"])
        for m, a in model_aggs.items()
        if a.get("quality_avg") and a["quality_avg"].get("actionable")
    ]
    if quality_data:
        rankings["quality"] = [m for m, _ in sorted(quality_data, key=lambda x: x[1], reverse=True)]

    eff_data = [(m, a["efficiency"]) for m, a in model_aggs.items() if a.get("efficiency")]
    if eff_data:
        rankings["efficiency"] = [m for m, _ in sorted(eff_data, key=lambda x: x[1], reverse=True)]

    findings_data = [
        (m, a["findings_total"]) for m, a in model_aggs.items() if a.get("findings_total")
    ]
    if findings_data:
        rankings["thoroughness"] = [
            m for m, _ in sorted(findings_data, key=lambda x: x[1], reverse=True)
        ]

    return rankings


def _calculate_phase_aggregates(phases_raw: dict[str, list[int]]) -> dict[str, dict[str, int]]:
    """Calculate aggregate statistics per workflow phase.

    Args:
        phases_raw: Dict of phase_id -> list of durations in ms.

    Returns:
        Dict of phase_id -> aggregate stats.

    """
    aggregates: dict[str, dict[str, Any]] = {}

    for phase_id, durations in phases_raw.items():
        if not durations:
            continue

        total_ms = sum(durations)
        agg: dict[str, Any] = {
            "count": len(durations),
            "total_ms": total_ms,
            "total_min": round(total_ms / 60000, 1),
            "avg_ms": int(mean(durations)),
            "median_ms": int(median(durations)),
            "min_ms": min(durations),
            "max_ms": max(durations),
        }

        if len(durations) > 1:
            agg["std_ms"] = int(stdev(durations))

        aggregates[phase_id] = agg

    return aggregates


def _calculate_correlations(models_raw: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
    """Calculate correlations between metrics.

    Args:
        models_raw: Raw model metrics data.

    Returns:
        Dict of correlation_name -> correlation coefficient.

    """
    all_records = []
    for records in models_raw.values():
        all_records.extend(records)

    dur_quality_pairs = [
        (r["dur_ms"], r["quality"]["actionable"])
        for r in all_records
        if r.get("dur_ms") and r.get("quality") and r["quality"].get("actionable")
    ]

    tokens_findings_pairs = [
        (r["tokens"], r["findings"]["total"])
        for r in all_records
        if r.get("tokens") and r.get("findings") and r["findings"].get("total")
    ]

    correlations: dict[str, float] = {}

    if len(dur_quality_pairs) > 2:
        correlations["duration_vs_quality"] = _pearson(
            [p[0] for p in dur_quality_pairs], [p[1] for p in dur_quality_pairs]
        )

    if len(tokens_findings_pairs) > 2:
        correlations["tokens_vs_findings"] = _pearson(
            [p[0] for p in tokens_findings_pairs], [p[1] for p in tokens_findings_pairs]
        )

    return correlations


def _pearson(x: list[float], y: list[float]) -> float:
    """Calculate Pearson correlation coefficient.

    Args:
        x: First variable values.
        y: Second variable values.

    Returns:
        Correlation coefficient (-1 to 1).

    """
    n = len(x)
    if n < 2:
        return 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y, strict=True))

    sum_sq_x = sum((xi - mean_x) ** 2 for xi in x)
    sum_sq_y = sum((yi - mean_y) ** 2 for yi in y)

    denominator = (sum_sq_x * sum_sq_y) ** 0.5

    if denominator == 0:
        return 0.0

    return float(round(numerator / denominator, 3))


def _condense_story_data(story_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Condense multiple records for a single story.

    Args:
        story_records: List of all eval records for one story.

    Returns:
        Condensed story summary.

    """
    validators = {}
    synthesis = None

    for r in story_records:
        role = r.get("role", "")
        model = r.get("model_resolved", "unknown")

        condensed = {
            "model": model,
            "dur": r.get("dur_ms", 0),
            "tokens": r.get("tokens", 0),
        }

        if r.get("quality"):
            condensed["q"] = r["quality"].get("actionable")

        if r.get("findings"):
            condensed["findings"] = r["findings"].get("total", 0)
            condensed["sev"] = r["findings"].get("by_sev", {})

        if role == "validator":
            role_id = r.get("role_id", "?")
            validators[role_id] = condensed
        elif role == "synthesizer":
            if r.get("consensus"):
                condensed["consensus"] = r["consensus"]
            synthesis = condensed

    result: dict[str, Any] = {}
    if validators:
        result["v"] = validators
    if synthesis:
        result["s"] = synthesis

    return result


def _build_summary(
    project_path: Path,
    stories: dict[str, list[dict[str, Any]]],
    model_aggs: dict[str, dict[str, Any]],
    phase_aggs: dict[str, dict[str, int]],
    rankings: dict[str, list[str]],
    correlations: dict[str, float],
) -> dict[str, Any]:
    """Build the final summary structure.

    Args:
        project_path: Project root path.
        stories: Per-story metrics data.
        model_aggs: Per-model aggregate stats.
        phase_aggs: Per-phase timing stats.
        rankings: Model rankings.
        correlations: Metric correlations.

    Returns:
        Complete summary dict.

    """
    total_evals = sum(a["evals"] for a in model_aggs.values())
    total_time_ms = sum(p["total_ms"] for p in phase_aggs.values())

    summary: dict[str, Any] = {
        "meta": {
            "project": project_path.name,
            "stories": len(stories),
            "evals": total_evals,
            "total_time_min": round(total_time_ms / 60000, 1),
            "models": list(model_aggs.keys()),
        },
        "phases": phase_aggs,
        "models": model_aggs,
        "stories": {k: _condense_story_data(v) for k, v in sorted(stories.items())},
        "rankings": rankings,
    }

    if correlations:
        summary["correlations"] = correlations

    return summary


# =============================================================================
# Synthesis File Processing
# =============================================================================


def _parse_synthesis_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from synthesis markdown.

    Args:
        content: Full markdown content.

    Returns:
        Tuple of (frontmatter_dict, body_content).

    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        frontmatter = {}

    return frontmatter, parts[2].strip()


def _extract_section(content: str, header: str) -> str | None:
    """Extract content of a markdown section.

    Args:
        content: Markdown content.
        header: Section header (e.g., "## Synthesis Summary").

    Returns:
        Section content or None if not found.

    """
    pattern = rf"^{re.escape(header)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def _clean_issues_verified(section: str) -> list[dict[str, Any]]:
    """Parse and clean Issues Verified section.

    Args:
        section: Raw section content.

    Returns:
        List of {severity, issue} dicts.

    """
    issues: list[dict[str, Any]] = []
    current_severity = "unknown"

    for line in section.split("\n"):
        line = line.strip()

        if line.startswith("### "):
            current_severity = line[4:].strip().lower()
            continue

        if line.startswith("No ") and "issues" in line:
            continue

        if line.startswith("- **Issue**:"):
            issue_match = re.match(r"- \*\*Issue\*\*:\s*(.+?)(?:\s*\||\s*$)", line)
            if issue_match:
                issues.append(
                    {
                        "severity": current_severity,
                        "issue": issue_match.group(1).strip(),
                    }
                )

    return issues


def _clean_issues_dismissed(section: str) -> list[dict[str, Any]]:
    """Parse and clean Issues Dismissed section.

    Args:
        section: Raw section content.

    Returns:
        List of {issue, raised_by} dicts.

    """
    issues: list[dict[str, Any]] = []

    for line in section.split("\n"):
        line = line.strip()

        if line.startswith("- **Claimed Issue**:"):
            issue_match = re.search(r"\*\*Claimed Issue\*\*:\s*(.+?)\s*\|", line)
            raised_match = re.search(r"\*\*Raised by\*\*:\s*(.+?)(?:\s*\||\s*$)", line)

            if issue_match:
                entry: dict[str, Any] = {"issue": issue_match.group(1).strip()}
                if raised_match:
                    entry["raised_by"] = raised_match.group(1).strip()
                issues.append(entry)

    return issues


def _parse_quality_table(section: str) -> list[dict[str, str]]:
    """Parse Validations Quality markdown table.

    Args:
        section: Raw section content.

    Returns:
        List of {reviewer, score, assessment} dicts.

    """
    rows: list[dict[str, str]] = []

    for line in section.split("\n"):
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        if "Reviewer" in line or ("Validator" in line and "Score" in line):
            continue

        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]

        if len(parts) >= 3:
            rows.append(
                {
                    "reviewer": parts[0],
                    "score": parts[1],
                    "assessment": parts[2],
                }
            )

    return rows


def _process_synthesis_file(path: Path) -> dict[str, Any] | None:
    """Process a single synthesis markdown file.

    Args:
        path: Path to synthesis-{epic}-{story}.md file.

    Returns:
        Condensed synthesis dict or None on error.

    """
    try:
        content = path.read_text()
    except OSError as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None

    frontmatter, body = _parse_synthesis_frontmatter(content)

    epic = frontmatter.get("epic")
    story = frontmatter.get("story")

    if epic is None or story is None:
        match = re.match(r"synthesis-(\d+)-(\d+)\.md", path.name)
        if match:
            epic = int(match.group(1))
            story = int(match.group(2))
        else:
            return None

    result: dict[str, Any] = {
        "story": story,
    }

    summary = _extract_section(body, "## Synthesis Summary")
    if summary:
        result["summary"] = summary

    quality_section = _extract_section(body, "## Validations Quality")
    if quality_section:
        quality = _parse_quality_table(quality_section)
        if quality:
            result["quality"] = quality

    verified_section = _extract_section(body, "## Issues Verified (by severity)")
    if not verified_section:
        verified_section = _extract_section(body, "## Issues Verified")
    if verified_section:
        verified = _clean_issues_verified(verified_section)
        if verified:
            result["verified"] = verified

    dismissed_section = _extract_section(body, "## Issues Dismissed")
    if dismissed_section:
        dismissed = _clean_issues_dismissed(dismissed_section)
        if dismissed:
            result["dismissed"] = dismissed

    return {"epic": epic, "data": result}


def _process_all_syntheses(synthesis_files: list[Path]) -> dict[int, list[dict[str, Any]]]:
    """Process all synthesis files and group by epic.

    Args:
        synthesis_files: List of synthesis markdown file paths.

    Returns:
        Dict mapping epic_num to list of condensed story syntheses.

    """
    by_epic: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for path in synthesis_files:
        result = _process_synthesis_file(path)
        if result:
            epic = result["epic"]
            by_epic[epic].append(result["data"])

    for epic in by_epic:
        by_epic[epic].sort(key=lambda x: x.get("story", 0))

    return dict(by_epic)


def _extract_deterministic_metrics(content: str) -> dict[str, Any] | None:
    """Extract deterministic metrics section from validation synthesis.

    Args:
        content: Markdown content.

    Returns:
        Dict with parsed metrics or None if not found.

    """
    start_marker = "<!-- DETERMINISTIC_METRICS_START -->"
    end_marker = "<!-- DETERMINISTIC_METRICS_END -->"

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        return None

    section = content[start_idx + len(start_marker) : end_idx].strip()

    metrics: dict[str, Any] = {}

    summary_match = re.search(
        r"### Summary\s*\n\s*\|[^\n]+\n\s*\|[-|\s]+\n((?:\s*\|[^\n]+\n)+)",
        section,
    )
    if summary_match:
        summary = {}
        for line in summary_match.group(1).strip().split("\n"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                key = parts[0].lower().replace(" ", "_").replace("(", "").replace(")", "")
                summary[key] = parts[1]
        if summary:
            metrics["summary"] = summary

    findings_match = re.search(
        r"### Findings by Category\s*\n\s*\|[^\n]+\n\s*\|[-|\s]+\n((?:\s*\|[^\n]+\n)+)",
        section,
    )
    if findings_match:
        findings = []
        for line in findings_match.group(1).strip().split("\n"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 3:
                category = re.sub(r"[^\w\s]", "", parts[0]).strip()
                findings.append(
                    {
                        "category": category,
                        "total": parts[1],
                        "validators": parts[2],
                    }
                )
        if findings:
            metrics["findings_by_category"] = findings

    validator_match = re.search(
        r"### Per-Validator Breakdown\s*\n\s*\|[^\n]+\n\s*\|[-|\s]+\n((?:\s*\|[^\n]+\n)+)",
        section,
    )
    if validator_match:
        validators = []
        for line in validator_match.group(1).strip().split("\n"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 7:
                validators.append(
                    {
                        "id": parts[0],
                        "score": parts[1],
                        "critical": parts[2],
                        "enhancement": parts[3],
                        "optimization": parts[4],
                        "llm_opt": parts[5],
                        "total": parts[6],
                    }
                )
        if validators:
            metrics["validators"] = validators

    verdicts_match = re.search(
        r"### Verdicts\s*\n((?:\s*-[^\n]+\n)+)",
        section,
    )
    if verdicts_match:
        verdicts = {}
        for line in verdicts_match.group(1).strip().split("\n"):
            match = re.match(r"-\s*\*\*(.+?)\*\*:\s*(.+)", line.strip())
            if match:
                verdicts[match.group(1)] = match.group(2)
        if verdicts:
            metrics["verdicts"] = verdicts

    return metrics if metrics else None


def _process_validation_synthesis_file(path: Path) -> dict[str, Any] | None:
    """Process a single story validation synthesis markdown file.

    Args:
        path: Path to synthesis-{epic}-{story}-{timestamp}.md file.

    Returns:
        Condensed synthesis dict or None on error.

    """
    try:
        content = path.read_text()
    except OSError as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None

    frontmatter, body = _parse_synthesis_frontmatter(content)

    epic = frontmatter.get("epic")
    story = frontmatter.get("story")

    if epic is None or story is None:
        match = re.match(r"synthesis-(\d+)-(\d+)-", path.name)
        if match:
            epic = int(match.group(1))
            story = int(match.group(2))
        else:
            return None

    result: dict[str, Any] = {
        "story": story,
    }

    det_metrics = _extract_deterministic_metrics(content)
    if det_metrics:
        result["metrics"] = det_metrics

    summary = _extract_section(body, "## Synthesis Summary")
    if summary:
        result["summary"] = summary

    quality_section = _extract_section(body, "## Validations Quality")
    if quality_section:
        quality = _parse_quality_table(quality_section)
        if quality:
            result["quality"] = quality

    verified_section = _extract_section(body, "## Issues Verified (by severity)")
    if not verified_section:
        verified_section = _extract_section(body, "## Issues Verified")
    if verified_section:
        verified = _clean_issues_verified(verified_section)
        if verified:
            result["verified"] = verified

    dismissed_section = _extract_section(body, "## Issues Dismissed")
    if dismissed_section:
        dismissed = _clean_issues_dismissed(dismissed_section)
        if dismissed:
            result["dismissed"] = dismissed

    return {"epic": epic, "data": result}


def _process_all_validation_syntheses(
    synthesis_files: list[Path],
) -> dict[int, list[dict[str, Any]]]:
    """Process all validation synthesis files and group by epic.

    Args:
        synthesis_files: List of synthesis markdown file paths.

    Returns:
        Dict mapping epic_num to list of condensed story syntheses.

    """
    by_epic: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for path in synthesis_files:
        result = _process_validation_synthesis_file(path)
        if result:
            epic = result["epic"]
            by_epic[epic].append(result["data"])

    for epic in by_epic:
        by_epic[epic].sort(key=lambda x: x.get("story", 0))

    return dict(by_epic)


def _save_synthesis_summaries(
    by_epic: dict[int, list[dict[str, Any]]],
    output_dir: Path,
    timestamp: str,
    prefix: str = "epic",
) -> list[Path]:
    """Save condensed synthesis summaries per epic.

    Args:
        by_epic: Dict mapping epic_num to list of story syntheses.
        output_dir: Directory for output files.
        timestamp: Timestamp string for filename.
        prefix: Filename prefix (e.g., "epic" or "validation-epic").

    Returns:
        List of created file paths.

    """
    created_files: list[Path] = []

    for epic_num, stories in sorted(by_epic.items()):
        output = {
            "epic": epic_num,
            "story_count": len(stories),
            "stories": stories,
        }

        filename = f"{prefix}-{epic_num}-synthesis-{timestamp}.json"
        output_path = output_dir / filename
        content = json.dumps(output, separators=(",", ":"), ensure_ascii=False)
        _atomic_write_text(output_path, content)
        created_files.append(output_path)

    return created_files


def _build_index(
    project_path: Path,
    timestamp: str,
    benchmark_path: Path | None,
    code_review_files: list[Path],
    validation_files: list[Path],
) -> dict[str, Any]:
    """Build index of all generated files.

    Args:
        project_path: Project root path.
        timestamp: Generation timestamp.
        benchmark_path: Path to benchmark summary file.
        code_review_files: List of code review synthesis files.
        validation_files: List of validation synthesis files.

    Returns:
        Index dict with file references and summary stats.

    """
    index: dict[str, Any] = {
        "generated_at": timestamp,
        "project": project_path.name,
        "files": {},
    }

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(project_path))
        except ValueError:
            return str(p)

    if benchmark_path and benchmark_path.exists():
        try:
            with open(benchmark_path) as f:
                bench_data = json.load(f)
            index["files"]["benchmarks"] = {
                "path": rel(benchmark_path),
                "stats": {
                    "stories": bench_data.get("meta", {}).get("stories", 0),
                    "evals": bench_data.get("meta", {}).get("evals", 0),
                    "total_time_min": bench_data.get("meta", {}).get("total_time_min", 0),
                    "models": bench_data.get("meta", {}).get("models", []),
                },
            }
        except (json.JSONDecodeError, OSError):
            index["files"]["benchmarks"] = {"path": rel(benchmark_path)}

    if code_review_files:
        cr_entries = []
        total_stories = 0
        for cr_file in sorted(code_review_files):
            try:
                with open(cr_file) as fp:
                    data = json.load(fp)
                epic = data.get("epic", 0)
                story_count = data.get("story_count", 0)
                total_stories += story_count
                cr_entries.append(
                    {
                        "epic": epic,
                        "stories": story_count,
                        "path": rel(cr_file),
                    }
                )
            except (json.JSONDecodeError, OSError):
                cr_entries.append({"path": rel(cr_file)})

        index["files"]["code_reviews"] = {
            "total_stories": total_stories,
            "epics": cr_entries,
        }

    if validation_files:
        val_entries = []
        total_stories = 0
        for val_file in sorted(validation_files):
            try:
                with open(val_file) as fp:
                    data = json.load(fp)
                epic = data.get("epic", 0)
                story_count = data.get("story_count", 0)
                total_stories += story_count
                val_entries.append(
                    {
                        "epic": epic,
                        "stories": story_count,
                        "path": rel(val_file),
                    }
                )
            except (json.JSONDecodeError, OSError):
                val_entries.append({"path": rel(val_file)})

        index["files"]["validations"] = {
            "total_stories": total_stories,
            "epics": val_entries,
        }

    return index


# =============================================================================
# BenchmarkPreparer Class
# =============================================================================


class BenchmarkPreparer:
    """Prepares benchmark data for LLM analysis.

    Supports two modes:
    - project: Traditional single-project consolidation (backward compat)
    - experiments: Multi-fixture experiment consolidation

    Usage:
        # Project mode (backward compat)
        preparer = BenchmarkPreparer(Path("./my-project"), mode="project")
        result = preparer.prepare_project()

        # Experiments mode
        preparer = BenchmarkPreparer(Path("./my-project"), mode="experiments")
        results = preparer.prepare_experiments()

    """

    def __init__(
        self,
        base_dir: Path,
        mode: Literal["project", "experiments"] = "project",
    ) -> None:
        """Initialize the preparer.

        Args:
            base_dir: Project root or experiments base directory.
            mode: Operating mode ("project" or "experiments").

        Raises:
            ConfigError: If base_dir does not exist.

        """
        self._base_dir = base_dir.resolve()
        self._mode = mode

        if not self._base_dir.exists():
            raise ConfigError(f"Base directory does not exist: {self._base_dir}")

    @property
    def mode(self) -> Literal["project", "experiments"]:
        """Return the operating mode."""
        return self._mode

    @property
    def base_dir(self) -> Path:
        """Return the base directory."""
        return self._base_dir

    def prepare_project(
        self,
        output_path: Path | None = None,
        stdout: bool = False,
    ) -> PrepareResult:
        """Prepare benchmark summary for single project.

        Args:
            output_path: Custom output path (default: docs/benchmark-summary-{ts}.json).
            stdout: If True, output to stdout instead of file.

        Returns:
            PrepareResult with summary statistics.

        Raises:
            ConfigError: If no benchmark or synthesis files found.

        """
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

        # Find files
        (
            benchmark_files,
            mapping_files,
            code_review_syntheses,
            validation_syntheses,
        ) = _find_project_files(self._base_dir)

        total_syntheses = len(code_review_syntheses) + len(validation_syntheses)
        if not benchmark_files and total_syntheses == 0:
            raise ConfigError(f"No benchmark or synthesis files found in {self._base_dir}")

        logger.info(
            "Found %d benchmark files, %d mapping files, "
            "%d code-review syntheses, %d validation syntheses",
            len(benchmark_files),
            len(mapping_files),
            len(code_review_syntheses),
            len(validation_syntheses),
        )

        # Load mappings and build lookup
        mappings = _load_validation_mappings(mapping_files)
        model_lookup = _build_model_lookup(mappings)

        # Process benchmarks
        stories, models_raw, phases_raw = _process_benchmarks(benchmark_files, model_lookup)

        # Calculate aggregates
        model_aggs = _calculate_model_aggregates(models_raw)
        phase_aggs = _calculate_phase_aggregates(phases_raw)
        rankings = _calculate_rankings(model_aggs)
        correlations = _calculate_correlations(models_raw)

        # Build summary
        summary = _build_summary(
            self._base_dir, stories, model_aggs, phase_aggs, rankings, correlations
        )

        # Track created files for index
        created_benchmark_path: Path | None = None
        created_code_review_files: list[Path] = []
        created_validation_files: list[Path] = []

        # Determine output path
        if stdout:
            final_output_path = Path("/dev/null")  # Placeholder for stdout
        elif output_path:
            final_output_path = output_path
        else:
            docs_dir = self._base_dir / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            final_output_path = docs_dir / f"benchmark-summary-{timestamp}.json"

        # Output minified JSON for benchmarks
        if benchmark_files:
            output = json.dumps(summary, separators=(",", ":"), ensure_ascii=False)

            if stdout:
                print(output)
            else:
                _atomic_write_text(final_output_path, output)
                created_benchmark_path = final_output_path
                logger.info("Written benchmark summary to %s", final_output_path)

        # Process and save code review synthesis files
        if code_review_syntheses:
            syntheses_by_epic = _process_all_syntheses(code_review_syntheses)

            if syntheses_by_epic:
                if not stdout:
                    impl_artifacts = self._base_dir / "_bmad-output" / "implementation-artifacts"
                    code_reviews_dir = impl_artifacts / "code-reviews"
                    code_reviews_dir.mkdir(parents=True, exist_ok=True)
                    created_code_review_files = _save_synthesis_summaries(
                        syntheses_by_epic, code_reviews_dir, timestamp, prefix="reviews-epic"
                    )
                    for f in created_code_review_files:
                        logger.info("Written code review synthesis to %s", f)
                else:
                    all_syntheses = {
                        "type": "code-review",
                        "epics": [
                            {"epic": epic, "story_count": len(s), "stories": s}
                            for epic, s in sorted(syntheses_by_epic.items())
                        ],
                    }
                    print(json.dumps(all_syntheses, separators=(",", ":"), ensure_ascii=False))

        # Process and save story validation synthesis files
        if validation_syntheses:
            validations_by_epic = _process_all_validation_syntheses(validation_syntheses)

            if validations_by_epic:
                if not stdout:
                    impl_artifacts = self._base_dir / "_bmad-output" / "implementation-artifacts"
                    validations_dir = impl_artifacts / "story-validations"
                    validations_dir.mkdir(parents=True, exist_ok=True)
                    created_validation_files = _save_synthesis_summaries(
                        validations_by_epic, validations_dir, timestamp, prefix="validations-epic"
                    )
                    for f in created_validation_files:
                        logger.info("Written validation synthesis to %s", f)
                else:
                    all_validations = {
                        "type": "story-validation",
                        "epics": [
                            {"epic": epic, "story_count": len(s), "stories": s}
                            for epic, s in sorted(validations_by_epic.items())
                        ],
                    }
                    print(json.dumps(all_validations, separators=(",", ":"), ensure_ascii=False))

        # Generate index file
        has_created_files = (
            created_benchmark_path or created_code_review_files or created_validation_files
        )
        if not stdout and has_created_files:
            index = _build_index(
                self._base_dir,
                timestamp,
                created_benchmark_path,
                created_code_review_files,
                created_validation_files,
            )
            index_path = self._base_dir / "docs" / f"benchmark-index-{timestamp}.json"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_content = json.dumps(index, separators=(",", ":"), ensure_ascii=False)
            _atomic_write_text(index_path, index_content)
            logger.info("Written index to %s", index_path)

        # Calculate result statistics
        total_evals = sum(a["evals"] for a in model_aggs.values())
        total_time_ms = sum(p["total_ms"] for p in phase_aggs.values())

        return PrepareResult(
            fixture_or_project=self._base_dir.name,
            output_path=final_output_path,
            runs_processed=len(stories),
            evals_count=total_evals,
            total_time_minutes=round(total_time_ms / 60000, 1),
            models=list(model_aggs.keys()),
            generated_at=datetime.now(UTC),
        )

    def prepare_experiments(
        self,
        output_dir: Path | None = None,
    ) -> dict[str, PrepareResult]:
        """Prepare benchmark summaries for experiment runs.

        Groups runs by fixture and generates per-fixture summaries.

        Args:
            output_dir: Directory for output files (default: base_dir/docs/).

        Returns:
            Dict mapping fixture name to PrepareResult.

        """
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

        # Discover runs
        runs = self._discover_runs()
        if not runs:
            logger.warning("No experiment runs found in %s/experiments/runs/", self._base_dir)
            return {}

        # Load run data
        run_data_list: list[RunData] = []
        for run_dir in runs:
            run_data = self._load_run_data(run_dir)
            if run_data:
                run_data_list.append(run_data)
            else:
                logger.warning("Skipping run %s: invalid or missing manifest", run_dir.name)

        if not run_data_list:
            logger.warning("No valid experiment runs found")
            return {}

        # Group by fixture
        by_fixture = self._group_by_fixture(run_data_list)

        # Determine output directory
        if output_dir is None:
            output_dir = self._base_dir / "docs"

        # Guard against file-like paths being passed as directory
        if output_dir.suffix == ".json":
            logger.warning(
                "Output directory '%s' looks like a file path. "
                "In experiments mode, this argument must be a directory.",
                output_dir,
            )

        output_dir.mkdir(parents=True, exist_ok=True)

        # Process each fixture group
        results: dict[str, PrepareResult] = {}
        all_summaries: dict[str, dict[str, Any]] = {}

        for fixture_name, fixture_runs in by_fixture.items():
            logger.info("Processing fixture '%s' with %d runs", fixture_name, len(fixture_runs))

            # Collect all benchmark files from all runs in this fixture group
            all_benchmark_files: list[Path] = []
            all_mapping_files: list[Path] = []
            all_code_review_syntheses: list[Path] = []
            all_validation_syntheses: list[Path] = []

            for run in fixture_runs:
                all_benchmark_files.extend(run.benchmark_files)
                all_mapping_files.extend(run.mapping_files)
                all_code_review_syntheses.extend(run.code_review_syntheses)
                all_validation_syntheses.extend(run.validation_syntheses)

            has_no_files = (
                not all_benchmark_files
                and not all_code_review_syntheses
                and not all_validation_syntheses
            )
            if has_no_files:
                logger.warning(
                    "No benchmark or synthesis files found for fixture '%s'",
                    fixture_name,
                )
                continue

            # Load mappings and build lookup
            mappings = _load_validation_mappings(all_mapping_files)
            model_lookup = _build_model_lookup(mappings)

            # Process benchmarks - for experiments mode, track by run_id
            stories_by_run: dict[str, dict[str, list[dict[str, Any]]]] = {}
            models_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
            phases_raw: dict[str, list[int]] = defaultdict(list)

            for run in fixture_runs:
                stories_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)

                for path in run.benchmark_files:
                    record = _load_benchmark_record(path)
                    if not record:
                        continue

                    story_info = record.get("story", {})
                    epic = story_info.get("epic_num", 0)
                    story_num = story_info.get("story_num", 0)
                    story_key = f"{epic}-{story_num}"

                    workflow = record.get("workflow", {})
                    phase_id = workflow.get("id", "unknown")

                    metrics = _extract_essential_metrics(record)

                    if metrics.get("dur_ms"):
                        phases_raw[phase_id].append(metrics["dur_ms"])

                    session_id = metrics.get("session_id", "")
                    if session_id in model_lookup:
                        metrics["model_resolved"] = model_lookup[session_id]["model"]
                    else:
                        metrics["model_resolved"] = metrics.get("model", "unknown")

                    stories_raw[story_key].append(metrics)

                    model_name = metrics["model_resolved"]
                    models_raw[model_name].append(metrics)

                stories_by_run[run.run_id] = dict(stories_raw)

            # Calculate aggregates
            model_aggs = _calculate_model_aggregates(dict(models_raw))
            phase_aggs = _calculate_phase_aggregates(dict(phases_raw))
            rankings = _calculate_rankings(model_aggs)
            correlations = _calculate_correlations(dict(models_raw))

            # Build summary with experiments mode schema (stories grouped by run)
            total_evals = sum(a["evals"] for a in model_aggs.values())
            total_time_ms = sum(p["total_ms"] for p in phase_aggs.values())

            # Count unique stories across all runs
            all_story_keys: set[str] = set()
            for run_stories in stories_by_run.values():
                all_story_keys.update(run_stories.keys())

            summary: dict[str, Any] = {
                "meta": {
                    "project": fixture_name,
                    "fixture": fixture_name,
                    "run_count": len(fixture_runs),
                    "stories": len(all_story_keys),
                    "evals": total_evals,
                    "total_time_min": round(total_time_ms / 60000, 1),
                    "models": list(model_aggs.keys()),
                },
                "phases": phase_aggs,
                "models": model_aggs,
                "stories": {},
                "rankings": rankings,
            }

            if correlations:
                summary["correlations"] = correlations

            # Build stories structure with runs nested
            stories_output: dict[str, dict[str, Any]] = {}
            for run_id, run_stories in stories_by_run.items():
                for story_key, records in run_stories.items():
                    if story_key not in stories_output:
                        stories_output[story_key] = {"runs": {}}
                    stories_output[story_key]["runs"][run_id] = _condense_story_data(records)

            summary["stories"] = dict(sorted(stories_output.items()))

            # Process synthesis files (AC11)
            if all_code_review_syntheses:
                cr_by_epic = _process_all_syntheses(all_code_review_syntheses)
                if cr_by_epic:
                    summary["code_reviews"] = {
                        str(epic): stories for epic, stories in cr_by_epic.items()
                    }

            if all_validation_syntheses:
                val_by_epic = _process_all_validation_syntheses(all_validation_syntheses)
                if val_by_epic:
                    summary["story_validations"] = {
                        str(epic): stories for epic, stories in val_by_epic.items()
                    }

            # Save summary
            output_path = output_dir / f"benchmark-{fixture_name}-{timestamp}.json"
            output = json.dumps(summary, separators=(",", ":"), ensure_ascii=False)
            _atomic_write_text(output_path, output)
            logger.info("Written fixture summary to %s", output_path)

            all_summaries[fixture_name] = summary

            results[fixture_name] = PrepareResult(
                fixture_or_project=fixture_name,
                output_path=output_path,
                runs_processed=len(fixture_runs),
                evals_count=total_evals,
                total_time_minutes=round(total_time_ms / 60000, 1),
                models=list(model_aggs.keys()),
                generated_at=datetime.now(UTC),
            )

        # Generate index file
        if results:
            index = self._generate_index(results, output_dir, timestamp, all_summaries)
            index_path = output_dir / f"benchmark-index-{timestamp}.json"
            index_content = json.dumps(index, separators=(",", ":"), ensure_ascii=False)
            _atomic_write_text(index_path, index_content)
            logger.info("Written experiments index to %s", index_path)

        return results

    def _discover_runs(self) -> list[Path]:
        """Discover all run directories in experiments/runs/."""
        runs_dir = self._base_dir / "experiments" / "runs"
        if not runs_dir.exists():
            return []
        return [d for d in runs_dir.iterdir() if d.is_dir()]

    def _load_run_data(self, run_dir: Path) -> RunData | None:
        """Load data from a single run directory.

        Args:
            run_dir: Path to run directory.

        Returns:
            RunData with extracted information or None if manifest invalid/missing.

        """
        manifest_path = run_dir / "manifest.yaml"
        if not manifest_path.exists():
            return None

        # Load manifest
        try:
            from bmad_assist.experiments.manifest import ManifestManager

            manager = ManifestManager(run_dir)
            manifest = manager.load()
        except (ConfigError, ValidationError) as e:
            logger.warning("Failed to load manifest for %s: %s", run_dir.name, e)
            return None

        # Get fixture name
        fixture = manifest.input.fixture if manifest.input else "unknown"

        # Check for metrics.yaml
        metrics_file = run_dir / "metrics.yaml"
        metrics_path: Path | None = metrics_file if metrics_file.exists() else None

        # Find benchmark files (check output/ first, then fixture-snapshot/)
        benchmark_files: list[Path] = []
        mapping_files: list[Path] = []
        code_review_syntheses: list[Path] = []
        validation_syntheses: list[Path] = []

        # Primary location: output/
        output_dir = run_dir / "output"
        if output_dir.exists():
            benchmark_dir = output_dir / "benchmarks"
            if benchmark_dir.exists():
                benchmark_files.extend(benchmark_dir.glob("**/*.yaml"))

            cache_dir = output_dir / ".bmad-assist" / "cache"
            if cache_dir.exists():
                mapping_files.extend(cache_dir.glob("validation-mapping-*.json"))

            code_reviews_dir = output_dir / "code-reviews"
            if code_reviews_dir.exists():
                code_review_syntheses.extend(code_reviews_dir.glob("synthesis-*.md"))

            validations_dir = output_dir / "story-validations"
            if validations_dir.exists():
                validation_syntheses.extend(validations_dir.glob("synthesis-*.md"))

        # Fallback location: fixture-snapshot/
        snapshot_dir = run_dir / "fixture-snapshot"
        if snapshot_dir.exists():
            snapshot_impl = snapshot_dir / "_bmad-output" / "implementation-artifacts"
            snapshot_benchmarks = snapshot_impl / "benchmarks"
            if snapshot_benchmarks.exists() and not benchmark_files:
                benchmark_files.extend(snapshot_benchmarks.glob("**/*.yaml"))

            snapshot_cache = snapshot_dir / ".bmad-assist" / "cache"
            if snapshot_cache.exists() and not mapping_files:
                mapping_files.extend(snapshot_cache.glob("validation-mapping-*.json"))

            snapshot_code_reviews = snapshot_impl / "code-reviews"
            if snapshot_code_reviews.exists() and not code_review_syntheses:
                code_review_syntheses.extend(snapshot_code_reviews.glob("synthesis-*.md"))

            snapshot_validations = snapshot_impl / "story-validations"
            if snapshot_validations.exists() and not validation_syntheses:
                validation_syntheses.extend(snapshot_validations.glob("synthesis-*.md"))

        return RunData(
            run_id=run_dir.name,
            fixture=fixture,
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            benchmark_files=benchmark_files,
            mapping_files=mapping_files,
            code_review_syntheses=code_review_syntheses,
            validation_syntheses=validation_syntheses,
        )

    def _group_by_fixture(self, runs: list[RunData]) -> dict[str, list[RunData]]:
        """Group runs by fixture name.

        Args:
            runs: List of run data.

        Returns:
            Dict mapping fixture name to list of runs.

        """
        by_fixture: dict[str, list[RunData]] = defaultdict(list)
        for run in runs:
            by_fixture[run.fixture].append(run)
        return dict(by_fixture)

    def _generate_index(
        self,
        results: dict[str, PrepareResult],
        output_dir: Path,
        timestamp: str,
        summaries: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate index file for experiments mode.

        Args:
            results: Per-fixture prepare results.
            output_dir: Output directory.
            timestamp: Generation timestamp.
            summaries: Per-fixture summary dicts.

        Returns:
            Index dict.

        """
        # Aggregate totals across all fixtures
        total_evals = sum(r.evals_count for r in results.values())
        total_time = sum(r.total_time_minutes for r in results.values())
        total_runs = sum(r.runs_processed for r in results.values())

        # Collect all models
        all_models: set[str] = set()
        for r in results.values():
            all_models.update(r.models)

        index: dict[str, Any] = {
            "generated_at": timestamp,
            "mode": "experiments",
            "aggregate": {
                "fixtures": len(results),
                "runs": total_runs,
                "evals": total_evals,
                "total_time_min": round(total_time, 1),
                "models": sorted(all_models),
            },
            "fixtures": {},
        }

        # Per-fixture entries
        for fixture_name, result in results.items():
            summary = summaries.get(fixture_name, {})
            meta = summary.get("meta", {})

            try:
                rel_path = result.output_path.relative_to(output_dir.parent)
            except ValueError:
                rel_path = result.output_path

            index["fixtures"][fixture_name] = {
                "path": str(rel_path),
                "runs": result.runs_processed,
                "stories": meta.get("stories", 0),
                "evals": result.evals_count,
                "total_time_min": result.total_time_minutes,
                "models": result.models,
            }

        return index
